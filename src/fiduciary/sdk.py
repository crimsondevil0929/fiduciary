"""Governed tools in your own process: ``@gov.tool`` (1.14.0).

An agent's tool is often a Python function that writes to a database, a directory or an
API. Wrap it, and each call runs in a stage: the function gets a handle on the stage
instead of the system, its writes land there, and when it returns the stage is measured,
checked against the mandate, and committed or refused as a whole::

    import fiduciary

    gov = fiduciary.open()          # the deployment `fiduciary init --solo` wrote

    @gov.tool
    def discount_order(db: fiduciary.Sql, order_id: int, percent: int) -> str:
        \"\"\"Take a percentage off one order's total.\"\"\"
        (order,) = db.query("SELECT total FROM orders WHERE id = :id", {"id": order_id})
        db.execute(
            "UPDATE orders SET total = :total WHERE id = :id",
            {"total": order["total"] * (100 - percent) / 100, "id": order_id},
        )
        return f"order {order_id} discounted {percent}%"

    discount_order(order_id=500, percent=10)

What the call does:

- The arguments are checked against the tool's input schema, which is read from the
  function's signature (or given as ``input_schema=``).
- A stage opens on the system the deployment governs, and the function is handed its
  handle: :class:`Sql` for SQLite and PostgreSQL, :class:`Workspace` for a directory,
  :class:`Rest` for a REST API. A read through the handle sees the stage's own writes.
- When the function returns, the plan its writes add up to is measured and judged by the
  mandate in force. It commits, and the call returns the function's value; or it is
  refused, nothing changes, and the call raises :class:`Refused`, whose text is written
  for the agent to read.
- If the function raises, the stage is discarded: nothing changes and nothing is recorded.

Under a shadow mandate (``fiduciary init --shadow``) nothing is refused for the mandate's
bounds: each plan commits, and what would have refused it is recorded on the escrow chain,
in :attr:`Governor.observed`, and in ``fiduciary shadow report``. The agent is not told, so
it behaves as it would with no governor at all.

**The boundary.** Only what goes through the handle is staged. A function that opens its
own connection, writes a file by its own path, or calls an API directly does so outside
the stage: none of it is measured, checked or undone, and nothing records it. Keep every
write a tool makes on its handle.

To hand the tools to an agent framework, see :mod:`fiduciary.adapters`.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
import re
import types
import typing
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, TypeVar, overload

from interlock.engine import StageResult
from interlock.exceptions import CompensatedError, InterlockError
from interlock.feedback import AgentFeedback
from interlock.outbound import schema_problems
from interlock.types import Compensation, EffectKind

from fiduciary.exceptions import FiduciaryError

if TYPE_CHECKING:
    from interlock.engine import OpenStage

    from fiduciary.binding import MandateEngine
    from fiduciary.bridge import Fiduciary

__all__ = [
    "CONFIG_ENV",
    "STATE_DIRECTORY",
    "GovernedTool",
    "Governor",
    "Observation",
    "Refused",
    "Rest",
    "Sql",
    "Workspace",
    "open",
]

logger = logging.getLogger("fiduciary.sdk")

STATE_DIRECTORY: Final = ".fiduciary"
"""Where ``fiduciary init --solo`` writes a deployment, and where :func:`open` looks."""
CONFIG_ENV: Final = "FIDUCIARY_CONFIG"
"""A ``fiduciary.toml`` for :func:`open` to use when it is given none."""
INTENT: Final = "fiduciary sdk "
"""How a plan an SDK tool made names itself on the escrow chain: then the tool's name and
the first 16 hex of its source's SHA-256 (at most 79 characters, inside the chain's 80)."""


class Refused(FiduciaryError):  # noqa: N818 - `except fiduciary.Refused`, as the docs say it
    """The call changed nothing. ``str(exc)`` is written for the agent: what was refused,
    in terms of its own arguments, and what it may try instead.

    :ivar outcome: ``refused`` (the mandate's checks refused the plan), ``rejected`` (the
        arguments were refused before anything ran), ``compensated`` (written and put
        back), or ``error`` (the stage could not be completed).
    :ivar result: The stage's outcome, with the measured diff and the verdict, when the
        plan reached the checks: the operator's view, which names more than the agent's.
    """

    def __init__(self, text: str, *, outcome: str, result: StageResult | None = None) -> None:
        super().__init__(text)
        self.outcome = outcome
        self.result = result


@dataclass(frozen=True, slots=True)
class Observation:
    """A plan that committed under a shadow mandate, and what would have refused it."""

    tool: str
    plan_id: str
    would_refuse: tuple[str, ...]
    messages: tuple[str, ...]
    changes: int


# --------------------------------------------------------------------------
# handles
# --------------------------------------------------------------------------

_VERB: Final = re.compile(r"\s*(insert|update|delete)\b", re.I)
_TABLE: Final = re.compile(
    r"\s*(?:insert\s+(?:or\s+\w+\s+)?into|update(?:\s+or\s+\w+)?|delete\s+from)\s+"
    r"(?:\"?[A-Za-z_][A-Za-z0-9_]*\"?\.)?\"?([A-Za-z_][A-Za-z0-9_]*)\"?",
    re.I,
)


class _Handle:
    __slots__ = ("_stage",)

    def __init__(self, stage: OpenStage) -> None:
        self._stage = stage


class Sql(_Handle):
    """A SQLite or PostgreSQL database, as the stage holds it.

    Placeholders are the driver's: ``:name`` on SQLite, ``%(name)s`` on PostgreSQL.
    """

    __slots__ = ()

    def query(
        self, statement: str, parameters: Mapping[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Rows, read inside the stage: what this call has written is visible. Whatever
        the statement would change is rolled back before it returns."""
        rows: list[dict[str, Any]] = self._stage.read(statement, dict(parameters or {}))
        return rows

    def execute(
        self,
        statement: str,
        parameters: Mapping[str, Any] | None = None,
        *,
        table: str | None = None,
    ) -> int:
        """Run one ``INSERT``, ``UPDATE`` or ``DELETE`` in the stage, and return the rows
        it reports. The table is read from the statement unless ``table`` names it.

        :raises ValueError: On any other statement, or a table that cannot be read from it.
        :raises interlock.exceptions.InterlockError: On a statement the stage refuses.
        """
        verb = _VERB.match(statement)
        if verb is None:
            raise ValueError(
                "execute() runs one INSERT, UPDATE or DELETE; read with query(), and leave "
                "schema changes to a migration"
            )
        named = table
        if named is None:
            found = _TABLE.match(statement)
            if found is None:
                raise ValueError(f"cannot tell which table {statement[:60]!r} writes: pass table=")
            named = found.group(1)
        outcome = self._stage.add(
            EffectKind(verb.group(1).lower()),
            table=named,
            statement=statement,
            parameters=dict(parameters or {}),
        )
        return outcome.rows_affected


class Workspace(_Handle):
    """A directory, as the stage's private copy holds it. Paths are relative to it."""

    __slots__ = ()

    def read(self, path: str) -> str | None:
        """The file's text as the stage holds it, or ``None`` when there is none."""
        data = self.read_bytes(path)
        return None if data is None else data.decode("utf-8")

    def read_bytes(self, path: str) -> bytes | None:
        found: bytes | None = self._stage.read(path)
        return found

    def paths(self) -> list[str]:
        """Every file the stage holds, sorted."""
        lister = getattr(self._stage.substrate, "paths", None)
        if not callable(lister):
            raise TypeError("this stage cannot list its files")
        listed: list[str] = lister(self._stage.handle)
        return listed

    def write(self, path: str, text: str) -> None:
        """Create or replace a file."""
        self._file(EffectKind.UPDATE, path, "write :path :text", text=text)

    def append(self, path: str, text: str) -> None:
        """Add text to the end of a file, creating it if there is none."""
        self._file(EffectKind.UPDATE, path, "append :path :text", text=text)

    def replace(self, path: str, old: str, new: str) -> None:
        """Replace the one occurrence of ``old`` in a file."""
        self._file(EffectKind.UPDATE, path, "replace :path :old :new", old=old, new=new)

    def delete(self, path: str) -> None:
        self._file(EffectKind.DELETE, path, "delete :path")

    def move(self, path: str, to: str) -> None:
        """Rename a file. Nothing may be at ``to``."""
        self._file(EffectKind.UPDATE, path, "move :path :to", to=to)

    def _file(self, kind: EffectKind, path: str, statement: str, **values: str) -> None:
        classify = getattr(self._stage.substrate, "classify", None)
        kind_of = classify(path) if callable(classify) else "other"
        self._stage.add(
            kind, table=kind_of, statement=statement, parameters={"path": path, **values}
        )


class Rest(_Handle):
    """A REST API, as the stage predicts it. Nothing is sent until the plan commits; each
    write is then read back, and put back if the API did other than predicted.

    There is no read: the stage holds predictions, not the API. Read the API yourself
    before you write; ``expect_version`` pins a write to the version you read.
    """

    __slots__ = ()

    def create(self, kind: str, resource: Mapping[str, Any]) -> None:
        """Create one resource of ``kind`` from ``resource``."""
        self._add(
            EffectKind.INSERT,
            kind,
            f"create {kind} {_literal(resource)}",
            {},
            f"void {kind}",
        )

    def patch(self, kind: str, resource_id: str, changes: Mapping[str, Any]) -> None:
        """Merge ``changes`` into one resource."""
        self._add(
            EffectKind.UPDATE,
            kind,
            f"patch {kind} :id {_literal(changes)}",
            {"id": resource_id},
            f"restore {kind}",
        )

    def expect_version(self, kind: str, resource_id: str, version: str) -> None:
        """Refuse the plan unless the resource is still at ``version``."""
        self._add(
            EffectKind.UPDATE,
            kind,
            f"expect-version {kind} :id :version",
            {"id": resource_id, "version": version},
            f"restore {kind}",
        )

    def _add(
        self,
        kind: EffectKind,
        table: str,
        statement: str,
        parameters: dict[str, Any],
        undo: str,
    ) -> None:
        self._stage.add(
            kind,
            table=table,
            statement=statement,
            parameters=parameters,
            compensation=Compensation(undo, idempotency_key=f"sdk-{uuid.uuid4().hex}"),
        )


def _literal(document: Mapping[str, Any]) -> str:
    """A JSON template as a quoted statement literal (``''`` escapes a quote)."""
    text = json.dumps(dict(document), sort_keys=True, separators=(",", ":"))
    return "'" + text.replace("'", "''") + "'"


_HANDLES: Final[Mapping[str, type[_Handle]]] = {
    "sqlite": Sql,
    "postgres": Sql,
    "filesystem": Workspace,
    "rest": Rest,
}


# --------------------------------------------------------------------------
# input schemas, from a signature
# --------------------------------------------------------------------------

_TYPES: Final[Mapping[Any, dict[str, Any]]] = {
    str: {"type": "string"},
    int: {"type": "integer"},
    float: {"type": "number"},
    bool: {"type": "boolean"},
    dict: {"type": "object"},
}


def _schema_of(annotation: Any, where: str) -> dict[str, Any]:
    if annotation in _TYPES:
        return dict(_TYPES[annotation])
    origin = typing.get_origin(annotation)
    arguments = typing.get_args(annotation)
    if origin is list and len(arguments) == 1:
        return {"type": "array", "items": _schema_of(arguments[0], where)}
    if origin is dict:
        return {"type": "object"}
    if origin is typing.Literal and all(isinstance(a, str) for a in arguments):
        return {"type": "string", "enum": list(arguments)}
    if origin in (typing.Union, types.UnionType) and type(None) in arguments:
        rest = [a for a in arguments if a is not type(None)]
        if len(rest) == 1:
            found = _schema_of(rest[0], where)
            found["type"] = [found["type"], "null"]
            return found
    raise TypeError(
        f"{where}: annotate it as str, int, float, bool, a list of one of them, a dict, or a "
        f"Literal of strings, or give the tool an input_schema"
    )


def input_schema(function: Callable[..., Any]) -> dict[str, Any]:
    """The JSON schema of a tool's arguments: every parameter after the first (the
    handle), each typed by its annotation, required unless it has a default."""
    signature = inspect.signature(function)
    hints = typing.get_type_hints(function)
    parameters = list(signature.parameters.values())[1:]
    properties: dict[str, Any] = {}
    required: list[str] = []
    for parameter in parameters:
        where = f"{function.__name__}({parameter.name})"
        if parameter.kind not in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY):
            raise TypeError(f"{where}: a tool takes keyword arguments only")
        if parameter.name not in hints:
            raise TypeError(f"{where}: annotate it, or give the tool an input_schema")
        properties[parameter.name] = _schema_of(hints[parameter.name], where)
        if parameter.default is parameter.empty:
            required.append(parameter.name)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


# --------------------------------------------------------------------------
# the governor
# --------------------------------------------------------------------------

F = TypeVar("F", bound=Callable[..., Any])


class GovernedTool:
    """A function wrapped by :meth:`Governor.tool`. Call it with the tool's arguments, by
    name; the handle is passed for you."""

    def __init__(
        self,
        governor: Governor,
        function: Callable[..., Any],
        *,
        name: str,
        description: str,
        schema: Mapping[str, Any],
    ) -> None:
        self._governor = governor
        self._function = function
        self.name = name
        self.description = description
        self.input_schema: dict[str, Any] = dict(schema)
        self.code_digest = _code_digest(function)
        """The SHA-256 of the function's source: each plan the tool makes names its first 16
        hex on the escrow chain, so an audit bundle says which code made it."""
        self.__doc__ = function.__doc__
        self.__name__ = name
        self.__wrapped__ = function

    def __repr__(self) -> str:
        return f"<governed tool {self.name}>"

    def __call__(self, **arguments: Any) -> Any:
        return self._governor._call(self, arguments)

    def call_for_agent(self, arguments: Mapping[str, Any]) -> tuple[str, bool]:
        """Call the tool as an agent framework does, and say what happened in words the
        agent reads: ``(text, is_error)``. A :class:`Refused` call is an error, never an
        exception; any other exception propagates."""
        try:
            value = self(**dict(arguments))
        except Refused as exc:
            return str(exc), True
        if isinstance(value, str):
            return value, False
        return json.dumps(value, sort_keys=True, default=str), False


class Governor:
    """One deployment's mandate, governing tools in this process. :func:`open` makes one.

    Not for use from several threads at once: a stage holds its system's write lock, or
    its private copy, for the length of one call.
    """

    def __init__(self, fiduciary: Fiduciary, mandate_id: str, *, config: Path) -> None:
        self._fiduciary = fiduciary
        self._mandate_id = mandate_id
        self.config = config
        self.trajectory = f"fiduciary-sdk-{uuid.uuid4()}"
        self.tools: dict[str, GovernedTool] = {}
        self.observed: list[Observation] = []
        """What a shadow mandate let through in this process, call by call."""

    # -- what is governed -----------------------------------------------------

    def _engine(self) -> MandateEngine:
        self._fiduciary.binder.follow()  # what another process bound, at this call
        return self._fiduciary.binder.engine(self._mandate_id)

    @property
    def mandate_id(self) -> str:
        return self._mandate_id

    @property
    def shadow(self) -> bool:
        """Whether the mandate in force watches and does not refuse."""
        return self._engine().compiled.shadow

    @property
    def scope(self) -> str:
        """The scope every call is billed to and recorded under: the mandate's subject."""
        return self._engine().compiled.subject

    # -- tools ----------------------------------------------------------------

    @overload
    def tool(self, function: F, /) -> GovernedTool: ...

    @overload
    def tool(
        self,
        function: None = None,
        /,
        *,
        name: str | None = None,
        description: str | None = None,
        input_schema: Mapping[str, Any] | None = None,
    ) -> Callable[[F], GovernedTool]: ...

    def tool(
        self,
        function: F | None = None,
        /,
        *,
        name: str | None = None,
        description: str | None = None,
        input_schema: Mapping[str, Any] | None = None,
    ) -> GovernedTool | Callable[[F], GovernedTool]:
        """Govern ``function``: ``@gov.tool``, or ``@gov.tool(name=..., ...)``.

        The function's first parameter is the handle; the others are the tool's
        arguments. Its name and docstring are the tool's unless given.
        """

        def wrap(found: F) -> GovernedTool:
            tool_name = name or found.__name__
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", tool_name):
                raise ValueError(f"{tool_name!r}: a tool's name is a lowercase identifier")
            if tool_name in self.tools:
                raise ValueError(f"a tool called {tool_name} is already governed here")
            text = description or inspect.getdoc(found) or ""
            if not text:
                raise ValueError(f"{tool_name}: give it a docstring or a description")
            schema = dict(input_schema) if input_schema is not None else _input(found)
            governed = GovernedTool(self, found, name=tool_name, description=text, schema=schema)
            self.tools[tool_name] = governed
            return governed

        return wrap(function) if function is not None else wrap

    # -- one call ---------------------------------------------------------------

    def _call(self, tool: GovernedTool, arguments: Mapping[str, Any]) -> Any:
        problems = schema_problems(dict(arguments), tool.input_schema)
        if problems:
            raise Refused(
                "The arguments were refused: " + "; ".join(problems)[:1024] + ".",
                outcome="rejected",
            )
        engine = self._engine()
        stage = engine.stage(
            engine.compiled.subject,
            trajectory=self.trajectory,
            intent=f"{INTENT}{tool.name} {tool.code_digest[:16]}",
        )
        opened = stage.opened
        handle_type = _HANDLES.get(opened.substrate.substrate_id)
        if handle_type is None:
            stage.discard()
            raise FiduciaryError(
                f"tools in this process are governed on SQLite, PostgreSQL, a directory or a "
                f"REST API; this deployment stages on {opened.substrate.substrate_id}"
            )
        try:
            value = tool._function(handle_type(opened), **arguments)
        except InterlockError as exc:
            stage.discard()
            raise Refused(_told(exc), outcome="error") from exc
        except BaseException:
            stage.discard()
            raise
        try:
            result = stage.settle()
        except CompensatedError as exc:
            found = exc.result if isinstance(exc.result, StageResult) else None
            raise Refused(_told(exc), outcome="compensated", result=found) from exc
        except InterlockError as exc:
            raise Refused(_told(exc), outcome="error") from exc
        if result is None:
            return value  # it wrote nothing: nothing to judge, and nothing recorded
        if not result.committed:
            raise Refused(_shown(result.feedback), outcome="refused", result=result)
        if result.would_refuse:
            self._observe(tool, result)
        return value

    def _observe(self, tool: GovernedTool, result: StageResult) -> None:
        verdict = result.verdict
        messages = tuple(v.message for v in verdict.observed) if verdict is not None else ()
        found = Observation(
            tool=tool.name,
            plan_id=str(result.plan.plan_id),
            would_refuse=result.would_refuse,
            messages=messages,
            changes=result.diff.change_count if result.diff is not None else 0,
        )
        self.observed.append(found)
        logger.info(
            "shadow: %s plan %s would have been refused by %s: %s",
            tool.name,
            found.plan_id,
            ", ".join(found.would_refuse),
            "; ".join(messages),
        )

    def close(self) -> None:
        self._fiduciary.close()

    def __enter__(self) -> Governor:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _code_digest(function: Callable[..., Any]) -> str:
    """The SHA-256 of ``function``'s source, or of its qualified name where Python keeps no
    source (a function typed into a REPL)."""
    try:
        source = inspect.getsource(function)
    except (OSError, TypeError):
        source = f"{function.__module__}.{function.__qualname__}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _input(function: Callable[..., Any]) -> dict[str, Any]:
    if not list(inspect.signature(function).parameters):
        raise TypeError(f"{function.__name__}: its first parameter is the handle")
    return input_schema(function)


def _shown(feedback: AgentFeedback | None) -> str:
    """What the agent is told of a refusal: the blocking constraints, never what a shadow
    mandate only observed."""
    if feedback is None:
        return "The plan was refused; nothing changed."
    kept = tuple(c for c in feedback.constraints if not c.observed)
    return replace(feedback, constraints=kept).render()


def _told(exc: InterlockError) -> str:
    feedback = exc.feedback
    if isinstance(feedback, AgentFeedback):
        return _shown(feedback)
    return "The call did not complete; nothing changed."


def find_config(path: str | Path | None = None) -> Path:
    """The ``fiduciary.toml`` :func:`open` uses: ``path`` (a file, or a directory that
    holds one), else ``$FIDUCIARY_CONFIG``, else ``.fiduciary/fiduciary.toml`` in this
    directory or the nearest one above it.

    :raises FileNotFoundError: When there is none.
    """
    if path is None and os.environ.get(CONFIG_ENV):
        path = os.environ[CONFIG_ENV]
    if path is not None:
        found = Path(path)
        if found.is_dir():
            found = found / "fiduciary.toml"
        if not found.is_file():
            raise FileNotFoundError(f"no fiduciary.toml at {found}")
        return found.resolve()
    here = Path.cwd().resolve()
    for directory in (here, *here.parents):
        candidate = directory / STATE_DIRECTORY / "fiduciary.toml"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"no {STATE_DIRECTORY}/fiduciary.toml here or above: run `fiduciary init --solo` "
        f"first, or pass the deployment's fiduciary.toml"
    )


def open(path: str | Path | None = None, *, mandate: str | None = None) -> Governor:
    """Open a deployment to govern tools in this process.

    :param path: Its ``fiduciary.toml``, or the directory holding it (:func:`find_config`).
    :param mandate: The lineage to act under (its id, or its subject scope); the one in
        force when there is one, and it may be left out.
    :raises FileNotFoundError: When there is no deployment to open.
    :raises fiduciary.exceptions.FiduciaryError: When no single lineage is in force, or
        the configuration keeps sessions out of this process (``in_process = "no"``).
    """
    from fiduciary.bridge import Fiduciary

    config = find_config(path)
    fid = Fiduciary.from_config(config)
    try:
        if getattr(fid, "_in_process", "no") != "tool-calls-only":
            raise FiduciaryError(
                'tools run in this process only with in_process = "tool-calls-only" in '
                f"{config}: this deployment serves its agent out of process"
            )
        state = fid.log.state()
        if mandate is not None:
            mandate_id = fid._lineage(state, mandate)
        else:
            live = sorted(mid for mid, engine in fid.binder.engines().items() if not engine.closed)
            if len(live) != 1:
                raise FiduciaryError(
                    f"{len(live)} lineages are bound here: name one with mandate="
                    if live
                    else "no mandate is bound to this deployment"
                )
            (mandate_id,) = live
        fid.binder.engine(mandate_id)
    except BaseException:
        fid.close()
        raise
    return Governor(fid, mandate_id, config=config)


def describe(tools: Sequence[GovernedTool]) -> list[dict[str, Any]]:
    """Each tool as a tool list carries it: name, description, input schema."""
    return [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in tools
    ]

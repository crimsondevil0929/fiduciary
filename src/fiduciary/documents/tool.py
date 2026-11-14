"""The tool binding: what an agent may call, as a principal reviews it (§4.3).

The agent supplies arguments, validated against ``input`` before anything is
built. The statements, tables, sinks and operations are the binding's, pinned
by digest in the mandate: a change to any of them is a new digest, refused
until a mandate version pins it (F4-16).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, ClassVar, Final

from interlock.text import noun

from fiduciary import fidu1
from fiduciary.documents._base import IDENTIFIER, SCOPE, Document
from fiduciary.documents.mandate import OPERATION, SINK_NAME, TOOL
from fiduciary.exceptions import MalformedError
from fiduciary.fidu1 import Fields, Signature

__all__ = ["SCHEMA_KEYWORDS", "Effect", "Request", "ToolBinding", "check_schema", "placeholders"]

SCHEMA_KEYWORDS: Final = frozenset(
    {
        # Interlock's sink-schema subset (interlock.outbound): what it enforces,
        # and the annotations it carries without enforcing.
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "minLength",
        "maxLength",
        "pattern",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
        "title",
        "description",
        "$comment",
    }
)
_TYPES: Final = frozenset({"object", "array", "string", "integer", "boolean", "null"})
_SOURCE: Final = re.compile(r"\$(\.[A-Za-z_][A-Za-z0-9_]{0,63}|session\.(tenant|trajectory))")
_PLACEHOLDER: Final = re.compile(r"(?<![:\w]):([A-Za-z_][A-Za-z0-9_]*)")
_PYFORMAT: Final = re.compile(r"(?<!%)%\(([A-Za-z_][A-Za-z0-9_]*)\)s")
_EFFECT_ID: Final = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_NUMBER_PATH: Final = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]{0,63}(\[\])?(\.[A-Za-z_][A-Za-z0-9_]{0,63}(\[\])?){0,7}"
)
"""An argument's path (1.17.0): dotted names, ``[]`` for each item of an array."""
_UPSTREAM: Final = re.compile(r"[A-Za-z0-9_.:/-]{1,128}")
MAX_ITEMS: Final = 64


def check_schema(schema: object, path: str) -> None:
    """Refuse a schema Interlock's validator would not wholly enforce."""
    if not isinstance(schema, dict):
        raise MalformedError("type", path, "a schema is an object")
    unknown = sorted(set(schema) - SCHEMA_KEYWORDS)
    if unknown:
        raise MalformedError(
            "fields",
            path,
            f"unsupported {noun(len(unknown), 'keyword')} {', '.join(unknown)}: ignored, not "
            f"enforced",
        )
    kinds = schema.get("type")
    if kinds is not None:
        listed = [kinds] if isinstance(kinds, str) else kinds
        if not isinstance(listed, list) or not listed or not all(k in _TYPES for k in listed):
            raise MalformedError(
                "format",
                f"{path}.type",
                f"is one of {', '.join(sorted(_TYPES))}, or a list of them",
            )
    if "properties" in schema:
        properties = schema["properties"]
        if not isinstance(properties, dict):
            raise MalformedError("type", f"{path}.properties", "must be an object")
        for name, sub in properties.items():
            check_schema(sub, f"{path}.properties.{name}")
    if "items" in schema:
        check_schema(schema["items"], f"{path}.items")
    if "required" in schema:
        fidu1.check_strings(schema["required"], f"{path}.required", fidu1.TEXT, "a property name")
    if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], bool):
        raise MalformedError(
            "type", f"{path}.additionalProperties", "is true or false in this subset"
        )
    if "pattern" in schema:
        try:
            re.compile(fidu1.check_text(schema["pattern"], f"{path}.pattern"))
        except re.error as exc:
            raise MalformedError("format", f"{path}.pattern", f"does not compile: {exc}") from exc
    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        if key in schema:
            fidu1.check_integer(schema[key], f"{path}.{key}")
    for key in ("minimum", "maximum"):
        if key in schema:
            bound = schema[key]
            if isinstance(bound, str):
                fidu1.check_text(
                    bound,
                    f"{path}.{key}",
                    re.compile(r"-?(0|[1-9][0-9]{0,17})(\.[0-9]{1,8})?"),
                    "a decimal",
                )
            else:
                fidu1.check_integer(bound, f"{path}.{key}", -fidu1.MAX_SAFE_INTEGER)
    if "enum" in schema and not isinstance(schema["enum"], list):
        raise MalformedError("type", f"{path}.enum", "must be an array")


_ARGUMENT: Final = re.compile(r"\$\.[A-Za-z_][A-Za-z0-9_]{0,63}")
_DIMENSION: Final = re.compile(r"[a-z][a-z0-9_]{0,31}")


def _partitions(entry: Fields) -> dict[str, str]:
    """A step's declared partitions: each dimension, other than the tenant,
    labeled by one of the call's arguments."""
    if not entry.has("partitions"):
        return {}
    found = {}
    for dimension, path, source in entry.mapping("partitions", _DIMENSION):
        if dimension == "tenant":
            raise MalformedError("agreement", path, "the tenant is the binding's tenant field")
        found[dimension] = fidu1.check_text(source, path, _ARGUMENT, "an argument, $.<name>")
    if not found:
        raise MalformedError(
            "agreement", entry.path("partitions"), "is written only when it declares one"
        )
    return found


def placeholders(statement: str) -> tuple[str, ...]:
    """The named placeholders of a statement, outside its string literals,
    sorted: ``:name``, as Interlock's SQLite substrate binds them, or
    ``%(name)s``, as its PostgreSQL substrate does (psycopg's). A statement is
    written for one database, so it uses one style; a ``?`` placeholder is
    refused, since parameters bind by name.

    :raises ValueError: On a ``?`` outside a literal, or both styles in one
        statement.
    """
    outside = re.sub(r"'(?:[^']|'')*'", "''", statement)
    if "?" in outside:
        raise ValueError("a positional placeholder '?': parameters bind by name (:name)")
    colon = set(_PLACEHOLDER.findall(outside))
    pyformat = set(_PYFORMAT.findall(outside))
    if colon and pyformat:
        raise ValueError("a statement binds by :name or by %(name)s, not both")
    return tuple(sorted(colon | pyformat))


@dataclass(frozen=True, slots=True)
class Effect:
    """A row effect the binding builds: Interlock's ``PlanBuilder.add``."""

    effect_id: str
    kind: str
    table: str
    statement: str
    parameters: Mapping[str, str]
    """Each placeholder, and where its value comes from: ``$.<argument>``,
    ``$session.tenant`` or ``$session.trajectory``."""
    stated_rows: int | None
    after: tuple[str, ...]
    partitions: Mapping[str, str] = field(default_factory=dict)
    """The labels the effect declares beside its tenant, by dimension, each
    from ``$.<argument>`` (amended in 1.4.0): what a mandate's
    ``data.partitions`` with ``declared`` holds its rows to. Written only when set."""
    compensation: str | None = None
    """The undo the effect declares, in its substrate's language (amended in
    1.6.0): what a substrate that cannot roll back runs to put the write back
    (a FHIR server's ``void <table>`` or ``restore <table>``). Written only
    when set."""

    def to_json(self) -> dict[str, Any]:
        found: dict[str, Any] = {
            "id": self.effect_id,
            "kind": self.kind,
            "table": self.table,
            "statement": self.statement,
            "parameters": dict(sorted(self.parameters.items())),
            "stated_rows": self.stated_rows,
            "after": list(self.after),
        }
        if self.partitions:
            found["partitions"] = dict(sorted(self.partitions.items()))
        if self.compensation is not None:
            found["compensation"] = self.compensation
        return found

    @property
    def statement_digest(self) -> str:
        return hashlib.sha256(self.statement.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Request:
    """An outbound request the binding builds: Interlock's ``PlanBuilder.enqueue``."""

    request_id: str
    sink: str
    operation: str
    payload: Mapping[str, Any]
    """A JSON object; a string ``$.<argument>`` or ``$session.<...>`` in it is filled in."""
    compensation: Mapping[str, Any] | None
    """``{"sink", "operation", "payload"}``, or ``None``."""
    after: tuple[str, ...]
    partitions: Mapping[str, str] = field(default_factory=dict)
    """As an effect's: the labels a window per partition counts it toward."""

    def to_json(self) -> dict[str, Any]:
        found: dict[str, Any] = {
            "id": self.request_id,
            "sink": self.sink,
            "operation": self.operation,
            "payload": dict(self.payload),
            "compensation": None if self.compensation is None else dict(self.compensation),
            "after": list(self.after),
        }
        if self.partitions:
            found["partitions"] = dict(sorted(self.partitions.items()))
        return found


SYSTEM: Final = "system"
UNTRUSTED: Final = "untrusted"
TRUST: Final = ("principal", SYSTEM, UNTRUSTED)
CONFIDENTIALITY: Final = ("secret", "tenant")
_OUTPUT_TYPES: Final = frozenset({"integer", "number", "boolean"})


def constrained(schema: object, path: str = "$.quarantine.output") -> None:
    """Refuse an output schema that lets free text through (1.16.0).

    A reader sub-agent's result may be read by a session that read nothing untrusted. So it
    holds only what cannot carry an instruction: integers, numbers and booleans; strings
    with an ``enum``, or a ``pattern`` and a ``maxLength`` of at most 64; and arrays of
    those, at most 16 items. An object's every property is listed and required, and
    ``additionalProperties`` is false.
    """
    if not isinstance(schema, dict):
        raise MalformedError("type", path, "must be an object schema")
    kind = schema.get("type")
    if kind == "object":
        properties = schema.get("properties")
        if not isinstance(properties, dict) or not properties:
            raise MalformedError("agreement", path, "an object lists its properties")
        if schema.get("additionalProperties") is not False:
            raise MalformedError("agreement", path, "an object takes no other property")
        if sorted(schema.get("required", ())) != sorted(properties):
            raise MalformedError("agreement", path, "an object requires every property")
        for name, sub in properties.items():
            constrained(sub, f"{path}.properties.{name}")
        return
    if kind == "array":
        if not isinstance(schema.get("maxItems"), int) or schema["maxItems"] > 16:
            raise MalformedError("agreement", path, "an array holds at most 16 items")
        constrained(schema.get("items"), f"{path}.items")
        return
    if kind in _OUTPUT_TYPES:
        return
    if kind == "string":
        if isinstance(schema.get("enum"), list) and schema["enum"]:
            return
        length = schema.get("maxLength")
        if isinstance(schema.get("pattern"), str) and isinstance(length, int) and length <= 64:
            return
        raise MalformedError(
            "agreement",
            path,
            "a string is an enum, or a pattern of at most 64 characters: free text could carry "
            "an instruction from what the reader read",
        )
    raise MalformedError("agreement", path, f"{kind!r} is not a type a reader's result holds")


@dataclass(frozen=True, slots=True)
class Quarantine:
    """A reader sub-agent behind a passthrough tool (1.16.0).

    The reader works under its own lineage, ``reader`` (a subject scope), whose mandate
    grants no write and no outbound request: it may read untrusted input and change
    nothing. What it returns to the calling session must match ``output``, a schema with no
    free text (:func:`constrained`). The calling session is labeled ``quarantined:<tool>``,
    not ``untrusted``: what crossed is a typed value, recorded as such.
    """

    reader: str
    output: Mapping[str, Any]

    def to_json(self) -> dict[str, Any]:
        return {"reader": self.reader, "output": dict(self.output)}

    @classmethod
    def read(cls, fields: Fields) -> Quarantine:
        output = fields.raw("output")
        check_schema(output, fields.path("output"))
        constrained(output, fields.path("output"))
        return cls(reader=fields.text("reader", SCOPE, "a lineage's subject scope"), output=output)


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolBinding(Document):
    """A tool binding (F4-13 to F4-16)."""

    TYPE: ClassVar[str] = "tool"
    FIELDS: ClassVar[tuple[str, ...]] = (
        "name",
        "description",
        "input",
        "kind",
        "tenant",
        "effects",
        "requests",
        "price",
    )
    OPTIONAL: ClassVar[tuple[str, ...]] = (
        "trust",
        "confidentiality",
        "quarantine",
        "numbers",
        "pin",
        "upstream",
    )

    name: str
    description: str
    input: Mapping[str, Any]
    kind: str
    """``plan`` (escrowed) or ``passthrough`` (metered, not escrowed)."""
    tenant: str
    """``session``: every effect is stamped with the session's tenant; ``none``."""
    effects: tuple[Effect, ...]
    requests: tuple[Request, ...]
    price: Decimal
    trust: str = SYSTEM
    """What the tool's result is (1.16.0): ``principal`` (written by a principal),
    ``system`` (the deployment's own records, the default), or ``untrusted`` (anyone's:
    an email, a web page, a ticket a customer wrote). Written only when not ``system``."""
    confidentiality: str | None = None
    """``secret``, or ``tenant`` (the session's tenant's data), or ``None`` (1.16.0)."""
    quarantine: Quarantine | None = None
    """A reader sub-agent's typed result (1.16.0): see :class:`Quarantine`."""
    numbers: tuple[str, ...] = ()
    """The arguments the tool takes as JSON numbers (1.17.0), by path (``price``,
    ``lines[].qty``): held here as decimal strings, so no float is ever hashed or signed,
    and sent to the tool as the numbers they are."""
    pin: str | None = None
    """For a tool an MCP server lists (1.17.0): the SHA-256 of its whole definition as
    the server listed it when the binding was made (:func:`fiduciary.protect.pin_of`).
    A proxy withholds the tool while the server lists anything else."""
    upstream: str | None = None
    """The server's own name for the tool, when it is not the binding's (1.17.0)."""
    signature: Signature | None = None

    def labels(self, tenant: str | None) -> tuple[str, ...]:
        """The provenance labels the tool's result brings into a session (1.16.0)."""
        if self.quarantine is not None:
            return (f"quarantined:{self.name}",)
        found = []
        if self.trust == UNTRUSTED:
            found.append(UNTRUSTED)
        if self.confidentiality == "secret":
            found.append("secret")
        elif self.confidentiality == "tenant":
            found.append(f"partition:tenant={tenant or '-'}")
        return tuple(sorted(found))

    def _body(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input": self.input,
            "kind": self.kind,
            "tenant": self.tenant,
            "effects": [e.to_json() for e in self.effects],
            "requests": [r.to_json() for r in self.requests],
            "price": format(self.price, "f"),
            **({"trust": self.trust} if self.trust != SYSTEM else {}),
            **({"confidentiality": self.confidentiality} if self.confidentiality else {}),
            **({"quarantine": self.quarantine.to_json()} if self.quarantine else {}),
            **({"numbers": list(self.numbers)} if self.numbers else {}),
            **({"pin": self.pin} if self.pin else {}),
            **({"upstream": self.upstream} if self.upstream else {}),
        }

    @classmethod
    def _read(cls, fields: Fields) -> dict[str, Any]:
        check_schema(fields.raw("input"), fields.path("input"))
        effects = []
        for path, item in fields.array("effects", MAX_ITEMS):
            entry = Fields(
                item,
                path,
                ("id", "kind", "table", "statement", "parameters", "stated_rows", "after"),
                ("partitions", "compensation"),
            )
            parameters = {
                name: fidu1.check_text(
                    source,
                    p,
                    _SOURCE,
                    "a source: $.<argument>, $session.tenant or $session.trajectory",
                )
                for name, p, source in entry.mapping(
                    "parameters", re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
                )
            }
            effects.append(
                Effect(
                    effect_id=entry.text("id", _EFFECT_ID, "an id, [a-z][a-z0-9_-]{0,63}"),
                    kind=entry.choice("kind", ("apply", "delete", "insert", "update")),
                    table=entry.text("table", IDENTIFIER, "a table"),
                    statement=entry.text(
                        "statement", fidu1.STATEMENT, "a statement of 1 to 8192 characters"
                    ),
                    parameters=parameters,
                    stated_rows=None
                    if entry.is_null("stated_rows")
                    else entry.integer("stated_rows"),
                    after=entry.strings("after", _EFFECT_ID, "an effect or request id"),
                    partitions=_partitions(entry),
                    compensation=entry.text(
                        "compensation", fidu1.STATEMENT, "a statement of 1 to 8192 characters"
                    )
                    if entry.has("compensation")
                    else None,
                )
            )
        requests = []
        for path, item in fields.array("requests", MAX_ITEMS):
            entry = Fields(
                item,
                path,
                ("id", "sink", "operation", "payload", "compensation", "after"),
                ("partitions",),
            )
            payload = entry.raw("payload")
            if not isinstance(payload, dict):
                raise MalformedError("type", entry.path("payload"), "must be an object")
            compensation = None
            if not entry.is_null("compensation"):
                comp = entry.object("compensation", ("sink", "operation", "payload"))
                comp.text("sink", SINK_NAME)
                comp.text("operation", OPERATION)
                if not isinstance(comp.raw("payload"), dict):
                    raise MalformedError("type", comp.path("payload"), "must be an object")
                compensation = entry.raw("compensation")
            requests.append(
                Request(
                    request_id=entry.text("id", _EFFECT_ID, "an id, [a-z][a-z0-9_-]{0,63}"),
                    sink=entry.text("sink", SINK_NAME, "a sink"),
                    operation=entry.text("operation", OPERATION, "an operation"),
                    payload=payload,
                    compensation=compensation,
                    after=entry.strings("after", _EFFECT_ID, "an effect or request id"),
                    partitions=_partitions(entry),
                )
            )
        return {
            "name": fields.text("name", TOOL, "a tool name, [a-z][a-z0-9_-]{0,63}"),
            "description": fields.text("description"),
            "input": fields.raw("input"),
            "kind": fields.choice("kind", ("passthrough", "plan")),
            "tenant": fields.choice("tenant", ("none", "session")),
            "effects": tuple(effects),
            "requests": tuple(requests),
            "price": fields.money("price"),
            # the default is never written: one binding has one encoding
            "trust": fields.choice("trust", ("principal", UNTRUSTED))
            if fields.has("trust")
            else SYSTEM,
            "confidentiality": fields.choice("confidentiality", CONFIDENTIALITY)
            if fields.has("confidentiality")
            else None,
            "quarantine": Quarantine.read(fields.object("quarantine", ("reader", "output")))
            if fields.has("quarantine")
            else None,
            "numbers": fields.strings("numbers", _NUMBER_PATH, "an argument path", MAX_ITEMS)
            if fields.has("numbers")
            else (),
            "pin": fields.hash("pin") if fields.has("pin") else None,
            "upstream": fields.text("upstream", _UPSTREAM, "a tool name of 1 to 128 characters")
            if fields.has("upstream")
            else None,
        }

    def _agree(self) -> None:
        schema = self.input
        if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
            raise MalformedError(
                "agreement",
                "$.input",
                "is an object schema with additionalProperties false: an unknown argument "
                "refuses the call",
            )
        arguments = set(schema.get("properties", {}))
        if self.quarantine is not None and self.kind != "passthrough":
            raise MalformedError(
                "agreement", "$.quarantine", "a reader's result comes from a passthrough tool"
            )
        if self.kind == "passthrough":
            if self.effects or self.requests:
                raise MalformedError(
                    "agreement",
                    "$.effects",
                    "a passthrough tool builds no effect and no request (F4-15)",
                )
            return
        if not self.effects and not self.requests:
            raise MalformedError(
                "agreement", "$.effects", "a plan tool builds at least one effect or request"
            )
        seen: set[str] = set()
        steps: list[tuple[str, str, tuple[str, ...]]] = [
            *((f"$.effects[{i}]", e.effect_id, e.after) for i, e in enumerate(self.effects)),
            *((f"$.requests[{i}]", r.request_id, r.after) for i, r in enumerate(self.requests)),
        ]
        for path, step_id, after in steps:
            if step_id in seen:
                raise MalformedError("agreement", f"{path}.id", f"{step_id!r} names two steps")
            unknown = [a for a in after if a not in seen]
            if unknown:
                raise MalformedError(
                    "agreement",
                    f"{path}.after",
                    f"waits for {', '.join(unknown)}, which no earlier step is",
                )
            seen.add(step_id)
        for i, effect in enumerate(self.effects):
            path = f"$.effects[{i}]"
            try:
                found = placeholders(effect.statement)
            except ValueError as exc:
                raise MalformedError("agreement", f"{path}.statement", str(exc)) from exc
            if found != tuple(sorted(effect.parameters)):
                raise MalformedError(
                    "agreement",
                    f"{path}.parameters",
                    f"binds {', '.join(sorted(effect.parameters)) or 'nothing'}, and the "
                    f"statement names "
                    f"{', '.join(found) or 'nothing'}",
                )
            for name, source in effect.parameters.items():
                self._check_source(source, f"{path}.parameters.{name}", arguments)
            for dimension, source in effect.partitions.items():
                self._check_source(source, f"{path}.partitions.{dimension}", arguments)
        for i, request in enumerate(self.requests):
            self._check_payload(request.payload, f"$.requests[{i}].payload", arguments)
            for dimension, source in request.partitions.items():
                self._check_source(source, f"$.requests[{i}].partitions.{dimension}", arguments)
            if request.compensation is not None:
                self._check_payload(
                    request.compensation["payload"],
                    f"$.requests[{i}].compensation.payload",
                    arguments,
                )

    def _check_source(self, source: str, path: str, arguments: set[str]) -> None:
        if source.startswith("$session.tenant") and self.tenant != "session":
            raise MalformedError(
                "agreement", path, "reads the session's tenant, and the binding takes none"
            )
        if source.startswith("$.") and source[2:] not in arguments:
            raise MalformedError(
                "agreement",
                path,
                f"reads argument {source[2:]!r}, which the input does not declare",
            )

    def _check_payload(self, value: object, path: str, arguments: set[str]) -> None:
        if isinstance(value, str) and value.startswith("$"):
            fidu1.check_text(
                value,
                path,
                _SOURCE,
                "a source: $.<argument>, $session.tenant or $session.trajectory",
            )
            self._check_source(value, path, arguments)
        elif isinstance(value, dict):
            for key, item in value.items():
                self._check_payload(item, f"{path}.{key}", arguments)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                self._check_payload(item, f"{path}[{index}]", arguments)

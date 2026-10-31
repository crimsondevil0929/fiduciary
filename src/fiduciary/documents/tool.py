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

from fiduciary import fidu1
from fiduciary.documents._base import IDENTIFIER, Document
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
            f"unsupported keyword(s) {', '.join(unknown)}: they would be ignored, not enforced",
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
    signature: Signature | None = None

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
                ("partitions",),
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

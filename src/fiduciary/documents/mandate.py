"""The mandate: what principals delegate to one agent scope (``docs/FIDUCIARY_SPEC.md`` §4.2).

Every field is a bound section 6 compiles into Interlock's checkers, sinks
and windows and AgentGov's policies, and section 5 orders: a derived child
refines its parent field by field (:mod:`fiduciary.refines`). Lists of rules
are held as sets, sorted by what identifies each rule, so one mandate has one
spelling and one digest.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar, Final

from fiduciary import fidu1
from fiduciary.documents._base import IDENTIFIER, SCOPE, Document, sorted_by
from fiduciary.documents.charter import Charter
from fiduciary.exceptions import MalformedError
from fiduciary.fidu1 import (
    FRACTION,
    Fields,
    Signature,
    check_decimal,
    check_integer,
    check_strings,
    check_text,
)

__all__ = [
    "AUTO_RESOLUTIONS",
    "GRANTING",
    "QUORUM_CLASSES",
    "REDUCING",
    "TICKET_CLASSES",
    "TRIP_KINDS",
    "Agreement",
    "AmountCap",
    "AutoRule",
    "Budget",
    "Cognitive",
    "Data",
    "Delegation",
    "Directive",
    "Drawdown",
    "Escalation",
    "Extension",
    "ExtensionAuto",
    "FactRule",
    "Footprint",
    "Governance",
    "Mandate",
    "MandateRef",
    "Measure",
    "Outbound",
    "OutboundTenants",
    "Recipients",
    "Recovery",
    "Roster",
    "SinkBounds",
    "Subject",
    "ToolPin",
    "Tools",
    "Window",
]

GRANTING: Final = (
    "amend",
    "compensate",
    "compensate_late",
    "grant",
    "release",
    "requeue",
    "reset",
    "reset_safety",
)
"""Quorum classes whose resolutions grant authority (§7.2)."""
REDUCING: Final = ("reduce", "revoke")
"""Quorum classes whose resolutions only take authority away."""
QUORUM_CLASSES: Final = tuple(sorted((*GRANTING, *REDUCING, "accept_repair")))

TICKET_CLASSES: Final = ("amend", "dead", "extension", "held", "refused", "undo")
AUTO_RESOLUTIONS: Final[Mapping[str, tuple[str, ...]]] = {
    # §7.2: which resolutions of each class a mandated rule may take. The ones
    # marked "never" there (reset_safety, compensate_late, amend) appear nowhere.
    "refused": ("accept_repair", "reject"),
    "held": ("cancel",),
    "dead": ("cancel", "requeue"),
    "undo": ("reject",),
    "extension": ("decline", "grant"),
    "amend": ("reject",),
}
WHEN: Final[Mapping[str, tuple[str, ...]]] = {
    # F7-9: the closed vocabulary a rule's predicate is written in, per class.
    "refused": ("causes", "exhaustive", "max_dropped"),
    "dead": ("max_requeues", "outcome"),
    "extension": ("max_amount",),
}
DROP_CAUSES: Final = ("depends", "inadmissible", "refused", "unevaluated")
TRIP_KINDS: Final = ("budget", "halted", "refused", "runaway", "thrashing")
OPERATIONS: Final = ("delete", "insert", "update")
TOOL: Final = re.compile(r"[a-z][a-z0-9_-]{0,63}")
SINK_NAME: Final = TOOL
OPERATION: Final = re.compile(r"[a-z][a-z0-9_.-]{0,126}")
MODEL: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,127}")
PATH: Final = re.compile(r"[A-Za-z0-9_*][A-Za-z0-9_.*-]{0,255}")
"""A dotted path into a payload: ``amount``, ``to.*.email``."""
WINDOW: Final = re.compile(r"[a-z][a-z0-9_]{0,62}")
DOMAIN: Final = re.compile(
    r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+"
)
ADDRESS: Final = re.compile(r"[^\s@\x00-\x1f\x7f]{1,64}@[a-z0-9.-]{1,255}")
CURRENCY: Final = re.compile(r"[a-z]{3}")


def _scope(fields: Fields, name: str) -> str:
    return fields.text(name, SCOPE, "a scope id")


def _money_positive(fields: Fields, name: str) -> Decimal:
    amount = fields.money(name)
    if amount <= 0:
        raise MalformedError("range", fields.path(name), "must be more than zero")
    return amount


def _fraction(fields: Fields, name: str, maximum: Decimal | None = None) -> Decimal:
    value = fields.decimal(name, FRACTION)
    if maximum is not None and value > maximum:
        raise MalformedError("range", fields.path(name), f"must be at most {maximum}")
    return value


def _money(amount: Decimal) -> str:
    return format(amount.quantize(Decimal("0.00000001")), "f")


def _key_pair(fields: Fields, name: str) -> tuple[str, str] | None:
    """``null``, or ``[a, b]``: two identifiers that join two things on a key."""
    if fields.is_null(name):
        return None
    items = fields.array(name, 2)
    if len(items) != 2:
        raise MalformedError("range", fields.path(name), "is a pair: [one, other]")
    first, second = (check_text(v, p, PATH, "a field or column") for p, v in items)
    return first, second


def _pair(value: tuple[str, str] | None) -> list[str] | None:
    return None if value is None else list(value)


# --------------------------------------------------------------------------
# header parts
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MandateRef:
    """A mandate version by id, version and digest."""

    mandate_id: str
    version: int
    digest: str

    def to_json(self) -> dict[str, Any]:
        return {"mandate_id": self.mandate_id, "version": self.version, "digest": self.digest}

    @classmethod
    def read(cls, fields: Fields) -> MandateRef:
        return cls(fields.uuid("mandate_id"), fields.integer("version", 1), fields.hash("digest"))


@dataclass(frozen=True, slots=True)
class CharterRef:
    charter_id: str
    version: int
    digest: str

    def to_json(self) -> dict[str, Any]:
        return {"charter_id": self.charter_id, "version": self.version, "digest": self.digest}


@dataclass(frozen=True, slots=True)
class Subject:
    scope: str
    funded_from: str
    label: str

    def to_json(self) -> dict[str, Any]:
        return {"scope": self.scope, "funded_from": self.funded_from, "label": self.label}


@dataclass(frozen=True, slots=True)
class Roster:
    principals: tuple[str, ...]
    quorums: Mapping[str, int]
    """Every class in :data:`QUORUM_CLASSES`, with the approvals it needs."""

    def to_json(self) -> dict[str, Any]:
        return {"principals": list(self.principals), "quorums": dict(sorted(self.quorums.items()))}

    def quorum(self, name: str) -> int:
        return self.quorums[name]


# --------------------------------------------------------------------------
# money and governance
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Budget:
    envelope: Decimal
    per_call: Decimal
    models: tuple[str, ...]
    max_output_tokens: int

    def to_json(self) -> dict[str, Any]:
        return {
            "envelope": _money(self.envelope),
            "per_call": _money(self.per_call),
            "models": list(self.models),
            "max_output_tokens": self.max_output_tokens,
        }


@dataclass(frozen=True, slots=True)
class Cognitive:
    """The bounds of AgentGov's ``CognitivePolicy`` a mandate sets."""

    max_identical_repeats: int
    similarity_threshold: Decimal
    max_similar_streak: int
    result_similarity_threshold: Decimal
    max_cycle_period: int
    min_cycle_repeats: int
    min_novelty_ratio: Decimal
    exempt_tools: tuple[str, ...]

    FIELDS: ClassVar[tuple[str, ...]] = (
        "max_identical_repeats",
        "similarity_threshold",
        "max_similar_streak",
        "result_similarity_threshold",
        "max_cycle_period",
        "min_cycle_repeats",
        "min_novelty_ratio",
        "exempt_tools",
    )

    def to_json(self) -> dict[str, Any]:
        return {
            "max_identical_repeats": self.max_identical_repeats,
            "similarity_threshold": str(self.similarity_threshold),
            "max_similar_streak": self.max_similar_streak,
            "result_similarity_threshold": str(self.result_similarity_threshold),
            "max_cycle_period": self.max_cycle_period,
            "min_cycle_repeats": self.min_cycle_repeats,
            "min_novelty_ratio": str(self.min_novelty_ratio),
            "exempt_tools": list(self.exempt_tools),
        }

    @classmethod
    def read(cls, fields: Fields) -> Cognitive:
        one = Decimal(1)
        return cls(
            max_identical_repeats=fields.integer("max_identical_repeats", 1, 1000),
            similarity_threshold=_fraction(fields, "similarity_threshold", one),
            max_similar_streak=fields.integer("max_similar_streak", 1, 1000),
            result_similarity_threshold=_fraction(fields, "result_similarity_threshold", one),
            max_cycle_period=fields.integer("max_cycle_period", 1, 1000),
            min_cycle_repeats=fields.integer("min_cycle_repeats", 1, 1000),
            min_novelty_ratio=_fraction(fields, "min_novelty_ratio", one),
            exempt_tools=fields.strings("exempt_tools", fidu1.TEXT),
        )


@dataclass(frozen=True, slots=True)
class Governance:
    max_calls: int
    seconds: int
    fleet_velocity: bool
    cognitive: Cognitive

    def to_json(self) -> dict[str, Any]:
        return {
            "max_calls": self.max_calls,
            "seconds": self.seconds,
            "fleet_velocity": self.fleet_velocity,
            "cognitive": self.cognitive.to_json(),
        }


# --------------------------------------------------------------------------
# rows
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Drawdown:
    table: str
    column: str
    per: str
    """``table`` (``ColumnValueGuard``) or ``tenant`` (``TenantDrawdownGuard``)."""
    max_drop: Decimal

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.table, self.column, self.per)

    def to_json(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "column": self.column,
            "per": self.per,
            "max_drop": str(self.max_drop),
        }


@dataclass(frozen=True, slots=True)
class Footprint:
    tolerance: Decimal
    blocking: bool

    def to_json(self) -> dict[str, Any]:
        return {"tolerance": str(self.tolerance), "blocking": self.blocking}


@dataclass(frozen=True, slots=True)
class Agreement:
    """A ``CrossEffectAgreement``: a request's field held to rows' column."""

    sink: str
    operation: str
    field: str
    table: str
    column: str
    measure: str
    key: tuple[str, str] | None

    @property
    def identity(self) -> tuple[str, ...]:
        return (
            self.sink,
            self.operation,
            self.field,
            self.table,
            self.column,
            self.measure,
            *(self.key or ()),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "sink": self.sink,
            "operation": self.operation,
            "field": self.field,
            "table": self.table,
            "column": self.column,
            "measure": self.measure,
            "key": _pair(self.key),
        }


@dataclass(frozen=True, slots=True)
class FactRule:
    """A ``FactAgreement``: an inbound fact's field held to rows' column."""

    kind: tuple[str, ...]
    field: str
    table: str
    column: str
    key: tuple[str, str] | None
    exempt: tuple[str, ...]

    @property
    def identity(self) -> tuple[str, ...]:
        return (*self.kind, "|", self.field, self.table, self.column, *(self.key or ()))

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": list(self.kind),
            "field": self.field,
            "table": self.table,
            "column": self.column,
            "key": _pair(self.key),
            "exempt": list(self.exempt),
        }


@dataclass(frozen=True, slots=True)
class Data:
    tables: Mapping[str, tuple[str, ...]]
    """Each table the plans may write, and the operations they may make on it."""
    tenants: tuple[str, ...] | None
    """The tenants the rows may belong to; ``None`` is ``"*"``, any."""
    declared_tenants: bool
    max_rows: int
    max_tenants: int
    drawdown: tuple[Drawdown, ...]
    stated_footprint: Footprint | None
    agreements: tuple[Agreement, ...]
    facts: tuple[FactRule, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "tables": {t: list(ops) for t, ops in sorted(self.tables.items())},
            "tenants": "*" if self.tenants is None else list(self.tenants),
            "declared_tenants": self.declared_tenants,
            "max_rows": self.max_rows,
            "max_tenants": self.max_tenants,
            "drawdown": [d.to_json() for d in self.drawdown],
            "stated_footprint": None
            if self.stated_footprint is None
            else self.stated_footprint.to_json(),
            "agreements": [a.to_json() for a in self.agreements],
            "facts": [f.to_json() for f in self.facts],
        }


# --------------------------------------------------------------------------
# requests
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SinkBounds:
    operations: tuple[str, ...]
    max_attempts: int
    not_after_seconds: int
    max_payload_bytes: int

    def to_json(self) -> dict[str, Any]:
        return {
            "operations": list(self.operations),
            "max_attempts": self.max_attempts,
            "not_after_seconds": self.not_after_seconds,
            "max_payload_bytes": self.max_payload_bytes,
        }


@dataclass(frozen=True, slots=True)
class AmountCap:
    """A ``PayloadAmountCap``."""

    sink: str
    operation: str
    field: str
    maximum: Decimal | Mapping[str, Decimal]
    """One cap, or a cap per currency (read at ``currency_field``)."""
    per: str
    currency_field: str | None

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return (self.sink, self.operation, self.field, self.per)

    def to_json(self) -> dict[str, Any]:
        maximum: Any = (
            {c: str(v) for c, v in sorted(self.maximum.items())}
            if isinstance(self.maximum, Mapping)
            else str(self.maximum)
        )
        return {
            "sink": self.sink,
            "operation": self.operation,
            "field": self.field,
            "maximum": maximum,
            "per": self.per,
            "currency_field": self.currency_field,
        }


@dataclass(frozen=True, slots=True)
class Recipients:
    """A ``RecipientAllowlist``."""

    sink: str
    operation: str
    kind: str
    fields: tuple[str, ...]
    domains: tuple[str, ...]
    addresses: tuple[str, ...]
    tenant_domains: Mapping[str, tuple[str, ...]]
    subdomains: bool

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.sink, self.operation, self.kind)

    def to_json(self) -> dict[str, Any]:
        return {
            "sink": self.sink,
            "operation": self.operation,
            "kind": self.kind,
            "fields": list(self.fields),
            "domains": list(self.domains),
            "addresses": list(self.addresses),
            "tenant_domains": {t: list(d) for t, d in sorted(self.tenant_domains.items())},
            "subdomains": self.subdomains,
        }


@dataclass(frozen=True, slots=True)
class OutboundTenants:
    """An ``OutboundTenantIsolation``."""

    max_tenants: int
    require_tenant: bool
    fields: Mapping[str, str]

    def to_json(self) -> dict[str, Any]:
        return {
            "max_tenants": self.max_tenants,
            "require_tenant": self.require_tenant,
            "fields": dict(sorted(self.fields.items())),
        }


@dataclass(frozen=True, slots=True)
class Outbound:
    sinks: Mapping[str, SinkBounds]
    max_per_plan: int | None
    per_sink: Mapping[str, int]
    caps: tuple[AmountCap, ...]
    recipients: tuple[Recipients, ...]
    tenants: OutboundTenants | None

    def to_json(self) -> dict[str, Any]:
        return {
            "sinks": {s: b.to_json() for s, b in sorted(self.sinks.items())},
            "max_per_plan": self.max_per_plan,
            "per_sink": dict(sorted(self.per_sink.items())),
            "caps": [c.to_json() for c in self.caps],
            "recipients": [r.to_json() for r in self.recipients],
            "tenants": None if self.tenants is None else self.tenants.to_json(),
        }


# --------------------------------------------------------------------------
# windows, tools
# --------------------------------------------------------------------------

_MEASURES: Final[Mapping[str, tuple[str, ...]]] = {
    "plans": (),
    "requests": ("sink", "operation"),
    "request_sum": ("sink", "operation", "field"),
    "row_sum": ("table", "column", "rows"),
}


@dataclass(frozen=True, slots=True)
class Measure:
    """What a plan adds to a window: Interlock's ``Plans``, ``Requests``,
    ``RequestSum`` or ``RowSum``."""

    kind: str
    sink: str | None = None
    operation: str | None = None
    field: str | None = None
    table: str | None = None
    column: str | None = None
    rows: str | None = None

    def to_json(self) -> dict[str, Any]:
        found: dict[str, Any] = {"kind": self.kind}
        for name in _MEASURES[self.kind]:
            found[name] = getattr(self, name)
        return found

    @classmethod
    def read(cls, raw: object, path: str) -> Measure:
        if not isinstance(raw, dict):
            raise MalformedError("type", path, "must be an object")
        kind = str(raw.get("kind"))
        if kind not in _MEASURES:
            raise MalformedError("format", f"{path}.kind", f"must be one of {', '.join(_MEASURES)}")
        fields = Fields(raw, path, ("kind", *_MEASURES[kind]))
        if kind == "plans":
            return cls(kind)
        if kind == "requests":
            operation = None if fields.is_null("operation") else fields.text("operation", OPERATION)
            return cls(kind, sink=fields.text("sink", SINK_NAME), operation=operation)
        if kind == "request_sum":
            return cls(
                kind,
                sink=fields.text("sink", SINK_NAME),
                operation=fields.text("operation", OPERATION),
                field=fields.text("field", PATH),
            )
        return cls(
            kind,
            table=fields.text("table", IDENTIFIER),
            column=fields.text("column", IDENTIFIER),
            rows=fields.choice("rows", ("inserted", "net")),
        )


@dataclass(frozen=True, slots=True)
class Window:
    """At most ``limit`` of ``measure`` within any ``span_seconds``, over the
    lineage (``per="lineage"``) or each tenant in it (``per="tenant"``)."""

    name: str
    measure: Measure
    span_seconds: int
    limit: Decimal
    per: str

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "measure": self.measure.to_json(),
            "span_seconds": self.span_seconds,
            "limit": str(self.limit),
            "per": self.per,
        }


@dataclass(frozen=True, slots=True)
class ToolPin:
    name: str
    digest: str

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "digest": self.digest}


@dataclass(frozen=True, slots=True)
class Tools:
    statements: str
    """``bound``: only statements a pinned binding carries; ``any``."""
    bindings: tuple[ToolPin, ...]

    def to_json(self) -> dict[str, Any]:
        return {"statements": self.statements, "bindings": [b.to_json() for b in self.bindings]}


# --------------------------------------------------------------------------
# recovery, extension, escalation, delegation
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Directive:
    directive_id: str
    sha256: str
    answers: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {"id": self.directive_id, "sha256": self.sha256, "answers": list(self.answers)}


@dataclass(frozen=True, slots=True)
class Recovery:
    """Interlock's ``RecoveryPolicy``."""

    reserve: Decimal
    step_estimate: Decimal
    max_steps: int
    directives: tuple[Directive, ...]
    revoke_on: tuple[str, ...]
    revocable: tuple[str, ...] | None
    """``None``: any tool the task was granted."""
    token_ceiling: int | None
    guidance: bool
    channel: str
    funding: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "reserve": _money(self.reserve),
            "step_estimate": _money(self.step_estimate),
            "max_steps": self.max_steps,
            "directives": [d.to_json() for d in self.directives],
            "revoke_on": list(self.revoke_on),
            "revocable": None if self.revocable is None else list(self.revocable),
            "token_ceiling": self.token_ceiling,
            "guidance": self.guidance,
            "channel": self.channel,
            "funding": self.funding,
        }


@dataclass(frozen=True, slots=True)
class ExtensionAuto:
    max_grant: Decimal
    max_total: Decimal

    def to_json(self) -> dict[str, Any]:
        return {"max_grant": _money(self.max_grant), "max_total": _money(self.max_total)}


@dataclass(frozen=True, slots=True)
class Extension:
    max_grant: Decimal
    max_total: Decimal
    margin: Decimal
    ttl_seconds: int
    auto: ExtensionAuto | None

    def to_json(self) -> dict[str, Any]:
        return {
            "max_grant": _money(self.max_grant),
            "max_total": _money(self.max_total),
            "margin": str(self.margin),
            "ttl_seconds": self.ttl_seconds,
            "auto": None if self.auto is None else self.auto.to_json(),
        }


@dataclass(frozen=True, slots=True)
class AutoRule:
    """A mandated rule (§7.4): a class, a resolution, and a predicate over the
    ticket's typed evidence in that class's closed vocabulary (F7-9)."""

    rule_id: str
    ticket_class: str
    resolution: str
    when: Mapping[str, Any]
    """``exhaustive`` (bool), ``max_dropped`` (int), ``causes`` (set);
    ``max_requeues`` (int), ``outcome`` (set); ``max_amount`` (Decimal)."""

    def to_json(self) -> dict[str, Any]:
        when: dict[str, Any] = {}
        for name, value in sorted(self.when.items()):
            if isinstance(value, Decimal):
                when[name] = _money(value)
            elif isinstance(value, tuple):
                when[name] = list(value)
            else:
                when[name] = value
        return {
            "id": self.rule_id,
            "class": self.ticket_class,
            "resolution": self.resolution,
            "when": when,
        }


@dataclass(frozen=True, slots=True)
class Escalation:
    max_open: int
    ttl_seconds: int
    auto: tuple[AutoRule, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "max_open": self.max_open,
            "ttl_seconds": self.ttl_seconds,
            "auto": [r.to_json() for r in self.auto],
        }


@dataclass(frozen=True, slots=True)
class Delegation:
    max_depth: int
    max_children: int
    envelope: Decimal

    def to_json(self) -> dict[str, Any]:
        return {
            "max_depth": self.max_depth,
            "max_children": self.max_children,
            "envelope": _money(self.envelope),
        }


# --------------------------------------------------------------------------
# the mandate
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Mandate(Document):
    """One version of a mandate (F4-7 to F4-12)."""

    TYPE: ClassVar[str] = "mandate"
    FIELDS: ClassVar[tuple[str, ...]] = (
        "mandate_id",
        "version",
        "prev",
        "charter",
        "parent",
        "issuance",
        "subject",
        "term",
        "roster",
        "budget",
        "governance",
        "data",
        "outbound",
        "windows",
        "tools",
        "recovery",
        "extension",
        "escalation",
        "delegation",
        "issued_at",
    )

    mandate_id: str
    version: int
    prev: str | None
    charter: CharterRef
    parent: MandateRef | None
    issuance: str
    subject: Subject
    not_before: datetime
    not_after: datetime
    roster: Roster
    budget: Budget
    governance: Governance
    data: Data
    outbound: Outbound
    windows: tuple[Window, ...]
    tools: Tools
    recovery: Recovery | None
    extension: Extension | None
    escalation: Escalation
    delegation: Delegation | None
    issued_at: datetime
    signature: Signature | None = None

    @property
    def ref(self) -> MandateRef:
        return MandateRef(self.mandate_id, self.version, self.digest)

    def _body(self) -> dict[str, Any]:
        return {
            "mandate_id": self.mandate_id,
            "version": self.version,
            "prev": self.prev,
            "charter": self.charter.to_json(),
            "parent": None if self.parent is None else self.parent.to_json(),
            "issuance": self.issuance,
            "subject": self.subject.to_json(),
            "term": {
                "not_before": fidu1.format_instant(self.not_before),
                "not_after": fidu1.format_instant(self.not_after),
            },
            "roster": self.roster.to_json(),
            "budget": self.budget.to_json(),
            "governance": self.governance.to_json(),
            "data": self.data.to_json(),
            "outbound": self.outbound.to_json(),
            "windows": [w.to_json() for w in self.windows],
            "tools": self.tools.to_json(),
            "recovery": None if self.recovery is None else self.recovery.to_json(),
            "extension": None if self.extension is None else self.extension.to_json(),
            "escalation": self.escalation.to_json(),
            "delegation": None if self.delegation is None else self.delegation.to_json(),
            "issued_at": fidu1.format_instant(self.issued_at),
        }

    # -- reading ------------------------------------------------------------------

    @classmethod
    def _read(cls, fields: Fields) -> dict[str, Any]:
        charter = fields.object("charter", ("charter_id", "version", "digest"))
        subject = fields.object("subject", ("scope", "funded_from", "label"))
        term = fields.object("term", ("not_before", "not_after"))
        return {
            "mandate_id": fields.uuid("mandate_id"),
            "version": fields.integer("version", 1),
            "prev": None if fields.is_null("prev") else fields.hash("prev"),
            "charter": CharterRef(
                charter.uuid("charter_id"), charter.integer("version", 1), charter.hash("digest")
            ),
            "parent": None
            if fields.is_null("parent")
            else MandateRef.read(fields.object("parent", ("mandate_id", "version", "digest"))),
            "issuance": fields.choice("issuance", ("derived", "quorum")),
            "subject": Subject(
                _scope(subject, "scope"), _scope(subject, "funded_from"), subject.text("label")
            ),
            "not_before": term.instant("not_before"),
            "not_after": term.instant("not_after"),
            "roster": _read_roster(fields.object("roster", ("principals", "quorums"))),
            "budget": _read_budget(
                fields.object("budget", ("envelope", "per_call", "models", "max_output_tokens"))
            ),
            "governance": _read_governance(
                fields.object("governance", ("max_calls", "seconds", "fleet_velocity", "cognitive"))
            ),
            "data": _read_data(fields.object("data", _DATA_FIELDS)),
            "outbound": _read_outbound(fields.object("outbound", _OUTBOUND_FIELDS)),
            "windows": _read_windows(fields),
            "tools": _read_tools(fields.object("tools", ("statements", "bindings"))),
            "recovery": None
            if fields.is_null("recovery")
            else _read_recovery(fields.object("recovery", _RECOVERY_FIELDS)),
            "extension": None
            if fields.is_null("extension")
            else _read_extension(
                fields.object(
                    "extension", ("max_grant", "max_total", "margin", "ttl_seconds", "auto")
                )
            ),
            "escalation": _read_escalation(
                fields.object("escalation", ("max_open", "ttl_seconds", "auto"))
            ),
            "delegation": None
            if fields.is_null("delegation")
            else _read_delegation(
                fields.object("delegation", ("max_depth", "max_children", "envelope"))
            ),
            "issued_at": fields.instant("issued_at"),
        }

    def _agree(self) -> None:
        def wrong(path: str, message: str) -> MalformedError:
            return MalformedError("agreement", path, message)

        if (self.version == 1) != (self.prev is None):
            raise wrong("$.prev", "is null exactly for version 1")
        if self.parent is None and self.issuance != "quorum":
            raise wrong("$.issuance", "a root mandate is issued by a quorum, never derived (F4-8)")
        if self.subject.scope == self.subject.funded_from:
            raise wrong(
                "$.subject.funded_from", "a scope is funded from another scope, never itself"
            )
        if self.not_before >= self.not_after:
            raise wrong("$.term", "not_before comes before not_after")
        quorums = self.roster.quorums
        size = len(self.roster.principals)
        if size == 0:
            raise wrong("$.roster.principals", "names no principal")
        for name, value in quorums.items():
            if value > size:
                raise wrong(
                    f"$.roster.quorums.{name}",
                    f"is {value}, more than the roster's {size} principals",
                )
        if not quorums["reduce"] <= quorums["revoke"] <= min(quorums[c] for c in GRANTING):
            raise wrong(
                "$.roster.quorums",
                "must satisfy reduce <= revoke <= every granting quorum: stopping is never "
                "harder than starting (F4-9)",
            )
        if self.budget.per_call > self.budget.envelope:
            raise wrong("$.budget.per_call", "is more than the envelope")
        sinks = self.outbound.sinks
        for sink in self.outbound.per_sink:
            if sink not in sinks:
                raise wrong(
                    f"$.outbound.per_sink.{sink}", "names a sink the mandate does not allow"
                )
        for i, cap in enumerate(self.outbound.caps):
            if cap.sink not in sinks or cap.operation not in sinks[cap.sink].operations:
                raise wrong(f"$.outbound.caps[{i}]", "caps an operation the mandate does not allow")
        for i, allowed in enumerate(self.outbound.recipients):
            if allowed.sink not in sinks or allowed.operation not in sinks[allowed.sink].operations:
                raise wrong(
                    f"$.outbound.recipients[{i}]", "holds an operation the mandate does not allow"
                )
        for i, drawdown in enumerate(self.data.drawdown):
            if drawdown.table not in self.data.tables:
                raise wrong(f"$.data.drawdown[{i}]", "guards a table the mandate does not allow")
        if self.recovery is not None and self.recovery.step_estimate > self.recovery.reserve:
            raise wrong("$.recovery.step_estimate", "is more than the reserve")
        extension = self.extension
        if extension is not None:
            if extension.max_grant > extension.max_total:
                raise wrong("$.extension.max_grant", "is more than max_total")
            auto = extension.auto
            if auto is not None and (
                auto.max_grant > auto.max_total
                or auto.max_grant > extension.max_grant
                or auto.max_total > extension.max_total
            ):
                raise wrong(
                    "$.extension.auto", "must lie within itself and within the extension's bounds"
                )
        for i, rule in enumerate(self.escalation.auto):
            path = f"$.escalation.auto[{i}]"
            if rule.resolution == "grant" and (extension is None or extension.auto is None):
                raise wrong(
                    path, "grants automatically, and extension.auto bounds no automatic grant"
                )
            if rule.resolution == "requeue" and "max_requeues" not in rule.when:
                raise wrong(
                    path, "requeues automatically with no max_requeues: it would requeue forever"
                )
        if self.delegation is not None and self.delegation.envelope > self.budget.envelope:
            raise wrong("$.delegation.envelope", "is more than the mandate's own envelope")

    # -- against the charter ------------------------------------------------------

    def charter_problems(self, charter: Charter) -> tuple[str, ...]:
        """Where this mandate disagrees with the charter it names (F4-9)."""
        problems = []
        if (charter.charter_id, charter.version) != (self.charter.charter_id, self.charter.version):
            problems.append(
                f"names charter {self.charter.charter_id} version {self.charter.version}, not "
                f"{charter.charter_id} version {charter.version}"
            )
        elif charter.digest != self.charter.digest:
            problems.append("names its charter by another digest")
        unknown = sorted(set(self.roster.principals) - set(charter.names))
        if unknown:
            problems.append(f"its roster names {', '.join(unknown)}, whom the charter does not")
        if self.parent is None and self.roster.quorums["amend"] < charter.issue_quorum:
            problems.append(
                f"a root's amend quorum ({self.roster.quorums['amend']}) is below the "
                f"charter's issue "
                f"quorum ({charter.issue_quorum}): its roster could widen it with fewer approvals "
                f"than issued it"
            )
        return tuple(problems)


# --------------------------------------------------------------------------
# readers
# --------------------------------------------------------------------------

_DATA_FIELDS: Final = (
    "tables",
    "tenants",
    "declared_tenants",
    "max_rows",
    "max_tenants",
    "drawdown",
    "stated_footprint",
    "agreements",
    "facts",
)
_OUTBOUND_FIELDS: Final = ("sinks", "max_per_plan", "per_sink", "caps", "recipients", "tenants")
_RECOVERY_FIELDS: Final = (
    "reserve",
    "step_estimate",
    "max_steps",
    "directives",
    "revoke_on",
    "revocable",
    "token_ceiling",
    "guidance",
    "channel",
    "funding",
)


def _read_roster(fields: Fields) -> Roster:
    principals = fields.strings("principals", fidu1.NAME, "a principal's name", 64)
    quorums = fields.object("quorums", QUORUM_CLASSES)
    return Roster(principals, {name: quorums.integer(name, 1, 64) for name in QUORUM_CLASSES})


def _read_budget(fields: Fields) -> Budget:
    models = fields.strings("models", MODEL, "a model id", 64)
    if not models:
        raise MalformedError("agreement", fields.path("models"), "allows no model")
    return Budget(
        envelope=_money_positive(fields, "envelope"),
        per_call=_money_positive(fields, "per_call"),
        models=models,
        max_output_tokens=fields.integer("max_output_tokens", 1, 10_000_000),
    )


def _read_governance(fields: Fields) -> Governance:
    return Governance(
        max_calls=fields.integer("max_calls", 1, 1_000_000),
        seconds=fields.integer("seconds", 1, 86_400 * 366),
        fleet_velocity=fields.boolean("fleet_velocity"),
        cognitive=Cognitive.read(fields.object("cognitive", Cognitive.FIELDS)),
    )


def _read_data(fields: Fields) -> Data:
    tables: dict[str, tuple[str, ...]] = {}
    for table, path, ops in fields.mapping("tables", IDENTIFIER):
        operations = check_strings(ops, path, fidu1.one_of(OPERATIONS), "insert, update or delete")
        if not operations:
            raise MalformedError("agreement", path, "allows no operation: leave the table out")
        tables[table] = operations
    if not tables:
        raise MalformedError("agreement", fields.path("tables"), "allows no table")
    raw = fields.raw("tenants")
    tenants = None if raw == "*" else fields.strings("tenants", fidu1.TEXT, "a tenant")
    drawdown = []
    for path, item in fields.array("drawdown"):
        entry = Fields(item, path, ("table", "column", "per", "max_drop"))
        drawdown.append(
            Drawdown(
                entry.text("table", IDENTIFIER),
                entry.text("column", IDENTIFIER),
                entry.choice("per", ("table", "tenant")),
                _fraction(entry, "max_drop", Decimal(1)),
            )
        )
    footprint = None
    if not fields.is_null("stated_footprint"):
        entry = fields.object("stated_footprint", ("tolerance", "blocking"))
        footprint = Footprint(_fraction(entry, "tolerance"), entry.boolean("blocking"))
    agreements = []
    for path, item in fields.array("agreements"):
        entry = Fields(
            item, path, ("sink", "operation", "field", "table", "column", "measure", "key")
        )
        agreements.append(
            Agreement(
                entry.text("sink", SINK_NAME),
                entry.text("operation", OPERATION),
                entry.text("field", PATH),
                entry.text("table", IDENTIFIER),
                entry.text("column", IDENTIFIER),
                entry.choice("measure", ("inserted", "net", "value")),
                _key_pair(entry, "key"),
            )
        )
    facts = []
    for path, item in fields.array("facts"):
        entry = Fields(item, path, ("kind", "field", "table", "column", "key", "exempt"))
        kinds = entry.strings("kind", fidu1.TEXT, "an event kind")
        if not kinds:
            raise MalformedError("agreement", entry.path("kind"), "names no kind of fact")
        facts.append(
            FactRule(
                kinds,
                entry.text("field", PATH),
                entry.text("table", IDENTIFIER),
                entry.text("column", IDENTIFIER),
                _key_pair(entry, "key"),
                entry.strings("exempt", fidu1.TEXT),
            )
        )
    return Data(
        tables=tables,
        tenants=tenants,
        declared_tenants=fields.boolean("declared_tenants"),
        max_rows=fields.integer("max_rows", 1),
        max_tenants=fields.integer("max_tenants", 1, 1_000_000),
        drawdown=sorted_by(drawdown, lambda d: d.identity, fields.path("drawdown")),
        stated_footprint=footprint,
        agreements=sorted_by(agreements, lambda a: a.identity, fields.path("agreements")),
        facts=sorted_by(facts, lambda f: f.identity, fields.path("facts")),
    )


def _read_outbound(fields: Fields) -> Outbound:
    sinks: dict[str, SinkBounds] = {}
    for sink, path, raw in fields.mapping("sinks", SINK_NAME):
        entry = Fields(
            raw, path, ("operations", "max_attempts", "not_after_seconds", "max_payload_bytes")
        )
        operations = entry.strings("operations", OPERATION, "an operation")
        if not operations:
            raise MalformedError("agreement", path, "allows no operation: leave the sink out")
        sinks[sink] = SinkBounds(
            operations,
            entry.integer("max_attempts", 1, 1000),
            entry.integer("not_after_seconds", 1, 86_400 * 366),
            entry.integer("max_payload_bytes", 1, 16 * 1024 * 1024),
        )
    per_sink = {
        sink: check_integer(raw, path, 0, 1_000_000)
        for sink, path, raw in fields.mapping("per_sink", SINK_NAME)
    }
    caps = []
    for path, item in fields.array("caps"):
        entry = Fields(
            item, path, ("sink", "operation", "field", "maximum", "per", "currency_field")
        )
        raw_max = entry.raw("maximum")
        maximum: Decimal | dict[str, Decimal]
        if isinstance(raw_max, dict):
            maximum = {
                currency: check_decimal(v, p)
                for currency, p, v in entry.mapping("maximum", CURRENCY)
            }
            if not maximum:
                raise MalformedError("agreement", entry.path("maximum"), "caps no currency")
        else:
            maximum = entry.decimal("maximum")
        currency = None if entry.is_null("currency_field") else entry.text("currency_field", PATH)
        if isinstance(maximum, dict) and currency is None:
            raise MalformedError(
                "agreement",
                entry.path("currency_field"),
                "a cap by currency reads the currency somewhere",
            )
        caps.append(
            AmountCap(
                entry.text("sink", SINK_NAME),
                entry.text("operation", OPERATION),
                entry.text("field", PATH),
                maximum,
                entry.choice("per", ("plan", "request", "tenant")),
                currency,
            )
        )
    recipients = []
    for path, item in fields.array("recipients"):
        entry = Fields(
            item,
            path,
            (
                "sink",
                "operation",
                "kind",
                "fields",
                "domains",
                "addresses",
                "tenant_domains",
                "subdomains",
            ),
        )
        tenant_domains = {
            tenant: check_strings(raw, p, DOMAIN, "a domain name")
            for tenant, p, raw in entry.mapping("tenant_domains", fidu1.TEXT)
        }
        rule = Recipients(
            sink=entry.text("sink", SINK_NAME),
            operation=entry.text("operation", OPERATION),
            kind=entry.choice("kind", ("email", "url")),
            fields=entry.strings("fields", PATH, "a payload path"),
            domains=entry.strings("domains", DOMAIN, "a domain name"),
            addresses=entry.strings("addresses", ADDRESS, "an address"),
            tenant_domains=tenant_domains,
            subdomains=entry.boolean("subdomains"),
        )
        if not rule.fields:
            raise MalformedError(
                "agreement", entry.path("fields"), "names no field recipients are at"
            )
        if not (rule.domains or rule.addresses or rule.tenant_domains):
            raise MalformedError("agreement", path, "allows no recipient at all")
        if rule.kind == "url" and rule.addresses:
            raise MalformedError(
                "agreement", entry.path("addresses"), "a URL recipient is allowed by its domain"
            )
        recipients.append(rule)
    tenants = None
    if not fields.is_null("tenants"):
        entry = fields.object("tenants", ("max_tenants", "require_tenant", "fields"))
        tenants = OutboundTenants(
            entry.integer("max_tenants", 1, 1_000_000),
            entry.boolean("require_tenant"),
            {
                target: check_text(raw, p, PATH, "a payload path")
                for target, p, raw in entry.mapping("fields", OPERATION)
            },
        )
    return Outbound(
        sinks=sinks,
        max_per_plan=None
        if fields.is_null("max_per_plan")
        else fields.integer("max_per_plan", 0, 1_000_000),
        per_sink=per_sink,
        caps=sorted_by(caps, lambda c: c.identity, fields.path("caps")),
        recipients=sorted_by(recipients, lambda r: r.identity, fields.path("recipients")),
        tenants=tenants,
    )


def _read_windows(fields: Fields) -> tuple[Window, ...]:
    windows = []
    for path, item in fields.array("windows"):
        entry = Fields(item, path, ("name", "measure", "span_seconds", "limit", "per"))
        windows.append(
            Window(
                name=entry.text("name", WINDOW, "a window name, [a-z][a-z0-9_]{0,62}"),
                measure=Measure.read(entry.raw("measure"), entry.path("measure")),
                span_seconds=entry.integer("span_seconds", 1, 86_400 * 366),
                limit=entry.decimal("limit"),
                per=entry.choice("per", ("lineage", "tenant")),
            )
        )
    return sorted_by(windows, lambda w: (w.name,), fields.path("windows"))


def _read_tools(fields: Fields) -> Tools:
    pins = []
    for path, item in fields.array("bindings"):
        entry = Fields(item, path, ("name", "digest"))
        pins.append(ToolPin(entry.text("name", TOOL, "a tool name"), entry.hash("digest")))
    return Tools(
        statements=fields.choice("statements", ("any", "bound")),
        bindings=sorted_by(pins, lambda p: (p.name,), fields.path("bindings")),
    )


def _read_recovery(fields: Fields) -> Recovery:
    directives = []
    for path, item in fields.array("directives"):
        entry = Fields(item, path, ("id", "sha256", "answers"))
        answers = entry.strings("answers", fidu1.one_of(TRIP_KINDS), "a kind of halt")
        if not answers:
            raise MalformedError("agreement", entry.path("answers"), "answers no kind of halt")
        directives.append(Directive(entry.text("id", fidu1.NAME), entry.hash("sha256"), answers))
    return Recovery(
        reserve=_money_positive(fields, "reserve"),
        step_estimate=_money_positive(fields, "step_estimate"),
        max_steps=fields.integer("max_steps", 1, 100),
        directives=sorted_by(directives, lambda d: (d.directive_id,), fields.path("directives")),
        revoke_on=fields.strings("revoke_on", fidu1.one_of(TRIP_KINDS), "a kind of halt"),
        revocable=None
        if fields.is_null("revocable")
        else fields.strings("revocable", fidu1.TEXT, "a tool"),
        token_ceiling=None
        if fields.is_null("token_ceiling")
        else fields.integer("token_ceiling", 1, 10_000_000),
        guidance=fields.boolean("guidance"),
        channel=fields.choice("channel", ("system", "user")),
        funding=None if fields.is_null("funding") else _scope(fields, "funding"),
    )


def _read_extension(fields: Fields) -> Extension:
    auto = None
    if not fields.is_null("auto"):
        entry = fields.object("auto", ("max_grant", "max_total"))
        auto = ExtensionAuto(
            _money_positive(entry, "max_grant"), _money_positive(entry, "max_total")
        )
    return Extension(
        max_grant=_money_positive(fields, "max_grant"),
        max_total=_money_positive(fields, "max_total"),
        margin=_fraction(fields, "margin"),
        ttl_seconds=fields.integer("ttl_seconds", 1, 86_400 * 31),
        auto=auto,
    )


def _read_escalation(fields: Fields) -> Escalation:
    rules = []
    for path, item in fields.array("auto"):
        entry = Fields(item, path, ("id", "class", "resolution", "when"))
        ticket_class = entry.choice("class", TICKET_CLASSES)
        resolution = entry.choice("resolution", AUTO_RESOLUTIONS[ticket_class])
        vocabulary = WHEN.get(ticket_class, ())
        raw_when = entry.raw("when")
        if not isinstance(raw_when, dict):
            raise MalformedError("type", entry.path("when"), "must be an object")
        unknown = sorted(set(raw_when) - set(vocabulary))
        if unknown:
            raise MalformedError(
                "fields",
                entry.path("when"),
                f"holds {', '.join(unknown)}, outside the vocabulary of a {ticket_class} rule",
            )
        when_fields = Fields(raw_when, entry.path("when"), raw_when.keys())
        when: dict[str, Any] = {}
        for name in raw_when:
            if name == "exhaustive":
                when[name] = when_fields.boolean(name)
            elif name in ("max_dropped", "max_requeues"):
                when[name] = when_fields.integer(name, 0, 1000)
            elif name == "causes":
                when[name] = when_fields.strings(name, fidu1.one_of(DROP_CAUSES), "a cause")
            elif name == "outcome":
                when[name] = when_fields.strings(name, fidu1.TEXT, "an outcome class")
            else:  # max_amount
                when[name] = _money_positive(when_fields, name)
        rules.append(AutoRule(entry.text("id", fidu1.NAME), ticket_class, resolution, when))
    return Escalation(
        max_open=fields.integer("max_open", 0, 10_000),
        ttl_seconds=fields.integer("ttl_seconds", 1, 86_400 * 31),
        auto=sorted_by(rules, lambda r: (r.rule_id,), fields.path("auto")),
    )


def _read_delegation(fields: Fields) -> Delegation:
    return Delegation(
        max_depth=fields.integer("max_depth", 0, 64),
        max_children=fields.integer("max_children", 0, 10_000),
        envelope=_money_positive(fields, "envelope"),
    )

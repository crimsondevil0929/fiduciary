"""Compilation: a mandate's lineage into what Interlock and AgentGov enforce (§6).

:func:`compile` is pure (F6-1): no I/O, no clock, no environment. It reads the
version in force and its ancestors, root first, and a :class:`Deployment`
snapshot, and returns a :class:`CompiledMandate`. Compilation happens in two
steps:

1. it writes a *description*: canonical JSON naming every constructor and its
   arguments, sets sorted. The description's SHA-256 is the compiled digest
   (F6-2), fiduciary's own encoding, dependent on no ``repr``;
2. it builds every Interlock and AgentGov object *from the description*, so
   the digest describes exactly the objects built.

It fails closed (F6-3): a table the substrate does not observe, a sink or
operation the installed registry lacks, a pinned binding with no document, a
window name too long once namespaced, a directive whose text the deployment
lacks. Every reason is named in one :class:`CompileError`.

:func:`receipt_problems` holds an ARC1 receipt to the compiled policy it says
it ran under (F6-7, F6-8).
"""

from __future__ import annotations

import dataclasses
import hashlib
import itertools
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from functools import cache
from typing import Any, Final

from agentgov import BudgetManager, CognitivePolicy, GovernancePolicy, Unit
from agentgov.receipts import CheckerRecord, ReceiptLog
from interlock.checker_packs import CheckerPackError, PackPin, load_pack
from interlock.invariants import (
    BlastRadius,
    ColumnValueGuard,
    CrossEffectAgreement,
    FactAgreement,
    InvariantChecker,
    NoDelete,
    NoSchemaChange,
    StatedFootprint,
    TableAllowlist,
    TenantDrawdownGuard,
    TenantIsolation,
    TruncationGuard,
)
from interlock.meters import Meter, MeterCheck
from interlock.outbound import SinkRegistry, SinkSpec
from interlock.outbound_checks import (
    OutboundCount,
    OutboundTenantIsolation,
    PayloadAmountCap,
    RecipientAllowlist,
    SinkAllowlist,
)
from interlock.receipts import ReceiptIssuer, checker_records
from interlock.recovery import Channel, Directive, RecoveryPolicy, TripKind
from interlock.sensors import SensorAttestation
from interlock.types import Severity, canonical_hash
from interlock.windows import (
    Measure,
    Plans,
    RateWindow,
    RateWindowCheck,
    Requests,
    RequestSum,
    RowSum,
)

from fiduciary import fidu1
from fiduciary.checkers import BoundStatements, MandateScope, TenantAllowlist
from fiduciary.documents import Mandate, MandateRef, ToolBinding
from fiduciary.documents.mandate import PER_PARTITION
from fiduciary.exceptions import FiduciaryError

__all__ = [
    "SET_BEARING",
    "CompileError",
    "CompiledMandate",
    "Deployment",
    "GuardBounds",
    "InterceptorBounds",
    "UnitMeter",
    "canonical_digests",
    "compile",
    "receipt_problems",
    "window_name",
]

COMPILER: Final = "fiduciary-compile/1"
SET_BEARING: Final = frozenset(
    {"TableAllowlist", "NoDelete", "SinkAllowlist", "RecipientAllowlist", "FactAgreement"}
)
"""The built-in checkers whose configuration holds a set: before U-1, the
digests receipts record for them change with the hash seed (F6-8)."""
MAX_WINDOW: Final = 63


class CompileError(FiduciaryError):
    """A mandate that cannot be compiled against the deployment, with every reason."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = tuple(problems)


@dataclass(frozen=True, slots=True)
class Deployment:
    """What a deployment is, as compilation reads it: a snapshot, never a connection.

    :ivar ledger: The ``entry_hash`` of AgentGov entry 1.
    :ivar tables: Each observed table and its columns.
    :ivar sinks: The sink registry the last signed install vouched for.
    :ivar tools: Tool binding documents, by digest.
    :ivar directives: Directive texts, by their SHA-256.
    :ivar root_depth: How deep in AgentGov's tree the lineage root's
        ``funded_from`` scope is: 0 for a root scope.
    :ivar units: The units the deployment keeps an AgentGov ledger of, beside
        its ledger of dollars, each with the places it counts (1.4): what a
        mandate's ``budgets`` may conserve.
    """

    ledger: str
    tables: Mapping[str, frozenset[str]]
    sinks: SinkRegistry = field(default_factory=SinkRegistry)
    tools: Mapping[str, ToolBinding] = field(default_factory=dict)
    directives: Mapping[str, str] = field(default_factory=dict)
    root_depth: int = 0
    units: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InterceptorBounds:
    """What AgentGov's ``Interceptor`` is held to for a session's model calls."""

    per_call: Decimal
    max_output_tokens: int
    models: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GuardBounds:
    """``BudgetGuard``'s parameters, and the caps fiduciary holds a grant to."""

    margin: Decimal
    ttl: timedelta
    max_grant: Decimal
    max_total: Decimal
    auto_max_grant: Decimal | None
    auto_max_total: Decimal | None


@dataclass(frozen=True, slots=True)
class UnitMeter:
    """A mandate's budget in a unit, compiled: Interlock's ``Meter`` once the
    deployment's ledger of the unit is given (:meth:`meter`)."""

    unit: str
    places: int
    envelope: Decimal
    per_plan: Decimal
    measure: Measure

    def meter(self, ledger: BudgetManager, *, scope: str | None = None) -> Meter:
        """The meter over ``ledger``, which must conserve this unit."""
        if ledger.unit != Unit(self.unit, self.places):
            raise CompileError(
                [f"budgets.{self.unit}: the ledger given conserves {ledger.unit.text()}"]
            )
        return Meter(ledger, self.measure, self.per_plan, scope=scope)

    def check(self) -> MeterCheck:
        """The meter's check, as an engine built over any ledger of the unit
        runs it: its digest names the unit, the measure and the bound alone."""
        return MeterCheck(
            Meter(BudgetManager(unit=Unit(self.unit, self.places)), self.measure, self.per_plan)
        )


@dataclass(frozen=True, slots=True)
class CompiledMandate:
    """A mandate version compiled (§6.2). Build an engine from ``checkers``,
    ``windows`` and ``sinks``; its receipts from :meth:`receipt_issuer`."""

    mandate: MandateRef
    lineage: tuple[MandateRef, ...]
    subject: str
    funded_from: str
    envelope: Decimal
    description: Mapping[str, Any]
    digest: str
    checkers: tuple[InvariantChecker, ...]
    windows: tuple[RateWindow, ...]
    sinks: SinkRegistry | None
    governance: GovernancePolicy
    cognitive: CognitivePolicy
    interceptor: InterceptorBounds
    recovery: RecoveryPolicy | None
    guard: GuardBounds | None
    issuer: str
    policy_epoch: int
    meters: tuple[UnitMeter, ...] = ()

    def receipt_issuer(self, log: ReceiptLog, *, row_secret: bytes | None = None) -> ReceiptIssuer:
        """The issuer of this version's receipts: ``fiduciary:<mandate_id>``, at
        epoch ``version`` (F6-7), naming the tenants ``data.receipt_tenants``
        says."""
        return ReceiptIssuer(
            log,
            issuer=self.issuer,
            row_secret=row_secret,
            policy_epoch=self.policy_epoch,
            tenants=self.description["receipts"].get("tenants", "touched"),
        )

    def engine_checkers(self) -> tuple[InvariantChecker, ...]:
        """Every checker an engine built from this runs: the compiled ones, the
        ``RateWindowCheck`` the engine adds for each window, and the
        ``MeterCheck`` it adds for each meter."""
        return (
            self.checkers
            + tuple(RateWindowCheck(w) for w in self.windows)
            + tuple(m.check() for m in self.meters)
        )

    def checker_records(self) -> tuple[CheckerRecord, ...]:
        """What a receipt of this version records in ``decision.checkers``."""
        return checker_records(self.engine_checkers())


def window_name(mandate_id: str, name: str) -> str:
    """A mandate window's name in Interlock: ``f<h>_<name>``, ``h`` the first 16
    hex of SHA-256 of ``mandate_id`` and the name (F5-5). It depends on the
    lineage alone, so an amendment never resets the window's history."""
    digest = hashlib.sha256(f"{mandate_id}\n{name}".encode()).hexdigest()[:16]
    return f"f{digest}_{name}"


# --------------------------------------------------------------------------
# the description: canonical JSON of every constructor and its arguments
# --------------------------------------------------------------------------


def _dec(value: Decimal) -> str:
    return format(value.normalize(), "f") if value == value.to_integral() else format(value, "f")


def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.00000001")), "f")


def _describe(
    lineage: Sequence[Mandate], deployment: Deployment
) -> tuple[dict[str, Any], list[str]]:
    mandate = lineage[-1]
    problems: list[str] = []
    data, outbound = mandate.data, mandate.outbound

    # -- the deployment must hold what the mandate names (F6-3) --------------------
    def column(table: str, name: str, where: str) -> None:
        columns = deployment.tables.get(table)
        if columns is None:
            problems.append(f"{where}: table {table!r} is not observed by the substrate")
        elif name not in columns:
            problems.append(f"{where}: column {table}.{name} is not observed")

    for table in sorted(data.tables):
        if table not in deployment.tables:
            problems.append(f"data.tables: table {table!r} is not observed by the substrate")
    for guard in data.drawdown:
        column(guard.table, guard.column, "data.drawdown")
    for rule in data.agreements:
        column(rule.table, rule.column, "data.agreements")
    for fact in data.facts:
        column(fact.table, fact.column, "data.facts")
    for unit, budget in sorted(mandate.budgets.items()):
        places = deployment.units.get(unit)
        if places is None:
            problems.append(f"budgets.{unit}: the deployment keeps no ledger of {unit}")
        elif places != budget.places:
            problems.append(
                f"budgets.{unit}: the deployment's ledger counts {unit} to {places} places, "
                f"not {budget.places}"
            )
        if budget.measure.kind == "row_sum":
            column(str(budget.measure.table), str(budget.measure.column), f"budgets.{unit}")
    installed: dict[str, SinkSpec] = {}
    for sink, bounds in sorted(outbound.sinks.items()):
        spec = deployment.sinks.get(sink)
        if spec is None:
            problems.append(f"outbound.sinks: sink {sink!r} is not installed")
            continue
        missing = [op for op in bounds.operations if spec.operation(op) is None]
        if missing:
            problems.append(
                f"outbound.sinks.{sink}: operation(s) {', '.join(missing)} not installed"
            )
            continue
        installed[sink] = spec

    # -- tool bindings: pinned documents, and the statements they carry ------------
    bound: set[tuple[str, str, str]] = set()
    for pin in mandate.tools.bindings:
        tool = deployment.tools.get(pin.digest)
        where = f"tools.bindings[{pin.name}]"
        if tool is None:
            problems.append(f"{where}: no tool binding document has digest {pin.digest[:16]}")
            continue
        if tool.name != pin.name:
            problems.append(f"{where}: the document pinned is tool {tool.name!r}")
        for effect in tool.effects:
            operations = data.tables.get(effect.table)
            if operations is None or effect.kind not in operations:
                problems.append(
                    f"{where}: it {effect.kind}s table {effect.table!r}, which the mandate "
                    f"does not allow"
                )
            bound.add((effect.kind, effect.table, effect.statement_digest))
        for request in tool.requests:
            limit = outbound.sinks.get(request.sink)
            if limit is None or request.operation not in limit.operations:
                problems.append(
                    f"{where}: it requests {request.sink}.{request.operation}, which the "
                    f"mandate does not allow"
                )

    # -- windows, along the whole lineage (F5-5) -----------------------------------
    windows: list[dict[str, Any]] = []
    for member in lineage:
        for window in member.windows:
            name = window_name(member.mandate_id, window.name)
            if len(name) > MAX_WINDOW:
                problems.append(
                    f"windows[{window.name}]: namespaced as {name}, past {MAX_WINDOW} characters"
                )
            measure = window.measure
            if measure.kind == "row_sum":
                column(str(measure.table), str(measure.column), f"windows[{window.name}]")
            windows.append(
                {
                    "name": name,
                    "span_seconds": window.span_seconds,
                    "limit": _dec(window.limit),
                    "measure": measure.to_json(),
                    "per": {"lineage": "global", "tenant": "tenant"}.get(window.per, window.per),
                }
            )
    windows.sort(key=lambda w: str(w["name"]))

    # -- the directives the recovery policy may send --------------------------------
    recovery = mandate.recovery
    directives = []
    if recovery is not None:
        for directive in recovery.directives:
            text = deployment.directives.get(directive.sha256)
            if text is None:
                problems.append(
                    f"recovery.directives[{directive.directive_id}]: the deployment holds no "
                    f"text with its sha256"
                )
            elif hashlib.sha256(text.encode("utf-8")).hexdigest() != directive.sha256:
                problems.append(
                    f"recovery.directives[{directive.directive_id}]: its text does not hash to "
                    f"its sha256"
                )
            else:
                directives.append(
                    {"id": directive.directive_id, "text": text, "answers": list(directive.answers)}
                )

    # -- the checkers ---------------------------------------------------------------
    checkers: list[list[Any]] = [
        # The unmeasured values a mandate accepts only when it names some, so
        # every policy compiled before keeps its description (1.5.0).
        [
            "TruncationGuard",
            {"acknowledged_unknown": list(data.acknowledge_unknown)}
            if data.acknowledge_unknown
            else {},
        ],
        ["NoSchemaChange", {}],
        ["BlastRadius", {"limit": data.max_rows}],
        ["TenantIsolation", {"max_tenants": data.max_tenants, "declared": data.declared_tenants}],
        *(
            [
                "TenantIsolation",
                {"max_tenants": rule.max, "declared": rule.declared, "dimension": dimension},
            ]
            for dimension, rule in sorted(data.partitions.items())
        ),
        ["TableAllowlist", {"tables": sorted(data.tables)}],
        ["NoDelete", {"granted": sorted(t for t, ops in data.tables.items() if "delete" in ops)}],
    ]
    if data.stated_footprint is not None:
        checkers.append(
            [
                "StatedFootprint",
                {
                    "tolerance": _dec(data.stated_footprint.tolerance),
                    "blocking": data.stated_footprint.blocking,
                },
            ]
        )
    for guard in data.drawdown:
        kind = "ColumnValueGuard" if guard.per == "table" else "TenantDrawdownGuard"
        args: dict[str, Any] = {
            "table": guard.table,
            "column": guard.column,
            "max_drop": None if guard.max_drop is None else _dec(guard.max_drop),
        }
        # Only when used, so a guard compiled before keeps its description.
        if guard.max_rise is not None:
            args["max_rise"] = _dec(guard.max_rise)
        if guard.per.startswith(PER_PARTITION):
            args["dimension"] = guard.per.removeprefix(PER_PARTITION)
        checkers.append([kind, args])
    for rule in data.agreements:
        checkers.append(["CrossEffectAgreement", rule.to_json()])
    for fact in data.facts:
        checkers.append(["FactAgreement", fact.to_json()])
    for sensor in data.sensors:
        checkers.append(["SensorAttestation", sensor.to_json()])
    for pinned in data.checkers:
        checkers.append(["CheckerPack", pinned.to_json()])
    if outbound.sinks:
        checkers.append(
            [
                "SinkAllowlist",
                {"allowed": {s: list(b.operations) for s, b in sorted(outbound.sinks.items())}},
            ]
        )
    if outbound.max_per_plan is not None or outbound.per_sink:
        checkers.append(
            [
                "OutboundCount",
                {
                    "max_per_plan": outbound.max_per_plan,
                    "per_sink": dict(sorted(outbound.per_sink.items())),
                },
            ]
        )
    for cap in outbound.caps:
        checkers.append(["PayloadAmountCap", cap.to_json()])
    for recipients in outbound.recipients:
        checkers.append(["RecipientAllowlist", recipients.to_json()])
    if outbound.tenants is not None:
        checkers.append(["OutboundTenantIsolation", outbound.tenants.to_json()])

    # -- what is not a checker ---------------------------------------------------
    sinks = []
    for sink, spec in sorted(installed.items()):
        bounds = outbound.sinks[sink]
        sinks.append(
            {
                "name": sink,
                "operations": list(bounds.operations),
                "max_attempts": min(spec.max_attempts, bounds.max_attempts),
                "not_after_seconds": min(
                    int(spec.not_after.total_seconds()), bounds.not_after_seconds
                ),
                "max_payload_bytes": min(spec.max_payload_bytes, bounds.max_payload_bytes),
            }
        )
    depth = deployment.root_depth + len(lineage)
    delegation = mandate.delegation.max_depth if mandate.delegation is not None else 0
    cognitive = mandate.governance.cognitive
    description: dict[str, Any] = {
        "compiler": COMPILER,
        "mandate": mandate.ref.to_json(),
        "lineage": [m.ref.to_json() for m in lineage],
        "deployment": {"ledger": deployment.ledger},
        "subject": mandate.subject.scope,
        "funded_from": mandate.subject.funded_from,
        "envelope": _money(mandate.budget.envelope),
        "checkers": checkers,
        "fiduciary": {
            "subject": mandate.subject.scope,
            "tenants": None if data.tenants is None else list(data.tenants),
            "bound": None
            if mandate.tools.statements == "any"
            else [list(b) for b in sorted(bound)],
        },
        "windows": windows,
        "sinks": sinks,
        "governance": {
            "max_calls_per_window": mandate.governance.max_calls,
            "window_seconds": mandate.governance.seconds,
            "max_depth": depth + delegation,
            "fleet_velocity": mandate.governance.fleet_velocity,
        },
        "cognitive": cognitive.to_json(),
        "interceptor": {
            "per_call": _money(mandate.budget.per_call),
            "max_output_tokens": mandate.budget.max_output_tokens,
            "models": list(mandate.budget.models),
        },
        "recovery": None if recovery is None else {**recovery.to_json(), "directives": directives},
        "guard": None if mandate.extension is None else mandate.extension.to_json(),
        "receipts": {"issuer": f"fiduciary:{mandate.mandate_id}", "policy_epoch": mandate.version},
    }
    if mandate.budgets:
        description["budgets"] = {u: b.to_json() for u, b in sorted(mandate.budgets.items())}
    if mandate.data.receipt_tenants != "touched":
        description["receipts"]["tenants"] = mandate.data.receipt_tenants
    return description, problems


# --------------------------------------------------------------------------
# objects, built from the description
# --------------------------------------------------------------------------


def _key(value: Any) -> tuple[str, str] | None:
    return None if value is None else (str(value[0]), str(value[1]))


def _maximum(value: Any) -> Any:
    return {c: Decimal(v) for c, v in value.items()} if isinstance(value, dict) else Decimal(value)


_BUILT: Final[Mapping[str, Callable[[Mapping[str, Any]], InvariantChecker]]] = {
    "TruncationGuard": lambda a: TruncationGuard(a.get("acknowledged_unknown", ())),
    "NoSchemaChange": lambda a: NoSchemaChange(),
    "BlastRadius": lambda a: BlastRadius(a["limit"]),
    "TenantIsolation": lambda a: TenantIsolation(
        a["max_tenants"], declared=a["declared"], dimension=a.get("dimension", "tenant")
    ),
    "TableAllowlist": lambda a: TableAllowlist(a["tables"]),
    "NoDelete": lambda a: NoDelete(a["granted"]),
    "StatedFootprint": lambda a: StatedFootprint(
        tolerance=float(Decimal(a["tolerance"])),
        severity=Severity.BLOCKING if a["blocking"] else Severity.ADVISORY,
    ),
    "ColumnValueGuard": lambda a: ColumnValueGuard(
        a["table"], a["column"], max_drop_fraction=_drop(a), max_rise_fraction=_rise(a)
    ),
    "TenantDrawdownGuard": lambda a: TenantDrawdownGuard(
        a["table"],
        a["column"],
        max_drop_fraction=_drop(a),
        max_rise_fraction=_rise(a),
        dimension=a.get("dimension", "tenant"),
    ),
    "CrossEffectAgreement": lambda a: CrossEffectAgreement(
        a["sink"],
        a["operation"],
        field=a["field"],
        table=a["table"],
        column=a["column"],
        measure=a["measure"],
        key=_key(a["key"]),
        also=tuple(a.get("also", ())),
    ),
    "FactAgreement": lambda a: FactAgreement(
        list(a["kind"]),
        field=a["field"],
        table=a["table"],
        column=a["column"],
        key=_key(a["key"]),
        exempt=list(a["exempt"]),
    ),
    "SinkAllowlist": lambda a: SinkAllowlist({s: list(ops) for s, ops in a["allowed"].items()}),
    "SensorAttestation": lambda a: SensorAttestation(a["sensor"], a["keys"]),
    "CheckerPack": lambda a: _pack_checker(a),
    "OutboundCount": lambda a: OutboundCount(
        a["max_per_plan"], per_sink=dict(a["per_sink"]) or None
    ),
    "PayloadAmountCap": lambda a: PayloadAmountCap(
        a["sink"],
        a["operation"],
        field=a["field"],
        maximum=_maximum(a["maximum"]),
        per=a["per"],
        currency_field=a["currency_field"],
        unit_field=a.get("unit_field"),
    ),
    "RecipientAllowlist": lambda a: RecipientAllowlist(
        a["sink"],
        a["operation"],
        fields=list(a["fields"]),
        domains=_domains(a),
        addresses=list(a["addresses"]),
        tenant_domains={t: list(d) for t, d in a["tenant_domains"].items()},
        subdomains=a["subdomains"],
        kind=a["kind"],
    ),
    "OutboundTenantIsolation": lambda a: OutboundTenantIsolation(
        fields=dict(a["fields"]), max_tenants=a["max_tenants"], require_tenant=a["require_tenant"]
    ),
}


SEALED_NOWHERE: Final = "sealed.invalid"
"""What a rule whose every tenant is sealed admits where it is sealed: a name
RFC 2606 reserves, at which no address is, so it admits nothing until a child
opens an entry (``fiduciary.sealing``)."""


def _pack_checker(args: Mapping[str, Any]) -> InvariantChecker:
    """A pinned pack's checker, if exactly the code pinned is installed.

    :raises CompileError: If it is not, naming the pack, so a verifier
        replaying the policy (V4) says which pack it needs.
    """
    try:
        pack = load_pack(PackPin(args["module"], args["version"], args["digest"]))
        return pack.checker(args["checker"], args["config"])
    except CheckerPackError as exc:
        raise CompileError([f"data.checkers[{args['module']}.{args['checker']}]: {exc}"]) from exc


def _domains(args: Mapping[str, Any]) -> list[str]:
    domains = list(args["domains"])
    if args.get("sealed") and not (domains or args["addresses"] or args["tenant_domains"]):
        return [SEALED_NOWHERE]
    return domains


def _drop(args: Mapping[str, Any]) -> float | None:
    # A float, as every guard was built before: its digest is unchanged.
    return None if args["max_drop"] is None else float(Decimal(args["max_drop"]))


def _rise(args: Mapping[str, Any]) -> Decimal | None:
    return None if "max_rise" not in args else Decimal(args["max_rise"])


def _measure(raw: Mapping[str, Any]) -> Measure:
    kind = raw["kind"]
    if kind == "plans":
        return Plans()
    if kind == "requests":
        return Requests(raw["sink"], raw["operation"])
    if kind == "request_sum":
        return RequestSum(raw["sink"], raw["operation"], raw["field"])
    return RowSum(raw["table"], raw["column"], raw["rows"])


def _build(description: Mapping[str, Any], digest: str, deployment: Deployment) -> dict[str, Any]:
    mandate = description["mandate"]
    checkers: list[InvariantChecker] = [
        _BUILT[name](args) for name, args in description["checkers"]
    ]
    extra = description["fiduciary"]
    checkers.append(
        MandateScope(
            mandate["mandate_id"], mandate["version"], mandate["digest"], digest, extra["subject"]
        )
    )
    if extra["tenants"] is not None:
        checkers.append(TenantAllowlist(extra["tenants"]))
    if extra["bound"] is not None:
        checkers.append(BoundStatements(tuple(b) for b in extra["bound"]))
    windows = tuple(
        RateWindow(
            w["name"],
            timedelta(seconds=w["span_seconds"]),
            Decimal(w["limit"]),
            _measure(w["measure"]),
            w["per"],
        )
        for w in description["windows"]
    )
    sinks = None
    if description["sinks"]:
        narrowed = []
        for entry in description["sinks"]:
            spec = deployment.sinks.get(entry["name"])
            assert spec is not None  # the description is built only over installed sinks
            narrowed.append(
                dataclasses.replace(
                    spec,
                    operations=tuple(
                        op for op in spec.operations if op.name in entry["operations"]
                    ),
                    max_attempts=entry["max_attempts"],
                    not_after=timedelta(seconds=entry["not_after_seconds"]),
                    max_payload_bytes=entry["max_payload_bytes"],
                )
            )
        sinks = SinkRegistry(narrowed)
    g = description["governance"]
    governance = GovernancePolicy(
        max_calls_per_window=g["max_calls_per_window"],
        window_seconds=float(g["window_seconds"]),
        max_depth=g["max_depth"],
        trip_on_overdraft=True,
        trip_on_exhaustion=True,
        fleet_velocity=g["fleet_velocity"],
    )
    c = description["cognitive"]
    cognitive = CognitivePolicy(
        max_identical_repeats=c["max_identical_repeats"],
        similarity_threshold=float(Decimal(c["similarity_threshold"])),
        max_similar_streak=c["max_similar_streak"],
        result_similarity_threshold=float(Decimal(c["result_similarity_threshold"])),
        max_cycle_period=c["max_cycle_period"],
        min_cycle_repeats=c["min_cycle_repeats"],
        min_novelty_ratio=float(Decimal(c["min_novelty_ratio"])),
        exempt_tools=frozenset(c["exempt_tools"]),
    )
    i = description["interceptor"]
    interceptor = InterceptorBounds(
        Decimal(i["per_call"]), i["max_output_tokens"], tuple(i["models"])
    )
    recovery = None
    r = description["recovery"]
    if r is not None:
        recovery = RecoveryPolicy(
            reserve=Decimal(r["reserve"]),
            step_estimate=Decimal(r["step_estimate"]),
            max_steps=r["max_steps"],
            directives=tuple(
                Directive(d["id"], d["text"], frozenset(TripKind(k) for k in d["answers"]))
                for d in r["directives"]
            ),
            revoke_on=frozenset(TripKind(k) for k in r["revoke_on"]),
            revocable=None if r["revocable"] is None else frozenset(r["revocable"]),
            token_ceiling=r["token_ceiling"],
            guidance=r["guidance"],
            channel=Channel(r["channel"]),
            funding=r["funding"],
        )
    guard = None
    x = description["guard"]
    if x is not None:
        auto = x["auto"]
        guard = GuardBounds(
            margin=Decimal(x["margin"]),
            ttl=timedelta(seconds=x["ttl_seconds"]),
            max_grant=Decimal(x["max_grant"]),
            max_total=Decimal(x["max_total"]),
            auto_max_grant=None if auto is None else Decimal(auto["max_grant"]),
            auto_max_total=None if auto is None else Decimal(auto["max_total"]),
        )
    meters = tuple(
        UnitMeter(
            unit,
            b["places"],
            Decimal(b["envelope"]),
            Decimal(b["per_plan"]),
            _measure(b["measure"]),
        )
        for unit, b in sorted(description.get("budgets", {}).items())
    )
    return {
        "checkers": tuple(checkers),
        "windows": windows,
        "sinks": sinks,
        "governance": governance,
        "cognitive": cognitive,
        "interceptor": interceptor,
        "recovery": recovery,
        "guard": guard,
        "meters": meters,
    }


def compile(lineage: Sequence[Mandate], *, deployment: Deployment) -> CompiledMandate:
    """Compile a mandate version (§6). ``lineage`` is the version in force and
    every ancestor's version in force, root first.

    :raises CompileError: On anything the deployment cannot enforce, every
        reason at once (F6-3).
    """
    if not lineage:
        raise CompileError(["nothing to compile"])
    problems = []
    for parent, child in itertools.pairwise(lineage):
        if child.parent is None or child.parent.mandate_id != parent.mandate_id:
            problems.append(f"mandate {child.mandate_id} is not a child of {parent.mandate_id}")
    if lineage[0].parent is not None:
        problems.append("the lineage begins at a child: give every ancestor, root first")
    description, found = _describe(lineage, deployment)
    problems.extend(found)
    if problems:
        raise CompileError(problems)
    digest = hashlib.sha256(fidu1.canonical(description)).hexdigest()
    try:
        built = _build(description, digest, deployment)
    except ValueError as exc:
        raise CompileError([f"Interlock refuses the compiled policy: {exc}"]) from exc
    mandate = lineage[-1]
    return CompiledMandate(
        mandate=mandate.ref,
        lineage=tuple(m.ref for m in lineage),
        subject=mandate.subject.scope,
        funded_from=mandate.subject.funded_from,
        envelope=mandate.budget.envelope,
        description=description,
        digest=digest,
        issuer=description["receipts"]["issuer"],
        policy_epoch=mandate.version,
        **built,
    )


# --------------------------------------------------------------------------
# receipts (F6-7, F6-8)
# --------------------------------------------------------------------------


@cache
def canonical_digests() -> bool:
    """Whether this Interlock digests a checker's sets in sorted order (U-1):
    then every checker record of a receipt can be recomputed in any process.

    Probed by behaviour, not by version: a checker holding twelve tables is
    digested as Interlock would with its sets sorted; a match by chance, with
    the sets in hash order, is one in twelve factorial. A probe that cannot
    tell answers ``False``, the weaker check, never the stronger.
    """
    names = [f"t{i:02d}" for i in range(12)]
    rendered = "frozenset({" + ", ".join(repr(n) for n in sorted(names)) + "})"
    config = [("TableAllowlist", ""), ("_allowed", rendered)]
    expected = canonical_hash(["table_allowlist", sorted(map(repr, [config]))])
    (record,) = checker_records([TableAllowlist(names)])
    return record.config_hash == expected


def receipt_problems(
    receipt: Any, compiled: CompiledMandate, *, strict: bool | None = None
) -> list[str]:
    """Why an ARC1 action receipt does not show it was decided under
    ``compiled`` (F6-7, F6-8): its issuer, its policy epoch, and the
    configuration digests of its checkers.

    Every checker the compiled policy implies must be recorded; extra ones may
    be, since an extra checker only tightens (§5.6). With ``strict`` (by
    default, when :func:`canonical_digests`), every record must carry the
    digest the compiled policy implies. Without it, the records of checkers
    whose configuration holds a set are checked by name only.
    """
    if strict is None:
        strict = canonical_digests()
    problems = []
    if receipt.issuer != compiled.issuer:
        problems.append(f"issued by {receipt.issuer!r}, not {compiled.issuer!r}")
    if receipt.decision.policy_epoch != compiled.policy_epoch:
        problems.append(
            f"policy epoch {receipt.decision.policy_epoch}, not version {compiled.policy_epoch}"
        )
    recorded = {r.name: r.config_hash for r in receipt.decision.checkers}
    loose = _set_bearing(compiled)
    for record in compiled.checker_records():
        found = recorded.get(record.name)
        if found is None:
            problems.append(f"checker {record.name} is not recorded")
        elif found != record.config_hash and (strict or record.name not in loose):
            problems.append(f"checker {record.name} ran with another configuration")
    return problems


def _set_bearing(compiled: CompiledMandate) -> set[str]:
    """The names of ``compiled``'s checkers whose configuration holds a set."""
    return {
        name
        for name, cls in ((c.name, type(c).__name__) for c in compiled.engine_checkers())
        if cls in SET_BEARING
    }


def sets_canonical(receipt: Any, compiled: CompiledMandate) -> bool:
    """Whether every set-bearing checker ``receipt`` records carries the digest
    ``compiled`` implies here. False for a receipt an Interlock issued before
    U-1, whose set digests followed its process's hash seed, and for one that
    ran another set: the two cannot be told apart."""
    recorded = {r.name: r.config_hash for r in receipt.decision.checkers}
    loose = _set_bearing(compiled)
    return all(
        recorded.get(r.name) == r.config_hash for r in compiled.checker_records() if r.name in loose
    )

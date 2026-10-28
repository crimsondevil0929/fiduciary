"""What every domain pack shares: its deployment, its gateway, and the acts
whose shape is the same in every industry.

A pack (:class:`Pack`) is data: a schema with the triggers that came with it,
the tables the stage observes and the column each reads its partition from,
the sinks, the tool bindings, the mandate's data, outbound and window
sections, the gateway's Cedar policies, and the acts. :func:`build` stands its
deployment up exactly as :func:`fiduciary.demo.world.build` stands up the
fintech one: fresh keys, a charter of three principals adopted on its quorum,
a mandate issued on two approvals and bound, and the same AgentGov ledger,
Interlock outbox and fiduciary store. Nothing here is specific to a domain.

The acts every pack plays the same way are here too: a request held by a
pause and released on a 2-of-3 warrant (:func:`act_quorum`), a release by
hand with a break-glass key (:func:`act_break_glass`), and the offline audit
(:func:`act_audit`). Each pack's own acts call :func:`call` the way the
fintech acts do: the gateway first, then the session, every figure measured.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.metadata
import json
import sqlite3
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from agentgov import BudgetManager
from agentgov.receipts import ReceiptLog
from agentgov.receipts.signing import Signer, verifier_of
from interlock.deliveries import operations
from interlock.engine import StageResult
from interlock.operators import OPERATOR_LOG as OPERATOR_SCOPE
from interlock.operators import Operator, OperatorLog
from interlock.outbound import SinkRegistry, SinkSpec
from interlock.records import Keyring, RecordLog
from interlock.relay import DELIVERED, Delivery, DeliveryResult, LedgerBreaker, Relay
from interlock.sqlite_outbox import OPERATOR, RELAY, SqliteOutboxStore, install_sqlite_outbox
from interlock.substrate import SqliteSubstrate, TableSpec

from fiduciary import _upstream, fidu1
from fiduciary.authority import Authority
from fiduciary.binding import Binder
from fiduciary.bridge import CallResult, Fiduciary, Session
from fiduciary.bundle import BundleReport, Evidence, TrustedKeys, export_bundle, verify_bundle
from fiduciary.bundle.export import spec as key_spec
from fiduciary.compiler import Deployment
from fiduciary.demo.acts import measured as fintech_measured
from fiduciary.demo.acts import no_network, scan
from fiduciary.demo.gateway import Condition, Decision, Policy
from fiduciary.demo.world import DIRECTIVE, PRICING, World, _key
from fiduciary.documents import Charter, Mandate, ToolBinding
from fiduciary.log import Log, ensure_anchor_scope
from fiduciary.sqlite import SqliteStore

__all__ = [
    "Pack",
    "PackGateway",
    "PackRun",
    "PackWorld",
    "Table",
    "Vendor",
    "act_audit",
    "act_break_glass",
    "act_quorum",
    "build",
    "call",
    "mandate_json",
    "plan_tool",
]

QUORUMS: Final = {
    "amend": 2,
    "revoke": 1,
    "reduce": 1,
    "grant": 2,
    "reset": 2,
    "reset_safety": 2,
    "release": 2,
    "requeue": 2,
    "compensate": 2,
    "compensate_late": 2,
    "accept_repair": 2,
}


# --------------------------------------------------------------------------
# a pack, as data
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Table:
    name: str
    columns: tuple[str, ...]
    tenant: str | None
    """The column the stage reads each row's partition from, or none."""


@dataclass(frozen=True, slots=True)
class Pack:
    """One industry's consequence profile (module docstring)."""

    key: str
    industry: str
    title: str
    frameworks: tuple[str, ...]
    """The control-framework packs this industry's assessor reads first."""
    scope: str
    funding: str
    label: str
    principals: tuple[str, str, str]
    operator: str
    """The break-glass key's holder, declared in the charter."""
    tenants: tuple[str, ...]
    partition: str
    """What a tenant is here, in the industry's words."""
    schema: str
    seed: Callable[[sqlite3.Connection], None]
    tables: tuple[Table, ...]
    sinks: tuple[SinkSpec, ...]
    tools: Callable[[], list[dict[str, Any]]]
    data: Callable[[], dict[str, Any]]
    outbound: Callable[[], dict[str, Any]]
    windows: Callable[[], list[dict[str, Any]]]
    policies: tuple[Policy, ...]
    entities: Callable[[], Mapping[str, str]]
    """The gateway's entity store: each resource it knows (``argument:value``),
    and its tenant."""
    resource: Mapping[str, str]
    """For each tool, the argument that names its resource."""
    summed: Mapping[str, str]
    """For each tool the gateway counts per session, the argument it sums."""
    titles: Mapping[str, str]
    acts: Callable[[PackRun], None]
    canaries: Callable[[PackRun], dict[str, list[str]]]
    nouns: Mapping[str, str]
    quorums: Mapping[str, int] = field(default_factory=lambda: dict(QUORUMS))
    delegation: Mapping[str, Any] | None = None


# --------------------------------------------------------------------------
# the gateway: the same Cedar machinery as the fintech one, over any tools
# --------------------------------------------------------------------------


class PackGateway:
    """Cedar over each call's input, with a running sum per session for the
    tools a pack counts (:mod:`fiduciary.demo.gateway` describes the fintech
    one; this decides alike, over a pack's own policies and entity store).

    :param engine: ``"auto"`` (cedarpy when installed), ``"cedarpy"`` or ``"builtin"``.
    """

    def __init__(self, pack: Pack, tenant: str, *, engine: str = "auto") -> None:
        self._pack = pack
        self._principal = pack.scope
        self._tenant = tenant
        self._entities_of = dict(pack.entities())
        self._sums: dict[tuple[str, str], int] = defaultdict(int)
        self._sessions: dict[str, str] = {}
        self._cedar: Any = None
        if engine not in ("auto", "cedarpy", "builtin"):
            raise ValueError(f"engine is auto, cedarpy or builtin, not {engine!r}")
        if engine != "builtin":
            try:
                self._cedar = importlib.import_module("cedarpy")
            except ImportError:
                if engine == "cedarpy":
                    raise
        if self._cedar is not None:
            self.engine = f"cedarpy {importlib.metadata.version('cedarpy')}"
        else:
            self.engine = "built-in evaluator"
        self.policies = "\n\n".join(p.cedar() for p in pack.policies) + "\n"
        self._parsed: Any = self.policies
        if self._cedar is not None:
            if hasattr(self._cedar, "PolicySet"):
                self._parsed = self._cedar.PolicySet.from_str(self.policies)
            first = pack.policies[0].action
            self._cedar.is_authorized(self.request("warm-up", first, {}), self._parsed, [])
            self._sums.clear()

    def _resource(self, tool: str, arguments: Mapping[str, Any]) -> str:
        name = self._pack.resource.get(tool)
        return f"{name}:{arguments.get(name, '')}" if name else tool

    def request(self, session: str, tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        context: dict[str, Any] = {"session": session}
        for name, value in arguments.items():
            if isinstance(value, int) and not isinstance(value, bool):
                context[name] = value
        summed = self._pack.summed.get(tool)
        if summed is not None and summed in context:
            context[f"session_{summed}"] = self._sums[(session, summed)]
        return {
            "principal": f'Agent::"{self._principal}"',
            "action": f'Action::"{tool}"',
            "resource": f'Resource::"{self._resource(tool, arguments)}"',
            "context": context,
        }

    def opened(self, session: str, tenant: str | None) -> None:
        """The tenant a session acts for, as the gateway's caller tells it."""
        if tenant is not None:
            self._sessions[session] = tenant

    def _entities(self, session: str, resource: str) -> list[dict[str, Any]]:
        attrs = {"tenant": self._entities_of[resource]} if resource in self._entities_of else {}
        return [
            {
                "uid": {"type": "Agent", "id": self._principal},
                "attrs": {"tenant": self._sessions.get(session, self._tenant)},
                "parents": [],
            },
            {"uid": {"type": "Resource", "id": resource}, "attrs": attrs, "parents": []},
        ]

    def decide(self, session: str, tool: str, arguments: Mapping[str, Any]) -> Decision:
        """Decide one call; a permitted call adds to its session's sum."""
        request = self.request(session, tool, arguments)
        resource = self._resource(tool, arguments)
        started = time.perf_counter_ns()
        if self._cedar is not None:
            entities = self._entities(session, resource)
            result = self._cedar.is_authorized(request, self._parsed, entities)
            permitted = bool(result.allowed)
            reasons = tuple(sorted(self._policy_id(str(r)) for r in result.diagnostics.reasons))
        else:
            reasons = self._builtin(session, tool, request["context"], resource)
            permitted = bool(reasons)
        micros = (time.perf_counter_ns() - started) // 1000
        summed = self._pack.summed.get(tool)
        if permitted and summed is not None and summed in request["context"]:
            self._sums[(session, summed)] += int(request["context"][summed])
        decision = "PERMIT" if permitted else "DENY"
        return Decision(tool, session, decision, reasons, request, self.engine, micros)

    def _policy_id(self, reason: str) -> str:
        policies = self._pack.policies
        if reason.startswith("policy") and reason[6:].isdigit() and int(reason[6:]) < len(policies):
            return policies[int(reason[6:])].policy_id
        return reason

    def _builtin(
        self, session: str, tool: str, context: Mapping[str, Any], resource: str
    ) -> tuple[str, ...]:
        """The policies that permit the request: Cedar's semantics for the
        forms :class:`~fiduciary.demo.gateway.Policy` writes."""
        attributes: dict[str, Any] = {
            "principal.tenant": self._sessions.get(session, self._tenant),
            **{f"context.{k}": v for k, v in context.items()},
        }
        if resource in self._entities_of:
            attributes["resource.tenant"] = self._entities_of[resource]
        missing = object()

        def value(term: str | int) -> Any:
            if isinstance(term, int):
                return term
            return attributes.get(term, missing) if "." in term else term

        def holds(c: Condition) -> bool:
            left, right = value(c.left), value(c.right)
            if c.plus is not None:
                extra = value(c.plus)
                if left is missing or extra is missing:
                    return False
                left = left + extra
            if left is missing or right is missing:
                return False
            if c.op == "==":
                return bool(left == right)
            if c.op == "<=":
                return bool(left <= right)
            raise AssertionError(c.op)

        return tuple(
            p.policy_id
            for p in self._pack.policies
            if p.action == tool and all(holds(c) for c in p.when)
        )


OWN: Final = Condition("resource.tenant", "==", "principal.tenant")


def limit(name: str, maximum: int) -> Condition:
    return Condition(f"context.{name}", "<=", maximum)


def per_session(name: str, maximum: int) -> Condition:
    return Condition(f"context.session_{name}", "<=", maximum, plus=f"context.{name}")


# --------------------------------------------------------------------------
# the deployment
# --------------------------------------------------------------------------


@dataclass
class PackWorld(World):
    pack: Pack | None = None
    children: dict[str, Mandate] = field(default_factory=dict)

    @property
    def spec(self) -> Pack:
        assert self.pack is not None
        return self.pack

    def fingerprint(self) -> str:
        digest = hashlib.sha256()
        for table in self.spec.tables:
            rows = self.fetch(f"SELECT * FROM {table.name} ORDER BY id")  # noqa: S608 - our names
            for row in rows:
                digest.update(json.dumps(row, default=str).encode())
        return digest.hexdigest()


def plan_tool(
    name: str,
    description: str,
    schema: Mapping[str, Any],
    effects: list[dict[str, Any]],
    requests: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A ``plan`` binding bound to the session's tenant, unsigned."""
    return {
        "v": "FIDU1",
        "type": "tool",
        "name": name,
        "description": description,
        "input": {
            "type": "object",
            "additionalProperties": False,
            "required": sorted(schema),
            "properties": dict(schema),
        },
        "kind": "plan",
        "tenant": "session",
        "effects": effects,
        "requests": requests or [],
        "price": "0.00000000",
    }


def effect(
    effect_id: str,
    kind: str,
    table: str,
    statement: str,
    parameters: Mapping[str, str],
    after: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "id": effect_id,
        "kind": kind,
        "table": table,
        "statement": statement,
        "parameters": dict(parameters),
        "stated_rows": 1,
        "after": list(after),
    }


def request(
    request_id: str, sink: str, operation: str, payload: Mapping[str, Any], after: Sequence[str]
) -> dict[str, Any]:
    return {
        "id": request_id,
        "sink": sink,
        "operation": operation,
        "payload": dict(payload),
        "compensation": None,
        "after": list(after),
    }


def mandate_json(world: PackWorld, *, envelope: str = "25.00000000") -> dict[str, Any]:
    """The pack's root mandate, version 1: the fintech mandate's budget,
    governance and recovery, and the pack's own data, outbound and windows."""
    pack = world.spec
    now = world.now()
    return {
        "v": "FIDU1",
        "type": "mandate",
        "mandate_id": str(uuid.uuid4()),
        "version": 1,
        "prev": None,
        "charter": {
            "charter_id": world.charter.charter_id,
            "version": 1,
            "digest": world.charter.digest,
        },
        "parent": None,
        "issuance": "quorum",
        "subject": {"scope": pack.scope, "funded_from": pack.funding, "label": pack.label},
        "term": {
            "not_before": fidu1.format_instant(now - timedelta(hours=1)),
            "not_after": fidu1.format_instant(now + timedelta(days=30)),
        },
        "roster": {"principals": sorted(pack.principals), "quorums": dict(pack.quorums)},
        "budget": {
            "envelope": envelope,
            "per_call": "0.50000000",
            "models": sorted(PRICING),
            "max_output_tokens": 4096,
        },
        "governance": {
            "max_calls": 600,
            "seconds": 60,
            "fleet_velocity": True,
            "cognitive": {
                "max_identical_repeats": 6,
                "similarity_threshold": "0.7",
                "max_similar_streak": 60,
                "result_similarity_threshold": "0.3",
                "max_cycle_period": 4,
                "min_cycle_repeats": 6,
                "min_novelty_ratio": "0.02",
                "exempt_tools": [],
            },
        },
        "data": pack.data(),
        "outbound": pack.outbound(),
        "windows": pack.windows(),
        "tools": {
            "statements": "bound",
            "bindings": sorted(
                ({"name": t.name, "digest": t.digest} for t in world.tools.values()),
                key=lambda pin: str(pin["name"]),
            ),
        },
        "recovery": {
            "reserve": "1.00000000",
            "step_estimate": "0.50000000",
            "max_steps": 1,
            "directives": [
                {
                    "id": "stop-retrying",
                    "sha256": hashlib.sha256(DIRECTIVE).hexdigest(),
                    "answers": ["thrashing"],
                }
            ],
            "revoke_on": [],
            "revocable": None,
            "token_ceiling": 2048,
            "guidance": True,
            "channel": "user",
            "funding": pack.funding,
        },
        "extension": {
            "max_grant": "5.00000000",
            "max_total": "10.00000000",
            "margin": "0.25",
            "ttl_seconds": 3600,
            "auto": None,
        },
        "escalation": {"max_open": 5, "ttl_seconds": 3600, "auto": []},
        "delegation": dict(pack.delegation) if pack.delegation is not None else None,
        "issued_at": fidu1.format_instant(now),
    }


def child_json(
    world: PackWorld, parent: Mandate, scope: str, label: str, **changes: Any
) -> dict[str, Any]:
    """A derived child of ``parent``, narrowed by ``changes`` (dotted paths)."""
    raw = copy.deepcopy(parent.body())
    raw.pop("v", None)
    raw.pop("type", None)
    assert parent.delegation is not None
    raw.update(
        {
            "mandate_id": str(uuid.uuid4()),
            "version": 1,
            "prev": None,
            "parent": parent.ref.to_json(),
            "issuance": "derived",
            "subject": {
                "scope": f"{parent.subject.scope}/{scope}",
                "funded_from": parent.subject.scope,
                "label": label,
            },
            "delegation": None,
            "windows": [],
            "issued_at": fidu1.format_instant(world.now()),
        }
    )
    raw["budget"] = {**raw["budget"], "envelope": format(parent.delegation.envelope, "f")}
    for path, value in changes.items():
        target = raw
        *parts, last = path.split(".")
        for part in parts:
            target = target[int(part)] if isinstance(target, list) else target[part]
        target[last] = value
    return {"v": "FIDU1", "type": "mandate", **raw}


def _write_config(world: PackWorld) -> None:
    """``fiduciary.toml`` and ``interlock.toml``, as the fintech demo writes them."""
    root, pack = world.root, world.spec
    tools = []
    for binding in world.tools.values():
        path = root / "tools" / f"{binding.name}.tool.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(binding.pretty())
        tools.append(str(path))
    (root / "stop-retrying.txt").write_bytes(DIRECTIVE)
    tables = "".join(
        f'[[tables]]\nname = "{t.name}"\ncolumns = {json.dumps(list(t.columns))}\n'
        + (f'tenant_column = "{t.tenant}"\n' if t.tenant else "")
        for t in pack.tables
    )
    sinks = "".join(
        f'[[sinks]]\nname = "{s.name}"\ncost_per_call = "{s.cost_per_call}"\n'
        f'idempotency = "{s.idempotency}"\nmax_payload_bytes = {s.max_payload_bytes}\n'
        "operations = [" + ", ".join(f'{{ name = "{op.name}" }}' for op in s.operations) + "]\n"
        for s in pack.sinks
    )
    (root / "interlock.toml").write_text(
        f'substrate = "sqlite"\ndatabase = "{world.back_office}"\n' + tables + sinks
    )
    service = root / "keys" / "service.key"
    (root / "fiduciary.toml").write_text(
        f'database = "{root / "fiduciary.db"}"\n'
        f'ledger = "{root / "ledger.db"}"\n'
        f'interlock = "{root / "interlock.toml"}"\n'
        f'[charter]\nfile = "{root / "charter.json"}"\ndigest = "{world.charter.digest}"\n'
        f'[service]\nkey = "{service}"\n'
        f"[binding]\ntools = {json.dumps(tools)}\n"
        f'directives = ["{root / "stop-retrying.txt"}"]\nchains = "{world.chains}"\n'
        f'[executor]\nkey = "{service}"\noutbox = "{world.back_office}"\n'
        f'log = "{world.operator_log}"\n'
        f'[runtime]\nrecords = "{root / "runtime.jsonl"}"\nkey = "{service}"\n'
        f'[receipts]\nlog = "{root / "receipts.jsonl"}"\n'
        f'key = "{root / "keys" / "receipts.key"}"\n'
    )


def build(root: Path, pack: Pack) -> PackWorld:
    """Stand ``pack``'s deployment up under ``root``, which must be empty or absent."""
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise FileExistsError(f"{root} is not empty: the demo writes a fresh deployment")
    names = (
        *pack.principals,
        "service",
        "developer",
        "ledger-checkpoints",
        "receipts",
        pack.operator,
        "relay-a",
        "relay-b",
    )
    signers: dict[str, Signer] = {name: _key(root, name) for name in names}
    closers: list[Callable[[], None]] = []

    back_office = root / "back_office.db"
    with closing(sqlite3.connect(back_office)) as conn:
        conn.executescript(pack.schema)
        pack.seed(conn)
        conn.commit()
    install_sqlite_outbox(back_office, pack.sinks, ())
    specs = [
        TableSpec(t.name, columns=list(t.columns), tenant_column=t.tenant) for t in pack.tables
    ]

    governor = BudgetManager.open_sqlite(str(root / "ledger.db"))
    closers.append(governor.close)
    ensure_anchor_scope(governor)
    ensure_anchor_scope(governor, OPERATOR_SCOPE)
    governor.open_root(pack.funding, "1000", memo=f"{pack.funding}: the {pack.industry} budget")
    genesis = governor.entry(1).entry_hash

    store = SqliteStore(root / "fiduciary.db")
    closers.append(store.close)
    log = Log(store, signers["service"], ledger=governor)
    charter = Charter.from_json(
        {
            "v": "FIDU1",
            "type": "charter",
            "charter_id": str(uuid.uuid4()),
            "version": 1,
            "prev": None,
            "deployment": {"ledger": genesis, "label": f"{pack.key} demo"},
            "principals": [
                {"name": n, "key": key_spec(signers[n].public_key())}
                for n in sorted(pack.principals)
            ],
            "quorums": {"charter": 3, "issue": 2, "revoke": 1},
            "break_glass": [str(signers[pack.operator].public_key().key_id)],
            "issued_at": fidu1.format_instant(datetime.now(UTC)),
        },
        signed=False,
    ).signed(signers[pack.principals[0]])
    (root / "charter.json").write_text(charter.pretty())
    authority = Authority(log, pinned=charter.digest)

    tools = {
        raw["name"]: ToolBinding.from_json(raw, signed=False).signed(signers["developer"])
        for raw in pack.tools()
    }
    deployment = Deployment(
        ledger=genesis,
        tables={t.name: frozenset(t.columns) for t in pack.tables},
        sinks=SinkRegistry(pack.sinks),
        tools={t.digest: t for t in tools.values()},
        directives={hashlib.sha256(DIRECTIVE).hexdigest(): DIRECTIVE.decode()},
    )
    receipts = ReceiptLog("fiduciary-receipts", signers["receipts"], path=root / "receipts.jsonl")
    closers.append(receipts.close)
    binder = Binder(
        authority,
        governor,
        deployment=deployment,
        substrate=lambda: SqliteSubstrate(str(back_office), tables=specs),
        chains=root / "chains",
        receipts=receipts,
    )
    closers.append(binder.close)
    service = signers["service"].public_key()
    runtime = RecordLog(
        signers["service"], path=root / "runtime.jsonl", keyring=Keyring({service.key_id: service})
    )
    closers.append(runtime.close)
    outbox_store = SqliteOutboxStore(str(back_office), writes=OPERATOR)
    closers.append(outbox_store.close)
    outbox = operations(outbox_store)
    fid = Fiduciary(
        authority,
        binder,
        governor,
        runtime_records=runtime,
        outbox=outbox,
        operator_log=root / "operators.jsonl",
        executor_signer=signers["service"],
        receipts=receipts,
        handlers={},
        pricing=PRICING,
        in_process="tool-calls-only",
    )
    closers.append(fid.close)
    world = PackWorld(
        root,
        signers,
        governor,
        store,
        log,
        authority,
        binder,
        receipts,
        runtime,
        charter,
        tools,
        deployment,
        fid,
        outbox_store,
        outbox,
        webhook_secret="",
        closers=closers,
        pack=pack,
    )
    try:
        authority.propose(charter)
        for principal in pack.principals:
            world.approve(principal, "charter", charter.digest)
        authority.adopt_charter(charter.digest)
        signer = signers[pack.principals[0]]
        mandate = Mandate.from_json(mandate_json(world), signed=False).signed(signer)
        authority.propose(mandate)
        for principal in pack.principals[:2]:
            world.approve(principal, "mandate", mandate.digest)
        binder.issue(mandate.digest)
        binder.bind(mandate.digest)
        world.mandate = mandate
        (root / "mandate.json").write_text(mandate.pretty())
        _write_config(world)
    except BaseException:
        world.close()
        raise
    return world


# --------------------------------------------------------------------------
# one run, and one call
# --------------------------------------------------------------------------


class Vendor:
    """A stand-in for every outside API a pack's sinks reach: one effect per
    idempotency key, as a vendor that honors keys keeps one, and every request."""

    API_KEY: Final = "sk_demo_vendor_" + uuid.uuid4().hex
    """The relay's credential: the zero-data scan looks for it."""

    def __init__(self) -> None:
        self.effects: dict[str, dict[str, Any]] = {}
        self.requests: list[dict[str, Any]] = []

    def send(self, delivery: Delivery) -> DeliveryResult:
        key = delivery.idempotency_key
        self.requests.append(
            {"sink": delivery.sink, "operation": delivery.operation, "idempotency_key": key}
        )
        if key not in self.effects:
            self.effects[key] = {
                "id": "ref_" + hashlib.sha256(key.encode()).hexdigest()[:20],
                "sink": delivery.sink,
                "payload": json.loads(delivery.payload),
            }
        return DeliveryResult(DELIVERED, status_code=200, remote_ref=self.effects[key]["id"])


@dataclass
class PackRun:
    """One pack's run: the world, the gateway, and what the acts recorded."""

    world: PackWorld
    gateway: PackGateway
    say: Callable[[str], None] = lambda _: None
    vendor: Vendor = field(default_factory=Vendor)
    acts: list[dict[str, Any]] = field(default_factory=list)
    results: list[StageResult] = field(default_factory=list)
    bundles: dict[str, bytes] = field(default_factory=dict)
    reports: dict[str, BundleReport] = field(default_factory=dict)

    @property
    def pack(self) -> Pack:
        return self.world.spec

    def observe(self, mandate: Mandate | None = None) -> None:
        found = mandate or self.world.mandate
        assert found is not None
        self.world.binder.engine(found.mandate_id).observe(self.results.append)

    def act(self, n: int, key: str, lede: str, **body: Any) -> dict[str, Any]:
        entry = {"n": n, "key": key, "title": self.pack.titles[key], "lede": lede, **body}
        self.acts.append(entry)
        return entry


def measured(result: StageResult) -> dict[str, Any]:
    """The fintech demo's view of one adjudicated plan, without its order
    totals, and with the name of every checker that ran."""
    found = fintech_measured(result)
    found.pop("orders_total", None)
    verdict = result.verdict
    found["checkers_run"] = sorted(verdict.checkers_run) if verdict is not None else []
    distinct = {(d.table, d.primary_key) for d in (result.diff.deltas if result.diff else ())}
    found["tables"] = dict(sorted(Counter(table for table, _ in distinct).items()))
    return found


@dataclass
class Called:
    tool: str
    arguments: dict[str, Any]
    gateway: Decision
    result: CallResult | None
    measured: dict[str, Any] | None
    unchanged: bool | None

    @property
    def committed(self) -> bool:
        return self.result is not None and self.result.committed

    def to_json(self) -> dict[str, Any]:
        found: dict[str, Any] = {
            "tool": self.tool,
            "arguments": self.arguments,
            "gateway": self.gateway.to_json(),
            "outcome": self.result.outcome if self.result is not None else "not sent",
            "feedback": self.result.render() if self.result is not None else "",
        }
        if self.measured is not None:
            found["measured"] = self.measured
            found["unchanged"] = self.unchanged
        return found


def call(run: PackRun, session: Session, tool: str, arguments: Mapping[str, Any]) -> Called:
    """One call behind both controls: the gateway first (a call it denies is
    never sent), then the session, whose plan is measured."""
    run.gateway.opened(session.session_id, session.tenant)
    decision = run.gateway.decide(session.session_id, tool, arguments)
    if not decision.permitted:
        return Called(tool, dict(arguments), decision, None, None, None)
    seen = len(run.results)
    before = run.world.fingerprint()
    result = session.call(tool, arguments)
    observed = run.results[seen:]
    found = measured(observed[-1]) if observed else None
    unchanged = None
    if found is not None and not found["committed"]:
        unchanged = run.world.fingerprint() == before
    return Called(tool, dict(arguments), decision, result, found, unchanged)


def one(
    run: PackRun,
    tool: str,
    arguments: Mapping[str, Any],
    *,
    tenant: str | None = None,
    trajectory: str,
    scope: str | None = None,
) -> Called:
    """One call in a session of its own."""
    with run.world.fid.session(
        scope or run.pack.scope, tenant=tenant or run.pack.tenants[0], trajectory=trajectory
    ) as session:
        return call(run, session, tool, arguments)


def blocked(found: Called) -> list[str]:
    """The blocking checkers that refused a call's plan."""
    if found.measured is None:
        return []
    return sorted({f["check"] for f in found.measured["findings"] if f["severity"] == "blocking"})


def say_call(run: PackRun, found: Called, what: str) -> None:
    m = found.measured or {}
    stack = (
        "committed"
        if found.committed
        else f"rolled back ({', '.join(blocked(found)) or (found.result and found.result.outcome)})"
    )
    rows = f"{m.get('rows', 0)} rows" if m else "nothing measured"
    run.say(f"  {what}: gateway {found.gateway.decision}; stack {rows}, {stack}")


# --------------------------------------------------------------------------
# the relay
# --------------------------------------------------------------------------


def _relay(world: PackWorld, name: str, vendor: Vendor) -> tuple[Relay, SqliteOutboxStore]:
    store = SqliteOutboxStore(str(world.back_office), writes=RELAY)
    relay = Relay(
        store,
        adapters={s.name: vendor for s in world.spec.sinks},
        breaker=LedgerBreaker(world.governor),
        signer=world.signers[name],
        relay_id=name,
        lease=timedelta(milliseconds=800),
        timeout=timedelta(milliseconds=300),
    )
    return relay, store


def drain(world: PackWorld, vendor: Vendor) -> None:
    relay, store = _relay(world, "relay-b", vendor)
    try:
        for _ in range(200):
            if not relay.run_once(limit=20):
                break
    finally:
        store.close()


# --------------------------------------------------------------------------
# the acts every pack plays alike
# --------------------------------------------------------------------------


def hold(
    run: PackRun,
    tool: str,
    arguments: Mapping[str, Any],
    why: str,
    *,
    trajectory: str,
    tenant: str | None = None,
) -> tuple[str, Called]:
    """A call committed, the scope paused before the relay reaches its
    request, the message held. Returns the message and the call."""
    world = run.world
    drain(world, run.vendor)  # what earlier acts committed is delivered first
    found = one(run, tool, arguments, trajectory=trajectory, tenant=tenant)
    assert found.committed, found.to_json()
    world.governor.trip(run.pack.scope, why)
    drain(world, run.vendor)
    held = [m for m, s in world.outbox_states().items() if s == "held"]
    assert len(held) == 1, world.outbox_states()
    return held[0], found


def act_quorum(
    run: PackRun,
    n: int,
    lede: str,
    tool: str,
    arguments: Mapping[str, Any],
    why: str,
    *,
    forged: str,
    tenant: str | None = None,
) -> None:
    """A request held by a pause, a ``held`` ticket, and its release on 2 of
    3 principals: the first counts once however often it signs, and an
    approval in the second's name under another key is refused."""
    world = run.world
    assert world.mandate is not None
    first, second, third = run.pack.principals
    message, found = hold(run, tool, arguments, why, trajectory="held-request", tenant=tenant)
    escalation = world.fid.escalation
    ticket, _ = escalation.raise_held(world.mandate.mandate_id, world.outbox)
    assert ticket is not None
    release = next(o for o in ticket.options if o.resolution.action == "release")
    votes: list[dict[str, Any]] = []

    def vote(principal: str, note: str, **kw: Any) -> None:
        approval = world.approve(
            principal, "ticket", ticket.digest, resolution=release.digest, **kw
        )
        records = escalation.process_intake()
        kinds = [r.kind for r in records]
        warrant = escalation.warrant_for_ticket(ticket.ticket_id)
        votes.append(
            {
                "principal": principal,
                "note": note,
                "approval": approval.digest,
                "recorded": kinds,
                "counted": "approval.recorded" in kinds,
                "warrant": warrant.digest if warrant is not None else None,
            }
        )

    vote(first, "approves the release")
    vote(first, "approves again; a principal counts once")
    vote(
        second,
        f"approval in {second}'s name, signed with a key that is not in the charter",
        signer=world.signers[forged],
    )
    before_warrant = escalation.warrant_for_ticket(ticket.ticket_id) is not None
    vote(second, "approves the release with their own key; quorum reached")
    warrant = escalation.warrant_for_ticket(ticket.ticket_id)
    assert warrant is not None, votes
    records = world.fid.executor.execute(warrant)
    state_after = world.outbox_states()[message]
    drain(world, run.vendor)
    counted = len({v["principal"] for v in votes if v["counted"]})
    run.say(
        f"  ticket {ticket.ticket_id[:8]} (held): {len(votes)} approvals submitted, {counted} "
        f"principals counted, quorum {release.quorum} of 3; warrant executed; message "
        f"{state_after} -> {world.outbox_states()[message]}"
    )
    run.act(
        n,
        "quorum",
        lede,
        call=found.to_json(),
        message=message,
        third=third,
        ticket={
            "id": ticket.ticket_id,
            "digest": ticket.digest,
            "class": ticket.ticket_class,
            "observed": ticket.observed.to_json(),
            "options": [
                {"action": o.resolution.action, "digest": o.digest, "quorum": o.quorum}
                for o in ticket.options
            ],
        },
        votes=votes,
        warrant_before_quorum=before_warrant,
        warrant={
            "id": warrant.warrant_id,
            "digest": warrant.digest,
            "resolution": warrant.resolution_digest,
            "basis": list(warrant.basis.approvals),
            "formed_at": warrant.body()["formed_at"],
            "not_after": warrant.body()["not_after"],
        },
        executed=[r.kind for r in records],
        expected_heads=_upstream.EXPECTED_HEADS,
        delivered=world.outbox_states()[message],
    )


def evidence(world: PackWorld) -> Evidence:
    pack = world.spec
    service = world.signers["service"].public_key()
    names = ("service", pack.operator)
    keyring = Keyring({world.key_id(n): world.signers[n].public_key() for n in names})
    operators = RecordLog.load(world.operator_log, keyring) if world.operator_log.exists() else ()
    return Evidence(
        ledger=world.governor,
        ledger_signer=world.signers["ledger-checkpoints"],
        receipts=world.receipts,
        receipt_keys=[verifier_of(world.signers["receipts"])],
        operator_records=operators,
        operator_keys={
            "fiduciary": key_spec(service),
            pack.operator: key_spec(world.signers[pack.operator].public_key()),
        },
        runtime_records=world.runtime.records(),
        runtime_key=service,
        chains=world.chains,
        outbox=world.outbox,
        service=world.signers["service"],
    )


def export(world: PackWorld, shift: str, mandate: Mandate | None = None) -> bytes:
    """A lineage's bundle, exported once ``shift``'s session has opened and
    closed, so everything before that record is inside the bundle's range."""
    found = mandate or world.mandate
    assert found is not None
    tenant = (found.data.tenants or world.spec.tenants)[0]
    with world.fid.session(found.mandate_id, tenant=tenant, trajectory=shift):
        pass
    world.governor.refresh()
    return export_bundle(
        world.store, found.mandate_id, deployment=world.deployment, evidence=evidence(world)
    )


def pinned(world: PackWorld) -> dict[str, Any]:
    """What the auditor pins from outside the bundle: the deployment's public keys."""
    service = key_spec(world.signers["service"].public_key())
    return {
        "service": service,
        "ledger": key_spec(world.signers["ledger-checkpoints"].public_key()),
        "receipts": [key_spec(world.signers["receipts"].public_key())],
        "runtime": service,
        "operators": {
            "fiduciary": service,
            world.spec.operator: key_spec(world.signers[world.spec.operator].public_key()),
        },
    }


def act_break_glass(
    run: PackRun,
    n: int,
    lede: str,
    tool: str,
    arguments: Mapping[str, Any],
    why: str,
    reason: str,
    *,
    tenant: str | None = None,
) -> None:
    """A second request held, and released by hand with the declared
    break-glass key, with the breaker reset: no ticket, no warrant."""
    world = run.world
    pack = run.pack
    run.bundles["before"] = export(world, "handover")
    message, found = hold(run, tool, arguments, why, trajectory="second-hold", tenant=tenant)
    names = ("service", pack.operator)
    keyring = Keyring({world.key_id(name): world.signers[name].public_key() for name in names})
    log = OperatorLog(
        world.operator_log, world.signers[pack.operator], keyring, ledger=world.governor
    )
    try:
        Operator(log, world.outbox).release([uuid.UUID(message)], reason=reason)
    finally:
        log.close()
    world.governor.reset(pack.scope)
    drain(world, run.vendor)
    run.say(
        f"  {pack.operator} key released message {message[:8]} and reset the breaker without "
        f"a warrant; message {world.outbox_states()[message]}"
    )
    run.act(
        n,
        "break-glass",
        lede,
        call=found.to_json(),
        message=message,
        operator=world.key_id(pack.operator),
        holder=pack.operator,
        delivered=world.outbox_states()[message],
    )


def verdict(
    run: PackRun, name: str, data: bytes, planted: Mapping[str, Sequence[str]]
) -> dict[str, Any]:
    """``data`` verified offline with every socket refused, and scanned."""
    keys = TrustedKeys(pinned(run.world))
    with no_network() as attempts:
        report = verify_bundle(data, charter=run.world.charter.digest, keys=keys)
    run.reports[name] = report
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "exit_code": report.exit_code,
        "passed": report.passed,
        "findings": [f.render() for f in report.findings],
        "sockets": len(attempts),
        "scan": scan(data, planted),
    }


def act_audit(run: PackRun, n: int, lede: str, **extra: Any) -> None:
    """Both bundles, verified with sockets refused, and scanned for every
    row, payload and secret the deployment holds."""
    world = run.world
    run.bundles["after"] = export(world, "morning-shift")
    planted = run.pack.canaries(run)
    verdicts = {}
    for name in ("before", "after"):
        verdicts[name] = verdict(run, name, run.bundles[name], planted)
        v = verdicts[name]
        result = "PASS" if v["passed"] else f"FAIL (exit {v['exit_code']})"
        run.say(
            f"  {name}.fidb: {result}, {len(v['findings'])} findings, {v['sockets']} network "
            f"calls, {sum(v['scan']['found'].values())} planted values found"
        )
    world.governor.verify_conservation()
    labels = scan(run.bundles["after"], {"labels": list(run.pack.tenants)})
    run.act(
        n,
        "audit",
        lede,
        verdicts=verdicts,
        conservation="verified",
        labels={
            "partition": run.pack.partition,
            "known": len(run.pack.tenants),
            "named": sum(
                1
                for t in run.pack.tenants
                if any(h.endswith(f": {t[:24]}") for h in labels["hits"]["labels"])
            ),
            "members": len({h.split(":")[0] for h in labels["hits"]["labels"]}),
        },
        **extra,
    )


def secrets(run: PackRun) -> list[str]:
    """Every private key in the deployment, and the relay's credential."""
    keys_dir = run.world.root / "keys"
    return [p.read_text().strip() for p in sorted(keys_dir.glob("*.key"))] + [Vendor.API_KEY]


def payloads(run: PackRun) -> list[str]:
    """Every value the stand-in vendor received or answered with, as text:
    what an agent's arguments put in a payload, not the constants a signed
    tool binding names (the bundle holds the bindings, as it must)."""
    bound = json.dumps([t.body() for t in run.world.tools.values()])
    labels = set(run.pack.tenants)
    found: set[str] = set()
    for item in run.vendor.effects.values():
        found.add(item["id"])
        for value in item["payload"].values():
            text = str(value)
            if len(text) >= 6 and text not in bound and text not in labels:
                found.add(text)
    return sorted(found)

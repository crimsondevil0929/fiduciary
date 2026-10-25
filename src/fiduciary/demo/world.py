"""The demo's deployment: every part ``fiduciary demo`` stands up, in one directory.

One SQLite back office (``back_office.db``): 200 orders of four tenants, each
order 1000.00, in settlement batches of 50 that span all four tenants. Two
triggers came with the schema, as a legacy system's do:

- ``orders_settlement``: an order marked ``disputed`` puts every other open
  order of its settlement batch ``on_hold``;
- ``orders_reprice``: ``discount_pct`` is read as the share the customer
  *pays*, so setting it reprices the order to that share of its total.

Neither is in any tool's description, and neither is visible in a tool call's
arguments. Interlock is installed in the same file (the outbox, the inbox and
the sink registry), and the stage measures what the triggers do as it measures
what the statements do.

Beside it: an AgentGov ledger (``ledger.db``), fiduciary's store
(``fiduciary.db``), the escrow chains, the receipt, runtime and operator logs,
and every key, written as ``fiduciary keygen`` writes one (``keys/``). The keys
are made fresh for each run. A charter of three principals, adopted on its
quorum; a mandate for ``support-agent``, issued on two principals' approvals
and bound; ``fiduciary.toml`` and ``interlock.toml``, so the ordinary commands
open the same deployment afterwards.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from agentgov import BudgetManager
from agentgov.interceptor import ModelPricing
from agentgov.receipts import ReceiptLog
from agentgov.receipts.signing import Signer
from interlock.deliveries import OutboxOperations, operations
from interlock.inbox import InboundSource
from interlock.operators import OPERATOR_LOG as OPERATOR_SCOPE
from interlock.outbound import OperationSpec, SinkRegistry, SinkSpec
from interlock.records import Keyring, RecordLog
from interlock.sqlite_outbox import OPERATOR, SqliteOutboxStore, install_sqlite_outbox
from interlock.substrate import SqliteSubstrate, TableSpec

from fiduciary import fidu1
from fiduciary.authority import Authority
from fiduciary.binding import Binder
from fiduciary.bridge import Fiduciary
from fiduciary.bundle.export import spec
from fiduciary.compiler import Deployment
from fiduciary.documents import Approval, Charter, Mandate, ToolBinding
from fiduciary.keys import generate_key
from fiduciary.log import Log, ensure_anchor_scope
from fiduciary.sqlite import SqliteStore

__all__ = ["PRICING", "SCOPE", "TENANTS", "World", "build"]

SCOPE: Final = "support-agent"
FUNDING: Final = "support"
TENANTS: Final = ("acme", "globex", "initech", "umbrella")
ORDERS: Final = 200
BATCH: Final = 50
PRINCIPALS: Final = ("cfo", "head-ops", "security")
STRIPE_SECRET_ENV: Final = "DEMO_STRIPE_WEBHOOK_SECRET"  # noqa: S105 - a variable's name
DIRECTIVE: Final = b"Stop retrying."

PRICING: Final[Mapping[str, ModelPricing]] = {
    # Anthropic's published rates per million tokens (input, output, cache
    # read, cache write), as scripts/live_acceptance.py meters them.
    "claude-opus-5-5": ModelPricing(
        "claude-opus-5-5", Decimal("4"), Decimal("20"), Decimal("0.20"), Decimal("5")
    ),
    "claude-sonnet-5-5": ModelPricing(
        "claude-sonnet-5-5", Decimal("2"), Decimal("10"), Decimal("0.10"), Decimal("2.50")
    ),
}

_SCHEMA: Final = """
CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    tenant TEXT NOT NULL,
    customer TEXT NOT NULL,
    total NUMERIC NOT NULL,
    status TEXT NOT NULL,
    batch TEXT NOT NULL,
    discount_pct INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE refunds (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL,
    tenant TEXT NOT NULL,
    amount TEXT NOT NULL
);
CREATE TRIGGER orders_settlement AFTER UPDATE OF status ON orders
WHEN NEW.status = 'disputed' AND OLD.status <> 'disputed'
BEGIN
    UPDATE orders SET status = 'on_hold'
    WHERE batch = NEW.batch AND id <> NEW.id AND status = 'open';
END;
CREATE TRIGGER orders_reprice AFTER UPDATE OF discount_pct ON orders
BEGIN
    UPDATE orders SET total = ROUND(total * NEW.discount_pct / 100.0, 2) WHERE id = NEW.id;
END;
"""

TABLES: Final[dict[str, tuple[str, ...]]] = {
    "orders": ("id", "tenant", "customer", "total", "status", "batch", "discount_pct"),
    "refunds": ("id", "order_id", "tenant", "amount"),
}

SINKS: Final = (
    SinkSpec(
        "stripe",
        (OperationSpec("refunds.create"),),
        cost_per_call=Decimal("0.01"),
        idempotency="header",
        max_payload_bytes=65536,
    ),
)
SOURCES: Final = (InboundSource("stripe", "stripe", secret_env=STRIPE_SECRET_ENV),)


def tenant_of(order: int) -> str:
    return TENANTS[(order - 1) % len(TENANTS)]


def customer_of(order: int) -> str:
    """A customer's name, as the back office holds it: the demo scans the
    audit bundle for every one of them (F9-1)."""
    return f"{tenant_of(order)}-customer-{order:03d}"


def _orders() -> list[tuple[Any, ...]]:
    return [
        (n, tenant_of(n), customer_of(n), 1000, "open", f"B-{(n - 1) // BATCH + 1:02d}", 0)
        for n in range(1, ORDERS + 1)
    ]


# --------------------------------------------------------------------------
# the tool bindings: what a principal reviews, and an agent may call
# --------------------------------------------------------------------------


def _schema(required: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(required),
        "properties": required,
    }


_ORDER: Final = {"type": "integer", "minimum": 1, "maximum": ORDERS}


def tool_documents() -> list[dict[str, Any]]:
    """The four bindings, unsigned."""
    by_session = {"order_id": "$.order_id", "tenant": "$session.tenant"}

    def plan(
        name: str,
        description: str,
        schema: dict[str, Any],
        effects: list[Any],
        requests: list[Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "v": "FIDU1",
            "type": "tool",
            "name": name,
            "description": description,
            "input": _schema(schema),
            "kind": "plan",
            "tenant": "session",
            "effects": effects,
            "requests": requests or [],
            "price": "0.00000000",
        }

    def update(effect_id: str, statement: str, parameters: dict[str, str]) -> dict[str, Any]:
        return {
            "id": effect_id,
            "kind": "update",
            "table": "orders",
            "statement": statement,
            "parameters": parameters,
            "stated_rows": 1,
            "after": [],
        }

    refund = plan(
        "refund_order",
        "Refund part or all of one order to its original payment.",
        {
            "order_id": _ORDER,
            "amount": {"type": "string", "pattern": "^[0-9]{1,4}\\.[0-9]{2}$"},
            "payment_intent": {"type": "string", "pattern": "^pi_[A-Za-z0-9]{8,64}$"},
        },
        [
            update(
                "debit",
                "UPDATE orders SET total = total - CAST(:amount AS NUMERIC) "
                "WHERE id = :order_id AND tenant = :tenant",
                {"amount": "$.amount", **by_session},
            ),
            {
                "id": "record",
                "kind": "insert",
                "table": "refunds",
                "statement": "INSERT INTO refunds (order_id, tenant, amount) "
                "VALUES (:order_id, :tenant, :amount)",
                "parameters": {"amount": "$.amount", **by_session},
                "stated_rows": 1,
                "after": ["debit"],
            },
        ],
        [
            {
                "id": "stripe-refund",
                "sink": "stripe",
                "operation": "refunds.create",
                "payload": {
                    "payment_intent": "$.payment_intent",
                    "amount": "$.amount",
                    "currency": "usd",
                },
                "compensation": None,
                "after": ["record"],
            }
        ],
    )
    discount = plan(
        "apply_discount",
        "Apply a loyalty discount, in whole percent, to one open order.",
        {"order_id": _ORDER, "percent": {"type": "integer", "minimum": 1, "maximum": 15}},
        [
            update(
                "discount",
                "UPDATE orders SET discount_pct = :percent "
                "WHERE id = :order_id AND tenant = :tenant",
                {"percent": "$.percent", **by_session},
            )
        ],
    )
    dispute = plan(
        "flag_dispute",
        "Mark one order as disputed, after the customer's bank opens a dispute.",
        {"order_id": _ORDER},
        [
            update(
                "flag",
                "UPDATE orders SET status = 'disputed' WHERE id = :order_id AND tenant = :tenant",
                by_session,
            )
        ],
    )
    lookup = {
        "v": "FIDU1",
        "type": "tool",
        "name": "lookup_order",
        "description": "Read one order: its status and total.",
        "input": _schema({"order_id": _ORDER}),
        "kind": "passthrough",
        "tenant": "none",
        "effects": [],
        "requests": [],
        "price": "0.00100000",
    }
    return [refund, discount, dispute, lookup]


# --------------------------------------------------------------------------
# the world
# --------------------------------------------------------------------------


@dataclass
class World:
    """The running deployment, and the keys of everyone who acts in it."""

    root: Path
    signers: dict[str, Signer]
    governor: BudgetManager
    store: SqliteStore
    log: Log
    authority: Authority
    binder: Binder
    receipts: ReceiptLog
    runtime: RecordLog
    charter: Charter
    tools: dict[str, ToolBinding]
    deployment: Deployment
    fid: Fiduciary
    outbox_store: SqliteOutboxStore
    outbox: OutboxOperations
    webhook_secret: str
    mandate: Mandate | None = None
    closers: list[Callable[[], None]] = field(default_factory=list)

    @property
    def back_office(self) -> Path:
        return self.root / "back_office.db"

    @property
    def operator_log(self) -> Path:
        return self.root / "operators.jsonl"

    @property
    def chains(self) -> Path:
        return self.root / "chains"

    def now(self) -> datetime:
        return datetime.now(UTC)

    def key_id(self, name: str) -> str:
        return str(self.signers[name].public_key().key_id)

    # -- the back office, read directly ----------------------------------------------

    def fetch(self, sql: str, parameters: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        with closing(sqlite3.connect(self.back_office)) as conn:
            return [tuple(row) for row in conn.execute(sql, parameters)]

    def fingerprint(self) -> str:
        """The SHA-256 of every application row, in order: equal before and
        after a refused plan when nothing moved."""
        digest = hashlib.sha256()
        for table in TABLES:
            for row in self.fetch(f"SELECT * FROM {table} ORDER BY id"):  # noqa: S608 - our names
                digest.update(json.dumps(row, default=str).encode())
        return digest.hexdigest()

    def outbox_states(self) -> dict[str, str]:
        rows = self.fetch("SELECT message_id, state FROM _interlock_outbox_state")
        return {str(m): str(s) for m, s in rows}

    # -- documents and approvals -----------------------------------------------------

    def approve(
        self,
        principal: str,
        subject: str,
        digest: str,
        *,
        resolution: str | None = None,
        signer: Signer | None = None,
        decision: str = "approve",
    ) -> Approval:
        """A principal's approval, signed on their own machine, and put into
        the store's intake (``fiduciary approve --submit``)."""
        now = self.now()
        approval = Approval.from_json(
            {
                "v": "FIDU1",
                "type": "approval",
                "approval_id": str(uuid.uuid4()),
                "charter": {"charter_id": self.charter.charter_id, "version": 1},
                "principal": principal,
                "subject": {"type": subject, "digest": digest},
                "resolution": resolution,
                "decision": decision,
                "issued_at": fidu1.format_instant(now),
                "not_after": fidu1.format_instant(now + timedelta(hours=1)),
            },
            signed=False,
        ).signed(signer or self.signers[principal])
        self.store.submit(approval.canonical())
        return approval

    def close(self) -> None:
        for close in reversed(self.closers):
            close()
        self.closers.clear()


def _key(root: Path, name: str) -> Signer:
    keys = root / "keys"
    keys.mkdir(parents=True, exist_ok=True)
    return generate_key(keys / f"{name}.key")


def mandate_json(world: World, *, envelope: str = "25.00000000") -> dict[str, Any]:
    """``support-agent``'s mandate, version 1."""
    now = world.now()
    quorums = {
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
        "subject": {"scope": SCOPE, "funded_from": FUNDING, "label": "Tier-1 support"},
        "term": {
            "not_before": fidu1.format_instant(now - timedelta(hours=1)),
            "not_after": fidu1.format_instant(now + timedelta(days=30)),
        },
        "roster": {"principals": list(PRINCIPALS), "quorums": quorums},
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
        "data": {
            "tables": {"orders": ["update"], "refunds": ["insert"]},
            "tenants": "*",
            "declared_tenants": True,
            "max_rows": 20,
            "max_tenants": 1,
            "drawdown": [
                {"table": "orders", "column": "total", "per": "tenant", "max_drop": "0.3"}
            ],
            "stated_footprint": {"tolerance": "2", "blocking": False},
            "agreements": [
                {
                    "sink": "stripe",
                    "operation": "refunds.create",
                    "field": "amount",
                    "table": "refunds",
                    "column": "amount",
                    "measure": "inserted",
                    "key": None,
                }
            ],
            "facts": [],
        },
        "outbound": {
            "sinks": {
                "stripe": {
                    "operations": ["refunds.create"],
                    "max_attempts": 10,
                    "not_after_seconds": 900,
                    "max_payload_bytes": 16384,
                }
            },
            "max_per_plan": 2,
            "per_sink": {"stripe": 2},
            "caps": [
                {
                    "sink": "stripe",
                    "operation": "refunds.create",
                    "field": "amount",
                    "maximum": {"usd": "200"},
                    "per": "request",
                    "currency_field": "currency",
                }
            ],
            "recipients": [],
            "tenants": {"max_tenants": 1, "require_tenant": True, "fields": {}},
        },
        "windows": [
            {
                "name": "refund_dollars",
                "measure": {
                    "kind": "request_sum",
                    "sink": "stripe",
                    "operation": "refunds.create",
                    "field": "amount",
                },
                "span_seconds": 86400,
                "limit": "500",
                "per": "lineage",
            }
        ],
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
            "funding": FUNDING,
        },
        "extension": {
            "max_grant": "5.00000000",
            "max_total": "10.00000000",
            "margin": "0.25",
            "ttl_seconds": 3600,
            "auto": None,
        },
        "escalation": {"max_open": 5, "ttl_seconds": 3600, "auto": []},
        "delegation": {"max_depth": 1, "max_children": 3, "envelope": "6.00000000"},
        "issued_at": fidu1.format_instant(now),
    }


def child_json(
    world: World, parent: Mandate, scope: str, label: str, **changes: Any
) -> dict[str, Any]:
    """A derived child of ``parent``: the parent's bounds, narrowed by ``changes``."""
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
            "subject": {"scope": f"{SCOPE}/{scope}", "funded_from": SCOPE, "label": label},
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


def _write_config(world: World) -> None:
    """``fiduciary.toml`` and ``interlock.toml``: the ordinary commands open
    this deployment (``fiduciary --config <dir>/fiduciary.toml verify``)."""
    root = world.root
    tools = []
    for binding in world.tools.values():
        path = root / "tools" / f"{binding.name}.tool.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(binding.pretty())
        tools.append(str(path))
    (root / "stop-retrying.txt").write_bytes(DIRECTIVE)
    tables = "".join(
        f'[[tables]]\nname = "{name}"\ncolumns = {json.dumps(list(columns))}\n'
        'tenant_column = "tenant"\n'
        for name, columns in TABLES.items()
    )
    sinks = (
        '[[sinks]]\nname = "stripe"\ncost_per_call = "0.01"\nidempotency = "header"\n'
        'max_payload_bytes = 65536\noperations = [{ name = "refunds.create" }]\n'
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


def lookup(world_path: Path) -> Callable[[Mapping[str, Any]], Any]:
    """``lookup_order``'s implementation: one order's status and total."""

    def handler(arguments: Mapping[str, Any]) -> dict[str, Any]:
        with closing(sqlite3.connect(world_path)) as conn:
            row = conn.execute(
                "SELECT status, total FROM orders WHERE id = ?", (int(arguments["order_id"]),)
            ).fetchone()
        if row is None:
            return {"order_id": arguments["order_id"], "found": False}
        return {"order_id": arguments["order_id"], "status": row[0], "total": f"{row[1]:.2f}"}

    return handler


def build(root: Path) -> World:
    """Stand the deployment up under ``root``, which must be empty or absent."""
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise FileExistsError(f"{root} is not empty: the demo writes a fresh deployment")
    names = (
        *PRINCIPALS,
        "service",
        "developer",
        "ledger-checkpoints",
        "receipts",
        "night-shift",
        "relay-a",
        "relay-b",
        "inbox",
    )
    signers = {name: _key(root, name) for name in names}
    closers: list[Callable[[], None]] = []

    back_office = root / "back_office.db"
    with closing(sqlite3.connect(back_office)) as conn:
        conn.executescript(_SCHEMA)
        conn.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?)", _orders())
        conn.commit()
    install_sqlite_outbox(back_office, SINKS, SOURCES)
    specs = [TableSpec(n, columns=list(c), tenant_column="tenant") for n, c in TABLES.items()]

    governor = BudgetManager.open_sqlite(str(root / "ledger.db"))
    closers.append(governor.close)
    ensure_anchor_scope(governor)
    ensure_anchor_scope(governor, OPERATOR_SCOPE)
    governor.open_root(FUNDING, "1000", memo="treasury: the support budget")
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
            "deployment": {"ledger": genesis, "label": "demo"},
            "principals": [{"name": n, "key": spec(signers[n].public_key())} for n in PRINCIPALS],
            "quorums": {"charter": 3, "issue": 2, "revoke": 1},
            "break_glass": [str(signers["night-shift"].public_key().key_id)],
            "issued_at": fidu1.format_instant(datetime.now(UTC)),
        },
        signed=False,
    ).signed(signers["cfo"])
    (root / "charter.json").write_text(charter.pretty())
    authority = Authority(log, pinned=charter.digest)

    tools = {
        raw["name"]: ToolBinding.from_json(raw, signed=False).signed(signers["developer"])
        for raw in tool_documents()
    }
    deployment = Deployment(
        ledger=genesis,
        tables={name: frozenset(columns) for name, columns in TABLES.items()},
        sinks=SinkRegistry(SINKS),
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
        signers["service"],
        path=root / "runtime.jsonl",
        keyring=Keyring({service.key_id: service}),
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
        handlers={"lookup_order": lookup(back_office)},
        pricing=PRICING,
        in_process="tool-calls-only",
    )
    closers.append(fid.close)
    world = World(
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
        webhook_secret="whsec_" + uuid.uuid4().hex,
        closers=closers,
    )
    try:
        authority.propose(charter)
        for principal in PRINCIPALS:
            world.approve(principal, "charter", charter.digest)
        authority.adopt_charter(charter.digest)
        mandate = Mandate.from_json(mandate_json(world), signed=False).signed(signers["cfo"])
        authority.propose(mandate)
        for principal in ("cfo", "head-ops"):
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

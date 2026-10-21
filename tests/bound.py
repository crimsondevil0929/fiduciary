"""A deployment to bind mandates to, on SQLite or PostgreSQL.

A :class:`World` holds an AgentGov ledger (SQLite), a charter that names its
first entry, adopted; fiduciary's store; and Interlock's back office (the
tables ``orders``, ``refunds`` and ``items``, with the outbox installed and
the sinks registered), which every engine the binder builds stages on. Its
tool binding's statements run unchanged on both databases.
"""

from __future__ import annotations

import copy
import re
import sqlite3
import uuid
from collections.abc import Callable, Generator
from contextlib import closing
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from agentgov import BudgetManager
from agentgov.receipts import ReceiptLog
from agentgov.receipts.signing import Ed25519Signer
from interlock import PlanBuilder
from interlock.engine import StageResult
from interlock.types import EffectId, EffectPlan

from fiduciary.authority import Authority
from fiduciary.binding import Binder
from fiduciary.documents import Charter, Mandate, ToolBinding
from fiduciary.log import Log, ensure_anchor_scope
from fiduciary.store import Store
from tests import deploy
from tests import examples as e
from tests.conftest import PG_DSN, Clock, approve

SCOPE = "support-agent"
DEBIT = (
    "UPDATE orders SET total = total - CAST(:amount AS NUMERIC) "
    "WHERE id = :order_id AND tenant = :tenant"
)
RECORD = "INSERT INTO refunds (order_id, tenant, amount) VALUES (:order_id, :tenant, :amount)"
"""``refunds.amount`` is text: the exact decimal the request carries, which
``CrossEffectAgreement`` compares (SQLite hands a NUMERIC back as a float)."""
ORDERS = 40
"""Seeded orders: id n is tenant ``acme`` for odd n, ``beta`` for even, each totalling 1000."""


def dialect(backend: str, statement: str) -> str:
    """``statement`` with its placeholders as ``backend``'s substrate binds them:
    ``:name`` on SQLite, ``%(name)s`` on PostgreSQL."""
    if backend == "sqlite":
        return statement
    return re.sub(r"(?<![:\w]):([A-Za-z_][A-Za-z0-9_]*)", r"%(\1)s", statement)


def tool_json(backend: str = "sqlite") -> dict[str, Any]:
    raw = e.tool_json()
    raw["effects"][0]["statement"] = dialect(backend, DEBIT)
    raw["effects"][1]["statement"] = dialect(backend, RECORD)
    return raw


def tool(backend: str = "sqlite") -> ToolBinding:
    """The refund tool, written for ``backend``'s database."""
    return ToolBinding.from_json(tool_json(backend), signed=False).signed(e.DEVELOPER)


def _ddl(serial: str) -> list[str]:
    return [
        "CREATE TABLE orders (id INTEGER PRIMARY KEY, tenant TEXT NOT NULL, "
        "total NUMERIC NOT NULL, status TEXT)",
        f"CREATE TABLE refunds (id {serial} PRIMARY KEY, order_id INTEGER, "
        "tenant TEXT NOT NULL, amount TEXT NOT NULL)",
        f"CREATE TABLE items (id {serial} PRIMARY KEY, tenant TEXT NOT NULL, "
        "sku TEXT, qty INTEGER)",
    ]


def _seed() -> list[tuple[int, str, int, str]]:
    return [(n, "acme" if n % 2 else "beta", 1000, "open") for n in range(1, ORDERS + 1)]


@dataclass
class World:
    backend: str
    tmp: Path
    clock: Clock
    governor: BudgetManager
    store: Store
    charter: Charter
    log: Log
    authority: Authority
    binder: Binder
    receipts: ReceiptLog
    back_office: str
    """The SQLite file, or the PostgreSQL DSN as the database's owner."""
    relay_dsn: str = ""
    cleanups: list[Callable[[], None]] = field(default_factory=list)
    stage_dsn: str = ""
    """What the engine stages on: the SQLite file, or the stage role's DSN."""

    # -- documents under this world's charter ----------------------------------------

    def charter_ref(self) -> dict[str, Any]:
        return {"charter_id": e.CHARTER_ID, "version": 1, "digest": self.charter.digest}

    def mandate_json(self, **changes: Any) -> dict[str, Any]:
        raw = e.mandate_json(charter=self.charter_ref())
        raw["tools"]["bindings"] = [{"name": "refund_order", "digest": tool(self.backend).digest}]
        raw.update(changes)
        return raw

    def mandate(self, **changes: Any) -> Mandate:
        return Mandate.from_json(self.mandate_json(**changes), signed=False).signed(e.signer("cfo"))

    def child_json(self, parent: Mandate, **changes: Any) -> dict[str, Any]:
        raw = copy.deepcopy(parent.body())
        raw.update(
            {
                "mandate_id": str(uuid.uuid4()),
                "version": 1,
                "prev": None,
                "parent": parent.ref.to_json(),
                "issuance": "derived",
                "subject": {
                    "scope": f"{parent.subject.scope}/child",
                    "funded_from": parent.subject.scope,
                    "label": "child",
                },
                "delegation": None,
                "windows": [],
            }
        )
        raw.pop("v"), raw.pop("type")
        raw = {"v": "FIDU1", "type": "mandate", **raw}
        assert parent.delegation is not None
        raw["budget"] = {**raw["budget"], "envelope": format(parent.delegation.envelope, "f")}
        raw.update(changes)
        return raw

    def child(self, parent: Mandate, **changes: Any) -> Mandate:
        return Mandate.from_json(self.child_json(parent, **changes), signed=False)

    # -- issuing and binding -----------------------------------------------------------

    def issue(self, mandate: Mandate, approvers: tuple[str, ...] = ("cfo", "head-ops")) -> None:
        self.authority.propose(mandate)
        if mandate.issuance == "quorum":
            approve(self.store, approvers, "mandate", mandate.digest)
        self.binder.issue(mandate.digest)

    def issue_and_bind(self, mandate: Mandate) -> None:
        self.issue(mandate)
        self.binder.bind(mandate.digest)

    # -- plans -------------------------------------------------------------------------

    def refund_plan(
        self,
        order: int,
        amount: str,
        *,
        scope: str = SCOPE,
        tenant: str | None = None,
        currency: str = "usd",
        statement: str | None = None,
        refund_amount: str | None = None,
    ) -> EffectPlan:
        tenant = tenant or ("acme" if order % 2 else "beta")
        builder = PlanBuilder(scope)
        builder.update(
            table="orders",
            statement=self.sql(statement or DEBIT),
            parameters={"amount": amount, "order_id": order, "tenant": tenant},
            tenant_id=tenant,
            stated_rows=1,
            effect_id=EffectId("debit"),
        )
        builder.insert(
            table="refunds",
            statement=self.sql(RECORD),
            parameters={"order_id": order, "tenant": tenant, "amount": refund_amount or amount},
            tenant_id=tenant,
            stated_rows=1,
            effect_id=EffectId("record"),
        )
        builder.enqueue(
            sink="stripe",
            operation="refunds.create",
            payload={"payment_intent": f"pi_{order:010d}", "amount": amount, "currency": currency},
            tenant_id=tenant,
            effect_id=EffectId("stripe-refund"),
        )
        return builder.build()

    def sql(self, statement: str) -> str:
        return dialect(self.backend, statement)

    def execute(self, mandate: Mandate, plan: EffectPlan) -> StageResult:
        return self.binder.engine(mandate.mandate_id).execute(plan)

    # -- reading the back office --------------------------------------------------------

    def fetch(self, sql: str) -> list[tuple[Any, ...]]:
        if self.backend == "sqlite":
            with closing(sqlite3.connect(self.back_office)) as conn:
                return [tuple(r) for r in conn.execute(sql)]
        import psycopg

        with psycopg.connect(self.back_office) as conn:
            return [tuple(r) for r in conn.execute(sql)]

    def outbox_states(self) -> list[str]:
        table = "_interlock_outbox_state" if self.backend == "sqlite" else "interlock.outbox_state"
        return [str(r[0]) for r in self.fetch(f"SELECT state FROM {table} ORDER BY message_id")]

    def relay_store(self) -> Any:
        if self.backend == "sqlite":
            from interlock.sqlite_outbox import SqliteOutboxStore

            return SqliteOutboxStore(self.back_office)
        from interlock.outbox_store import PostgresOutboxStore

        return PostgresOutboxStore(self.relay_dsn)

    def close(self) -> None:
        self.binder.close()
        self.receipts.close()
        self.store.close()
        self.governor.close()
        for cleanup in reversed(self.cleanups):
            cleanup()


def build(backend: str, tmp: Path, store: Store, clock: Clock) -> Generator[World, None, None]:
    """A world on ``backend``, its fiduciary store ``store``."""
    from interlock.substrate import SqliteSubstrate

    cleanups: list[Callable[[], None]] = []
    governor = BudgetManager.open_sqlite(str(tmp / "ledger.db"))
    ensure_anchor_scope(governor)
    governor.open_root("support", "1000")
    genesis = governor.entry(1).entry_hash
    relay_dsn = ""
    if backend == "sqlite":
        from interlock.sqlite_outbox import install_sqlite_outbox

        back_office = str(tmp / "back_office.db")
        stage_dsn = back_office
        with closing(sqlite3.connect(back_office)) as conn:
            for ddl in _ddl("INTEGER"):
                conn.execute(ddl)
            conn.executemany("INSERT INTO orders VALUES (?, ?, ?, ?)", _seed())
            conn.commit()
        install_sqlite_outbox(back_office, deploy.SINKS)

        def substrate() -> Any:
            return SqliteSubstrate(back_office, tables=deploy.table_specs())
    else:
        back_office, stage_dsn, relay_dsn, cleanup = _postgres_back_office()
        cleanups.append(cleanup)
        from interlock.postgres import PostgresSubstrate

        def substrate() -> Any:
            return PostgresSubstrate(stage_dsn, tables=deploy.table_specs())

    charter = Charter.from_json(
        e.charter_json(deployment={"ledger": genesis, "label": "test"}), signed=False
    ).signed(e.signer("cfo"))
    log = Log(store, e.SERVICE, ledger=governor, clock=clock)
    authority = Authority(log, pinned=charter.digest)
    authority.propose(charter)
    approve(store, e.PRINCIPALS, "charter", charter.digest)
    authority.adopt_charter(charter.digest)
    receipts = ReceiptLog(
        "fiduciary-receipts", Ed25519Signer(bytes(32)), path=tmp / "receipts.jsonl"
    )
    deployment = deploy.deployment(
        ledger=genesis, tools={tool(backend).digest: tool(backend), e.lookup().digest: e.lookup()}
    )
    binder = Binder(
        authority,
        governor,
        deployment=deployment,
        substrate=substrate,
        chains=tmp / "chains",
        receipts=receipts,
    )
    world = World(
        backend,
        tmp,
        clock,
        governor,
        store,
        charter,
        log,
        authority,
        binder,
        receipts,
        back_office,
        relay_dsn,
        cleanups,
        stage_dsn,
    )
    try:
        yield world
    finally:
        world.close()


def _postgres_back_office() -> tuple[str, str, str, Callable[[], None]]:
    """A fresh database: the tables, seeded; Interlock installed with a stage
    role and a relay role. Returns the owner's, the stage's and the relay's
    DSNs, and how to drop it all."""
    import psycopg
    from interlock.postgres import install
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    assert PG_DSN is not None
    name = f"fid_bo_{uuid.uuid4().hex[:12]}"
    stage, relay = f"fid_stage_{uuid.uuid4().hex[:8]}", f"fid_relay_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(PG_DSN, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
        for role in (stage, relay):
            admin.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'pw'").format(sql.Identifier(role))
            )
    owner = make_conninfo(PG_DSN, dbname=name)
    with psycopg.connect(owner, autocommit=True) as conn:
        for ddl in _ddl("SERIAL"):
            conn.execute(ddl)
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO orders VALUES (%s, %s, %s, %s)", _seed())
        install(
            conn, deploy.table_specs(), stage_roles=[stage], sinks=deploy.SINKS, relay_roles=[relay]
        )
        conn.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON orders, refunds, items TO {stage}")
        conn.execute(f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {stage}")

    def cleanup() -> None:
        with psycopg.connect(PG_DSN, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )
            for role in (stage, relay):
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))

    return (
        owner,
        make_conninfo(owner, user=stage, password="pw"),
        make_conninfo(owner, user=relay, password="pw"),
        cleanup,
    )


def money(text: str) -> Decimal:
    return Decimal(text)

"""A deployment reopened from disk, for the crash tests (§10.4).

The crash tests run the service as a child process, kill it at a checkpoint,
and restart it. Both the child and the restarted service open what the
deployment holds on disk afresh: the fiduciary store, the AgentGov ledger, the
back office and its outbox, the escrow chains, interlock's operator log and
the guard's record log. What either process held only in memory (a quote, a
repair proposal) is gone after a kill, as it is in production.

A :class:`Manifest` names those places. :func:`open_env` opens them, with a
fixed clock and the checkpoint hook the child kills itself at. The scenario
helpers raise one ticket of each resolution class, and approve it as the
principals would.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from agentgov import BudgetManager
from agentgov.receipts import ReceiptLog
from agentgov.receipts.signing import Ed25519Signer
from interlock import OutboundRequest, PlanBuilder
from interlock.deliveries import OutboxOperations, operations
from interlock.extension import BudgetGuard
from interlock.records import Keyring, RecordLog
from interlock.relay import DeliveryResult, LedgerBreaker, Relay
from interlock.types import EffectId, EffectPlan

from fiduciary import fidu1
from fiduciary.authority import Authority
from fiduciary.binding import Binder
from fiduciary.documents import Approval, Mandate, Ticket, Warrant
from fiduciary.escalation import Escalation
from fiduciary.executor import Executor
from fiduciary.log import Log
from fiduciary.sqlite import SqliteStore
from fiduciary.store import Store
from tests import bound, deploy
from tests import examples as e
from tests.conftest import Clock

SCOPE = bound.SCOPE
CLASSES: dict[str, str] = {
    # resolution: the ticket class it resolves
    "accept_repair": "refused",
    "reject": "amend",
    "release": "held",
    "reset": "held",
    "cancel": "held",
    "requeue": "dead",
    "compensate": "undo",
    "compensate_late": "undo",
    "grant": "extension",
    "decline": "extension",
    "amend": "amend",
}


@dataclass
class Manifest:
    backend: str
    tmp: str
    store: str
    """The SQLite file, or the PostgreSQL DSN."""
    schema: str
    ledger: str
    back_office: str
    stage_dsn: str
    relay_dsn: str
    charter: str
    genesis: str
    mandate_id: str
    now: str

    def dump(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self)))

    @classmethod
    def load(cls, path: Path) -> Manifest:
        return cls(**json.loads(path.read_text()))

    def at(self) -> datetime:
        return datetime.strptime(self.now, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)

    def advance(self, seconds: float) -> None:
        self.now = fidu1.format_instant(self.at() + timedelta(seconds=seconds))


def crash_mandate(world: bound.World) -> Mandate:
    """One mandate every class runs under: any statement, both stripe
    operations, no windows to run out of, room for many open tickets, and a
    rule that accepts a trimmed repair by itself (so ``formed`` is also
    reached as a ticket is raised)."""
    raw = world.mandate_json()
    raw["data"]["agreements"] = []
    raw["data"]["max_rows"] = 20
    raw["tools"] = {"statements": "any", "bindings": []}
    raw["windows"] = []
    raw["outbound"]["max_per_plan"] = 12
    raw["outbound"]["per_sink"] = {"stripe": 12}
    raw["outbound"]["sinks"]["stripe"]["operations"] = ["charges.capture", "refunds.create"]
    raw["escalation"] = {
        "max_open": 50,
        "ttl_seconds": 86400,
        "auto": [
            {
                "id": "trim-refused",
                "class": "refused",
                "resolution": "accept_repair",
                "when": {"max_dropped": 5},
            }
        ],
    }
    return Mandate.from_json(raw, signed=False).signed(e.signer("cfo"))


class Env:
    """The service's parts, opened from a :class:`Manifest`."""

    def __init__(self, m: Manifest, checkpoint: Callable[[str], None] | None = None) -> None:
        self.m = m
        tmp = Path(m.tmp)
        self.clock = Clock(m.at())
        self.store: Store
        if m.backend == "sqlite":
            self.store = SqliteStore(m.store)
        else:
            from fiduciary.postgres import PostgresStore

            self.store = PostgresStore(m.store, schema=m.schema)
        self.governor = BudgetManager.open_sqlite(m.ledger)
        self.log = Log(self.store, e.SERVICE, ledger=self.governor, clock=self.clock)
        self.authority = Authority(self.log, pinned=m.charter, checkpoint=checkpoint)
        self.receipts = ReceiptLog(
            "fiduciary-receipts", Ed25519Signer(bytes(32)), path=tmp / "receipts.jsonl"
        )
        if m.backend == "sqlite":
            from interlock.sqlite_outbox import OPERATOR, SqliteOutboxStore
            from interlock.substrate import SqliteSubstrate

            def substrate() -> Any:
                return SqliteSubstrate(m.stage_dsn, tables=deploy.table_specs())

            self._outbox_conn: Any = SqliteOutboxStore(m.back_office, writes=OPERATOR)
        else:
            import psycopg
            from interlock.postgres import PostgresSubstrate

            def substrate() -> Any:
                return PostgresSubstrate(m.stage_dsn, tables=deploy.table_specs())

            self._outbox_conn = psycopg.connect(m.back_office, autocommit=True)
        self.outbox: OutboxOperations = operations(self._outbox_conn)
        tool = bound.tool(m.backend)
        self.binder = Binder(
            self.authority,
            self.governor,
            deployment=deploy.deployment(
                ledger=m.genesis, tools={tool.digest: tool, e.lookup().digest: e.lookup()}
            ),
            substrate=substrate,
            chains=tmp / "chains",
            receipts=self.receipts,
        )
        self.binder.resume()
        public = e.SERVICE.public_key()
        self.runtime = RecordLog(
            e.SERVICE, path=tmp / "runtime.jsonl", keyring=Keyring({public.key_id: public})
        )
        self.guard = BudgetGuard(
            self.governor, self.runtime, receipts=self.receipts, clock=self.clock
        )
        self.escalation = Escalation(self.authority, self.governor, checkpoint=checkpoint)
        self.executor = Executor(
            self.binder,
            escalation=self.escalation,
            outbox=self.outbox,
            operator_log=tmp / "operators.jsonl",
            executor_signer=e.SERVICE,
            guard=self.guard,
            checkpoint=checkpoint,
        )

    @property
    def mandate(self) -> Mandate:
        state = self.log.state()
        return self.authority.decide.head_mandate(state, self.m.mandate_id)

    def close(self) -> None:
        self.runtime.close()
        self.binder.close()
        self.receipts.close()
        self._outbox_conn.close()
        self.store.close()
        self.governor.close()

    # -- the back office ----------------------------------------------------------

    def sql(self, statement: str) -> str:
        return bound.dialect(self.m.backend, statement)

    def relay(self, result: DeliveryResult) -> None:
        if self.m.backend == "sqlite":
            from interlock.sqlite_outbox import SqliteOutboxStore

            store: Any = SqliteOutboxStore(self.m.back_office)
        else:
            from interlock.outbox_store import PostgresOutboxStore

            store = PostgresOutboxStore(self.m.relay_dsn)
        try:
            relay = Relay(
                store,
                adapters={"stripe": _Fixed(result)},
                breaker=LedgerBreaker(self.governor),
                signer=Ed25519Signer.generate(),
            )
            for _ in range(10):  # a held message is visited again on every pass
                if not relay.run_once(limit=50):
                    break
        finally:
            store.close()

    def execute(self, plan: EffectPlan) -> Any:
        return self.binder.engine(self.m.mandate_id).execute(plan)

    def refund(self, order: int) -> EffectPlan:
        plan = PlanBuilder(SCOPE)
        plan.update(
            table="orders",
            statement=self.sql(bound.DEBIT),
            parameters={"amount": "1.00", "order_id": order, "tenant": "acme"},
            tenant_id="acme",
            stated_rows=1,
            effect_id=EffectId("debit"),
        )
        plan.enqueue(
            sink="stripe",
            operation="refunds.create",
            payload={"payment_intent": f"pi_{order:010d}", "amount": "1.00", "currency": "usd"},
            tenant_id="acme",
            effect_id=EffectId("stripe-refund"),
        )
        return plan.build()

    def capture(self, order: int) -> EffectPlan:
        """Two captures, the second after the first: interlock compensates them
        in reverse order, which need not be the order a ticket lists them in."""
        plan = PlanBuilder(SCOPE)
        for n, after in ((order, None), (order + 1, [EffectId(f"capture-{order}")])):
            payload = {"payment_intent": f"pi_{n:010d}", "amount": "2.00", "currency": "usd"}
            plan.enqueue(
                sink="stripe",
                operation="charges.capture",
                payload=payload,
                compensation=OutboundRequest(
                    sink="stripe", operation="refunds.create", payload=payload
                ),
                tenant_id="acme",
                effect_id=EffectId(f"capture-{n}"),
                after=after,
            )
        return plan.build()

    def mixed(self, order: int) -> EffectPlan:
        """A plan the tenant allowlist refuses in part: its repair keeps acme's."""
        plan = PlanBuilder(SCOPE)
        for n, tenant in ((order, "acme"), (order + 1, "beta")):
            plan.update(
                table="orders",
                statement=self.sql(bound.DEBIT),
                parameters={"amount": "1.00", "order_id": n, "tenant": tenant},
                tenant_id=tenant,
                stated_rows=1,
                effect_id=EffectId(f"debit-{tenant}"),
            )
        return plan.build()


class _Fixed:
    def __init__(self, result: DeliveryResult) -> None:
        self._result = result

    def send(self, delivery: Any) -> DeliveryResult:
        return self._result


# -- what each class needs before its ticket is raised (run by the parent) ------


def _committed(env: Env, plan: EffectPlan) -> None:
    staged = env.execute(plan)
    assert staged.committed, (staged.state, staged.verdict)


def prepare(env: Env, action: str, n: int) -> dict[str, Any]:
    """Bring the back office to where a ticket of ``action``'s class can be
    raised; returns what the child needs to raise it."""
    from interlock.relay import DELIVERED, PERMANENT

    ticket_class = CLASSES[action]
    if ticket_class == "held":
        if not env.outbox.held(SCOPE):
            if env.governor.halted_by(SCOPE) is not None:
                env.governor.reset(SCOPE)  # a halted scope commits nothing (E3-8)
            _committed(env, env.refund(2 * n + 1))
        if env.governor.halted_by(SCOPE) is None:
            env.governor.trip(SCOPE, "spend envelope exhausted")
        env.relay(DeliveryResult(outcome=DELIVERED, detail="ok"))
        assert env.outbox.held(SCOPE)
        return {}
    if ticket_class == "dead":
        plan = env.refund(2 * n + 1)
        _committed(env, plan)
        env.relay(DeliveryResult(outcome=PERMANENT, detail="declined"))
        (dead,) = [c for c in env.outbox.compensables(str(plan.plan_id)) if c.state == "dead"]
        return {"message_id": str(dead.message_id)}
    if ticket_class == "undo":
        plan = env.capture(2 * n + 1)
        _committed(env, plan)
        env.relay(DeliveryResult(outcome=DELIVERED, detail=f"ch_{n}"))
        found = {"plan_id": str(plan.plan_id)}
        if action == "compensate_late":
            deadline = max(c.not_after for c in env.outbox.compensables(found["plan_id"]))
            found["now"] = fidu1.format_instant(deadline + timedelta(seconds=1))
        return found
    return {}


# -- raising and approving (run by the child, or by the restarted parent) ------


def raise_ticket(env: Env, action: str, n: int, prepared: dict[str, Any]) -> Ticket:
    escalation, mandate_id = env.escalation, env.m.mandate_id
    ticket_class = CLASSES[action]
    if ticket_class == "refused":
        plan = env.mixed(2 * n + 1)
        assert not env.execute(plan).committed
        repair = env.binder.engine(mandate_id).engine.repair(plan)
        ticket, _ = escalation.raise_refused(mandate_id, repair)
    elif ticket_class == "held":
        ticket, _ = escalation.raise_held(mandate_id, env.outbox)
    elif ticket_class == "dead":
        ticket, _ = escalation.raise_dead(mandate_id, env.outbox, prepared["message_id"])
    elif ticket_class == "undo":
        ticket, _ = escalation.raise_undo(mandate_id, env.outbox, prepared["plan_id"])
    elif ticket_class == "extension":
        quote = env.guard.quote(SCOPE, Decimal("4.00"), task=SCOPE)
        ticket, _ = escalation.raise_extension(mandate_id, quote)
    else:
        current = env.mandate
        raw = current.body()
        raw.update(version=current.version + 1, prev=current.digest)
        raw["budget"] = {**raw["budget"], "per_call": f"0.4{n:07d}"}
        proposed = Mandate.from_json(raw, signed=False).signed(e.signer("cfo"))
        env.authority.propose(proposed)
        ticket, _ = escalation.raise_amend(mandate_id, proposed)
    assert ticket is not None
    return ticket


def submit_approvals(env: Env, ticket: Ticket, action: str) -> None:
    """The principals' approvals of the option ``action``, into the intake."""
    option = next(o for o in ticket.options if o.resolution.action == action)
    now = env.clock()
    for principal in e.PRINCIPALS[: option.quorum]:
        raw = e.approval_json(
            principal,
            subject="ticket",
            digest=ticket.digest,
            resolution=option.digest,
            approval_id=str(uuid.uuid4()),
            issued_at=fidu1.format_instant(now),
            not_after=fidu1.format_instant(now + timedelta(hours=1)),
        )
        approval = Approval.from_json(raw, signed=False).signed(e.signer(principal))
        env.store.submit(approval.canonical())


def warrant_of(env: Env, ticket: Ticket) -> Warrant | None:
    return env.escalation.warrant_for_ticket(ticket.ticket_id)


def back_office_rows(m: Manifest, sql: str) -> list[tuple[Any, ...]]:
    if m.backend == "sqlite":
        with closing(sqlite3.connect(m.back_office)) as conn:
            return [tuple(r) for r in conn.execute(sql)]
    import psycopg

    with psycopg.connect(m.back_office) as conn:
        return [tuple(r) for r in conn.execute(sql)]

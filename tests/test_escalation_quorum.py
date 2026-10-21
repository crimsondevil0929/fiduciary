"""Escalation triggers, deduplication, fatigue bounds, quorum intake, autonomous
rules and property tests (``docs/FIDUCIARY_SPEC.md`` §7.1 to §7.4, §10.2, §11
Phase 3), across SQLite and PostgreSQL."""

from __future__ import annotations

import copy
import sqlite3
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from agentgov.receipts.signing import Ed25519Signer
from hypothesis import HealthCheck, event, given, settings
from hypothesis import strategies as st
from interlock import PlanBuilder
from interlock.deliveries import OutboxOperations, operations
from interlock.extension import BudgetGuard, ExtensionRequest
from interlock.records import RecordLog
from interlock.relay import DELIVERED, PERMANENT, DeliveryResult, LedgerBreaker, Relay
from interlock.types import EffectId, OutboundRequest

from fiduciary import fidu1
from fiduciary.authority import Authority
from fiduciary.documents import (
    Approval,
    Charter,
    Mandate,
    Resolution,
    Ticket,
    Warrant,
    suspension_digest,
)
from fiduciary.documents.ticket import (
    AmendEvidence,
    DeadEvidence,
    ExtensionEvidence,
    HeldEvidence,
    RefusedEvidence,
    UndoEvidence,
)
from fiduciary.escalation import Escalation, EscalationError
from fiduciary.log import Log
from fiduciary.sqlite import SqliteStore
from fiduciary.store import Store
from fiduciary.verify import verify_store
from tests import examples as e
from tests.bound import DEBIT, RECORD, SCOPE, World, build
from tests.conftest import Clock, approve


@pytest.fixture
def world(store: Store, tmp_path: Path, clock: Clock) -> Iterator[World]:
    backend = "sqlite" if isinstance(store, SqliteStore) else "postgres"
    yield from build(backend, tmp_path, store, clock)


def _verified(world: World) -> None:
    world.governor.refresh()
    report = verify_store(
        world.store,
        service_key=e.SERVICE.public_key(),
        pinned=world.charter.digest,
        ledger=world.governor.audit_trail(),
    )
    assert report.ok, report.problems


def _outbox_ops(world: World) -> tuple[OutboxOperations, Any]:
    if world.backend == "sqlite":
        from interlock.sqlite_outbox import OPERATOR, SqliteOutboxStore

        raw = SqliteOutboxStore(world.back_office, writes=OPERATOR)
        return operations(raw), raw
    import psycopg

    conn = psycopg.connect(world.back_office, autocommit=True)
    return operations(conn), conn


class _FixedAdapter:
    def __init__(self, result: DeliveryResult) -> None:
        self._result = result

    def send(self, delivery: Any) -> DeliveryResult:
        return self._result


def _submit_ticket_approval(
    store: Store,
    principal: str,
    ticket: Ticket,
    *,
    decision: str = "approve",
    resolution: str | None = None,
    charter_version: int = 1,
    issued_at: str = "2026-10-18T10:05:40.004211Z",
    not_after: str = "2026-10-18T10:35:40.004211Z",
    signer_name: str | None = None,
) -> Approval:
    res = resolution if resolution is not None else ticket.options[0].digest
    raw = e.approval_json(
        principal,
        subject="ticket",
        digest=ticket.digest,
        resolution=res,
        decision=decision,
        charter={"charter_id": e.CHARTER_ID, "version": charter_version},
        issued_at=issued_at,
        not_after=not_after,
    )
    ap = Approval.from_json(raw, signed=False).signed(e.signer(signer_name or principal))
    store.submit(ap.canonical())
    return ap


def _past_compensation_deadline(world: World, ops: OutboxOperations, plan_id: str) -> None:
    deadline = max(c.not_after for c in ops.compensables(plan_id))
    world.clock.now = max(world.clock.now, deadline) + timedelta(seconds=1)


# -- 1. Refused trigger & autonomous trim rule (§7.1, §7.4) -------------------------


def test_refused_trigger_auto_warrants_when_rule_matches_and_stays_open_otherwise(
    world: World,
) -> None:
    escalation = Escalation(world.authority, world.governor)
    mandate = world.mandate()
    world.issue_and_bind(mandate)
    engine = world.binder.engine(mandate.mandate_id)

    # Build a plan with two independent parts: one valid refund, one exceeding the $200 cap
    builder = PlanBuilder(SCOPE)
    builder.update(
        table="orders",
        statement=world.sql(DEBIT),
        parameters={"amount": "20.00", "order_id": 1, "tenant": "acme"},
        tenant_id="acme",
        stated_rows=1,
        effect_id=EffectId("debit-ok"),
    )
    builder.insert(
        table="refunds",
        statement=world.sql(RECORD),
        parameters={"order_id": 1, "tenant": "acme", "amount": "20.00"},
        tenant_id="acme",
        stated_rows=1,
        effect_id=EffectId("record-ok"),
    )
    builder.enqueue(
        sink="stripe",
        operation="refunds.create",
        payload={"payment_intent": "pi_0000000001", "amount": "20.00", "currency": "usd"},
        tenant_id="acme",
        effect_id=EffectId("stripe-ok"),
    )
    builder.enqueue(
        sink="stripe",
        operation="refunds.create",
        payload={"payment_intent": "pi_0000000003", "amount": "250.00", "currency": "usd"},
        tenant_id="acme",
        effect_id=EffectId("stripe-over"),
        independent=True,
    )
    plan = builder.build()
    res = engine.execute(plan)
    assert not res.committed
    repair = engine.engine.repair(plan, max_trials=16)
    assert repair.proposal is not None

    # The default mandate has auto rule "trim-refused" (exhaustive=True, max_dropped=1)
    tk, wt = escalation.raise_refused(
        mandate.mandate_id,
        repair,
        receipt=res.receipt,
        statement="Drop\nthe over-cap refund",
    )
    assert tk is not None and wt is not None
    assert tk.statement == "Drop the over-cap refund"
    assert isinstance(tk.evidence, RefusedEvidence)
    assert wt.basis.rule is not None
    assert wt.basis.rule.rule_id == "trim-refused"
    assert wt.resolution.action == "accept_repair"
    assert escalation.warrant_for_ticket(tk.ticket_id) == wt
    assert escalation.ticket(tk.ticket_id) == tk
    assert escalation.warrant(wt.warrant_id) == wt
    _verified(world)


def test_refused_trigger_without_matching_auto_rule_waits_for_quorum(world: World) -> None:
    escalation = Escalation(world.authority, world.governor)
    mandate = world.mandate(escalation={"max_open": 5, "ttl_seconds": 86400, "auto": []})
    world.issue_and_bind(mandate)
    engine = world.binder.engine(mandate.mandate_id)

    builder = PlanBuilder(SCOPE)
    builder.update(
        table="orders",
        statement=world.sql(DEBIT),
        parameters={"amount": "20.00", "order_id": 1, "tenant": "acme"},
        tenant_id="acme",
        stated_rows=1,
        effect_id=EffectId("debit-ok"),
    )
    builder.insert(
        table="refunds",
        statement=world.sql(RECORD),
        parameters={"order_id": 1, "tenant": "acme", "amount": "20.00"},
        tenant_id="acme",
        stated_rows=1,
        effect_id=EffectId("record-ok"),
    )
    builder.enqueue(
        sink="stripe",
        operation="refunds.create",
        payload={"payment_intent": "pi_0000000001", "amount": "20.00", "currency": "usd"},
        tenant_id="acme",
        effect_id=EffectId("stripe-ok"),
    )
    builder.enqueue(
        sink="stripe",
        operation="refunds.create",
        payload={"payment_intent": "pi_0000000003", "amount": "250.00", "currency": "usd"},
        tenant_id="acme",
        effect_id=EffectId("stripe-over"),
        independent=True,
    )
    plan = builder.build()
    res = engine.execute(plan)
    repair = engine.engine.repair(plan, max_trials=16)

    tk, wt = escalation.raise_refused(mandate.mandate_id, repair, receipt=res.receipt)
    assert tk is not None and wt is None
    assert world.log.state().ticket_status(tk.ticket_id) == "open"

    # Admissible plan repair cannot be escalated
    ok_plan = world.refund_plan(1, "10.00")
    ok_repair = engine.engine.repair(ok_plan, max_trials=8)
    with pytest.raises(EscalationError):
        escalation.raise_refused(mandate.mandate_id, ok_repair, receipt=res.receipt)
    _verified(world)


# -- 2. Held, dead and undo triggers (§7.1) -----------------------------------------


def test_held_trigger_with_safety_and_spent_out_halts_and_narrowed_release(
    world: World,
) -> None:
    escalation = Escalation(world.authority, world.governor)
    mandate = world.mandate()
    world.issue_and_bind(mandate)

    r1 = world.execute(mandate, world.refund_plan(1, "15.00"))
    r2 = world.execute(mandate, world.refund_plan(3, "25.00"))
    assert r1.committed and r2.committed

    world.governor.trip(SCOPE, reason="spend envelope exhausted")
    relay = Relay(
        world.relay_store(),
        adapters={"stripe": _FixedAdapter(DeliveryResult(DELIVERED, remote_ref="re_1"))},
        breaker=LedgerBreaker(world.governor),
        signer=Ed25519Signer.generate(),
    )
    relay.run_once(limit=10)
    assert world.outbox_states() == ["held", "held"]

    ops, handle = _outbox_ops(world)
    try:
        tk, wt = escalation.raise_held(mandate.mandate_id, ops, statement="Funds exhausted")
        assert tk is not None and wt is None
        assert isinstance(tk.evidence, HeldEvidence)
        assert tk.evidence.halt is not None
        assert tk.evidence.halt.spent_out is True
        # Spent-out reset quorum is 2; release with reset is max(1, 2) == 2
        assert [opt.resolution.action for opt in tk.options] == ["release", "reset", "cancel"]
        assert tk.options[0].quorum == 2
        assert tk.options[1].quorum == 2

        # Narrow release to just the first message (F4-20)
        first_msg = tk.evidence.messages[0]
        narrowed = Resolution.read(
            {
                "action": "release",
                "messages": [{"id": first_msg.message_id, "head": first_msg.head}],
                "reset": SCOPE,
            }
        )
        escalation.propose_resolution(narrowed)
        _submit_ticket_approval(world.store, "cfo", tk, resolution=narrowed.digest)
        _submit_ticket_approval(world.store, "head-ops", tk, resolution=narrowed.digest)
        records = escalation.process_intake()
        assert [r.kind for r in records] == [
            "approval.recorded",
            "approval.recorded",
            "warrant.formed",
        ]
        formed = escalation.warrant_for_ticket(tk.ticket_id)
        assert formed is not None
        assert formed.resolution == narrowed
    finally:
        handle.close()
    _verified(world)


def test_dead_trigger_and_auto_requeue_bounded_by_max_requeues(world: World) -> None:
    escalation = Escalation(world.authority, world.governor)
    mandate = world.mandate(
        escalation={
            "max_open": 5,
            "ttl_seconds": 86400,
            "auto": [
                {
                    "id": "retry-dead-once",
                    "class": "dead",
                    "resolution": "requeue",
                    "when": {"max_requeues": 1, "outcome": ["permanent"]},
                }
            ],
        }
    )
    world.issue_and_bind(mandate)

    r1 = world.execute(mandate, world.refund_plan(1, "15.00"))
    assert r1.committed
    relay = Relay(
        world.relay_store(),
        adapters={"stripe": _FixedAdapter(DeliveryResult(PERMANENT, detail="card_declined"))},
        breaker=LedgerBreaker(world.governor),
        signer=Ed25519Signer.generate(),
    )
    relay.run_once(limit=10)
    assert world.outbox_states() == ["dead"]

    ops, handle = _outbox_ops(world)
    try:
        rows = world.fetch(
            "SELECT message_id FROM "
            + ("_interlock_outbox" if world.backend == "sqlite" else "interlock.outbox")
        )
        mid = uuid.UUID(str(rows[0][0]))

        # First dead ticket matches auto rule (requeues == 0 < max_requeues == 1)
        tk1, wt1 = escalation.raise_dead(mandate.mandate_id, ops, mid)
        assert tk1 is not None and wt1 is not None
        assert isinstance(tk1.evidence, DeadEvidence)
        assert tk1.evidence.message.outcome == "permanent"
        assert wt1.resolution.action == "requeue"
        assert wt1.basis.rule is not None and wt1.basis.rule.rule_id == "retry-dead-once"

        # A second dead ticket for the same message now sees 1 prior requeue warrant,
        # exceeding max_requeues=1, so it does not auto-resolve and stays open!
        tk2, wt2 = escalation.raise_dead(mandate.mandate_id, ops, mid)
        assert tk2 is not None and wt2 is None
        assert isinstance(tk2.evidence, DeadEvidence)
        assert tk2.evidence.message.requeues == 1
        assert world.log.state().ticket_status(tk2.ticket_id) == "open"
    finally:
        handle.close()
    _verified(world)


def test_undo_trigger_on_time_and_late(world: World) -> None:
    escalation = Escalation(world.authority, world.governor)
    # Allow stripe charges.capture (compensated by refunds.create)
    base_outbound = world.mandate_json()["outbound"]
    outbound = {
        **base_outbound,
        "sinks": {
            "stripe": {
                **base_outbound["sinks"]["stripe"],
                "operations": ["charges.capture", "refunds.create"],
            }
        },
    }
    raw_m = world.mandate_json(
        data={**world.mandate_json()["data"], "agreements": []},
        outbound=outbound,
        tools={"statements": "any", "bindings": []},
    )
    mandate = Mandate.from_json(raw_m, signed=False).signed(e.signer("cfo"))
    world.issue_and_bind(mandate)

    def build_undo_plan(order: int) -> Any:
        builder = PlanBuilder(SCOPE)
        builder.update(
            table="orders",
            statement=world.sql(DEBIT),
            parameters={"amount": "10.00", "order_id": order, "tenant": "acme"},
            tenant_id="acme",
            stated_rows=1,
            effect_id=EffectId("debit"),
        )
        builder.insert(
            table="refunds",
            statement=world.sql(RECORD),
            parameters={"order_id": order, "tenant": "acme", "amount": "10.00"},
            tenant_id="acme",
            stated_rows=1,
            effect_id=EffectId("record"),
        )
        builder.enqueue(
            sink="stripe",
            operation="charges.capture",
            payload={"payment_intent": f"pi_{order:010d}", "amount": "10.00", "currency": "usd"},
            tenant_id="acme",
            effect_id=EffectId("stripe-capture"),
            not_after=timedelta(seconds=600),
            compensation=OutboundRequest(
                sink="stripe",
                operation="refunds.create",
                payload={
                    "payment_intent": f"pi_{order:010d}",
                    "amount": "10.00",
                    "currency": "usd",
                },
            ),
        )
        return builder.build()

    p1 = build_undo_plan(1)
    p2 = build_undo_plan(3)
    assert world.execute(mandate, p1).committed
    assert world.execute(mandate, p2).committed

    relay = Relay(
        world.relay_store(),
        adapters={"stripe": _FixedAdapter(DeliveryResult(DELIVERED, remote_ref="ch_ok"))},
        breaker=LedgerBreaker(world.governor),
        signer=Ed25519Signer.generate(),
    )
    relay.run_once(limit=10)
    assert world.outbox_states() == ["delivered", "delivered"]

    ops, handle = _outbox_ops(world)
    try:
        # On-time undo -> option is compensate (quorum 1)
        tk1, wt1 = escalation.raise_undo(mandate.mandate_id, ops, str(p1.plan_id))
        assert tk1 is not None and wt1 is None
        assert isinstance(tk1.evidence, UndoEvidence)
        assert tk1.evidence.late is False
        assert tk1.options[0].resolution.action == "compensate"
        assert tk1.options[0].quorum == 1

        # Past the compensation window -> option is compensate_late (quorum 2). The
        # relay stamps deliveries with the wall clock, so the window is read back.
        _past_compensation_deadline(world, ops, str(p2.plan_id))
        tk2, wt2 = escalation.raise_undo(mandate.mandate_id, ops, str(p2.plan_id))
        assert tk2 is not None and wt2 is None
        assert isinstance(tk2.evidence, UndoEvidence)
        assert tk2.evidence.late is True
        assert tk2.options[0].resolution.action == "compensate_late"
        assert tk2.options[0].quorum == 2
    finally:
        handle.close()
    _verified(world)


# -- 3. Extension trigger & durable auto_extension_total (F7-11) --------------------


def test_extension_trigger_and_durable_auto_extension_total_across_restarts(
    world: World,
) -> None:
    escalation = Escalation(world.authority, world.governor)
    mandate = world.mandate(
        extension={
            "max_grant": "50.00000000",
            "max_total": "150.00000000",
            "margin": "0.25",
            "ttl_seconds": 3600,
            "auto": {"max_grant": "10.00000000", "max_total": "13.02000000"},
        },
        escalation={
            "max_open": 5,
            "ttl_seconds": 86400,
            "auto": [
                {
                    "id": "auto-ext",
                    "class": "extension",
                    "resolution": "grant",
                    "when": {"max_amount": "10.00000000"},
                }
            ],
        },
    )
    world.issue_and_bind(mandate)
    assert world.execute(mandate, world.refund_plan(1, "10.00")).committed

    records = RecordLog(
        Ed25519Signer(bytes([7]) * 32),
        log_id="interlock",
        path=world.tmp / "interlock.jsonl",
    )
    guard = BudgetGuard(
        world.governor,
        records,
        receipts=world.receipts,
        margin=Decimal("0.25"),
        ttl=timedelta(hours=1),
        clock=world.clock,
    )
    # Spend down support-agent from 249.99 by 244.00 -> available is 5.99,
    # so a 10.00 call (12.50 with 25% margin) requests 12.50 - 5.99 = 6.51
    auth = world.governor.authorize(SCOPE, Decimal("244.00"))
    world.governor.capture(auth, Decimal("244.00"))

    quote1 = guard.authorize(SCOPE, Decimal("10.00"))
    assert isinstance(quote1, ExtensionRequest)
    assert quote1.requested == Decimal("6.51")

    tk1, wt1 = escalation.raise_extension(mandate.mandate_id, quote1)
    assert tk1 is not None and wt1 is not None
    assert isinstance(tk1.evidence, ExtensionEvidence)
    assert tk1.evidence.progress.receipts is not None
    assert wt1.resolution.action == "grant"
    assert wt1.resolution.amount == Decimal("6.51")
    assert world.log.state().auto_extension_total(mandate.digest) == Decimal("6.51000000")

    # Simulate a service restart by constructing a fresh Log, Authority and Escalation
    log2 = Log(world.store, e.SERVICE, ledger=world.governor, clock=world.clock)
    auth2 = Authority(log2, pinned=world.charter.digest)
    escalation2 = Escalation(auth2, world.governor)
    assert log2.state().auto_extension_total(mandate.digest) == Decimal("6.51000000")

    # Second quote of 6.51 brings total to 13.02 == auto.max_total -> still auto-granted!
    guard.decline(quote1, declined_by="fiduciary", reason="superseded")
    quote2 = guard.authorize(SCOPE, Decimal("10.00"))
    assert isinstance(quote2, ExtensionRequest)
    tk2, wt2 = escalation2.raise_extension(mandate.mandate_id, quote2)
    assert tk2 is not None and wt2 is not None
    assert log2.state().auto_extension_total(mandate.digest) == Decimal("13.02000000")

    # Third quote of 6.51 would bring total to 19.53 > auto.max_total (13.02) -> NOT auto-granted!
    guard.decline(quote2, declined_by="fiduciary", reason="superseded")
    quote3 = guard.authorize(SCOPE, Decimal("10.00"))
    assert isinstance(quote3, ExtensionRequest)
    tk3, wt3 = escalation2.raise_extension(mandate.mandate_id, quote3)
    assert tk3 is not None and wt3 is None
    assert log2.state().ticket_status(tk3.ticket_id) == "open"
    records.close()
    _verified(world)


# -- 4. Amend trigger, deduplication (F7-1) and max_open fatigue bound (F7-2) -------


def test_amend_trigger_deduplication_and_max_open_fatigue_bound(world: World) -> None:
    escalation = Escalation(world.authority, world.governor)
    mandate = world.mandate(escalation={"max_open": 2, "ttl_seconds": 3600, "auto": []})
    world.issue_and_bind(mandate)

    v2_raw = world.mandate_json(
        version=2,
        prev=mandate.digest,
        budget={**mandate.budget.to_json(), "envelope": "300.00000000"},
        data={**mandate.data.to_json(), "max_rows": 10},
    )
    v2 = Mandate.from_json(v2_raw, signed=False).signed(e.signer("cfo"))

    tk1, wt1 = escalation.raise_amend(mandate.mandate_id, v2, statement="Need larger envelope")
    assert tk1 is not None and wt1 is None
    assert isinstance(tk1.evidence, AmendEvidence)
    assert any(d.path == "budget.envelope" for d in tk1.evidence.wider)
    assert any(d.path == "data.max_rows" for d in tk1.evidence.narrower)

    # F7-1: Raising again with the same evidence (even with a different agent statement!)
    # folds into tk1 and writes no new log record
    head_before = world.store.head(world.log.log_id)
    tk_dup, wt_dup = escalation.raise_amend(
        mandate.mandate_id, v2, statement="Different agent prose"
    )
    assert tk_dup == tk1 and wt_dup is None
    assert world.store.head(world.log.log_id) == head_before

    # Raise a second distinct amend ticket to hit max_open == 2
    v2_alt = Mandate.from_json(
        world.mandate_json(
            version=2,
            prev=mandate.digest,
            budget={**mandate.budget.to_json(), "envelope": "350.00000000"},
        ),
        signed=False,
    ).signed(e.signer("cfo"))
    tk2, _ = escalation.raise_amend(mandate.mandate_id, v2_alt)
    assert tk2 is not None and tk2.ticket_id != tk1.ticket_id

    # F7-2: A third distinct trigger exceeds max_open == 2 -> returns (None, None)
    v2_third = Mandate.from_json(
        world.mandate_json(
            version=2,
            prev=mandate.digest,
            budget={**mandate.budget.to_json(), "envelope": "400.00000000"},
        ),
        signed=False,
    ).signed(e.signer("cfo"))
    head_at_cap = world.store.head(world.log.log_id)
    tk3, wt3 = escalation.raise_amend(mandate.mandate_id, v2_third)
    assert tk3 is None and wt3 is None
    assert world.store.head(world.log.log_id) == head_at_cap

    # Once tk1 declines or lapses, the slot frees up
    for p in ("cfo", "head-ops", "security"):
        _submit_ticket_approval(world.store, p, tk1, decision="decline")
    recs = escalation.process_intake()
    assert any(r.kind == "ticket.declined" for r in recs)
    assert world.log.state().ticket_status(tk1.ticket_id) == "declined"

    tk3_retry, _ = escalation.raise_amend(mandate.mandate_id, v2_third)
    assert tk3_retry is not None
    _verified(world)


# -- 5. Quorum edge cases: superseded decisions, late approvals, expiry, revoked ----


def test_quorum_superseded_decisions_late_approvals_expiry_and_revoked_fallback(
    world: World,
) -> None:
    escalation = Escalation(world.authority, world.governor)
    mandate = world.mandate()
    world.issue_and_bind(mandate)
    child_draft = world.child(mandate)
    world.binder.derive(child_draft)
    child = world.authority.decide.head_mandate(world.log.state(), child_draft.mandate_id)

    # Raise an amend ticket on child (amend quorum is 2)
    child_v2 = Mandate.from_json(
        {
            **world.child_json(mandate),
            "mandate_id": child.mandate_id,
            "version": 2,
            "prev": child.digest,
            "data": {**child.data.to_json(), "max_rows": 10},
        },
        signed=False,
    ).signed(e.signer("cfo"))
    tk, _ = escalation.raise_amend(child.mandate_id, child_v2)
    assert tk is not None

    # cfo approves, then changes mind and declines -> 0 approvals count!
    _submit_ticket_approval(world.store, "cfo", tk, decision="approve")
    _submit_ticket_approval(
        world.store, "cfo", tk, decision="decline", issued_at="2026-10-18T10:06:00.000000Z"
    )
    escalation.process_intake()
    state = world.log.state()
    counted, _ = world.authority.decide.count_ticket(state, tk, tk.options[0].resolution)
    assert counted == []

    # Revoke child lineage -> roster falls back to nearest ancestor in force (mandate's roster)
    approve(world.store, ("cfo", "head-ops"), "revocation", child.digest)
    world.binder.revoke(child.digest)
    assert world.log.state().status(child.mandate_id) == "revoked"

    # Now head-ops and security approve tk -> warrant forms in the same transaction
    _submit_ticket_approval(world.store, "head-ops", tk, decision="approve")
    r_first = escalation.process_intake()
    assert [r.kind for r in r_first] == ["approval.recorded"]

    _submit_ticket_approval(world.store, "security", tk, decision="approve")
    r_second = escalation.process_intake()
    assert [r.kind for r in r_second] == ["approval.recorded", "warrant.formed"]
    wt = escalation.warrant_for_ticket(tk.ticket_id)
    assert wt is not None

    # Late approval from cfo after warrant.formed is recorded and changes nothing (F7-6)
    _submit_ticket_approval(
        world.store, "cfo", tk, decision="approve", issued_at="2026-10-18T10:09:00.000000Z"
    )
    r_late = escalation.process_intake()
    assert [r.kind for r in r_late] == ["approval.recorded"]
    counted_after, _ = world.authority.decide.count_ticket(
        world.log.state(), tk, tk.options[0].resolution
    )
    assert len(counted_after) == 2

    # Raise another ticket on root mandate and let it expire (F1-6)
    root_v2 = Mandate.from_json(
        world.mandate_json(
            version=2,
            prev=mandate.digest,
            data={**mandate.data.to_json(), "max_rows": 10},
        ),
        signed=False,
    ).signed(e.signer("cfo"))
    tk_exp, _ = escalation.raise_amend(mandate.mandate_id, root_v2)
    assert tk_exp is not None
    world.clock.advance(seconds=86401)
    lapsed = escalation.expire_tickets()
    assert [r.kind for r in lapsed] == ["ticket.lapsed"]
    assert world.log.state().ticket_status(tk_exp.ticket_id) == "lapsed"
    _verified(world)


# -- 6. Tamper detection on v3 projections and documents (§10.2) --------------------


def test_verify_catches_tampered_ticket_and_warrant_projections(
    tmp_path: Path, clock: Clock
) -> None:
    db = tmp_path / "tamper_v3.db"
    store = SqliteStore(db)
    charter = e.charter()
    log = Log(store, e.SERVICE, clock=clock)
    authority = Authority(log, pinned=charter.digest)
    authority.propose(charter)
    approve(store, e.PRINCIPALS, "charter", charter.digest)
    authority.adopt_charter(charter.digest)

    mandate = e.mandate()
    authority.propose(mandate)
    approve(store, ("cfo", "head-ops"), "mandate", mandate.digest)
    authority.issue_mandate(mandate.digest)

    tk_raw = copy.deepcopy(e.ticket_json(raised_at=fidu1.format_instant(clock())))
    tk_raw["options"][0]["quorum"] = 2
    tk = Ticket.from_json(tk_raw, signed=False).signed(e.SERVICE)
    log.put(tk)
    log.append("ticket.raised", authority.decide.raise_ticket(log.state(), tk.digest, clock()))
    _submit_ticket_approval(store, "cfo", tk)
    _submit_ticket_approval(store, "head-ops", tk)
    authority.process_intake()
    store.close()

    # Tamper with the warrants projection table directly in SQLite
    with sqlite3.connect(db) as raw:
        raw.execute("UPDATE warrants SET action = 'decline'")
        raw.commit()

    reopened = SqliteStore(db)
    report = verify_store(reopened, service_key=e.SERVICE.public_key(), pinned=charter.digest)
    reopened.close()
    assert not report.ok
    assert any("warrants" in p for p in report.problems)


# -- 7. Hypothesis quorum property tests (§11 Phase 3 exit criterion 1) -------------


ALL_NAMES = ("alice", "bob", "carol", "dave", "erin")


_FAULTS = ("wrong_key", "wrong_ticket", "wrong_resolution", "expired", "late", "other_charter")


@dataclass(frozen=True)
class _EventSpec:
    kind: str
    principal: str
    decision: str = "approve"
    option_idx: int = 0
    fault: str = ""
    """Why the approval must never count, or ``""``: signed by another key;
    for another ticket, resolution or deployment (charter); expired before it
    was submitted; or recorded after its own ``not_after``."""


@st.composite
def _quorum_scenario(draw: st.DrawFn) -> tuple[tuple[str, ...], int, int, list[_EventSpec]]:
    roster_size = draw(st.integers(min_value=2, max_value=5))
    roster = ALL_NAMES[:roster_size]
    q_grant = draw(st.integers(min_value=1, max_value=roster_size))
    q_reduce = draw(st.integers(min_value=1, max_value=q_grant))
    events = draw(
        st.lists(
            st.builds(
                _EventSpec,
                kind=st.sampled_from(["approval"] * 7 + ["suspend"]),
                principal=st.sampled_from(roster * 3 + ALL_NAMES),  # and outsiders
                decision=st.sampled_from(["approve", "approve", "decline"]),
                option_idx=st.sampled_from([0, 1]),
                fault=st.sampled_from(("",) * 2 * len(_FAULTS) + _FAULTS),
            ),
            min_size=1,
            max_size=18,
        )
    )
    return roster, q_grant, q_reduce, events


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(scenario=_quorum_scenario())
def test_quorum_counting_is_exact_under_generated_histories(
    tmp_path_factory: pytest.TempPathFactory,
    scenario: tuple[tuple[str, ...], int, int, list[_EventSpec]],
) -> None:
    """A warrant forms iff section 7.3 says it does, from the approvals it
    says count, and a ticket is declined iff declines make every option
    unreachable (F7-5 to F7-7, §11 Phase 3)."""
    roster, q_grant, q_reduce, events = scenario
    tmp = tmp_path_factory.mktemp("quorum_prop")
    clock = Clock(e.at("2026-10-18T10:10:00"))
    store = SqliteStore(tmp / "prop.db")
    try:
        principals_json = [{"name": n, "key": e.key(n)} for n in ALL_NAMES]
        charter = Charter.from_json(
            e.charter_json(
                principals=principals_json,
                quorums={"charter": 1, "issue": 1, "revoke": 1},
            ),
            signed=False,
        ).signed(e.signer("alice"))
        log = Log(store, e.SERVICE, clock=clock)
        authority = Authority(log, pinned=charter.digest)
        authority.propose(charter)
        approve(store, ("alice",), "charter", charter.digest)
        authority.adopt_charter(charter.digest)

        quorums = {
            k: q_grant
            for k in (
                "amend",
                "grant",
                "release",
                "reset",
                "reset_safety",
                "compensate",
                "compensate_late",
                "requeue",
                "accept_repair",
            )
        }
        quorums["revoke"] = q_reduce
        quorums["reduce"] = q_reduce
        mandate = Mandate.from_json(
            e.mandate_json(
                charter={"charter_id": charter.charter_id, "version": 1, "digest": charter.digest},
                roster={"principals": list(roster), "quorums": quorums},
                escalation={"max_open": 5, "ttl_seconds": 86400, "auto": []},
            ),
            signed=False,
        ).signed(e.signer(roster[0]))
        authority.propose(mandate)
        approve(store, ("alice",), "mandate", mandate.digest)
        authority.issue_mandate(mandate.digest)

        tk_raw = copy.deepcopy(
            e.ticket_json(mandate=mandate.ref.to_json(), raised_at=fidu1.format_instant(clock()))
        )
        tk_raw["options"][0]["quorum"] = q_grant
        tk_raw["options"][1]["quorum"] = q_reduce
        tk = Ticket.from_json(tk_raw, signed=False).signed(e.SERVICE)
        log.put(tk)
        log.append("ticket.raised", authority.decide.raise_ticket(log.state(), tk.digest, clock()))

        suspended: set[str] = set()
        latest: dict[str, tuple[str, int, str]] = {}  # principal: (decision, option, digest)
        outcome = "open"

        for idx, ev in enumerate(events, start=1):
            clock.advance(seconds=1)
            now = clock()
            if ev.kind == "suspend":
                if ev.principal not in suspended and len(suspended) + 1 < len(ALL_NAMES):
                    approver = next(
                        n for n in ALL_NAMES if n not in suspended and n != ev.principal
                    )
                    subj = suspension_digest(charter.charter_id, ev.principal, e.key(ev.principal))
                    approve(store, (approver,), "suspension", subj)
                    authority.suspend(ev.principal)
                    suspended.add(ev.principal)
                continue

            opt = tk.options[ev.option_idx]
            if ev.fault == "expired":
                not_after = now - timedelta(seconds=1)
            elif ev.fault == "late":
                not_after = now + timedelta(seconds=1)
            else:
                not_after = now + timedelta(seconds=1800)
            ap = Approval(
                approval_id=str(uuid.uuid5(uuid.NAMESPACE_OID, f"prop-{idx}")),
                charter_id=str(uuid.uuid4()) if ev.fault == "other_charter" else charter.charter_id,
                charter_version=1,
                principal=ev.principal,
                subject=e.approval().subject.__class__(
                    "ticket", "e" * 64 if ev.fault == "wrong_ticket" else tk.digest
                ),
                resolution="f" * 64 if ev.fault == "wrong_resolution" else opt.digest,
                decision=ev.decision,
                issued_at=now - timedelta(seconds=10),
                not_after=not_after,
            ).signed(e.signer("outsiders" if ev.fault == "wrong_key" else ev.principal))
            store.submit(ap.canonical())
            if ev.fault == "late":
                clock.advance(seconds=2)  # submitted in time, recorded after its not_after
            authority.process_intake()

            if ev.fault or ev.principal in suspended or ev.principal not in roster:
                continue
            if outcome != "open":
                continue  # recorded, and changes nothing (F7-6)
            latest[ev.principal] = (ev.decision, ev.option_idx, ap.digest)
            if ev.decision == "approve":
                counted = [
                    p for p, (d, o, _) in latest.items() if d == "approve" and o == ev.option_idx
                ]
                if len(counted) >= opt.quorum:
                    outcome = f"warranted:{ev.option_idx}"
            else:
                declined = sum(1 for d, _, _ in latest.values() if d == "decline")
                if all(len(roster) - declined < o.quorum for o in tk.options):
                    outcome = "declined"

        state = log.state()
        status = state.ticket_status(tk.ticket_id)
        event(f"ticket {outcome.split(':')[0]}")
        assert status == outcome.split(":")[0], (status, outcome)
        if outcome.startswith("warranted"):
            chosen = int(outcome.split(":")[1])
            (row,) = state.warrants.values()
            warrant = Warrant.loads(store.document(row.digest)[1])  # type: ignore[index]
            assert warrant.resolution_digest == tk.options[chosen].digest
            expected = sorted(
                digest for _, (d, o, digest) in latest.items() if d == "approve" and o == chosen
            )
            assert list(warrant.basis.approvals) == expected
        else:
            assert state.warrants == {}
        for opt_idx, opt in enumerate(tk.options):
            counted_now, _ = authority.decide.count_ticket(state, tk, opt.resolution)
            assert sorted(counted_now) == sorted(
                digest for _, (d, o, digest) in latest.items() if d == "approve" and o == opt_idx
            )

        report = verify_store(store, service_key=e.SERVICE.public_key(), pinned=charter.digest)
        assert report.ok, report.problems
    finally:
        store.close()

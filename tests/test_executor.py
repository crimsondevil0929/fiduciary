"""Tests for two-phase warrant execution, crash recovery and CLI commands
(``docs/FIDUCIARY_SPEC.md`` §3.5 F3-9, §7.5, §7.6, §10.3, §10.4, §10.6)."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from agentgov.receipts.kms_aws import AwsKmsSigner
from agentgov.receipts.signing import Ed25519Signer
from interlock import OutboundRequest, PlanBuilder
from interlock.deliveries import OutboxOperations, operations
from interlock.extension import BudgetGuard
from interlock.operators import Operator, OperatorLog
from interlock.records import Keyring, RecordKind, RecordLog
from interlock.relay import DELIVERED, PERMANENT, DeliveryResult, LedgerBreaker, Relay
from interlock.types import EffectId

from fiduciary import cli, fidu1
from fiduciary.config import ConfigError, load_config
from fiduciary.documents import Approval, Mandate, Resolution, Ticket, Warrant
from fiduciary.escalation import Escalation
from fiduciary.exceptions import AuthorityError
from fiduciary.executor import MAX_OUTBOX_TARGETS, ExecutionError, Executor, check_ilok1_signer
from fiduciary.keys import generate_key
from fiduciary.sqlite import SqliteStore
from fiduciary.store import State, Store
from fiduciary.verify import verify_store
from tests import bound
from tests import examples as e
from tests.conftest import PG_DSN, Clock, approve, reopen
from tests.fakes import FakeKms


@pytest.fixture
def world(store: Store, tmp_path: Path, clock: Clock) -> Iterator[bound.World]:
    backend = "sqlite" if isinstance(store, SqliteStore) else "postgres"
    yield from bound.build(backend, tmp_path, store, clock)


def _outbox_ops(w: bound.World) -> tuple[OutboxOperations, Any]:
    if w.backend == "sqlite":
        from interlock.sqlite_outbox import OPERATOR, SqliteOutboxStore

        raw = SqliteOutboxStore(w.back_office, writes=OPERATOR)
        return operations(raw), raw
    import psycopg

    conn = psycopg.connect(w.back_office, autocommit=True)
    return operations(conn), conn


class _FixedAdapter:
    def __init__(self, result: DeliveryResult) -> None:
        self._result = result

    def send(self, delivery: Any) -> DeliveryResult:
        return self._result


def _run_relay(w: bound.World, result: DeliveryResult, *, batch_size: int = 20) -> None:
    store = w.relay_store()
    try:
        relay = Relay(
            store,
            adapters={"stripe": _FixedAdapter(result)},
            breaker=LedgerBreaker(w.governor),
            signer=Ed25519Signer.generate(),
        )
        for _ in range(batch_size):
            if not relay.run_once(limit=batch_size):
                break
    finally:
        store.close()


def _op_log(w: bound.World) -> OperatorLog:
    pub = e.SERVICE.public_key()
    return OperatorLog(
        w.tmp / "operators.jsonl",
        e.SERVICE,
        Keyring({pub.key_id: pub}),
        ledger=w.governor,
    )


def _runtime_guard(w: bound.World) -> tuple[RecordLog, BudgetGuard]:
    pub = e.SERVICE.public_key()
    records = RecordLog(
        e.SERVICE,
        path=w.tmp / "runtime.jsonl",
        keyring=Keyring({pub.key_id: pub}),
    )
    guard = BudgetGuard(w.governor, records, receipts=w.receipts, clock=w.clock)
    return records, guard


def _approve_ticket(
    w: bound.World,
    esc: Escalation,
    ticket: Ticket,
    action: str,
    principals: tuple[str, ...] = ("cfo", "head-ops"),
    resolution_digest: str | None = None,
) -> Warrant:
    if resolution_digest is None:
        opt = next(o for o in ticket.options if o.resolution.action == action)
        resolution_digest = opt.digest
    now = w.clock()
    approve(
        w.store,
        principals,
        "ticket",
        ticket.digest,
        resolution=resolution_digest,
        issued_at=fidu1.format_instant(now),
        not_after=fidu1.format_instant(now + timedelta(seconds=3600)),
    )
    esc.process_intake()
    warrant = esc.warrant_for_ticket(ticket.ticket_id)
    assert warrant is not None
    return warrant


def _verify(w: bound.World) -> None:
    w.governor.refresh()
    report = verify_store(
        w.store,
        service_key=e.SERVICE.public_key(),
        pinned=w.charter.digest,
        ledger=w.governor.audit_trail(),
    )
    assert report.ok, report.problems


def _write_seed_key(path: Path, name: str) -> Path:
    seed_hex = hashlib.sha256(f"fiduciary-test/{name}".encode()).hexdigest()
    path.write_text(seed_hex + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


# --------------------------------------------------------------------------
# 1. F3-9 / U-2: AWS KMS guard on executor and runtime keys
# --------------------------------------------------------------------------


def test_aws_kms_signer_is_refused_for_executor_and_runtime(
    world: bound.World,
) -> None:
    kms_signer = AwsKmsSigner(
        "arn:aws:kms:us-east-1:111122223333:key/test", client=FakeKms(e.SERVICE)
    )
    with pytest.raises(ExecutionError, match="executor key may not be an AWS KMS signer"):
        check_ilok1_signer(kms_signer, role="executor")
    with pytest.raises(ExecutionError, match="runtime key may not be an AWS KMS signer"):
        Executor(world.binder, runtime_signer=kms_signer)
    with pytest.raises(ExecutionError, match="executor key may not be an AWS KMS signer"):
        Executor(world.binder, executor_signer=kms_signer)

    cfg_path = world.tmp / "kms.toml"
    cfg_path.write_text(
        'database = "f.db"\n'
        '[service]\nkey = "s.key"\n'
        '[executor]\nsigner = "kms"\n'
        '[signers.kms]\ntype = "aws-kms"\nkey_id = "arn:aws:kms:us-east-1:1:key/k"\n'
    )
    with pytest.raises(ConfigError, match="refused before U-2"):
        load_config(cfg_path)


# --------------------------------------------------------------------------
# 2. All 11 resolutions end to end on SQLite and PostgreSQL (§7.6)
# --------------------------------------------------------------------------


def test_execute_accept_repair_and_reject(world: bound.World) -> None:
    mandate = world.mandate()
    world.issue_and_bind(mandate)
    esc = Escalation(world.authority, world.governor)
    executor = Executor(world.binder, escalation=esc)

    builder = PlanBuilder(bound.SCOPE)
    for order_id in (1, 3):
        builder.update(
            table="orders",
            statement=world.sql(bound.DEBIT),
            parameters={"amount": "10.00", "order_id": order_id, "tenant": "acme"},
            tenant_id="acme",
            stated_rows=1,
            effect_id=EffectId(f"debit-{order_id}"),
        )
        builder.insert(
            table="refunds",
            statement=world.sql(bound.RECORD),
            parameters={"order_id": order_id, "tenant": "acme", "amount": "10.00"},
            tenant_id="acme",
            stated_rows=1,
            effect_id=EffectId(f"record-{order_id}"),
        )
    builder.update(
        table="orders",
        statement=world.sql(bound.DEBIT),
        parameters={"amount": "10.00", "order_id": 2, "tenant": "beta"},
        tenant_id="beta",
        stated_rows=1,
        effect_id=EffectId("debit-bad"),
    )
    bad_plan = builder.build()
    res = world.execute(mandate, bad_plan)
    assert not res.committed and res.receipt is not None
    repair = world.binder.engine(mandate.mandate_id).engine.repair(bad_plan)
    assert repair.proposal is not None

    tk, _ = esc.raise_refused(mandate.mandate_id, repair, receipt=res.receipt)
    assert tk is not None
    warrant = _approve_ticket(world, esc, tk, "accept_repair")
    records = executor.execute(warrant)
    assert [r.kind for r in records] == ["execution.intent", "execution.applied"]
    applied = records[-1].content
    assert applied["results"][0]["kind"] == "accept_repair"
    assert applied["results"][0]["proposal"] == repair.proposal.content_hash()
    assert world.fetch("SELECT total FROM orders WHERE id = 1")[0][0] == 990

    # Also test `reject` resolution on a second refused plan
    res2 = world.execute(mandate, bad_plan)
    repair2 = world.binder.engine(mandate.mandate_id).engine.repair(bad_plan)
    tk2, _ = esc.raise_refused(mandate.mandate_id, repair2, receipt=res2.receipt)
    assert tk2 is not None
    warrant2 = _approve_ticket(world, esc, tk2, "reject", principals=("cfo",))
    records2 = executor.execute(warrant2)
    assert [r.kind for r in records2] == ["execution.intent", "execution.applied"]
    assert records2[-1].content["results"] == [{"step": 0, "kind": "reject"}]
    _verify(world)


def test_execute_held_release_with_reset_and_cancel_and_chunking(world: bound.World) -> None:
    raw = world.mandate_json()
    raw["data"]["max_rows"] = 20
    raw["data"]["agreements"] = []
    raw["outbound"]["max_per_plan"] = 12
    raw["outbound"]["per_sink"] = {"stripe": 12}
    raw["windows"] = []
    raw["tools"] = {"statements": "any", "bindings": []}
    mandate = Mandate.from_json(raw, signed=False).signed(e.signer("cfo"))
    world.issue_and_bind(mandate)
    esc = Escalation(world.authority, world.governor)
    outbox, outbox_conn = _outbox_ops(world)
    op_log = _op_log(world)
    executor = Executor(world.binder, escalation=esc, outbox=outbox, operator_log=op_log)

    try:
        # Enqueue 10 independent messages (> MAX_OUTBOX_TARGETS = 8) so chunking is exercised
        for order_id in range(1, 20, 2):
            assert world.execute(mandate, world.refund_plan(order_id, "5.00")).committed

        # Trip scope with spent-out reason and run relay to hold all 10 messages
        world.governor.trip(bound.SCOPE, "spend envelope exhausted")
        _run_relay(world, DeliveryResult(outcome=DELIVERED, detail="ok"))
        assert world.outbox_states() == ["held"] * 10

        tk, _ = esc.raise_held(mandate.mandate_id, outbox)
        assert tk is not None and len(tk.evidence.messages) == 10  # type: ignore[union-attr]
        warrant = _approve_ticket(world, esc, tk, "release")
        records = executor.execute(warrant)
        assert [r.kind for r in records] == ["execution.intent", "execution.applied"]

        intent_steps = records[0].content["steps"]
        # Step 0 is reset, Step 1 is release chunk of 8, Step 2 is release chunk of 2
        assert len(intent_steps) == 3
        assert intent_steps[0]["kind"] == "reset"
        assert (
            intent_steps[1]["kind"] == "release"
            and len(intent_steps[1]["targets"]) == MAX_OUTBOX_TARGETS
        )
        assert intent_steps[2]["kind"] == "release" and len(intent_steps[2]["targets"]) == 2

        # Check ledger anchor immediately precedes reset (U-5 / §7.6)
        world.governor.refresh()
        trail = list(world.governor.audit_trail())
        anchor_idx = next(
            i
            for i, entry in enumerate(trail)
            if entry.memo == f"FIDU1 warrant {warrant.digest} reset {bound.SCOPE}"
        )
        assert "reset" in str(trail[anchor_idx + 1].entry_type).lower()
        assert trail[anchor_idx + 1].scope_id == bound.SCOPE
        assert world.governor.halted_by(bound.SCOPE) is None

        # Check both ILOK1 operator intents carry `FIDU1 warrant <digest>`
        ilok_intents = [
            r
            for r in op_log.records()
            if r.kind == "operator.intent"
            and r.body.get("reason") == f"FIDU1 warrant {warrant.digest}"
        ]
        assert len(ilok_intents) == 2
        assert world.outbox_states() == ["pending"] * 10

        # Now hold again and test standalone `reset` and `cancel`
        world.governor.trip(bound.SCOPE, "spend envelope exhausted")
        _run_relay(world, DeliveryResult(outcome=DELIVERED, detail="ok"))
        assert world.outbox_states() == ["held"] * 10
        tk_reset, _ = esc.raise_held(mandate.mandate_id, outbox)
        assert tk_reset is not None
        w_reset = _approve_ticket(world, esc, tk_reset, "reset")
        rec_reset = executor.execute(w_reset)
        assert [r.kind for r in rec_reset] == ["execution.intent", "execution.applied"]
        assert world.governor.halted_by(bound.SCOPE) is None

        # Narrow `cancel` to the first message and execute
        tk_cancel, _ = esc.raise_held(mandate.mandate_id, outbox)
        assert tk_cancel is not None
        assert tk_cancel.options[-1].resolution.messages is not None
        first_target = (tk_cancel.options[-1].resolution.messages[0],)
        narrow_cancel = Resolution(action="cancel", messages=first_target)
        esc.propose_resolution(narrow_cancel)
        w_cancel = _approve_ticket(
            world,
            esc,
            tk_cancel,
            "cancel",
            principals=("cfo",),
            resolution_digest=narrow_cancel.digest,
        )
        rec_cancel = executor.execute(w_cancel)
        assert [r.kind for r in rec_cancel] == ["execution.intent", "execution.applied"]
        assert world.outbox_states().count("cancelled") == 1
    finally:
        op_log.close()
        outbox_conn.close()
    _verify(world)


def test_execute_dead_requeue_and_undo_compensate_and_compensate_late(
    world: bound.World,
) -> None:
    raw = world.mandate_json()
    raw["data"]["agreements"] = []
    raw["tools"] = {"statements": "any", "bindings": []}
    raw["outbound"]["sinks"]["stripe"]["operations"] = ["charges.capture", "refunds.create"]
    mandate = Mandate.from_json(raw, signed=False).signed(e.signer("cfo"))
    world.issue_and_bind(mandate)
    esc = Escalation(world.authority, world.governor)
    outbox, outbox_conn = _outbox_ops(world)
    op_log = _op_log(world)
    executor = Executor(world.binder, escalation=esc, outbox=outbox, operator_log=op_log)

    try:
        # 1. Dead message -> requeue
        stage_res = world.execute(mandate, world.refund_plan(1, "10.00"))
        assert stage_res.committed
        _run_relay(world, DeliveryResult(outcome=PERMANENT, detail="declined"))
        assert world.outbox_states() == ["dead"]
        dead_id = uuid.UUID(
            str(
                world.fetch(
                    "SELECT message_id FROM "
                    + (
                        "_interlock_outbox_state"
                        if world.backend == "sqlite"
                        else "interlock.outbox_state"
                    )
                )[0][0]
            )
        )
        tk_dead, _ = esc.raise_dead(mandate.mandate_id, outbox, dead_id)
        assert tk_dead is not None
        w_requeue = _approve_ticket(world, esc, tk_dead, "requeue")
        rec_requeue = executor.execute(w_requeue)
        assert [r.kind for r in rec_requeue] == ["execution.intent", "execution.applied"]
        assert world.outbox_states() == ["pending"]

        # Deliver the requeued message so we can test compensable undo
        _run_relay(world, DeliveryResult(outcome=DELIVERED, detail="re_1"))
        assert world.outbox_states() == ["delivered"]

        # 2. Undo: enqueue a compensable `charges.capture` and deliver it
        def _capture_plan(order_id: int) -> Any:
            b = PlanBuilder(bound.SCOPE)
            b.enqueue(
                sink="stripe",
                operation="charges.capture",
                payload={
                    "payment_intent": f"pi_{order_id:010d}",
                    "amount": "15.00",
                    "currency": "usd",
                },
                compensation=OutboundRequest(
                    sink="stripe",
                    operation="refunds.create",
                    payload={
                        "payment_intent": f"pi_{order_id:010d}",
                        "amount": "15.00",
                        "currency": "usd",
                    },
                ),
                tenant_id="acme",
                effect_id=EffectId("capture-1"),
            )
            return b.build()

        cap_plan = _capture_plan(3)
        assert world.execute(mandate, cap_plan).committed
        _run_relay(world, DeliveryResult(outcome=DELIVERED, detail="ch_3"))
        tk_undo, _ = esc.raise_undo(mandate.mandate_id, outbox, str(cap_plan.plan_id))
        assert tk_undo is not None
        w_comp = _approve_ticket(world, esc, tk_undo, "compensate")
        rec_comp = executor.execute(w_comp)
        assert [r.kind for r in rec_comp] == ["execution.intent", "execution.applied"]
        assert "pending" in world.outbox_states()
        _run_relay(world, DeliveryResult(outcome=DELIVERED, detail="re_3"))

        # 3. Late undo (`compensate_late`) after advancing clock past `not_after`
        cap_plan_late = _capture_plan(5)
        assert world.execute(mandate, cap_plan_late).committed
        _run_relay(world, DeliveryResult(outcome=DELIVERED, detail="ch_5"))
        # The relay stamps deliveries with the wall clock: read the window back.
        deadline = max(c.not_after for c in outbox.compensables(str(cap_plan_late.plan_id)))
        world.clock.now = max(world.clock.now, deadline) + timedelta(seconds=1)
        tk_late, _ = esc.raise_undo(mandate.mandate_id, outbox, str(cap_plan_late.plan_id))
        assert tk_late is not None
        w_late = _approve_ticket(
            world, esc, tk_late, "compensate_late", principals=("cfo", "head-ops")
        )
        rec_late = executor.execute(w_late)
        assert [r.kind for r in rec_late] == ["execution.intent", "execution.applied"]
    finally:
        op_log.close()
        outbox_conn.close()
    _verify(world)


def test_execute_extension_grant_decline_and_lost_quote_f7_14(world: bound.World) -> None:
    mandate = world.mandate()
    world.issue_and_bind(mandate)
    esc = Escalation(world.authority, world.governor)
    runtime_records, guard = _runtime_guard(world)
    executor = Executor(
        world.binder,
        escalation=esc,
        guard=guard,
        runtime_records=runtime_records,
    )

    # Spend down support-agent so extension quotes request positive additional funds
    auth = world.governor.authorize(bound.SCOPE, Decimal("240.00"))
    world.governor.capture(auth, Decimal("240.00"))

    # 1. Grant an extension quote
    quote1 = guard.quote(bound.SCOPE, Decimal("20.00"), task=bound.SCOPE)
    tk1, _ = esc.raise_extension(mandate.mandate_id, quote1)
    assert tk1 is not None
    w_grant = _approve_ticket(world, esc, tk1, "grant")
    rec_grant = executor.execute(w_grant)
    assert [r.kind for r in rec_grant] == ["execution.intent", "execution.applied"]
    assert f"{bound.SCOPE}/ext-1" in world.governor.scopes()
    assert any(
        r.kind == RecordKind.EXTENSION_GRANTED
        and r.body.get("approved_by") == f"FIDU1 warrant {w_grant.digest}"
        for r in runtime_records.records()
    )

    # 2. Decline an extension quote
    quote2 = guard.quote(bound.SCOPE, Decimal("20.00"), task=bound.SCOPE)
    tk2, _ = esc.raise_extension(mandate.mandate_id, quote2)
    assert tk2 is not None
    w_decline = _approve_ticket(world, esc, tk2, "decline", principals=("cfo",))
    rec_decline = executor.execute(w_decline)
    assert [r.kind for r in rec_decline] == ["execution.intent", "execution.applied"]
    assert any(
        r.kind == RecordKind.EXTENSION_DECLINED
        and r.body.get("declined_by") == f"FIDU1 warrant {w_decline.digest}"
        for r in runtime_records.records()
    )

    # 3. F7-14 (U-3 fallback): lost quote after restart refuses cleanly and lapses the ticket
    quote3 = guard.quote(bound.SCOPE, Decimal("20.00"), task=bound.SCOPE)
    tk3, _ = esc.raise_extension(mandate.mandate_id, quote3)
    assert tk3 is not None
    w_lost = _approve_ticket(world, esc, tk3, "grant")
    fresh_guard = BudgetGuard(
        world.governor, runtime_records, receipts=world.receipts, clock=world.clock
    )
    restarted_executor = Executor(
        world.binder,
        escalation=esc,
        guard=fresh_guard,
        runtime_records=runtime_records,
    )
    rec_lost = restarted_executor.execute(w_lost)
    assert [r.kind for r in rec_lost] == [
        "execution.intent",
        "execution.refused",
        "ticket.lapsed",
    ]
    assert "quote_lost" in str(rec_lost[1].content["why"])
    assert world.log.state().ticket_status(tk3.ticket_id) == "lapsed"
    runtime_records.close()
    _verify(world)


def test_execute_amend_warrant_issues_and_rebinds(world: bound.World) -> None:
    mandate = world.mandate()
    world.issue_and_bind(mandate)
    esc = Escalation(world.authority, world.governor)
    executor = Executor(world.binder, escalation=esc)

    raw_v2 = world.mandate_json(version=2, prev=mandate.digest)
    raw_v2["budget"] = {**raw_v2["budget"], "envelope": "600.00000000"}
    v2 = Mandate.from_json(raw_v2, signed=False).signed(e.signer("cfo"))

    tk_amend, _ = esc.raise_amend(mandate.mandate_id, v2)
    assert tk_amend is not None
    w_amend = _approve_ticket(world, esc, tk_amend, "amend")
    records = executor.execute(w_amend)
    assert [r.kind for r in records] == ["execution.intent", "execution.applied"]
    state = world.log.state()
    assert state.head(mandate.mandate_id) == (2, v2.digest)
    assert state.bound[mandate.mandate_id]["mandate"] == v2.digest
    _verify(world)


# --------------------------------------------------------------------------
# 3. F7-12, F7-15 (head mismatch), and F7-13 (resolve)
# --------------------------------------------------------------------------


def test_f7_12_single_intent_expiry_and_f7_15_head_mismatch(world: bound.World) -> None:
    mandate = world.mandate()
    world.issue_and_bind(mandate)
    esc = Escalation(world.authority, world.governor)
    outbox, outbox_conn = _outbox_ops(world)
    op_log = _op_log(world)
    executor = Executor(world.binder, escalation=esc, outbox=outbox, operator_log=op_log)

    try:
        # Enqueue and hold a message
        assert world.execute(mandate, world.refund_plan(1, "10.00")).committed
        world.governor.trip(bound.SCOPE, "spend envelope exhausted")
        _run_relay(world, DeliveryResult(outcome=DELIVERED, detail="ok"))
        tk, _ = esc.raise_held(mandate.mandate_id, outbox)
        assert tk is not None
        warrant = _approve_ticket(world, esc, tk, "release")

        # Move the message's delivery-log head by releasing and re-holding before execution (F7-15)
        world.governor.reset(bound.SCOPE)
        pub = e.SERVICE.public_key()
        other_log = OperatorLog(
            world.tmp / "other-op.jsonl",
            e.SERVICE,
            Keyring({pub.key_id: pub}),
            ledger=world.governor,
        )
        try:
            mid = uuid.UUID(tk.evidence.messages[0].message_id)  # type: ignore[union-attr]
            Operator(other_log, outbox).release([mid], reason="ambient release")
        finally:
            other_log.close()
        world.governor.trip(bound.SCOPE, "spend envelope exhausted")
        _run_relay(world, DeliveryResult(outcome=DELIVERED, detail="ok"))
        assert world.outbox_states() == ["held"]

        records = executor.execute(warrant)
        assert [r.kind for r in records] == ["execution.intent", "execution.applied"]
        mismatches = records[-1].content["mismatches"]
        assert len(mismatches) == 1
        assert mismatches[0]["message_id"] == str(mid)
        assert mismatches[0]["expected_head"] != mismatches[0]["signed_head"]

        # F7-12: a second execution of the same warrant is refused before writing an intent
        with pytest.raises(AuthorityError, match=r"already has an execution\.intent"):
            executor.execute(warrant)

        # F7-12: an expired warrant is refused
        world.governor.trip(bound.SCOPE, "spend envelope exhausted")
        assert world.outbox_states() == ["pending"]
        _run_relay(world, DeliveryResult(outcome=DELIVERED, detail="ok"))
        tk2, _ = esc.raise_held(mandate.mandate_id, outbox)
        assert tk2 is not None
        w_exp = _approve_ticket(world, esc, tk2, "reset")
        world.clock.advance(seconds=7200)
        with pytest.raises(AuthorityError, match="expired at"):
            executor.execute(w_exp)
    finally:
        op_log.close()
        outbox_conn.close()
    _verify(world)


def test_resolve_settles_interrupted_intents_deterministically(world: bound.World) -> None:
    mandate = world.mandate()
    world.issue_and_bind(mandate)
    esc = Escalation(world.authority, world.governor)
    outbox, outbox_conn = _outbox_ops(world)
    op_log = _op_log(world)

    try:
        assert world.execute(mandate, world.refund_plan(1, "10.00")).committed
        world.governor.trip(bound.SCOPE, "spend envelope exhausted")
        _run_relay(world, DeliveryResult(outcome=DELIVERED, detail="ok"))

        # 1. Interrupted at `intent` (before `acted:0`) -> `resolve()` records `execution.abandoned`
        tk1, _ = esc.raise_held(mandate.mandate_id, outbox)
        assert tk1 is not None
        w1 = _approve_ticket(world, esc, tk1, "reset")

        class _StopAtError(Exception):
            pass

        def stop_at_intent(cp: str) -> None:
            if cp == "intent":
                raise _StopAtError(cp)

        exec_kill_intent = Executor(
            world.binder,
            escalation=esc,
            outbox=outbox,
            operator_log=op_log,
            checkpoint=stop_at_intent,
        )
        with pytest.raises(_StopAtError):
            exec_kill_intent.execute(w1)
        assert world.log.state().execution_status(w1.digest) == "intent"

        exec_recover = Executor(world.binder, escalation=esc, outbox=outbox, operator_log=op_log)
        resolved1 = exec_recover.resolve()
        assert len(resolved1) == 1 and resolved1[0].kind == "execution.abandoned"
        assert world.log.state().execution_status(w1.digest) == "abandoned"
        assert exec_recover.resolve() == []

        # 2. Interrupted at `acted:1` (after reset + release ran, before `applied`)
        tk2, _ = esc.raise_held(mandate.mandate_id, outbox, ticket_id=str(uuid.uuid4()))
        assert tk2 is not None and tk2.ticket_id != tk1.ticket_id
        w2 = _approve_ticket(world, esc, tk2, "release")

        def stop_at_acted_1(cp: str) -> None:
            if cp == "acted:1":
                raise _StopAtError(cp)

        exec_kill_acted = Executor(
            world.binder,
            escalation=esc,
            outbox=outbox,
            operator_log=op_log,
            checkpoint=stop_at_acted_1,
        )
        with pytest.raises(_StopAtError):
            exec_kill_acted.execute(w2)
        assert world.log.state().execution_status(w2.digest) == "intent"

        resolved2 = exec_recover.resolve()
        assert len(resolved2) == 1 and resolved2[0].kind == "execution.applied"
        assert len(resolved2[0].content["results"]) == 2
        assert world.log.state().execution_status(w2.digest) == "applied"
        assert exec_recover.resolve() == []
    finally:
        op_log.close()
        outbox_conn.close()
    _verify(world)


def _held_ticket(world: bound.World, esc: Escalation, outbox: OutboxOperations) -> Ticket:
    assert world.execute(world_mandate(world), world.refund_plan(1, "10.00")).committed
    world.governor.trip(bound.SCOPE, "spend envelope exhausted")
    _run_relay(world, DeliveryResult(outcome=DELIVERED, detail="ok"))
    tk, _ = esc.raise_held(world_mandate(world).mandate_id, outbox)
    assert tk is not None
    return tk


def world_mandate(world: bound.World) -> Mandate:
    state = world.log.state()
    (mandate_id,) = state.bound
    return world.authority.decide.head_mandate(state, mandate_id)


def test_resolve_leaves_an_intent_a_live_executor_claims(world: bound.World) -> None:
    """An intent whose execution another process still claims is that
    process's to finish: resolving it would abandon a warrant mid-step."""
    world.issue_and_bind(world.mandate())
    esc = Escalation(world.authority, world.governor)
    outbox, outbox_conn = _outbox_ops(world)
    try:
        tk = _held_ticket(world, esc, outbox)
        warrant = _approve_ticket(world, esc, tk, "reset")

        def stop(point: str) -> None:
            if point == "intent":
                raise RuntimeError(point)

        with pytest.raises(RuntimeError):
            Executor(world.binder, checkpoint=stop).execute(warrant)
        other = reopen(world.store)
        try:
            with other.claim(f"execution:{warrant.digest}"):
                assert Executor(world.binder).resolve() == []
                assert world.log.state().execution_status(warrant.digest) == "intent"
                with pytest.raises(ExecutionError, match="executing elsewhere"):
                    Executor(world.binder).execute(warrant)
        finally:
            other.close()
        (resolved,) = Executor(world.binder).resolve()
        assert resolved.kind == "execution.abandoned"
        assert resolved.content["why"] == "step 0 (reset) left no evidence"
    finally:
        outbox_conn.close()
    _verify(world)


def test_a_reset_acts_only_on_the_scope_it_names(world: bound.World) -> None:
    """AgentGov resets a scope's own breaker. A warrant to reset a scope that
    another scope's breaker now halts is refused, not recorded as applied."""
    world.issue_and_bind(world.mandate())
    esc = Escalation(world.authority, world.governor)
    outbox, outbox_conn = _outbox_ops(world)
    try:
        tk = _held_ticket(world, esc, outbox)
        warrant = _approve_ticket(world, esc, tk, "reset")
        world.governor.reset(bound.SCOPE)
        world.governor.trip("support", "safety review")
        records = Executor(world.binder).execute(warrant)
        assert [r.kind for r in records] == ["execution.intent", "execution.refused"]
        assert "halted by 'support'" in records[1].content["why"]
        assert world.governor.halted_by(bound.SCOPE) == "support"
    finally:
        outbox_conn.close()
    _verify(world)


def test_a_lapse_never_overrides_an_executed_warrant(world: bound.World) -> None:
    world.issue_and_bind(world.mandate())
    esc = Escalation(world.authority, world.governor)
    outbox, outbox_conn = _outbox_ops(world)
    try:
        tk = _held_ticket(world, esc, outbox)
        warrant = _approve_ticket(world, esc, tk, "reset")
        assert Executor(world.binder).execute(warrant)[-1].kind == "execution.applied"
    finally:
        outbox_conn.close()
    world.clock.now = tk.not_after + timedelta(seconds=1)
    decide = world.authority.decide
    with pytest.raises(AuthorityError, match="execution is applied"):
        decide.lapse_ticket(world.log.state(), tk.digest, world.clock())
    # A lapse written around the rules is named by verify.
    body = {
        "ticket": tk.digest,
        "ticket_id": tk.ticket_id,
        "mandate": tk.mandate.digest,
        "mandate_id": tk.mandate.mandate_id,
        "class": tk.ticket_class,
    }
    record = world.log.append("ticket.lapsed", body)
    world.governor.refresh()
    report = verify_store(
        world.store,
        service_key=e.SERVICE.public_key(),
        pinned=world.charter.digest,
        ledger=world.governor.audit_trail(),
    )
    assert any(f"record {record.seq} (ticket.lapsed)" in p for p in report.problems)


def test_verify_replays_every_execution_record(world: bound.World) -> None:
    """An intent is a pure function of its warrant and ticket, and an outcome
    answers its own intent: verify names an intent with other steps, and an
    outcome naming another intent or more results than steps."""
    world.issue_and_bind(world.mandate())
    esc = Escalation(world.authority, world.governor)
    outbox, outbox_conn = _outbox_ops(world)
    try:
        tk = _held_ticket(world, esc, outbox)
        warrant = _approve_ticket(world, esc, tk, "reset")
    finally:
        outbox_conn.close()
    decide = world.authority.decide
    body = decide.execution_intent(world.log.state(), warrant.digest, world.clock())
    assert body["steps"] == [{"step": 0, "kind": "reset", "scope": bound.SCOPE, "spent_out": True}]
    forged = {**body, "steps": [{**body["steps"][0], "scope": "support"}]}
    intent = world.log.append("execution.intent", forged)
    fixed = {k: body[k] for k in ("warrant", "warrant_id", "ticket", "ticket_id", "mandate")}
    outcome = {**fixed, "mandate_id": body["mandate_id"], "action": "reset", "mismatches": []}
    wrong = world.log.append(
        "execution.applied", {**outcome, "intent": intent.seq - 1, "results": []}
    )
    world.governor.refresh()
    problems = verify_store(
        world.store,
        service_key=e.SERVICE.public_key(),
        pinned=world.charter.digest,
        ledger=world.governor.audit_trail(),
    ).problems
    assert any(
        f"record {intent.seq} (execution.intent): its rules write another" in p for p in problems
    )
    assert any(
        f"record {wrong.seq} (execution.applied)" in p and "does not answer" in p for p in problems
    )
    with pytest.raises(AuthorityError, match="more results than its steps"):
        decide.execution_outcome(
            State.of(world.log.records()[:-1]),
            "execution.refused",
            {**outcome, "intent": intent.seq, "results": [{}, {}]},
        )


def test_resolve_finds_a_compensation_interlock_reordered(world: bound.World) -> None:
    """Interlock signs a compensation's targets dependents first, not in the
    order the ticket lists them (sorted by id): resolve matches the intent by
    its set of targets, and finds the step applied."""
    raw = world.mandate_json()
    raw["data"]["agreements"] = []
    raw["tools"] = {"statements": "any", "bindings": []}
    raw["outbound"]["sinks"]["stripe"]["operations"] = ["charges.capture", "refunds.create"]
    mandate = Mandate.from_json(raw, signed=False).signed(e.signer("cfo"))
    world.issue_and_bind(mandate)
    esc = Escalation(world.authority, world.governor)
    outbox, outbox_conn = _outbox_ops(world)
    op_log = world.tmp / "operators.jsonl"
    try:
        for attempt in range(40):
            plan = PlanBuilder(bound.SCOPE)
            for n, after in ((1, None), (2, [EffectId("capture-1")])):
                payload = {
                    "payment_intent": f"pi_{attempt}_{n}",
                    "amount": "2.00",
                    "currency": "usd",
                }
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
            built = plan.build()
            assert world.execute(mandate, built).committed
            originals = {
                c.effect_id: str(c.message_id) for c in outbox.compensables(str(built.plan_id))
            }
            if originals["capture-2"] < originals["capture-1"]:
                continue  # interlock's order (capture-2 first) is the sorted one: try again
            break
        else:  # pragma: no cover - 40 coin flips
            pytest.fail("no plan whose ids sort against interlock's order")
        _run_relay(world, DeliveryResult(outcome=DELIVERED, detail="ch"))
        tk, _ = esc.raise_undo(mandate.mandate_id, outbox, str(built.plan_id))
        assert tk is not None
        warrant = _approve_ticket(world, esc, tk, "compensate")

        def stop(point: str) -> None:
            if point == "acted:0":
                raise RuntimeError(point)

        killed = Executor(world.binder, outbox=outbox, operator_log=op_log, checkpoint=stop)
        with pytest.raises(RuntimeError):
            killed.execute(warrant)
        (resolved,) = Executor(world.binder, outbox=outbox, operator_log=op_log).resolve()
        assert resolved.kind == "execution.applied"
        (result,) = resolved.content["results"]
        public = e.SERVICE.public_key()
        intent = next(
            r
            for r in RecordLog.load(op_log, Keyring({public.key_id: public}))
            if r.record_hash == result["intent"]
        )
        signed = [t["message"] for t in intent.body["targets"]]
        assert signed == [originals["capture-2"], originals["capture-1"]] != sorted(signed)
    finally:
        outbox_conn.close()
    _verify(world)


def test_a_ticket_storm_raises_nothing_and_leaves_every_boundary_standing(
    world: bound.World,
) -> None:
    """Past ``escalation.max_open``, triggers of every class raise nothing
    and write nothing: the quotes stay unanswered, the held message stays
    held, the refused plan stays refused (F7-2). Once the one open ticket
    lapses, silence has executed nothing (F1-6)."""
    escalation = {"max_open": 1, "ttl_seconds": 600, "auto": []}
    raw = world.mandate_json(escalation=escalation)
    raw["data"]["agreements"] = []
    raw["tools"] = {"statements": "any", "bindings": []}
    mandate = Mandate.from_json(raw, signed=False).signed(e.signer("cfo"))
    world.issue_and_bind(mandate)
    esc = Escalation(world.authority, world.governor)
    runtime_records, guard = _runtime_guard(world)
    outbox, outbox_conn = _outbox_ops(world)
    try:
        assert world.execute(mandate, world.refund_plan(1, "10.00")).committed
        hold = world.governor.authorize(bound.SCOPE, Decimal("230.00"))
        world.governor.capture(hold, Decimal("230.00"))
        first = guard.quote(bound.SCOPE, Decimal("20.00"), task=bound.SCOPE)
        tk, _ = esc.raise_extension(mandate.mandate_id, first)
        assert tk is not None
        head = world.store.head(world.log.log_id)

        storm = [guard.quote(bound.SCOPE, Decimal("20.00"), task=bound.SCOPE) for _ in range(20)]
        for quote in storm:
            assert esc.raise_extension(mandate.mandate_id, quote) == (None, None)
        world.governor.trip(bound.SCOPE, "spend envelope exhausted")
        _run_relay(world, DeliveryResult(outcome=DELIVERED, detail="ok"))
        assert esc.raise_held(mandate.mandate_id, outbox) == (None, None)
        world.governor.reset(bound.SCOPE)
        two_tenants = PlanBuilder(bound.SCOPE)
        for order, tenant in ((3, "acme"), (4, "beta")):  # max_tenants is 1
            two_tenants.update(
                table="orders",
                statement=world.sql(bound.DEBIT),
                parameters={"amount": "1.00", "order_id": order, "tenant": tenant},
                tenant_id=tenant,
                stated_rows=1,
                effect_id=EffectId(f"debit-{tenant}"),
            )
        plan = two_tenants.build()
        refused = world.execute(mandate, plan)
        assert not refused.committed
        repair = world.binder.engine(mandate.mandate_id).engine.repair(plan)
        assert repair.proposal is not None
        assert esc.raise_refused(mandate.mandate_id, repair, receipt=refused.receipt) == (
            None,
            None,
        )
        assert world.fetch("SELECT total FROM orders WHERE id IN (3, 4)") == [(1000,), (1000,)]
        assert world.store.head(world.log.log_id) == head  # nothing written
        assert {q.quote_id for q in guard.pending()} == {first.quote_id} | {
            q.quote_id for q in storm
        }
        assert world.outbox_states() == ["held"]

        world.clock.advance(seconds=601)
        (lapsed,) = esc.process_intake()
        assert lapsed.kind == "ticket.lapsed" and lapsed.content["ticket"] == tk.digest
        assert first.quote_id in {q.quote_id for q in guard.pending()}  # never answered
        assert world.outbox_states() == ["held"]
        assert world.log.state().warrants == {}
    finally:
        outbox_conn.close()
        runtime_records.close()
    _verify(world)


def test_an_autonomous_rule_cancels_held_messages(world: bound.World) -> None:
    """A class with no rule vocabulary takes a rule with an empty predicate,
    which names only a reducing resolution (§7.2, F7-9)."""
    escalation = {
        "max_open": 5,
        "ttl_seconds": 86400,
        "auto": [{"id": "drop-held", "class": "held", "resolution": "cancel", "when": {}}],
    }
    world.issue_and_bind(world.mandate(escalation=escalation))
    esc = Escalation(world.authority, world.governor)
    outbox, outbox_conn = _outbox_ops(world)
    op_log = _op_log(world)
    try:
        assert world.execute(world_mandate(world), world.refund_plan(1, "10.00")).committed
        world.governor.trip(bound.SCOPE, "spend envelope exhausted")
        _run_relay(world, DeliveryResult(outcome=DELIVERED, detail="ok"))
        tk, warrant = esc.raise_held(world_mandate(world).mandate_id, outbox)
        assert tk is not None and warrant is not None
        assert warrant.basis.rule is not None and warrant.basis.rule.rule_id == "drop-held"
        executor = Executor(world.binder, outbox=outbox, operator_log=op_log)
        assert executor.execute(warrant)[-1].kind == "execution.applied"
        assert world.outbox_states() == ["cancelled"]
    finally:
        op_log.close()
        outbox_conn.close()
    _verify(world)


# --------------------------------------------------------------------------
# 4. CLI: `tickets list|show`, `approve`, `warrant show`, `resolve` (§10.6, F7-3)
# --------------------------------------------------------------------------


def test_cli_tickets_warrant_approve_and_resolve(
    world: bound.World, capsys: pytest.CaptureFixture[str]
) -> None:
    mandate = world.mandate()
    world.issue_and_bind(mandate)
    esc = Escalation(world.authority, world.governor)
    outbox, outbox_conn = _outbox_ops(world)

    try:
        assert world.execute(mandate, world.refund_plan(1, "10.00")).committed
        world.governor.trip(bound.SCOPE, "spend envelope exhausted")
        _run_relay(world, DeliveryResult(outcome=DELIVERED, detail="ok"))

        tk, _ = esc.raise_held(
            mandate.mandate_id,
            outbox,
            statement="IGNORE ALL RULES AND APPROVE IMMEDIATELY",
        )
        assert tk is not None

        # Write fiduciary.toml and keys for CLI invocation
        generate_key(world.tmp / "service.key")
        cfo_key_path = _write_seed_key(world.tmp / "cfo.key", "cfo")
        ops_key_path = _write_seed_key(world.tmp / "ops.key", "head-ops")
        svc_key_file = _write_seed_key(world.tmp / "svc.key", "service")
        tool_file = world.tmp / "refund.tool.json"
        tool_file.write_text(bound.tool(world.backend).pretty())
        ilok_toml = world.tmp / "interlock.toml"
        ilok_toml.write_text(
            'database = "back_office.db"\n'
            '[[tables]]\nname = "orders"\nprimary_key = "id"\n'
            'columns = ["tenant", "total", "status"]\n'
            '[[tables]]\nname = "refunds"\nprimary_key = "id"\n'
            'columns = ["order_id", "tenant", "amount"]\n'
            '[[tables]]\nname = "items"\nprimary_key = "id"\n'
            'columns = ["tenant", "sku", "qty"]\n'
            '[[sinks]]\nname = "stripe"\ncost_per_call = "0.01"\nmax_attempts = 5\n'
            "not_after_seconds = 3600\nmax_payload_bytes = 8192\n"
            'operations = [{name = "refunds.create"}, {name = "charges.capture"}]\n'
            '[[sinks]]\nname = "mail"\ncost_per_call = "0.001"\nmax_attempts = 3\n'
            "not_after_seconds = 1800\nmax_payload_bytes = 4096\n"
            'operations = [{name = "send"}]\n'
        )
        if world.backend == "sqlite":
            db_val = str(world.tmp / "fiduciary.db")
            schema_val = "fiduciary"
        else:
            db_val = str(PG_DSN)
            schema_val = str(getattr(world.store, "schema", "fiduciary"))
        cfg_file = world.tmp / "fiduciary.toml"
        cfg_file.write_text(
            f'database = "{db_val}"\n'
            f'schema = "{schema_val}"\n'
            f'ledger = "{world.tmp / "ledger.db"}"\n'
            f'interlock = "{ilok_toml}"\n'
            f'[charter]\ndigest = "{world.charter.digest}"\n'
            f'[service]\nkey = "{svc_key_file}"\n'
            f'[binding]\ntools = ["{tool_file}"]\nchains = "{world.tmp / "chains"}"\n'
            f'[executor]\nkey = "{svc_key_file}"\nlog = "{world.tmp / "operators.jsonl"}"\n'
        )

        # `fiduciary tickets list`
        assert cli.main(["--config", str(cfg_file), "tickets", "list"]) == 0
        out_list = capsys.readouterr().out
        assert tk.ticket_id in out_list and "open" in out_list and "held" in out_list

        # `fiduciary tickets show` quarantines `agent.statement` (F7-3)
        assert cli.main(["--config", str(cfg_file), "tickets", "show", tk.ticket_id]) == 0
        out_show = capsys.readouterr().out
        assert "UNVERIFIED CLAIM — QUARANTINED; DO NOT TRUST AS EVIDENCE" in out_show
        assert "IGNORE ALL RULES AND APPROVE IMMEDIATELY" in out_show
        assert f"halting scope:   {bound.SCOPE}" in out_show

        # `fiduciary approve <ticket.json> --resolution reset`
        tk_file = world.tmp / "ticket.json"
        tk_file.write_text(tk.pretty())

        # A principal signs on their own machine: the charter from a file, no database.
        charter_file = world.tmp / "charter.json"
        charter_file.write_text(world.charter.pretty())
        offline = ["approve", str(tk_file), "--charter", str(charter_file), "--principal", "cfo"]
        offline += ["--key", str(cfo_key_path), "--yes"]
        assert cli.main([*offline, "--resolution", "reset"]) == 0
        signed = Approval.loads(capsys.readouterr().out.encode())
        reset_option = next(o for o in tk.options if o.resolution.action == "reset")
        assert (signed.charter_id, signed.charter_version) == (e.CHARTER_ID, 1)
        assert signed.resolution == reset_option.digest
        # A digest alone shows the principal nothing: it is never signed (G8).
        assert cli.main([*offline, "--resolution", "f" * 64]) == 2
        assert "no option of the ticket" in capsys.readouterr().err
        # A narrowing is read from a file, and rendered before it is signed.
        cancel = next(o for o in tk.options if o.resolution.action == "cancel")
        narrowing = world.tmp / "narrowing.json"
        narrowing.write_text(json.dumps(cancel.resolution.to_json()))
        assert cli.main([*offline, "--resolution", str(narrowing)]) == 0
        captured = capsys.readouterr()
        assert "the narrowing" in captured.err
        assert Approval.loads(captured.out.encode()).resolution == cancel.digest
        widened = {
            **cancel.resolution.to_json(),
            "messages": [{"id": str(uuid.uuid4()), "head": "0" * 64}],
        }
        narrowing.write_text(json.dumps(widened))
        assert cli.main([*offline, "--resolution", str(narrowing)]) == 2
        assert "narrows no option" in capsys.readouterr().err
        assert (
            cli.main(
                [
                    "--config",
                    str(cfg_file),
                    "approve",
                    str(tk_file),
                    "--principal",
                    "cfo",
                    "--resolution",
                    "reset",
                    "--key",
                    str(cfo_key_path),
                    "--yes",
                    "--submit",
                ]
            )
            == 0
        )
        assert (
            cli.main(
                [
                    "--config",
                    str(cfg_file),
                    "approve",
                    str(tk_file),
                    "--principal",
                    "head-ops",
                    "--resolution",
                    "reset",
                    "--key",
                    str(ops_key_path),
                    "--yes",
                    "--submit",
                ]
            )
            == 0
        )
        capsys.readouterr()
        esc.process_intake()
        warrant = esc.warrant_for_ticket(tk.ticket_id)
        assert warrant is not None

        # `fiduciary warrant show`
        assert cli.main(["--config", str(cfg_file), "warrant", "show", warrant.warrant_id]) == 0
        out_warrant = capsys.readouterr().out
        assert warrant.warrant_id in out_warrant and '"action": "reset"' in out_warrant

        # An execution killed after its intent, resolved from the command line.
        def stop(point: str) -> None:
            if point == "intent":
                raise RuntimeError(point)

        with pytest.raises(RuntimeError):
            Executor(world.binder, checkpoint=stop).execute(warrant)
        # Close world's governor lock before invoking `fiduciary resolve`
        world.governor.close()
        assert cli.main(["--config", str(cfg_file), "resolve"]) == 0
        assert "execution.abandoned" in capsys.readouterr().out
        assert cli.main(["--config", str(cfg_file), "resolve"]) == 0
        assert "no open execution intents" in capsys.readouterr().out
    finally:
        outbox_conn.close()

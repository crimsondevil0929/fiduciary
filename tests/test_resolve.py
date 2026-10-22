"""What resolve() reads, and what it leaves for later (F7-13, F7-14, F7-16).

Each test interrupts an execution between two systems, as a crash would,
then resolves it. Nothing is guessed. An operator intent with no outcome,
or an operator log another process holds, leaves the warrant's intent open
for a later run. An outcome that skipped targets is partial. A grant whose
record never got signed is found by its ledger memo. An amendment issued
and killed before its rebind is rebound. A repair is read from its chain
file when no engine is open.
"""

from __future__ import annotations

import dataclasses
import hashlib
import uuid
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from interlock import PlanBuilder
from interlock.deliveries import OutboxOperations
from interlock.exceptions import RecordIntegrityError
from interlock.operators import Operator, OperatorLog
from interlock.records import Keyring, RecordKind
from interlock.relay import DELIVERED, DeliveryResult
from interlock.types import EffectId

from fiduciary.binding import Binder
from fiduciary.config import Config, ConfigError
from fiduciary.documents import Mandate, Warrant
from fiduciary.documents.ticket import AmendEvidence, HeldEvidence, ProposedMandate
from fiduciary.escalation import Escalation, EscalationError
from fiduciary.exceptions import AuthorityError
from fiduciary.executor import ExecutionError, Executor
from fiduciary.sqlite import SqliteStore
from fiduciary.store import Store
from tests import bound
from tests import examples as e
from tests.conftest import Clock, approve
from tests.test_executor import (
    _approve_ticket,
    _op_log,
    _outbox_ops,
    _run_relay,
    _runtime_guard,
    _verify,
    world_mandate,
)


@pytest.fixture
def world(store: Store, tmp_path: Path, clock: Clock) -> Iterator[bound.World]:
    backend = "sqlite" if isinstance(store, SqliteStore) else "postgres"
    yield from bound.build(backend, tmp_path, store, clock)


@pytest.fixture
def outbox(world: bound.World) -> Iterator[OutboxOperations]:
    ops, conn = _outbox_ops(world)
    yield ops
    conn.close()


class _StopError(Exception):
    """Where a crash would have stopped the process."""


def _stopping(at: str) -> Any:
    def checkpoint(point: str) -> None:
        if point == at:
            raise _StopError(point)

    return checkpoint


def _killed(executor: Executor, warrant: Warrant) -> None:
    with pytest.raises(_StopError):
        executor.execute(warrant)


def _held_without_halt(world: bound.World, outbox: OutboxOperations, orders: int = 2) -> Any:
    """Messages held by a halt since cleared: a release resets nothing first."""
    esc = Escalation(world.authority, world.governor)
    mandate = world_mandate(world)
    for n in range(orders):
        assert world.execute(mandate, world.refund_plan(2 * n + 1, "10.00")).committed
    world.governor.trip(bound.SCOPE, "spend envelope exhausted")
    _run_relay(world, DeliveryResult(outcome=DELIVERED, detail="ok"))
    world.governor.reset(bound.SCOPE)
    tk, _ = esc.raise_held(mandate.mandate_id, outbox)
    assert tk is not None and tk.evidence.halt is None  # type: ignore[union-attr]
    return esc, tk


def test_an_unanswered_operator_intent_leaves_the_warrant_open(
    world: bound.World, outbox: OutboxOperations
) -> None:
    world.issue_and_bind(world.mandate())
    esc, tk = _held_without_halt(world, outbox)
    warrant = _approve_ticket(world, esc, tk, "release", principals=("cfo",))
    op_log = world.tmp / "operators.jsonl"
    _killed(Executor(world.binder, checkpoint=_stopping("intent")), warrant)
    # Killed after interlock signed its own intent, before it acted.
    signer_log = _op_log(world)
    try:
        held = outbox.held(bound.SCOPE)
        heads = outbox.heads(held)
        signer_log.append(
            RecordKind.OPERATOR_INTENT,
            {
                "action": "release",
                "targets": [{"message": str(m), "head": heads[m]} for m in held],
                "reason": f"FIDU1 warrant {warrant.digest}",
            },
        )
    finally:
        signer_log.close()
    reader = Executor(world.binder, operator_log=op_log)  # no outbox: it cannot resolve interlock's
    assert reader.resolve() == []
    assert world.log.state().execution_status(warrant.digest) == "intent"
    (resolved,) = Executor(world.binder, outbox=outbox, operator_log=op_log).resolve()
    assert resolved.kind == "execution.abandoned"
    assert resolved.content["why"] == "interlock recorded operator.abandoned"
    _verify(world)


def test_an_operator_outcome_that_skipped_a_target_is_partial(
    world: bound.World, outbox: OutboxOperations
) -> None:
    world.issue_and_bind(world.mandate())
    esc, tk = _held_without_halt(world, outbox)
    warrant = _approve_ticket(world, esc, tk, "release", principals=("cfo",))
    op_log = world.tmp / "operators.jsonl"
    _killed(Executor(world.binder, checkpoint=_stopping("intent")), warrant)
    first, second = (uuid.UUID(m.message_id) for m in warrant.resolution.messages)
    acting = _op_log(world)
    try:
        operator = Operator(acting, outbox)
        operator.cancel(second, reason="ambient cancel")  # moves its head
        outcome = operator.release([first, second], reason=f"FIDU1 warrant {warrant.digest}")
        assert outcome.skipped and outcome.rows  # killed before fiduciary recorded this
    finally:
        acting.close()
    (resolved,) = Executor(world.binder, outbox=outbox, operator_log=op_log).resolve()
    assert resolved.kind == "execution.abandoned"
    assert resolved.content["why"] == "interlock skipped targets"
    (result,) = resolved.content["results"]
    assert result["kind"] == "release" and len(result["rows"]) == 1
    _verify(world)


def test_an_operator_log_claimed_elsewhere_delays_then_leaves_the_intent(
    world: bound.World, outbox: OutboxOperations
) -> None:
    """F7-16: a claim held elsewhere delays the step. Past the timeout the
    step gives up without refusing anything, and resolve settles it later."""
    world.issue_and_bind(world.mandate())
    esc, tk = _held_without_halt(world, outbox, orders=1)
    warrant = _approve_ticket(world, esc, tk, "release", principals=("cfo",))
    op_log = world.tmp / "operators.jsonl"
    holder = _op_log(world)
    try:
        executor = Executor(world.binder, outbox=outbox, operator_log=op_log, claim_timeout=0.2)
        with pytest.raises(ExecutionError, match=r"stayed claimed for 0\.2 seconds"):
            executor.execute(warrant)
        assert world.log.state().execution_status(warrant.digest) == "intent"
        assert executor.resolve() == []  # interlock's log is still claimed
    finally:
        holder.close()
    (resolved,) = executor.resolve()
    assert resolved.kind == "execution.abandoned"
    assert world.outbox_states() == ["held"]
    _verify(world)


def test_a_grant_whose_record_was_never_signed_is_found_by_its_memo(
    world: bound.World,
) -> None:
    world.issue_and_bind(world.mandate())
    esc = Escalation(world.authority, world.governor)
    records, guard = _runtime_guard(world)
    try:
        hold = world.governor.authorize(bound.SCOPE, Decimal("240.00"))
        world.governor.capture(hold, Decimal("240.00"))
        quote = guard.quote(bound.SCOPE, Decimal("20.00"), task=bound.SCOPE)
        tk, _ = esc.raise_extension(world_mandate(world).mandate_id, quote)
        assert tk is not None
        warrant = _approve_ticket(world, esc, tk, "grant")
        killed = Executor(world.binder, guard=guard, checkpoint=_stopping("acted:0"))
        _killed(killed, warrant)
    finally:
        records.close()
    # The guard's record log is lost: only the ledger's memo names the grant.
    blank = world.tmp / "blank-runtime.jsonl"
    (resolved,) = Executor(world.binder, runtime_records=blank, runtime_signer=e.SERVICE).resolve()
    assert resolved.kind == "execution.applied"
    (result,) = resolved.content["results"]
    assert result["record"] is None and Decimal(result["amount"]) == quote.requested
    _verify(world)


def test_an_amendment_killed_before_its_rebind_is_rebound(world: bound.World) -> None:
    mandate = world.mandate()
    world.issue_and_bind(mandate)
    esc = Escalation(world.authority, world.governor)
    raw = world.mandate_json(version=2, prev=mandate.digest)
    raw["budget"] = {**raw["budget"], "envelope": "300.00000000"}
    v2 = Mandate.from_json(raw, signed=False).signed(e.signer("cfo"))
    tk, _ = esc.raise_amend(mandate.mandate_id, v2)
    assert tk is not None
    warrant = _approve_ticket(world, esc, tk, "amend")
    _killed(Executor(world.binder, checkpoint=_stopping("intent")), warrant)
    log, decide = world.log, world.authority.decide
    log.append("mandate.issued", decide.issue(log.state(), v2.digest, world.clock()))
    assert log.state().bound[mandate.mandate_id]["mandate"] == mandate.digest
    (resolved,) = Executor(world.binder).resolve()
    assert resolved.kind == "execution.applied"
    (rebound,) = resolved.content["results"][0]["records"]
    state = log.state()
    assert state.bound[mandate.mandate_id]["mandate"] == v2.digest
    assert next(r for r in log.records() if r.seq == rebound).kind == "mandate.bound"
    _verify(world)


def _two_tenant_plan(world: bound.World) -> Any:
    plan = PlanBuilder(bound.SCOPE)
    for order, tenant in ((3, "acme"), (4, "beta")):  # max_tenants is 1
        plan.update(
            table="orders",
            statement=world.sql(bound.DEBIT),
            parameters={"amount": "1.00", "order_id": order, "tenant": tenant},
            tenant_id=tenant,
            stated_rows=1,
            effect_id=EffectId(f"debit-{tenant}"),
        )
    return plan.build()


def test_a_repair_is_read_from_its_chain_when_no_engine_is_open(world: bound.World) -> None:
    raw = world.mandate_json()
    raw["escalation"] = {"max_open": 5, "ttl_seconds": 3600, "auto": []}
    mandate = Mandate.from_json(raw, signed=False).signed(e.signer("cfo"))
    world.issue_and_bind(mandate)
    esc = Escalation(world.authority, world.governor)
    plan = _two_tenant_plan(world)
    refused = world.execute(mandate, plan)
    repair = world.binder.engine(mandate.mandate_id).engine.repair(plan)
    tk, _ = esc.raise_refused(mandate.mandate_id, repair, receipt=refused.receipt)
    assert tk is not None
    warrant = _approve_ticket(world, esc, tk, "accept_repair", principals=("cfo",))
    executor = Executor(world.binder, checkpoint=_stopping("acted:0"))
    assert repair.proposal is not None
    executor.register_proposal(repair.proposal)
    _killed(executor, warrant)
    reader = Binder(
        world.authority,
        world.governor,
        deployment=world.binder._deployment,
        chains=world.tmp / "chains",
    )  # the command line's: no substrate, no engine
    (resolved,) = Executor(reader).resolve()
    assert resolved.kind == "execution.applied"
    (result,) = resolved.content["results"]
    assert result["proposal"] == repair.proposal.content_hash() and result["receipt"] is None
    _verify(world)


def test_steps_refuse_what_their_systems_cannot_do(
    world: bound.World, outbox: OutboxOperations
) -> None:
    mandate = world.mandate()
    world.issue_and_bind(mandate)
    esc, tk = _held_without_halt(world, outbox, orders=1)
    warrant = _approve_ticket(world, esc, tk, "release", principals=("cfo",))
    by_id = Executor(world.binder)  # no outbox
    (_, refused) = by_id.execute(warrant.warrant_id)
    assert "needs interlock's outbox operations" in refused.content["why"]
    with pytest.raises(AuthorityError, match="no formed warrant"):
        Executor(world.binder).execute("f" * 64)

    # Interlock refuses: the message is no longer held.
    tk2, _ = Escalation(world.authority, world.governor).raise_held(mandate.mandate_id, outbox)
    assert tk2 is not None
    w2 = _approve_ticket(world, esc, tk2, "release", principals=("cfo",))
    op_log = _op_log(world)
    try:
        assert isinstance(tk2.evidence, HeldEvidence)
        Operator(op_log, outbox).cancel(
            uuid.UUID(tk2.evidence.messages[0].message_id), reason="ambient"
        )
        (_, refused2) = Executor(world.binder, outbox=outbox, operator_log=op_log).execute(w2)
        assert refused2.kind == "execution.refused"
    finally:
        op_log.close()
    _verify(world)


def test_a_grant_outside_the_mandate_is_refused(world: bound.World) -> None:
    raw = world.mandate_json()
    raw["extension"] = {**raw["extension"], "max_grant": "20.00000000", "max_total": "20.00000000"}
    mandate = Mandate.from_json(raw, signed=False).signed(e.signer("cfo"))
    world.issue_and_bind(mandate)
    esc = Escalation(world.authority, world.governor)
    records, guard = _runtime_guard(world)
    try:
        hold = world.governor.authorize(bound.SCOPE, Decimal("240.00"))
        world.governor.capture(hold, Decimal("240.00"))
        executor = Executor(world.binder, guard=guard)
        first = guard.quote(bound.SCOPE, Decimal("20.00"), task=bound.SCOPE)
        tk1, _ = esc.raise_extension(mandate.mandate_id, first)
        assert tk1 is not None
        assert (
            executor.execute(_approve_ticket(world, esc, tk1, "grant"))[-1].kind
            == "execution.applied"
        )
        second = guard.quote(bound.SCOPE, Decimal("20.00"), task=bound.SCOPE)
        tk2, _ = esc.raise_extension(mandate.mandate_id, second)
        assert tk2 is not None
        refused = executor.execute(_approve_ticket(world, esc, tk2, "grant"))[-1]
        assert refused.kind == "execution.refused"
        assert "passes the mandate's extension bounds" in refused.content["why"]
        # A quote re-signed since the ticket observed it is not the one approved.
        third = guard.quote(bound.SCOPE, Decimal("1.00"), task=bound.SCOPE)
        tk3, _ = esc.raise_extension(mandate.mandate_id, third)
        assert tk3 is not None
        w3 = _approve_ticket(world, esc, tk3, "decline", principals=("cfo",))
        pending = guard._pending[third.quote_id]  # as a guard re-quoting would
        moved = dataclasses.replace(
            third, record=dataclasses.replace(third.record, seq=third.record.seq + 1000)
        )
        pending.quote = moved
        refused3 = executor.execute(w3)[-1]
        assert "not the record the ticket observed" in refused3.content["why"]
    finally:
        records.close()
    _verify(world)


def test_escalation_refuses_triggers_with_nothing_to_escalate(
    world: bound.World, outbox: OutboxOperations
) -> None:
    mandate = world.mandate()
    world.issue_and_bind(mandate)
    esc = Escalation(world.authority, world.governor)
    mandate_id = mandate.mandate_id
    with pytest.raises(AuthorityError, match="no raised ticket"):
        esc.ticket("f" * 64)
    with pytest.raises(AuthorityError, match="no formed warrant"):
        esc.warrant("f" * 64)
    assert esc.warrant_for_ticket("f" * 64) is None
    with pytest.raises(EscalationError, match="no held outbox messages"):
        esc.raise_held(mandate_id, outbox)
    with pytest.raises(EscalationError, match="does not exist"):
        esc.raise_dead(mandate_id, outbox, uuid.uuid4())
    pending = world.refund_plan(1, "10.00")
    assert world.execute(mandate, pending).committed
    (message,) = [c.message_id for c in outbox.compensables(str(pending.plan_id))]
    with pytest.raises(EscalationError, match="not 'dead'"):
        esc.raise_dead(mandate_id, outbox, str(message))
    with pytest.raises(EscalationError, match="no compensable delivered messages"):
        esc.raise_undo(mandate_id, outbox, "plan-nothing")
    v1_again = world.mandate()
    with pytest.raises(EscalationError, match="is not version 2"):
        esc.raise_amend(mandate_id, v1_again)
    unsigned = dataclasses.replace(v1_again, signature=None)
    with pytest.raises(EscalationError, match="must be signed"):
        esc.raise_amend(mandate_id, unsigned)

    plan = _two_tenant_plan(world)
    refused = world.execute(mandate, plan)
    repair = world.binder.engine(mandate_id).engine.repair(plan)
    with pytest.raises(EscalationError, match="requires the refused plan's ARC1 receipt"):
        esc.raise_refused(mandate_id, dataclasses.replace(repair, receipt=None))
    with pytest.raises(EscalationError, match="no repair proposal"):
        esc.raise_refused(
            mandate_id, dataclasses.replace(repair, proposal=None), receipt=refused.receipt
        )

    records, guard = _runtime_guard(world)
    try:
        hold = world.governor.authorize(bound.SCOPE, Decimal("200.00"))
        world.governor.capture(hold, Decimal("200.00"))
        quote = guard.quote(bound.SCOPE, Decimal("60.00"), task=bound.SCOPE)
        with pytest.raises(EscalationError, match="has no signed record"):
            esc.raise_extension(mandate_id, dataclasses.replace(quote, record=None))  # type: ignore[arg-type]
    finally:
        records.close()


def test_a_lineage_not_in_force_raises_no_ticket(world: bound.World) -> None:
    mandate = world.mandate()
    world.issue_and_bind(mandate)
    approve(world.store, ("cfo",), "revocation", mandate.digest)
    world.binder.revoke(mandate.digest)
    esc = Escalation(world.authority, world.governor)
    raw = world.mandate_json(version=2, prev=mandate.digest)
    v2 = Mandate.from_json(raw, signed=False).signed(e.signer("cfo"))
    with pytest.raises(AuthorityError, match="revoked: it cannot raise a ticket"):
        esc.raise_ticket(mandate.mandate_id, "amend", _amend_evidence(v2), ())
    world.clock.advance(seconds=1)
    assert esc.expire_tickets() == []


def _amend_evidence(proposed: Mandate) -> AmendEvidence:
    return AmendEvidence(
        proposed=ProposedMandate(version=proposed.version, digest=proposed.digest),
        wider=(),
        narrower=(),
    )


def test_a_log_of_several_operators_is_read_under_the_configured_keyring(
    world: bound.World, outbox: OutboxOperations
) -> None:
    """Another operator's records in interlock's log: the executor's own key
    alone cannot read it, and the keyring interlock.toml configures can."""
    world.issue_and_bind(world.mandate())
    esc, tk = _held_without_halt(world, outbox)
    warrant = _approve_ticket(world, esc, tk, "release", principals=("cfo",))
    op_log = world.tmp / "operators.jsonl"
    _killed(Executor(world.binder, checkpoint=_stopping("intent")), warrant)
    service, night = e.SERVICE.public_key(), e.signer("night-ops")
    keys = {"fiduciary": service.spec(), "night-ops": night.public_key().spec()}
    acting = OperatorLog(op_log, night, Keyring(keys), ledger=world.governor)
    try:  # a second node, with its own operator key, acted on the warrant
        ids = [uuid.UUID(m.message_id) for m in warrant.resolution.messages]
        Operator(acting, outbox).release(ids, reason=f"FIDU1 warrant {warrant.digest}")
    finally:
        acting.close()
    with pytest.raises(RecordIntegrityError):
        Executor(world.binder, outbox=outbox, operator_log=op_log).resolve()

    (world.tmp / "interlock.toml").write_text(
        f'database = "{world.tmp / "unused.db"}"\n'
        '[[tables]]\nname = "orders"\nprimary_key = "id"\ncolumns = ["tenant"]\n'
        f'[operators]\nlog = "{op_log}"\n'
        f'[operators.keys]\nfiduciary = "{service.spec()}"\n"night-ops" = "{keys["night-ops"]}"\n'
    )
    for name in ("service", "stranger"):
        key = world.tmp / f"{name}.key"
        key.write_text(hashlib.sha256(f"fiduciary-test/{name}".encode()).hexdigest() + "\n")
        key.chmod(0o600)
    config = Config(
        database="unused.db",
        interlock=world.tmp / "interlock.toml",
        service_key=world.tmp / "service.key",
    )
    assert config.operators_log() == op_log
    keyring = config.operator_keyring()
    assert keyring is not None and keyring.verifier(night.public_key().key_id) is not None
    assert config.executor().public_key().key_id == service.key_id  # an operator of interlock's
    stranger = dataclasses.replace(config, executor_key=world.tmp / "stranger.key")
    with pytest.raises(ConfigError, match=r"is no operator of .*\[operators.keys\]"):
        stranger.executor()
    (resolved,) = Executor(
        world.binder, outbox=outbox, operator_log=config.operators_log(), operator_keyring=keyring
    ).resolve()
    assert resolved.kind == "execution.applied"
    _verify(world)

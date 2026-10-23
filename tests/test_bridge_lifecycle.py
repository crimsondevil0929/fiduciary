"""Phase 4's end to end, on SQLite and PostgreSQL, against AgentGov's ``DummyLLM``.

A session commits a refund; a second call is refused, repaired, and the
repair accepted by the mandate's rule; the session's model calls run its
envelope out; recovery takes steps on its reserve; the reserve runs out and
the guard's quote raises an ``extension`` ticket; two principals approve the
grant, each signing through a fake AWS KMS; the service executes the warrant;
the session's billing moves to the scope the grant funded (F8-8); and the
task finishes there. Every record verifies, and every anchor agrees.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from agentgov.dummy import DummyLLM
from agentgov.receipts.kms_aws import AwsKmsSigner

from fiduciary import fidu1
from fiduciary._upstream import RECOVERY_REOPENS
from fiduciary.bridge import SessionHaltedError
from fiduciary.documents import Approval
from fiduciary.store import Store
from tests import bound
from tests import bridge_kit as kit
from tests import examples as e
from tests.conftest import Clock
from tests.fakes import FakeKms
from tests.test_bridge import FakeAnthropic
from tests.test_executor import _verify

WORDS = (
    "refund invoice shipment customer ledger balance carrier warehouse discount receipt "
    "chargeback courier pallet coupon voucher statement deposit freight parcel subscription "
    "renewal quota estimate dispatch tariff surcharge manifest backorder rebate settlement"
).split()


def turns() -> Iterator[str]:
    """Distinct turns: a task that makes progress, which the cognitive
    breaker does not mistake for a loop."""
    rng = random.Random(4)
    while True:
        yield "Review the " + " ".join(rng.sample(WORDS, 12)) + "."


@pytest.fixture
def world(store: Store, tmp_path: Path, clock: Clock) -> Iterator[bound.World]:
    yield from kit.world(store, tmp_path, clock)


def _kms_approval(principal: str, ticket: Any, action: str, clock: Clock) -> Approval:
    """A principal's approval, signed on their own machine through a key
    service holding their charter key: here, a fake AWS KMS."""
    option = next(o for o in ticket.options if o.resolution.action == action)
    raw = e.approval_json(
        principal,
        subject="ticket",
        digest=ticket.digest,
        resolution=option.digest,
        approval_id=str(uuid.uuid4()),
        issued_at=fidu1.format_instant(clock()),
        not_after=fidu1.format_instant(clock() + timedelta(minutes=30)),
    )
    kms = FakeKms(e.signer(principal))
    approval = Approval.from_json(raw, signed=False).signed(AwsKmsSigner(kms.arn, client=kms))
    assert kms.messages, "signed through the key service"
    return approval


def test_a_session_from_commit_to_extension_and_back(world: bound.World, clock: Clock) -> None:
    mandate = kit.mandate(
        world,
        budget={**kit.mandate_json(world)["budget"], "envelope": "1.00000000"},
        delegation=None,
    )
    world.issue_and_bind(mandate)
    fid = kit.fiduciary(world)
    session = fid.session(bound.SCOPE, tenant="acme")
    subject = bound.SCOPE

    # 1. A refund for the session's tenant commits.
    done = session.call(
        "refund_order", {"order_id": 7, "amount": "12.50", "payment_intent": "pi_0000000007"}
    )
    assert done.committed, done.render()

    # 2. Two debits, the second another tenant's order: refused, repaired, and
    #    the repair accepted by the mandate's rule (trim-refused), here.
    refused = session.call("adjust_orders", {"first": 9, "second": 10, "amount": "1.00"})
    assert refused.outcome == "refused" and refused.ticket is not None
    assert refused.ticket.ticket_class == "refused" and refused.repaired, refused.render()
    assert world.fetch("SELECT total FROM orders WHERE id = 9")[0][0] == 999
    assert world.fetch("SELECT total FROM orders WHERE id = 10")[0][0] == 1000
    assert "beta" not in refused.render()

    # 3. The model's calls spend the envelope out; each holds per_call (0.50)
    #    and costs 0.40 (20,000 output tokens at $20 per million).
    model = session.govern(
        DummyLLM("claude-opus-5-5", output_tokens=20_000), model="claude-opus-5-5"
    )
    prompt = turns()
    model.complete(next(prompt))
    with pytest.raises(SessionHaltedError) as halted:
        for _ in range(3):
            model.complete(next(prompt))
    first = halted.value
    assert first.kind == "budget" and first.messages is not None and first.ticket is None
    assert session.billing_scope == f"{subject}/recovery"

    # 4. Recovery: each step's call is billed to the step, and the calls
    #    between steps to the reserve, until the reserve is spent and the
    #    guard quotes for more: an extension ticket.
    ticket = None
    for _ in range(20):
        try:
            model.complete(next(prompt))
        except SessionHaltedError as exc:
            if exc.ticket is not None:
                ticket = exc.ticket
                break
    assert ticket is not None and ticket.ticket_class == "extension"
    assert [t.ticket_id for t in session.tickets()] == sorted(
        [refused.ticket.ticket_id, ticket.ticket_id]
    )
    with pytest.raises(SessionHaltedError):
        model.complete(next(prompt))  # the halt stands until a grant

    # 5. Two principals approve the grant, through their key service; the
    #    service judges the intake and executes the warrant.
    document = fid.escalation.ticket(ticket.ticket_id)
    for principal in ("cfo", "head-ops"):
        world.store.submit(_kms_approval(principal, document, "grant", clock).canonical())
    records = fid.serve_once()
    assert [r.kind for r in records] == [
        "approval.recorded",
        "approval.recorded",
        "warrant.formed",
        "execution.intent",
        "execution.applied",
    ]
    (grant,) = records[-1].content["results"]
    assert grant["kind"] == "grant" and grant["scope"] == f"{subject}/recovery/ext-1"
    steps = [r for r in fid._runtime_records.records() if r.kind == "recovery.step"]  # type: ignore[union-attr]
    assert steps, "recovery took steps before the reserve ran out"

    # 6. The session takes the grant up: billing moves to the scope it funded.
    model.complete(next(prompt))
    assert session.billing_scope == f"{subject}/recovery/ext-1"
    finished = session.call(
        "refund_order", {"order_id": 11, "amount": "5.00", "payment_intent": "pi_0000000011"}
    )
    assert finished.committed, finished.render()
    assert [t.status for t in session.tickets()] == ["warranted", "warranted"]
    session.close()

    closed = world.log.records()[-1]
    assert closed.kind == "session.closed"
    assert closed.content["scopes"] == [
        subject,
        f"{subject}/recovery",
        f"{subject}/recovery/ext-1",
    ]
    _verify(world)


@pytest.mark.skipif(not RECOVERY_REOPENS, reason="this Interlock does not reopen a recovery (U-6)")
def test_a_reopened_recovery_answers_by_its_own_halts_alone(world: bound.World) -> None:
    """U-6, F8-9: a recovery that answered a safety halt, and closed, does not
    make the next one a safety recovery. The lineage's next halt, its money
    spent, recovers on a fresh reserve, and when that runs out, its quote
    goes to the principals."""
    raw = kit.mandate_json(world)
    world.issue_and_bind(
        kit.mandate(
            world,
            recovery={**raw["recovery"], "reserve": "1.00000000", "max_steps": 6},
            budget={**raw["budget"], "envelope": "1.00000000"},
            delegation=None,
        )
    )
    fid = kit.fiduciary(world)

    # 1. An operator's halt, recovered from until the reserve ends it: no quote.
    first = fid.session(bound.SCOPE, tenant="acme")
    world.governor.trip(bound.SCOPE, "operator stop: reviewing this agent")
    model = first.govern(FakeAnthropic(output_tokens=20_000))
    transcript: list[dict[str, Any]] = [{"role": "user", "content": "Carry on."}]
    with pytest.raises(SessionHaltedError):
        model.messages.create(model="claude-opus-5-5", max_tokens=1024, messages=transcript)
    ended = None
    for _ in range(12):
        try:
            model.messages.create(model="claude-opus-5-5", max_tokens=1024, messages=transcript)
        except SessionHaltedError as exc:
            if exc.messages is None:
                ended = exc
                break
    assert ended is not None and ended.ticket is None
    first.close()  # the last to bill the recovery: it closes
    world.governor.reset(bound.SCOPE)

    # 2. A later session spends the envelope out: the recovery opens again,
    #    and its reserve spent, the guard's quote is the principals' to answer.
    second = fid.session(bound.SCOPE, tenant="acme")
    spending = second.govern(
        DummyLLM("claude-opus-5-5", output_tokens=20_000), model="claude-opus-5-5"
    )
    prompt = turns()
    ticket = None
    for _ in range(40):
        try:
            spending.complete(next(prompt))
        except SessionHaltedError as exc:
            if exc.ticket is not None:
                ticket = exc.ticket
                break
    assert ticket is not None and ticket.ticket_class == "extension"
    assert second.billing_scope == f"{bound.SCOPE}/recovery/ext-1"
    second.close()
    _verify(world)

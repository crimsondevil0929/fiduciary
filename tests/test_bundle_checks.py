"""Each check of ``verify-bundle`` (§9.3), on a bundle that breaks it: the
check named, and the run's exit code the first failing check's.

Every violation here is written around the rules, as a deployment that went
wrong would write it: an act on the lineage's ledger footprint that nothing
the lineage authorized explains (V7), a warrant the rules would not have
formed or executed (V8), a commit after the lineage was revoked (V11), and
a bundle verified under keys other than the ones it was signed with. The
tampering sweep and the offline run are in ``tests/test_bundle_tamper.py``.
"""

from __future__ import annotations

import dataclasses
import time
import uuid
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from agentgov.receipts.signing import Ed25519Signer, verifier_of
from interlock.operators import Operator, OperatorLog
from interlock.relay import DELIVERED, DeliveryResult

from fiduciary import fidu1
from fiduciary.authority import Authority, Decide
from fiduciary.bridge import Fiduciary, build_plan
from fiduciary.bundle import BundleReport
from fiduciary.bundle.export import spec
from fiduciary.documents import Mandate, Ticket, Warrant
from fiduciary.escalation import Escalation
from fiduciary.executor import Executor
from fiduciary.log import Log
from fiduciary.store import Store
from tests import bound
from tests import bridge_kit as kit
from tests import bundle_kit as bk
from tests import examples as e
from tests.conftest import LiveClock, approve
from tests.test_executor import _approve_ticket, _outbox_ops, _run_relay

NIGHT_SHIFT = e.signer("night-shift")


@pytest.fixture
def world(store: Store, tmp_path: Path) -> Iterator[bound.World]:
    yield from bk.world(store, tmp_path)


@pytest.fixture
def glass_world(store: Store, tmp_path: Path) -> Iterator[bound.World]:
    """A world whose charter declares the night shift's key break-glass (F4-6)."""
    key_id = NIGHT_SHIFT.public_key().key_id
    yield from bk.world(store, tmp_path, charter={"break_glass": [key_id]})


# -- what the lineage does ---------------------------------------------------------------


def _refund(session: Any, order: int, amount: str = "5.00") -> None:
    arguments = {"order_id": order, "amount": amount, "payment_intent": f"pi_{order:010d}"}
    assert session.call("refund_order", arguments).committed


def _lineage(world: bound.World, mandate: Mandate | None = None) -> tuple[Mandate, Fiduciary]:
    """A lineage bound, and one refund committed in a closed session."""
    mandate = mandate or kit.mandate(world)
    world.issue_and_bind(mandate)
    fid = kit.fiduciary(world)
    with fid.session(bound.SCOPE, tenant="acme") as session:
        _refund(session, 7)
    return mandate, fid


def _later(fid: Fiduciary) -> None:
    """A session opened and closed: records after what came before, so the
    export's range (to the last anchored record) covers it."""
    with fid.session(bound.SCOPE, tenant="acme"):
        pass


@dataclasses.dataclass
class _Held:
    mandate: Mandate
    fid: Fiduciary
    escalation: Escalation
    outbox: Any
    ticket: Ticket


@pytest.fixture
def held(world: bound.World) -> Iterator[_Held]:
    yield from _hold(world)


@pytest.fixture
def glass_held(glass_world: bound.World) -> Iterator[_Held]:
    yield from _hold(glass_world)


def _hold(world: bound.World) -> Iterator[_Held]:
    """A lineage whose refund's message the relay holds, the subject halted,
    and the held ticket raised."""
    mandate, fid = _lineage(world)
    escalation = Escalation(world.authority, world.governor)
    outbox, conn = _outbox_ops(world)
    try:
        world.governor.trip(bound.SCOPE, "spend envelope exhausted")
        _run_relay(world, DeliveryResult(outcome=DELIVERED, detail="ok"))
        assert world.outbox_states() == ["held"]
        ticket, _ = escalation.raise_held(mandate.mandate_id, outbox)
        assert ticket is not None
        yield _Held(mandate, fid, escalation, outbox, ticket)
    finally:
        conn.close()


def _operator_log(world: bound.World, signer: Any = e.SERVICE) -> OperatorLog:
    keyring = bk.operator_keyring("night-shift")
    return OperatorLog(world.tmp / "operators.jsonl", signer, keyring, ledger=world.governor)


def _execute(world: bound.World, held: _Held, warrant: Warrant) -> list[Any]:
    log = _operator_log(world)
    try:
        executor = Executor(
            world.binder, escalation=held.escalation, outbox=held.outbox, operator_log=log
        )
        return executor.execute(warrant)
    finally:
        log.close()


def _message(held: _Held) -> uuid.UUID:
    evidence: Any = held.ticket.evidence
    return uuid.UUID(evidence.messages[0].message_id)


# -- verifying ---------------------------------------------------------------------------


def _report(world: bound.World, mandate: Mandate, **kw: Any) -> BundleReport:
    operators = kw.pop("operators", ())
    data = bk.export(world, mandate.mandate_id, operators=operators)
    kw.setdefault("keys", bk.auditor_keys(*operators))
    return bk.verify(world, data, **kw)


def _fails(report: BundleReport, check: str, *fragments: str, code: int) -> None:
    """``check`` names every fragment, and the run exits ``code``."""
    found = report.failed(check)
    text = report.render()
    assert found, text
    for fragment in fragments:
        assert any(fragment in f.message for f in found), (fragment, text)
    assert report.exit_code == code, text


def _only(report: BundleReport, *checks: str) -> None:
    assert {f.check for f in report.findings} == set(checks), report.render()


# -- what verifies -----------------------------------------------------------------------


def test_a_held_message_released_by_warrant_verifies(world: bound.World, held: _Held) -> None:
    """The warrant's reset follows its anchor; its operator intents name it;
    the bundle carries both, and every step's evidence (V7, V8)."""
    warrant = _approve_ticket(world, held.escalation, held.ticket, "release")
    records = _execute(world, held, warrant)
    assert records[-1].kind == "execution.applied"
    assert world.outbox_states() == ["pending"]
    _later(held.fid)
    report = _report(world, held.mandate)
    assert report.passed, report.render()


# -- V7 completeness: what nothing the lineage authorized explains -----------------------


def test_a_delegation_no_binding_records_is_ambient(world: bound.World) -> None:
    mandate, fid = _lineage(world)
    world.governor.delegate(
        bound.SCOPE, f"{bound.SCOPE}/side", "1", memo=f"FIDU1 bind {mandate.digest}"
    )
    _later(fid)
    report = _report(world, mandate)
    _fails(report, "V7", "a delegation no binding of the lineage records", code=7)
    _only(report, "V7")


def test_a_reset_no_warrant_names_is_ambient(world: bound.World) -> None:
    mandate, fid = _lineage(world)
    world.governor.trip(bound.SCOPE, "spend envelope exhausted")
    world.governor.reset(bound.SCOPE)
    _later(fid)
    report = _report(world, mandate)
    _fails(report, "V7", "a reset no warrant of the lineage names", code=7)
    _only(report, "V7")


def test_an_operator_release_under_no_warrant_is_ambient(world: bound.World, held: _Held) -> None:
    log = _operator_log(world)
    try:
        Operator(log, held.outbox).release([_message(held)], reason="manual release")
    finally:
        log.close()
    _later(held.fid)
    report = _report(world, held.mandate)
    _fails(report, "V7", "(release)", "under no warrant of it", code=7)
    assert all(f.label == "" for f in report.failed("V7")), report.render()
    _only(report, "V7")


def test_a_break_glass_release_is_ambient_and_labeled(
    glass_world: bound.World, glass_held: _Held
) -> None:
    """A key the charter declares break-glass still acts outside the
    lineage's authority: V7 names it, labeled (F4-6)."""
    log = _operator_log(glass_world, NIGHT_SHIFT)
    try:
        Operator(log, glass_held.outbox).release([_message(glass_held)], reason="night release")
    finally:
        log.close()
    _later(glass_held.fid)
    report = _report(glass_world, glass_held.mandate, operators=("night-shift",))
    _fails(report, "V7", "under no warrant of it", code=7)
    (finding,) = report.failed("V7")
    assert finding.label == "break-glass"
    assert "[break-glass]" in report.render()
    _only(report, "V7")


def test_a_plan_no_pinned_tool_built_is_ambient(world: bound.World) -> None:
    """A plan committed on the lineage's own engine, past the bridge: its
    effects are within the mandate, and no session's call made it."""
    mandate, fid = _lineage(world)
    binding = kit.refund(world.backend)
    plan = build_plan(
        binding,
        {"order_id": 9, "amount": "2.00", "payment_intent": "pi_0000000009"},
        scope=bound.SCOPE,
        trajectory="side-door",
        tenant="acme",
    )
    plan = dataclasses.replace(plan, intent="")
    assert world.execute(mandate, plan).committed
    _later(fid)
    report = _report(world, mandate)
    _fails(report, "V7", "no pinned tool's call", code=7)
    _only(report, "V7")


def test_a_tool_hold_that_names_no_session_after_named_ones_is_ambient(
    world: bound.World,
) -> None:
    """0.4 metered a passthrough call as ``FIDU1 tool <name> <digest>``; V7
    explains that form only before memos named their session. Written after,
    inside a session's life and on its scope, it is ambient."""
    mandate, fid = _lineage(world)
    with fid.session(bound.SCOPE, tenant="acme") as session:
        assert session.call("lookup_order", {"order_id": 7}).ok  # a named memo
        held = world.governor.authorize(
            bound.SCOPE, Decimal("0.001"), memo=f"FIDU1 tool lookup_order {e.lookup().digest[:16]}"
        )
        world.governor.void(held, memo="released")
    _later(fid)
    report = _report(world, mandate)
    _fails(report, "V7", "after memos began naming theirs", code=7)
    _only(report, "V7")


def test_a_reset_another_warrant_names_is_ambient(world: bound.World) -> None:
    """A reset its anchor attributes to a warrant the lineage never formed."""
    mandate, fid = _lineage(world)
    world.governor.trip(bound.SCOPE, "spend envelope exhausted")
    with world.governor.transaction():
        world.governor.anchor("fiduciary", f"FIDU1 warrant {'d' * 64} reset {bound.SCOPE}")
        world.governor.reset(bound.SCOPE)
    _later(fid)
    report = _report(world, mandate)
    _fails(report, "V7", "a reset no warrant of the lineage names", code=7)


def test_a_session_memo_outside_the_sessions_life_is_ambient(world: bound.World) -> None:
    """A hold that names a closed session of the lineage, written after it closed."""
    mandate, fid = _lineage(world)
    (session_id,) = world.log.state().sessions
    held = world.governor.authorize(
        bound.SCOPE, Decimal("0.001"), memo=f"FIDU1 session {session_id} tool lookup_order"
    )
    world.governor.void(held, memo="released")
    _later(fid)
    report = _report(world, mandate)
    _fails(report, "V7", "outside session", code=7)


def test_a_version_bound_as_another_compiled_policy_is_named(world: bound.World) -> None:
    """The deployment the bundle carries compiles the bound version to another
    policy than the one it was bound as (V4)."""
    from interlock.outbound import SinkRegistry

    mandate, fid = _lineage(world)
    _later(fid)
    deployment = world.binder.deployment
    tighter = SinkRegistry([dataclasses.replace(spec, max_attempts=2) for spec in deployment.sinks])
    data = bk.export(
        world, mandate.mandate_id, deployment=dataclasses.replace(deployment, sinks=tighter)
    )
    report = bk.verify(world, data)
    _fails(report, "V4", "compiles to", code=6)


def test_an_operator_act_on_other_messages_than_the_warrants_is_named(
    world: bound.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A release that acted on one of the two messages its warrant names (V8)."""
    mandate, fid = _lineage(world)
    with fid.session(bound.SCOPE, tenant="acme") as session:
        _refund(session, 9)
    escalation = Escalation(world.authority, world.governor)
    outbox, conn = _outbox_ops(world)
    try:
        world.governor.trip(bound.SCOPE, "spend envelope exhausted")
        _run_relay(world, DeliveryResult(outcome=DELIVERED, detail="ok"))
        ticket, _ = escalation.raise_held(mandate.mandate_id, outbox)
        assert ticket is not None
        assert len(ticket.evidence.messages) == 2  # type: ignore[union-attr]
        warrant = _approve_ticket(world, escalation, ticket, "release")
        release = Operator.release

        def fewer(self: Any, ids: Any, **kw: Any) -> Any:
            return release(self, list(ids)[:1], **kw)

        monkeypatch.setattr(Operator, "release", fewer)
        _execute(world, _Held(mandate, fid, escalation, outbox, ticket), warrant)
    finally:
        conn.close()
    _later(fid)
    report = _report(world, mandate)
    found = report.failed("V8")
    assert any("acted on other messages" in f.message for f in found), report.render()


# -- V8 warrants: formed and executed as the rules allow ---------------------------------


def test_a_warrant_one_approval_short_is_named(
    world: bound.World, held: _Held, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fiduciary.documents.ticket import quorum_for_resolution

    target = "fiduciary.authority.quorum_for_resolution"
    monkeypatch.setattr(target, lambda *a: quorum_for_resolution(*a) - 1)
    warrant = _approve_ticket(world, held.escalation, held.ticket, "release", principals=("cfo",))
    monkeypatch.undo()
    assert warrant.basis.approvals and len(warrant.basis.approvals) == 1
    assert _execute(world, held, warrant)[-1].kind == "execution.applied"
    _later(held.fid)
    report = _report(world, held.mandate)
    _fails(report, "V8", "needs 2 approvals; 1 count", code=5)


def test_a_rule_whose_predicate_is_false_is_named(
    world: bound.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mandate's rule trims a refusal only when nothing is dropped; the
    service formed its warrant for a refusal that dropped one."""
    escalation = {
        "max_open": 5,
        "ttl_seconds": 86400,
        "auto": [
            {
                "id": "trim-refused",
                "class": "refused",
                "resolution": "accept_repair",
                "when": {"exhaustive": True, "max_dropped": 0},
            }
        ],
    }
    mandate = kit.mandate(world, escalation=escalation)
    world.issue_and_bind(mandate)
    fid = kit.fiduciary(world)
    monkeypatch.setattr(Decide, "rule_matches", lambda *a: True)
    with fid.session(bound.SCOPE, tenant="acme") as session:
        adjust = {"first": 9, "second": 10, "amount": "1.00"}
        assert session.call("adjust_orders", adjust).repaired
    monkeypatch.undo()
    _later(fid)
    report = _report(world, mandate)
    _fails(report, "V8", "auto rule 'trim-refused' does not admit this ticket", code=5)


def test_an_execution_after_the_warrant_expired_is_named(
    world: bound.World, held: _Held, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = world.clock()
    approve(
        world.store,
        ("cfo", "head-ops"),
        "ticket",
        held.ticket.digest,
        resolution=next(o for o in held.ticket.options if o.resolution.action == "release").digest,
        issued_at=fidu1.format_instant(now),
        not_after=fidu1.format_instant(now + timedelta(seconds=1)),
    )
    held.escalation.process_intake()
    warrant = held.escalation.warrant_for_ticket(held.ticket.ticket_id)
    assert warrant is not None
    time.sleep(max(0.0, (warrant.not_after - world.clock()).total_seconds()) + 0.05)
    real = Decide.execution_intent
    monkeypatch.setattr(
        Decide,
        "execution_intent",
        lambda self, state, digest, at: real(self, state, digest, warrant.formed_at),
    )
    assert _execute(world, held, warrant)[-1].kind == "execution.applied"
    monkeypatch.undo()
    _later(held.fid)
    report = _report(world, held.mandate)
    _fails(report, "V8", "expired at", code=9)
    assert all(f.kind == "time" for f in report.failed("V8")), report.render()


def test_a_step_at_a_head_the_ticket_did_not_observe_is_named(
    world: bound.World, held: _Held, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Between the ticket and the warrant's execution, the message was
    released by hand and held again: the step acts on a head the principals
    never saw (F7-15), as it can where the operator reads each head itself,
    without U-4."""
    monkeypatch.setattr("fiduciary.executor.EXPECTED_HEADS", False)
    warrant = _approve_ticket(world, held.escalation, held.ticket, "release")
    world.governor.reset(bound.SCOPE)
    log = _operator_log(world)
    try:
        Operator(log, held.outbox).release([_message(held)], reason="manual release")
    finally:
        log.close()
    world.governor.trip(bound.SCOPE, "spend envelope exhausted")
    _run_relay(world, DeliveryResult(outcome=DELIVERED, detail="ok"))
    records = _execute(world, held, warrant)
    assert records[-1].content["mismatches"]
    _later(held.fid)
    report = _report(world, held.mandate)
    _fails(report, "V8", "at a head the ticket did not observe", code=7)
    assert report.failed("V7")  # the hand release and its reset, too


# -- V11 revocation ----------------------------------------------------------------------


def test_a_commit_after_revocation_is_named(world: bound.World) -> None:
    """The lineage is revoked and its subject halted; the halt is lifted by
    hand, and a plan commits on the lineage's chain after the trip (G7)."""
    mandate, _ = _lineage(world)
    compiled = world.binder.engine(mandate.mandate_id).compiled
    approve(world.store, ("security",), "revocation", mandate.digest, **bound.window(world.clock))
    revoked = world.binder.revoke(mandate.digest)
    assert revoked[0].content["trips"]
    world.governor.reset(bound.SCOPE)
    engine = world.binder._open_engine(mandate, compiled)
    plan = build_plan(
        kit.refund(world.backend),
        {"order_id": 11, "amount": "3.00", "payment_intent": "pi_0000000011"},
        scope=bound.SCOPE,
        trajectory="after-the-end",
        tenant="acme",
    )
    assert engine.execute(plan).committed
    engine.close()
    world.store.submit(b"not an approval")
    world.authority.process_intake()  # a record after: the range covers the commit
    report = _report(world, mandate)
    _fails(report, "V11", "after the trip at", code=7)
    assert any("commits after the trip" in f.message for f in report.failed("V11"))
    _only(report, "V7", "V11")


# -- the log, its anchors, and the clock -------------------------------------------------


def test_an_unanchored_record_is_named(world: bound.World) -> None:
    mandate, fid = _lineage(world)
    world.store.submit(b"not an approval")
    unanchored = Log(world.store, e.SERVICE, clock=world.clock)
    Authority(unanchored, pinned=world.charter.digest).process_intake()
    seq = world.store.head("fiduciary")[0]
    _later(fid)
    report = _report(world, mandate)
    _fails(report, "V10", f"record {seq} is in range, and no anchor names it", code=8)
    _only(report, "V10")


def test_records_ahead_of_the_ledger_are_named(world: bound.World) -> None:
    mandate, fid = _lineage(world)
    assert isinstance(world.clock, LiveClock)
    world.clock.advance(minutes=5)
    _later(fid)
    report = _report(world, mandate)
    _fails(report, "V12", code=9)
    _only(report, "V12")
    assert bk.verify(
        world, bk.export(world, mandate.mandate_id), skew_seconds=600
    ).passed  # within a skew the auditor allows


# -- keys --------------------------------------------------------------------------------


def test_the_wrong_pinned_charter_is_named(world: bound.World) -> None:
    mandate, _ = _lineage(world)
    data = bk.export(world, mandate.mandate_id)
    report = bk.verify(world, data, charter="0" * 64)
    _fails(report, "V2", "no charter with the pinned digest", code=5)


@pytest.mark.parametrize(
    ("role", "check"),
    [("service", "V9"), ("ledger", "V6"), ("receipts", "V5"), ("runtime", "V7")],
)
def test_a_pinned_key_the_bundle_was_not_signed_with_is_named(
    world: bound.World, role: str, check: str
) -> None:
    """What the role signed fails under the auditor's key, and keys.json,
    which names the key it was signed with, disagrees with the pin (V1)."""
    if role == "runtime":  # runtime records to sign: a recovery and an extension
        from tests.test_bridge_lifecycle import test_a_session_from_commit_to_extension_and_back

        test_a_session_from_commit_to_extension_and_back(world, world.clock)
        (mandate_id,) = world.log.state().lineages
    else:
        mandate_id = _lineage(world)[0].mandate_id
    other = spec(verifier_of(Ed25519Signer(bytes(range(32)))))
    data = bk.export(world, mandate_id)
    keys = bk.auditor_keys(**{role: [other] if role == "receipts" else other})
    report = bk.verify(world, data, keys=keys)
    assert report.failed(check), report.render()
    assert any("keys.json names" in f.message for f in report.failed("V1")), report.render()
    assert report.exit_code == 3, report.render()


def test_without_pinned_keys_the_bundle_verifies_under_its_own_and_says_so(
    world: bound.World,
) -> None:
    mandate, _ = _lineage(world)
    report = bk.verify(world, bk.export(world, mandate.mandate_id), keys=None)
    assert report.passed, report.render()
    assert any("exporter's statement" in note for note in report.notes)


def test_a_service_key_that_signed_nothing_in_the_log_is_named(world: bound.World) -> None:
    """keys.json and the pin agree on a key that did not sign the log."""
    mandate, _ = _lineage(world)
    other = Ed25519Signer(bytes(range(32)))
    data = bk.export(world, mandate.mandate_id, evidence={"service": other})
    report = bk.verify(world, data, keys=bk.auditor_keys(service=spec(verifier_of(other))))
    _fails(report, "V9", "record 1", code=4)
    assert not report.failed("V9")[0].message.startswith("manifest"), report.render()


def test_a_manifest_signed_by_another_key_is_named(world: bound.World) -> None:
    import io
    import tarfile

    from fiduciary.bundle.format import pack

    mandate, _ = _lineage(world)
    data = bk.export(world, mandate.mandate_id)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        members = {m.name: tar.extractfile(m).read() for m in tar.getmembers()}  # type: ignore[union-attr]
    manifest = fidu1.parse(members["manifest.json"])
    manifest.pop("sig")
    other = Ed25519Signer(bytes(range(32)))
    manifest["sig"] = fidu1.sign("bundle", manifest, other).to_json()
    members["manifest.json"] = fidu1.canonical(manifest)
    report = bk.verify(world, pack(members))
    _fails(report, "V9", "manifest.json", code=4)


def test_a_witness_that_never_cosigned_is_named(world: bound.World) -> None:
    mandate, _ = _lineage(world)
    data = bk.export(world, mandate.mandate_id)
    witness = verifier_of(Ed25519Signer(bytes(range(32))))
    report = bk.verify(world, data, witness=witness)
    _fails(report, "V6", "no cosignature of the checkpoint by the witness", code=8)


# -- V3 lineage, V4 compiled policy ------------------------------------------------------


def test_a_mandate_issued_one_approval_short_is_named(
    world: bound.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fiduciary.authority as authority

    mandate = kit.mandate(world)
    monkeypatch.setattr(authority, "_enough", lambda *a: None)
    world.issue(mandate, approvers=("cfo",))
    monkeypatch.undo()
    world.binder.bind(mandate.digest)
    fid = kit.fiduciary(world)
    with fid.session(bound.SCOPE, tenant="acme") as session:
        _refund(session, 7)
    report = _report(world, mandate)
    _fails(report, "V3", "(mandate.issued)", code=5)


def test_a_child_broader_than_its_parent_is_named(
    world: bound.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A child whose envelope is past what its parent delegates: derived,
    bound and funded, and no refinement of its parent (G2)."""
    import fiduciary.authority as authority

    parent, _ = _lineage(world)
    assert parent.delegation is not None
    wider = parent.delegation.envelope + 10
    raw = world.child_json(parent)
    raw["budget"] = {**raw["budget"], "envelope": format(wider, "f")}
    child = Mandate.from_json(raw, signed=False)
    monkeypatch.setattr(authority, "delegation_problems", lambda *a: [])
    world.binder.derive(child)
    digest = world.log.state().head(child.mandate_id)[1]  # type: ignore[index]
    world.binder.bind(digest)
    monkeypatch.undo()
    world.store.submit(b"not an approval")
    world.authority.process_intake()
    report = _report(world, world.binder.engine(child.mandate_id).mandate)
    _fails(report, "V3", "does not refine its parent", code=6)
    assert all(f.kind == "monotonicity" for f in report.failed("V3")), report.render()


def test_a_deployment_other_than_the_one_bound_is_named(world: bound.World) -> None:
    """The bundle's deployment holds another text for the recovery directive
    the mandate pins: the bound version no longer compiles from it."""
    mandate, _ = _lineage(world)
    directives = {d: f"{text} Then wait." for d, text in world.binder.deployment.directives.items()}
    other = dataclasses.replace(world.binder.deployment, directives=directives)
    data = bk.export(world, mandate.mandate_id, deployment=other)
    report = bk.verify(world, data)
    _fails(report, "V4", "does not compile", "its text does not hash", code=6)
    _only(report, "V4")


@pytest.mark.parametrize("legacy_first", [True, False])
def test_receipts_before_u1_are_checked_by_name_only_before_canonical_ones(
    world: bound.World, monkeypatch: pytest.MonkeyPatch, legacy_first: bool
) -> None:
    """Exported under an Interlock that digests sets canonically (U-1), V4 is
    strict, but for receipts anchored before the lineage's first canonical
    one: those an Interlock issued before U-1 are checked by name, and noted.
    A receipt after a canonical one has no such allowance (F6-8). Interlock
    1.0.2 issues no canonical receipt, so one receipt plays the part."""
    from agentgov.receipts import ActionReceipt, ReceiptBundle

    from fiduciary.bundle import verify as verifying
    from tests.test_bundle_tamper import _members, _remanifest, _tar

    mandate, fid = _lineage(world)
    with fid.session(bound.SCOPE, tenant="acme") as session:
        _refund(session, 9)
    _later(fid)
    members = dict(_members(bk.export(world, mandate.mandate_id)))
    manifest = fidu1.parse(members["manifest.json"])
    manifest["canonical_digests"] = True
    members["manifest.json"] = fidu1.canonical(manifest)
    data = _tar(_remanifest(sorted(members.items())))
    seqs = sorted(
        r.anchors.agentgov.seq
        for name, body in members.items()
        if name.startswith("receipts/")
        and name != "receipts/transitions.json"
        and isinstance(r := ReceiptBundle.loads(body).receipt, ActionReceipt)
        and r.anchors.agentgov is not None
    )
    assert len(seqs) >= 2
    legacy = seqs[0] if legacy_first else seqs[-1]
    from fiduciary.compiler import receipt_problems as problems

    def is_legacy(receipt: Any) -> bool:
        return bool(receipt.anchors.agentgov.seq == legacy)

    monkeypatch.setattr(verifying, "sets_canonical", lambda r, c: not is_legacy(r))

    def checked(receipt: Any, compiled: Any, *, strict: bool | None = None) -> list[str]:
        if strict and is_legacy(receipt):  # its set digest is 1.0.2's, not the canonical one
            return ["checker table_allowlist ran with another configuration"]
        return problems(receipt, compiled, strict=False)

    monkeypatch.setattr(verifying, "receipt_problems", checked)
    report = bk.verify(world, data)
    if legacy_first:
        assert not report.failed("V4"), report.render()
        assert any("before U-1" in note for note in report.notes), report.render()
    else:
        _fails(report, "V4", "table_allowlist ran with another configuration", code=6)

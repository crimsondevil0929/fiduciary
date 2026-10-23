"""Every refusal escalation's decisions make (§7.1 to §7.5), on SQLite and
PostgreSQL.

The service takes each decision from the state before its record, and
``verify`` takes it again (F10-1). These tests hold each decision to the
refusals that keep a ticket, a warrant or an execution from saying what the
rules would not: tickets that disagree with the lineage, approvals that must
never count, rules outside their vocabulary, warrants that overstate their
basis, and executions that do not answer their intent.
"""

from __future__ import annotations

import copy
import dataclasses
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest

from fiduciary import fidu1
from fiduciary.authority import Authority
from fiduciary.documents import Approval, Mandate, Resolution, Ticket, Warrant
from fiduciary.documents.mandate import AutoRule
from fiduciary.documents.warrant import RuleBasis, TicketRef, WarrantBasis
from fiduciary.exceptions import AuthorityError
from fiduciary.log import Log
from fiduciary.store import ApprovalRow, State
from tests import examples as e
from tests.conftest import Clock, approve
from tests.test_tamper_escalation import Escalated, _raise, _ticket, escalated_fixture  # noqa: F401
from tests.test_tickets_warrants import _dead_ticket_json, _refused_ticket_json


def _quote_ticket(clock: Clock, quote: str, log: Log | None = None, **changes: Any) -> Ticket:
    """The example extension ticket, for quote ``quote``: distinct evidence,
    observing ``log``'s head."""
    changes.setdefault("raised_at", fidu1.format_instant(clock()))
    if log is not None:
        changes.setdefault("observed", e.observed_json(log))
    raw = copy.deepcopy(e.ticket_json(ticket_id=str(uuid.uuid4()), **changes))
    raw["evidence"]["quote"]["id"] = quote
    for option, quorum in zip(raw["options"], (2, 1), strict=True):
        option["resolution"]["quote"] = quote
        option["digest"] = fidu1.digest(option["resolution"])
        option["quorum"] = quorum
    return Ticket.from_json(raw, signed=False).signed(e.SERVICE)


def _refuses(match: str, decision: Any, *args: Any) -> None:
    with pytest.raises(AuthorityError, match=match):
        decision(*args)


# -- raising (§7.1, F7-1, F7-2) --------------------------------------------------


def test_a_ticket_the_lineage_does_not_take_is_refused(world: Authority, clock: Clock) -> None:
    decide, log = world.decide, world.log
    raised = _ticket(clock, log)
    _raise(world, raised, clock)
    state = log.state()
    _refuses("raised already", decide.raise_ticket, state, raised.digest, clock())
    same = _quote_ticket(clock, "ext-91", log)  # another id, the same evidence
    log.put(same)
    _refuses(
        r"already open for this evidence \(F7-1\)", decide.raise_ticket, state, same.digest, clock()
    )

    early = _quote_ticket(
        clock, "ext-1", raised_at=fidu1.format_instant(clock() - timedelta(seconds=1))
    )
    log.put(early)
    _refuses("raised_at disagrees", decide.raise_ticket, state, early.digest, clock())

    blind = _quote_ticket(clock, "ext-4")  # observes a log record that is not the head
    log.put(blind)
    _refuses("observed log record 88", decide.raise_ticket, state, blind.digest, clock())
    behind = e.observed_json(log)
    previous = log.records()[-2]
    behind["log"] = {"seq": previous.seq, "hash": previous.hash}
    late = _quote_ticket(clock, "ext-5", observed=behind)  # read before the last record
    log.put(late)
    _refuses(f"not record {state.head_seq}", decide.raise_ticket, state, late.digest, clock())

    stale = _quote_ticket(clock, "ext-2", mandate={**e.mandate().ref.to_json(), "digest": "a" * 64})
    log.put(stale)
    _refuses("version in force", decide.raise_ticket, state, stale.digest, clock())

    raw = copy.deepcopy(_quote_ticket(clock, "ext-3", log).to_json())
    raw["options"][0]["quorum"] = 1
    raw.pop("sig")
    lenient = Ticket.from_json(raw, signed=False).signed(e.SERVICE)
    log.put(lenient)
    _refuses("has quorum 1, not 2", decide.raise_ticket, state, lenient.digest, clock())

    for n in range(4):  # the example mandate's max_open is 5
        _raise(world, _quote_ticket(clock, f"ext-storm-{n}", log), clock)
    storm = _quote_ticket(clock, "ext-storm-5", log)
    log.put(storm)
    _refuses(r"max_open bound \(F7-2\)", decide.raise_ticket, log.state(), storm.digest, clock())


def _amend_ticket(
    clock: Clock, log: Log, current: Mandate, proposed: Mandate, **evidence: Any
) -> Ticket:
    amend = {"action": "amend", "mandate": proposed.digest}
    reject = {"action": "reject", "mandate": proposed.digest}
    raw = e.ticket_json(
        ticket_id=str(uuid.uuid4()),
        raised_at=fidu1.format_instant(clock()),
        observed=e.observed_json(log),
        mandate=current.ref.to_json(),
        **{
            "class": "amend",
            "evidence": {
                "proposed": {"version": current.version + 1, "digest": proposed.digest},
                "wider": [],
                "narrower": [],
                **evidence,
            },
            "options": [
                {"resolution": amend, "digest": fidu1.digest(amend), "quorum": 2},
                {"resolution": reject, "digest": fidu1.digest(reject), "quorum": 1},
            ],
        },
    )
    return Ticket.from_json(raw, signed=False).signed(e.SERVICE)


def test_an_amend_ticket_names_the_next_version_and_its_true_diff(
    world: Authority, clock: Clock
) -> None:
    decide, log = world.decide, world.log
    current = e.mandate()
    raw = current.body()
    raw.update(version=2, prev=current.digest)
    raw["budget"] = {**raw["budget"], "envelope": "300.00000000"}
    wider = Mandate.from_json(raw, signed=False).signed(e.signer("cfo"))
    log.put(wider)
    hidden = _amend_ticket(clock, log, current, wider)  # its diff hides the wider envelope
    log.put(hidden)
    _refuses("does not match refines", decide.raise_ticket, log.state(), hidden.digest, clock())

    other = copy.deepcopy(raw)
    other["mandate_id"] = str(uuid.uuid4())
    elsewhere = Mandate.from_json(other, signed=False).signed(e.signer("cfo"))
    log.put(elsewhere)
    misplaced = _amend_ticket(clock, log, current, elsewhere)
    log.put(misplaced)
    _refuses("is not version 2", decide.raise_ticket, log.state(), misplaced.digest, clock())


# -- counting (§7.3) ---------------------------------------------------------------


def test_approvals_that_must_never_count_are_left_out(escalated: Escalated, clock: Clock) -> None:
    """Rows a store could hold only if written around the service: each fails
    one of §7.3's six conditions, and none counts, for approval or decline."""
    authority, log = escalated.authority, escalated.authority.log
    ticket = _quote_ticket(clock, "ext-count", authority.log)
    _raise(authority, ticket, clock)
    grant, decline = ticket.options
    state = log.state()
    seq = state.head_seq

    def row(principal: str, approval: Approval, *, at: int | None = None) -> ApprovalRow:
        log.put(approval)
        return ApprovalRow(
            digest=approval.digest,
            intake=0,
            principal=principal,
            decision=approval.decision,
            subject_type="ticket",
            subject_digest=ticket.digest,
            resolution=approval.resolution,
            seq=seq if at is None else at,
        )

    def signed(principal: str, decision: str, *, by: str | None = None, **changes: Any) -> Approval:
        raw = e.approval_json(
            principal,
            subject="ticket",
            digest=ticket.digest,
            resolution=(grant if decision == "approve" else decline).digest,
            decision=decision,
            approval_id=str(uuid.uuid4()),
            **changes,
        )
        return Approval.from_json(raw, signed=False).signed(e.signer(by or principal))

    for decision in ("approve", "decline"):
        crafted = State(
            **{f.name: copy.deepcopy(getattr(state, f.name)) for f in dataclasses.fields(State)}
        )
        crafted.approvals += [
            row("mallory", signed("mallory", decision)),  # no principal of the roster
            row(
                "cfo",
                signed("cfo", decision, charter={"charter_id": str(uuid.uuid4()), "version": 1}),
            ),
            row(
                "head-ops",
                signed("head-ops", decision, charter={"charter_id": e.CHARTER_ID, "version": 7}),
            ),
            row("security", signed("security", decision, by="mallory")),  # another key
        ]
        assert authority.decide.count_ticket(crafted, ticket, grant.resolution)[0] == []
        assert authority.decide.count_ticket_declines(crafted, ticket) == []
        # Recorded after one deadline and within the other: each deadline counts alone.
        issued = ticket.not_after - timedelta(hours=1)
        for approval_until, recorded in (
            (ticket.not_after + timedelta(hours=1), ticket.not_after + timedelta(seconds=1)),
            (ticket.not_after - timedelta(minutes=10), ticket.not_after - timedelta(minutes=5)),
            (ticket.not_after + timedelta(hours=1), ticket.not_after - timedelta(minutes=5)),
        ):
            late = State(
                **{f.name: copy.deepcopy(getattr(state, f.name)) for f in dataclasses.fields(State)}
            )
            window = {
                "issued_at": fidu1.format_instant(issued),
                "not_after": fidu1.format_instant(approval_until),
            }
            late.approvals.append(row("cfo", signed("cfo", decision, **window)))
            late.record_times[seq] = recorded
            in_time = recorded <= ticket.not_after and recorded <= approval_until
            expected = 1 if in_time else 0  # the last pair is in time: the control
            assert len(authority.decide.count_ticket(late, ticket, grant.resolution)[0]) == (
                expected if decision == "approve" else 0
            )
            assert len(authority.decide.count_ticket_declines(late, ticket)) == (
                expected if decision == "decline" else 0
            )
        suspended = State(
            **{f.name: copy.deepcopy(getattr(state, f.name)) for f in dataclasses.fields(State)}
        )
        suspended.approvals.append(row("cfo", signed("cfo", decision), at=seq + 1))
        suspended.suspended[("cfo", e.key("cfo"))] = seq  # suspended before the row
        assert authority.decide.count_ticket(suspended, ticket, grant.resolution)[0] == []
        assert authority.decide.count_ticket_declines(suspended, ticket) == []
    narrowed = Resolution(action="grant", quote="ext-other", amount=Decimal("1"))
    _refuses("no option or narrowing", authority.decide.count_ticket, state, ticket, narrowed)


def test_a_narrowing_must_narrow_an_option(world: Authority, clock: Clock) -> None:
    ticket = _quote_ticket(clock, "ext-narrow", world.log)
    _raise(world, ticket, clock)
    smaller = Resolution(action="grant", quote="ext-narrow", amount=Decimal("5"))
    larger = Resolution(action="grant", quote="ext-narrow", amount=Decimal("50"))
    world.propose_resolution(smaller)
    world.propose_resolution(larger)
    decide = world.decide
    assert decide.resolution_for_ticket(ticket, smaller.digest) == smaller
    _refuses("does not narrow any option", decide.resolution_for_ticket, ticket, larger.digest)
    _refuses("is no option of ticket", decide.resolution_for_ticket, ticket, "c" * 64)


# -- rules (§7.4, F7-9 to F7-11) ------------------------------------------------------


def _signed(raw: dict[str, Any]) -> Ticket:
    return Ticket.from_json(raw, signed=False).signed(e.SERVICE)


def test_a_rule_holds_only_inside_its_vocabulary(world: Authority) -> None:
    decide, state = world.decide, world.log.state()
    mandate = e.mandate()
    refused = _signed(_refused_ticket_json())
    accept = refused.options[0].resolution

    def rule(cls: str, resolution: str, **when: Any) -> AutoRule:
        return AutoRule("r", cls, resolution, when)

    def holds(r: AutoRule, ticket: Ticket, resolution: Resolution, m: Mandate = mandate) -> bool:
        return decide.rule_matches(state, m, ticket, r, resolution)

    assert holds(rule("refused", "accept_repair", exhaustive=True, max_dropped=1), refused, accept)
    assert not holds(rule("dead", "accept_repair"), refused, accept)
    assert not holds(rule("refused", "reject"), refused, accept)
    release = Resolution(action="release", messages=(), reset=None)
    assert not holds(rule("refused", "release"), refused, release)  # never autonomous
    other = Resolution(action="accept_repair", proposal="d" * 64)
    assert not holds(rule("refused", "accept_repair"), refused, other)  # no option of it
    assert not holds(rule("refused", "accept_repair", max_dropped=0), refused, accept)
    assert not holds(rule("refused", "accept_repair", causes=("depends",)), refused, accept)
    assert holds(rule("refused", "accept_repair", causes=("refused",)), refused, accept)
    exhaustive = _refused_ticket_json()
    exhaustive["evidence"]["exhaustive"] = False
    partial = _signed(exhaustive)
    assert not holds(rule("refused", "accept_repair", exhaustive=True), partial, accept)

    dead = _signed(_dead_ticket_json())
    requeue, cancel = (o.resolution for o in dead.options)
    assert dead.evidence.message.requeues == 1  # type: ignore[union-attr]
    assert holds(rule("dead", "requeue", max_requeues=2), dead, requeue)
    assert not holds(rule("dead", "requeue", max_requeues=1), dead, requeue)
    assert holds(rule("dead", "cancel", max_requeues=1), dead, cancel)
    many = _dead_ticket_json()
    many["evidence"]["message"]["requeues"] = 3
    assert not holds(rule("dead", "cancel", max_requeues=2), _signed(many), cancel)
    assert not holds(rule("dead", "requeue", outcome=("timeout",)), dead, requeue)

    extension = _signed(e.ticket_json())
    grant, decline = (o.resolution for o in extension.options)
    assert holds(rule("extension", "decline", max_amount=Decimal("19")), extension, decline)
    assert not holds(rule("extension", "decline", max_amount=Decimal("18")), extension, decline)
    no_auto = Mandate.from_json(
        e.mandate_json(extension={**e.mandate_json()["extension"], "auto": None}), signed=False
    )
    assert not holds(rule("extension", "grant"), extension, grant, no_auto)
    assert not holds(rule("extension", "grant"), extension, grant)  # 19 > max_grant 5


# -- forming (§4.6, §7.3, §7.4) ------------------------------------------------------


def _warrant(
    ticket: Ticket, resolution: Resolution, basis: WarrantBasis, clock: Clock, **changes: Any
) -> Warrant:
    fields: dict[str, Any] = {
        "warrant_id": str(uuid.uuid4()),
        "ticket": TicketRef(ticket.ticket_id, ticket.digest),
        "resolution": resolution,
        "resolution_digest": resolution.digest,
        "basis": basis,
        "formed_at": clock(),
        "not_after": ticket.not_after,
        **changes,
    }
    return Warrant(**fields).signed(e.SERVICE)


def test_a_warrant_that_overstates_its_basis_is_refused(escalated: Escalated, clock: Clock) -> None:
    authority, log = escalated.authority, escalated.authority.log
    decide = authority.decide
    state = log.state()
    _refuses("formed already", decide.form_warrant, state, escalated.warrant.digest, clock())

    def refused(match: str, warrant: Warrant, at: Any = None, s: State | None = None) -> None:
        log.put(warrant)
        _refuses(match, decide.form_warrant, s or log.state(), warrant.digest, at or clock())

    done = escalated.ticket
    refused(
        "is warranted already",
        _warrant(done, done.options[1].resolution, WarrantBasis(approvals=("a" * 64,)), clock),
    )
    unknown = _quote_ticket(clock, "ext-unraised")
    refused(
        "names no raised ticket",
        _warrant(
            unknown, unknown.options[1].resolution, WarrantBasis(approvals=("a" * 64,)), clock
        ),
    )

    ticket = _quote_ticket(clock, "ext-form", authority.log)
    _raise(authority, ticket, clock)
    _, decline = ticket.options
    by_quorum = WarrantBasis(approvals=("a" * 64,))
    refused(
        "disagrees with its record",
        _warrant(
            ticket, decline.resolution, by_quorum, clock, formed_at=clock() - timedelta(seconds=1)
        ),
    )
    other = Resolution(action="decline", quote="ext-elsewhere")
    refused("no option or narrowing", _warrant(ticket, other, by_quorum, clock))
    refused(r"needs 1 approvals; 0 count", _warrant(ticket, decline.resolution, by_quorum, clock))
    rule = WarrantBasis(rule=RuleBasis(mandate="b" * 64, rule_id="trim-refused"))
    refused("names another mandate", _warrant(ticket, decline.resolution, rule, clock))
    mandate = e.mandate()
    unknown_rule = WarrantBasis(rule=RuleBasis(mandate=mandate.digest, rule_id="nowhere"))
    refused("has no auto rule 'nowhere'", _warrant(ticket, decline.resolution, unknown_rule, clock))
    trim = WarrantBasis(rule=RuleBasis(mandate=mandate.digest, rule_id="trim-refused"))
    refused("does not admit this ticket", _warrant(ticket, decline.resolution, trim, clock))

    approve(log.store, ("cfo",), "ticket", ticket.digest, resolution=decline.digest)
    authority.process_intake()  # forms the decline warrant (quorum 1): read its basis
    state = log.state()
    (formed,) = [w for w in state.warrants.values() if w.ticket_id == ticket.ticket_id]
    found = log.store.document(formed.digest)
    assert found is not None
    basis = Warrant.loads(found[1]).basis
    before = State.of(log.records()[:-1])  # as the approval's record left it
    refused(
        "do not match the counted",
        _warrant(
            ticket, decline.resolution, WarrantBasis(approvals=(*basis.approvals, "f" * 64)), clock
        ),
        s=before,
    )
    refused(
        "not the minimum of its bounds",
        _warrant(ticket, decline.resolution, basis, clock),
        s=before,
    )
    late = clock() + timedelta(hours=2)
    refused(
        "expired at",
        _warrant(
            ticket,
            decline.resolution,
            basis,
            clock,
            formed_at=late,
            not_after=late + timedelta(seconds=1),
        ),
        at=late,
        s=before,
    )


# -- declining and lapsing (F7-7, F1-6, F7-14) ------------------------------------------


def test_declines_and_lapses_follow_the_ticket(escalated: Escalated, clock: Clock) -> None:
    authority, log = escalated.authority, escalated.authority.log
    decide = authority.decide
    state = log.state()
    _refuses("no ticket has digest", decide.decline_ticket, state, "e" * 64)
    _refuses("no ticket has digest", decide.lapse_ticket, state, "e" * 64, clock())
    done = escalated.ticket.digest
    _refuses("is warranted already", decide.decline_ticket, state, done)
    _refuses("execution is applied", decide.lapse_ticket, state, done, clock())

    ticket = _quote_ticket(clock, "ext-decline", authority.log)
    _raise(authority, ticket, clock)
    _refuses("still has a reachable option", decide.decline_ticket, log.state(), ticket.digest)
    _refuses("runs until", decide.lapse_ticket, log.state(), ticket.digest, clock())
    after = ticket.not_after
    log.append(
        "ticket.lapsed", decide.lapse_ticket(log.state(), ticket.digest, after), issued_at=after
    )
    _refuses("is lapsed already", decide.lapse_ticket, log.state(), ticket.digest, after)

    # A warrant with no intent lapses once it can no longer run.
    waiting = _quote_ticket(clock, "ext-waiting", authority.log)
    _raise(authority, waiting, clock)
    approve(log.store, ("cfo",), "ticket", waiting.digest, resolution=waiting.options[1].digest)
    authority.process_intake()
    (row,) = [w for w in log.state().warrants.values() if w.ticket_id == waiting.ticket_id]
    found = log.store.document(row.digest)
    assert found is not None
    warrant = Warrant.loads(found[1])
    _refuses("warrant runs until", decide.lapse_ticket, log.state(), waiting.digest, clock())
    assert (
        decide.lapse_ticket(log.state(), waiting.digest, warrant.not_after)["ticket"]
        == waiting.digest
    )


# -- executing (F7-12, §7.5) ----------------------------------------------------------------


def test_execution_records_answer_their_warrant(escalated: Escalated, clock: Clock) -> None:
    authority, log = escalated.authority, escalated.authority.log
    decide = authority.decide
    state = log.state()
    _refuses("no formed warrant", decide.execution_intent, state, "e" * 64, clock())
    digest = escalated.warrant.digest
    (_, intent, seq) = state.executions[digest][0]
    fixed = {
        k: intent[k]
        for k in ("warrant", "warrant_id", "ticket", "ticket_id", "mandate", "mandate_id", "action")
    }
    _refuses(
        "status 'applied', not 'intent'",
        decide.execution_outcome,
        state,
        "execution.applied",
        fixed,
    )
    before = State.of(log.records()[:-1])  # the intent open
    wrong_step = {
        **fixed,
        "intent": seq,
        "results": [{"step": 0, "kind": "reject"}],
        "mismatches": [],
    }
    _refuses(
        "do not follow its steps", decide.execution_outcome, before, "execution.refused", wrong_step
    )
    none = {**fixed, "intent": seq, "results": [], "mismatches": []}
    _refuses("a result for every step", decide.execution_outcome, before, "execution.applied", none)
    decide.execution_outcome(before, "execution.abandoned", {**none, "why": "x"})

    # A lapsed ticket's warrant runs no more.
    waiting = _quote_ticket(clock, "ext-lapsing", authority.log)
    _raise(authority, waiting, clock)
    approve(log.store, ("cfo",), "ticket", waiting.digest, resolution=waiting.options[1].digest)
    authority.process_intake()
    (row,) = [w for w in log.state().warrants.values() if w.ticket_id == waiting.ticket_id]
    crafted = log.state()
    crafted.ticket_events[waiting.ticket_id].append(
        ("lapsed", waiting.mandate.digest, crafted.head_seq + 1)
    )
    _refuses("is lapsed", decide.execution_intent, crafted, row.digest, clock())

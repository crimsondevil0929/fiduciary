"""Tampering with what escalation writes, on SQLite and PostgreSQL (§11
Phase 3, F10-1, F10-2, V8).

Each test starts from a log in which a ticket was raised, two principals
approved its grant, the warrant formed, and its execution was recorded.
It then forges, moves, deletes or reorders what that log holds, or edits a
projection around the service, and requires ``verify`` to name it. Some
edits are made as the database's owner, around the triggers. Others are
records signed with the service's own key, as a compromised service would
write them (A3): only replaying the rules names those.
"""

from __future__ import annotations

import copy
import dataclasses
from datetime import timedelta
from typing import Any

import pytest
from agentgov import BudgetManager
from agentgov.core import EntryType

from fiduciary import fidu1
from fiduciary.authority import Authority
from fiduciary.documents import Approval, Ticket, Warrant
from fiduciary.log import Log
from fiduciary.store import Store
from fiduciary.verify import verify_store
from tests import examples as e
from tests.conftest import Clock, approve
from tests.test_tamper import _names, _owner, _verify


class Escalated:
    def __init__(self, authority: Authority, ticket: Ticket, warrant: Warrant) -> None:
        self.authority = authority
        self.ticket = ticket
        self.warrant = warrant

    @property
    def store(self) -> Store:
        return self.authority.log.store

    def seq(self, kind: str) -> int:
        return next(r.seq for r in self.authority.log.records() if r.kind == kind)


def _ticket(clock: Clock, log: Log, **changes: Any) -> Ticket:
    changes.setdefault("observed", e.observed_json(log))
    raw = copy.deepcopy(e.ticket_json(raised_at=fidu1.format_instant(clock()), **changes))
    raw["options"][0]["quorum"] = 2  # the example mandate's grant quorum
    return Ticket.from_json(raw, signed=False).signed(e.SERVICE)


def _raise(authority: Authority, ticket: Ticket, clock: Clock) -> None:
    log = authority.log
    log.put(ticket)
    log.append("ticket.raised", authority.decide.raise_ticket(log.state(), ticket.digest, clock()))


@pytest.fixture(name="escalated")
def escalated_fixture(world: Authority, clock: Clock) -> Escalated:
    ticket = _ticket(clock, world.log)
    _raise(world, ticket, clock)
    grant = ticket.options[0]
    approve(world.log.store, ("cfo", "head-ops"), "ticket", ticket.digest, resolution=grant.digest)
    world.process_intake()
    state = world.log.state()
    (row,) = state.warrants.values()
    found = world.log.store.document(row.digest)
    assert found is not None
    warrant = Warrant.loads(found[1])
    log, decide = world.log, world.decide
    intent = log.append(
        "execution.intent", decide.execution_intent(log.state(), warrant.digest, clock())
    )
    fixed = ("warrant", "warrant_id", "ticket", "ticket_id", "mandate", "mandate_id", "action")
    log.append(
        "execution.applied",
        {
            **{k: intent.content[k] for k in fixed},
            "intent": intent.seq,
            "results": [{"step": 0, "kind": "grant", "quote": "ext-91", "record": "a" * 64}],
            "mismatches": [],
        },
    )
    return Escalated(world, ticket, warrant)


def test_an_untouched_escalation_verifies(escalated: Escalated, ledger: BudgetManager) -> None:
    report = _verify(escalated.store, ledger)
    assert report.ok, report.problems


def test_a_ticket_that_observed_another_ledger_is_named(
    world: Authority, ledger: BudgetManager, clock: Clock
) -> None:
    """``observed.ledger`` is held to the ledger: the entry it names, with the
    hash it names, and before the ticket's own anchor."""
    observed = e.observed_json(world.log)
    observed["ledger"]["head"] = "f" * 64
    forged = _ticket(clock, world.log, observed=observed)
    _raise(world, forged, clock)
    seq = world.log.records()[-1].seq
    _names(
        _verify(world.log.store, ledger),
        f"record {seq}: its ticket observed ledger entry {observed['ledger']['sequence']} as "
        "ffffffffffffffff, which the ledger does not hold",
    )


def test_a_ticket_anchored_before_what_it_observed_is_named(
    escalated: Escalated, ledger: BudgetManager
) -> None:
    """A ledger whose anchor of the ticket's record comes before the entry the
    ticket observed: the ledger's order and the ticket disagree."""
    ledger.refresh()
    entries = list(ledger.audit_trail())
    raised = escalated.seq("ticket.raised")
    seen = escalated.ticket.observed.ledger.sequence
    anchor = next(
        i for i, x in enumerate(entries) if x.memo.startswith(f"FIDU1 fiduciary {raised} ")
    )
    early = next(i for i, x in enumerate(entries) if x.sequence == seen)
    moved = dataclasses.replace(
        entries[early], memo=entries[anchor].memo, entry_type=EntryType.ANCHOR
    )
    entries[early], entries[anchor] = moved, dataclasses.replace(entries[anchor], memo="moved")
    report = verify_store(
        escalated.store,
        service_key=e.SERVICE.public_key(),
        pinned=e.charter().digest,
        ledger=entries,
    )
    _names(
        report,
        f"record {raised}: its ticket observed ledger entry {seen}, which is not before the "
        f"record's own anchor ({seen})",
    )


def test_a_forged_approval_is_named(
    escalated: Escalated, ledger: BudgetManager, clock: Clock
) -> None:
    """An approval in head-ops's name that another key signed, recorded by a
    service holding its own key: the rules refuse it when replayed."""
    authority, store = escalated.authority, escalated.store
    other = _ticket(clock, authority.log, ticket_id="4b1f3a52-8c0e-4e3a-9d7b-5f6a2c9e8d10")
    _raise(authority, other, clock)
    forged = Approval.from_json(
        e.approval_json(
            "head-ops", subject="ticket", digest=other.digest, resolution=other.options[1].digest
        ),
        signed=False,
    ).signed(e.signer("mallory"))
    intake = store.submit(forged.canonical())
    authority.log.put(forged)
    record = authority.log.append(
        "approval.recorded",
        {
            "intake": intake,
            "approval": forged.digest,
            "principal": "head-ops",
            "decision": "approve",
            "subject": forged.subject.to_json(),
            "resolution": forged.resolution,
        },
    )
    _names(
        _verify(store, ledger),
        f"record {record.seq} (approval.recorded): its rules refuse it now: the submission is "
        "refused now",
    )


def test_an_approval_moved_to_another_ticket_is_named(
    escalated: Escalated, ledger: BudgetManager, clock: Clock
) -> None:
    authority, store = escalated.authority, escalated.store
    other = _ticket(clock, authority.log, ticket_id="4b1f3a52-8c0e-4e3a-9d7b-5f6a2c9e8d10")
    _raise(authority, other, clock)
    # Around the service: the projection says the approval names the other ticket.
    _owner(
        store,
        [
            (
                "UPDATE {t}approvals SET subject_digest = ? WHERE subject_digest = ?",
                (other.digest, escalated.ticket.digest),
            )
        ],
    )
    _names(_verify(store, ledger), "projection approvals: differs")


def test_an_approval_recorded_against_another_ticket_is_named(
    escalated: Escalated, ledger: BudgetManager, clock: Clock
) -> None:
    """The service records a principal's approval of one ticket as an
    approval of another: its body is not what the rules write."""
    authority, store = escalated.authority, escalated.store
    other = _ticket(clock, authority.log, ticket_id="4b1f3a52-8c0e-4e3a-9d7b-5f6a2c9e8d10")
    _raise(authority, other, clock)
    approval = Approval.from_json(
        e.approval_json(
            "security",
            subject="ticket",
            digest=escalated.ticket.digest,
            resolution=escalated.ticket.options[1].digest,
        ),
        signed=False,
    ).signed(e.signer("security"))
    intake = store.submit(approval.canonical())
    authority.log.put(approval)
    record = authority.log.append(
        "approval.recorded",
        {
            "intake": intake,
            "approval": approval.digest,
            "principal": "security",
            "decision": "approve",
            "subject": {"type": "ticket", "digest": other.digest},
            "resolution": other.options[1].digest,
        },
    )
    _names(_verify(store, ledger), f"record {record.seq} (approval.recorded): its rules write")


def test_a_deleted_approval_is_named(escalated: Escalated, ledger: BudgetManager) -> None:
    """Deleting a counted approval, its submission and its document leaves a
    warrant whose basis no longer holds."""
    store = escalated.store
    (digest, _) = escalated.warrant.basis.approvals
    intake = next(
        int(r.content["intake"])
        for r in escalated.authority.log.records()
        if r.kind == "approval.recorded" and r.content["approval"] == digest
    )
    _owner(
        store,
        [
            ("DELETE FROM {t}intake WHERE id = ?", (intake,)),
            ("DELETE FROM {t}documents WHERE digest = ?", (digest,)),
        ],
    )
    _names(
        _verify(store, ledger),
        f"intake {intake} is not in the store",
        "references an approval that is not in the store",
        "(warrant.formed): its rules refuse it now",
    )


def test_a_reordered_escalation_is_named(escalated: Escalated, ledger: BudgetManager) -> None:
    """The warrant recorded before the approval that completed it."""
    store = escalated.store
    formed = escalated.seq("warrant.formed")
    _owner(
        store,
        [
            ("UPDATE {t}log SET seq = 1000 WHERE seq = ?", (formed,)),
            ("UPDATE {t}log SET seq = ? WHERE seq = ?", (formed, formed - 1)),
            ("UPDATE {t}log SET seq = ? WHERE seq = 1000", (formed - 1,)),
        ],
    )
    _names(_verify(store, ledger), f"expected record {formed - 1}", "does not link")


@pytest.mark.parametrize(
    ("table", "edit"),
    [
        ("tickets", "UPDATE {t}tickets SET event = 'open' WHERE event = 'warranted'"),
        ("tickets", "DELETE FROM {t}tickets WHERE event = 'raised'"),
        ("warrants", "UPDATE {t}warrants SET action = 'decline'"),
        ("warrants", "UPDATE {t}warrants SET basis = 'rule:trim-refused'"),
        ("executions", "UPDATE {t}executions SET event = 'abandoned' WHERE event = 'applied'"),
        ("executions", "DELETE FROM {t}executions WHERE event = 'intent'"),
    ],
)
def test_an_edited_escalation_projection_is_named(
    escalated: Escalated, ledger: BudgetManager, table: str, edit: str
) -> None:
    _owner(escalated.store, [(edit, ())])
    _names(_verify(escalated.store, ledger), f"projection {table}: differs")


def test_an_execution_written_twice_is_named(
    escalated: Escalated, ledger: BudgetManager, clock: Clock
) -> None:
    """A second intent for a warrant (F7-12), written by a service that
    skipped the rule, is named on replay."""
    log = escalated.authority.log
    (first,) = [r for r in log.records() if r.kind == "execution.intent"]
    clock.advance(seconds=1)
    second = log.append("execution.intent", dict(first.content))
    _names(
        _verify(escalated.store, ledger),
        f"record {second.seq} (execution.intent): its rules refuse it now",
        "already has an execution.intent",
    )


def test_a_lapse_after_its_deadline_passes_but_not_before(
    escalated: Escalated, ledger: BudgetManager, clock: Clock
) -> None:
    """A ticket left open lapses at its deadline (F1-6); a lapse written
    before it is named."""
    authority = escalated.authority
    other = _ticket(clock, authority.log, ticket_id="4b1f3a52-8c0e-4e3a-9d7b-5f6a2c9e8d10")
    _raise(authority, other, clock)
    body = {
        "ticket": other.digest,
        "ticket_id": other.ticket_id,
        "mandate": other.mandate.digest,
        "mandate_id": other.mandate.mandate_id,
        "class": other.ticket_class,
    }
    early = authority.log.append("ticket.lapsed", body)
    _names(
        _verify(escalated.store, ledger), f"record {early.seq} (ticket.lapsed): its rules refuse"
    )
    assert other.not_after - clock() > timedelta(0)

"""The state's indexes answer what its events say (§10.3, F10-1).

:class:`~fiduciary.store.State` keeps an index for each question a decision
asks (what is open, what is unexecuted, who decided what), so that asking costs
what the answer holds. These tests hold every index to the events it folds, over
logs in any order, odd ones among them (a log written around the service is
one the rules must still read the same way), and hold a state to staying as it
was once a copy of it is advanced.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from fiduciary import fidu1
from fiduciary.documents import Record
from fiduciary.store import TICKET_TERMINAL, State, TicketRow, WarrantRow
from tests import examples as e

T0 = e.at("2026-10-18T10:10:00")
TICKETS = ("t1", "t2", "t3")
WARRANTS = ("a" * 64, "b" * 64, "c" * 64, "d" * 64)
MANDATES = ("m" * 64, "n" * 64)
PRINCIPALS = ("cfo", "head-ops", "security")


def _ticket(ticket: str, mandate: str = MANDATES[0]) -> dict[str, Any]:
    return {
        "ticket": f"{ticket:0<64}",
        "ticket_id": ticket,
        "mandate": mandate,
        "mandate_id": "lineage",
        "class": "extension",
    }


def _op(draw: Any) -> tuple[str, dict[str, Any]]:
    kind = draw(
        st.sampled_from(
            ("raise", "form", "lapse", "decline", "intent", "outcome", "approve", "approve")
        )
    )
    ticket = draw(st.sampled_from(TICKETS))
    warrant = draw(st.sampled_from(WARRANTS))
    if kind == "raise":
        return "ticket.raised", _ticket(ticket, draw(st.sampled_from(MANDATES)))
    if kind in ("lapse", "decline"):
        body = _ticket(ticket)
        if kind == "decline":
            body["declines"] = []
        return f"ticket.{kind}d", body
    if kind == "form":
        rule = draw(st.booleans())
        mandate = draw(st.sampled_from(MANDATES))
        return "warrant.formed", {
            **_ticket(ticket, mandate),
            "warrant": warrant,
            "warrant_id": f"w-{warrant[:4]}",
            "action": draw(st.sampled_from(("grant", "grant", "requeue", "amend"))),
            "resolution": "r" * 64,
            "basis": {"rule": {"mandate": mandate, "id": "auto"}} if rule else {"approvals": []},
            "amount": draw(st.sampled_from((None, "1.50000000", "2.00000000"))),
        }
    if kind == "intent":
        return "execution.intent", {"warrant": warrant, "steps": []}
    if kind == "outcome":
        event = draw(st.sampled_from(("applied", "refused", "abandoned")))
        return f"execution.{event}", {"warrant": warrant, "results": [], "mismatches": []}
    return "approval.recorded", {
        "intake": draw(st.integers(1, 50)),
        "approval": draw(st.sampled_from(WARRANTS)),
        "principal": draw(st.sampled_from(PRINCIPALS)),
        "decision": draw(st.sampled_from(("approve", "decline"))),
        "subject": {"type": "ticket", "digest": f"{ticket:0<64}"},
        "resolution": None,
    }


@st.composite
def logs(draw: Any) -> list[Record]:
    ops = [_op(draw) for _ in range(draw(st.integers(0, 40)))]
    return [
        Record(
            log="fiduciary",
            seq=seq,
            kind=kind,
            issued_at=T0 + timedelta(seconds=seq),
            content=body,
            prev=fidu1.GENESIS,
        )
        for seq, (kind, body) in enumerate(ops, start=1)
    ]


# -- the answers, from the events alone -------------------------------------------------


def _status(state: State, ticket: str) -> str | None:
    if ticket not in state.tickets:
        return None
    ended = [e for e, _, _ in state.ticket_events.get(ticket, ()) if e in TICKET_TERMINAL]
    return ended[-1] if ended else "open"


def _ended_at(state: State, ticket: str) -> int | None:
    return next(
        (s for e, _, s in state.ticket_events.get(ticket, ()) if e in TICKET_TERMINAL), None
    )


def _open(state: State, mandate: str | None) -> list[TicketRow]:
    found = [
        row
        for row in state.tickets.values()
        if _status(state, row.ticket_id) == "open"
        and (mandate is None or row.mandate_id == mandate)
    ]
    return sorted(found, key=lambda r: r.seq)


def _intents(state: State) -> list[tuple[str, dict[str, Any], int]]:
    found = [
        (w, events[0][1], events[0][2])
        for w, events in state.executions.items()
        if len(events) == 1 and events[0][0] == "intent"
    ]
    return sorted(found, key=lambda item: item[2])


def _unexecuted(state: State) -> list[WarrantRow]:
    found = [row for d, row in state.warrants.items() if state.execution_status(d) is None]
    return sorted(found, key=lambda r: r.seq)


def _decisions(records: Sequence[Record], subject: str, cutoff: int | None) -> dict[str, str]:
    latest: dict[str, str] = {}
    for record in records:
        body = record.content
        if (
            record.kind == "approval.recorded"
            and body["subject"]["digest"] == subject
            and (cutoff is None or record.seq < cutoff)
        ):
            latest[body["principal"]] = body["approval"]
    return latest


def _cutoff(state: State, ticket: str) -> int | None:
    """Where a raised ticket's decisions stop counting: its first end."""
    return _ended_at(state, ticket) if ticket in state.tickets else None


def _granted(state: State, mandate: str, *, rule: bool) -> Decimal:
    total = Decimal("0.00000000")
    for row in state.warrants.values():
        if row.mandate_digest != mandate or row.action != "grant" or row.amount is None:
            continue
        status = state.execution_status(row.digest)
        if rule and "rule" in row.basis and status not in ("refused", "abandoned"):
            total += Decimal(row.amount)
        if not rule and status == "applied":
            total += Decimal(row.amount)
    return total


def _answers(state: State, records: Sequence[Record]) -> dict[str, Any]:
    tickets = {f"{t:0<64}": t for t in TICKETS}
    return {
        "status": {t: state.ticket_status(t) for t in TICKETS},
        "ended": {t: state.ticket_terminal_seq(t) for t in TICKETS},
        "open": [state.open_tickets(m) for m in (None, "lineage", "other")],
        "intents": state.open_intents(),
        "unexecuted": state.unexecuted_warrants(),
        "decisions": {
            d: {p: row.digest for p, row in state.decisions("ticket", d).items()} for d in tickets
        },
        "ticket_decisions": {
            d: {p: row.digest for p, row in state.ticket_decisions(d).items()} for d in tickets
        },
        "granted": [state.granted(m) for m in MANDATES],
        "auto": [state.auto_extension_total(m) for m in MANDATES],
        "requeues": sorted(state.warrants_by_action.get("requeue", ())),
    }


def _expected(state: State, records: Sequence[Record]) -> dict[str, Any]:
    tickets = {f"{t:0<64}": t for t in TICKETS}
    return {
        "status": {t: _status(state, t) for t in TICKETS},
        "ended": {t: _ended_at(state, t) for t in TICKETS},
        "open": [_open(state, m) for m in (None, "lineage", "other")],
        "intents": _intents(state),
        "unexecuted": _unexecuted(state),
        "decisions": {d: _decisions(records, d, None) for d in tickets},
        "ticket_decisions": {
            d: _decisions(records, d, _cutoff(state, ticket)) for d, ticket in tickets.items()
        },
        "granted": [_granted(state, m, rule=False) for m in MANDATES],
        "auto": [_granted(state, m, rule=True) for m in MANDATES],
        "requeues": sorted(d for d, row in state.warrants.items() if row.action == "requeue"),
    }


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(logs())
def test_every_index_answers_what_the_events_say(records: list[Record]) -> None:
    state = State.of(records)
    assert _answers(state, records) == _expected(state, records)


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(logs(), st.integers(0, 40))
def test_a_copy_advanced_leaves_the_state_it_was_taken_from(
    records: list[Record], cut: int
) -> None:
    """What :class:`~fiduciary.log.Log` returns once never changes: the next
    records are folded into a copy. And a state folded in two parts is the
    state folded at once."""
    before, after = records[:cut], records[cut:]
    kept = State.of(before)
    answered = _answers(kept, before)
    advanced = kept.copy()
    for record in after:
        advanced.apply(record)
    assert _answers(kept, before) == answered
    assert advanced == State.of(records)
    if after:
        simulated = kept.after(after[0].kind, after[0].content, issued_at=after[0].issued_at)
        assert simulated == State.of([*before, after[0]])
        assert _answers(kept, before) == answered

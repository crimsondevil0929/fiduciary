"""Tickets and warrants (``docs/FIDUCIARY_SPEC.md`` §4.4, §4.6, §7.2): strict
parsing, resolution narrowing (F4-20), warrant deadlines (F4-25), quorum mapping
and agreement checks."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

import pytest

from fiduciary import documents, fidu1
from fiduciary.documents import (
    Resolution,
    Ticket,
    Warrant,
    quorum_for_resolution,
    warrant_not_after,
)
from fiduciary.exceptions import MalformedError
from tests import examples as e


def _h(tag: str) -> str:
    return hashlib.sha256(tag.encode("utf-8")).hexdigest()


def _refused(cls: type[documents.Document], raw: dict[str, Any], code: str, path: str = "") -> None:
    with pytest.raises(MalformedError) as caught:
        cls.from_json(raw, signed=False)
    assert caught.value.code == code, caught.value
    assert caught.value.path.startswith(path), caught.value


def test_ticket_and_warrant_examples_round_trip() -> None:
    for doc in (e.ticket(), e.warrant()):
        again = documents.loads(doc.dumps())
        assert again == doc
        assert again.digest == doc.digest
        assert type(doc).loads(doc.pretty()) == doc


def _refused_ticket_json() -> dict[str, Any]:
    proposal = _h("proposal-1")
    accept = {"action": "accept_repair", "proposal": proposal}
    reject = {"action": "reject", "proposal": proposal}
    return e.ticket_json(
        **{
            "class": "refused",
            "evidence": {
                "receipt": {"id": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d", "digest": _h("rcpt")},
                "blocked_by": ["payload_amount_cap"],
                "proposal": proposal,
                "kept": ["debit-1"],
                "dropped": [{"id": "debit-2", "cause": "refused"}],
                "exhaustive": True,
                "trials": 3,
            },
            "options": [
                {"resolution": accept, "digest": fidu1.digest(accept), "quorum": 1},
                {"resolution": reject, "digest": fidu1.digest(reject), "quorum": 1},
            ],
        }
    )


def _held_ticket_json(spent_out: bool = True, with_halt: bool = True) -> dict[str, Any]:
    m1 = "11111111-2222-4333-8444-555555555555"
    m2 = "22222222-3333-4444-8555-666666666666"
    targets = [{"id": m1, "head": _h("h1")}, {"id": m2, "head": _h("h2")}]
    halt_scope = "support-agent" if with_halt else None
    release = {"action": "release", "messages": targets, "reset": halt_scope}
    cancel = {"action": "cancel", "messages": targets}
    options = [{"resolution": release, "digest": fidu1.digest(release), "quorum": 2}]
    if with_halt:
        reset = {"action": "reset", "scope": "support-agent"}
        options.append({"resolution": reset, "digest": fidu1.digest(reset), "quorum": 2})
    options.append({"resolution": cancel, "digest": fidu1.digest(cancel), "quorum": 1})
    return e.ticket_json(
        **{
            "class": "held",
            "evidence": {
                "messages": [
                    {
                        "id": m1,
                        "head": _h("h1"),
                        "sink": "stripe",
                        "operation": "refunds.create",
                        "payload_hash": _h("p1"),
                    },
                    {
                        "id": m2,
                        "head": _h("h2"),
                        "sink": "stripe",
                        "operation": "refunds.create",
                        "payload_hash": _h("p2"),
                    },
                ],
                "halt": None
                if not with_halt
                else {
                    "scope": "support-agent",
                    "reason": "exhausted" if spent_out else "velocity_exceeded",
                    "spent_out": spent_out,
                },
            },
            "options": options,
        }
    )


def _dead_ticket_json() -> dict[str, Any]:
    m1 = "11111111-2222-4333-8444-555555555555"
    target = [{"id": m1, "head": _h("h1")}]
    requeue = {"action": "requeue", "messages": target}
    cancel = {"action": "cancel", "messages": target}
    return e.ticket_json(
        **{
            "class": "dead",
            "evidence": {
                "message": {
                    "id": m1,
                    "head": _h("h1"),
                    "attempts": 5,
                    "requeues": 1,
                    "outcome": "retryable",
                }
            },
            "options": [
                {"resolution": requeue, "digest": fidu1.digest(requeue), "quorum": 2},
                {"resolution": cancel, "digest": fidu1.digest(cancel), "quorum": 1},
            ],
        }
    )


def _undo_ticket_json(late: bool = False) -> dict[str, Any]:
    m1 = "11111111-2222-4333-8444-555555555555"
    m2 = "22222222-3333-4444-8555-666666666666"
    targets = [{"id": m1, "head": _h("h1")}, {"id": m2, "head": _h("h2")}]
    action = "compensate_late" if late else "compensate"
    comp = {"action": action, "plan_id": "plan-77", "messages": targets}
    reject = {"action": "reject", "plan_id": "plan-77"}
    return e.ticket_json(
        **{
            "class": "undo",
            "evidence": {
                "plan_id": "plan-77",
                "messages": [
                    {
                        "id": m1,
                        "head": _h("h1"),
                        "not_after": "2026-10-18T10:30:00.000000Z",
                        "operation": "refunds.cancel",
                    },
                    {
                        "id": m2,
                        "head": _h("h2"),
                        "not_after": "2026-10-18T10:45:00.000000Z",
                        "operation": "refunds.cancel",
                    },
                ],
                "late": late,
            },
            "options": [
                {"resolution": comp, "digest": fidu1.digest(comp), "quorum": 2},
                {"resolution": reject, "digest": fidu1.digest(reject), "quorum": 1},
            ],
        }
    )


def _amend_ticket_json() -> dict[str, Any]:
    proposed = _h("mandate-v2")
    amend = {"action": "amend", "mandate": proposed}
    reject = {"action": "reject", "mandate": proposed}
    return e.ticket_json(
        **{
            "class": "amend",
            "evidence": {
                "proposed": {"version": 2, "digest": proposed},
                "wider": [{"path": "budget.envelope", "message": "300 > 250"}],
                "narrower": [],
            },
            "options": [
                {"resolution": amend, "digest": fidu1.digest(amend), "quorum": 2},
                {"resolution": reject, "digest": fidu1.digest(reject), "quorum": 1},
            ],
        }
    )


def test_all_six_ticket_classes_round_trip_and_compute_quorums() -> None:
    mandate = e.mandate()
    cases = [
        _refused_ticket_json(),
        _held_ticket_json(spent_out=True, with_halt=True),
        _held_ticket_json(spent_out=False, with_halt=True),
        _held_ticket_json(with_halt=False),
        _dead_ticket_json(),
        _undo_ticket_json(late=False),
        _undo_ticket_json(late=True),
        e.ticket_json(),
        _amend_ticket_json(),
    ]
    for raw in cases:
        tk = Ticket.from_json(raw, signed=False).signed(e.SERVICE)
        assert Ticket.loads(tk.dumps()) == tk
        assert tk.evidence_json() == raw["evidence"]
        for opt in tk.options:
            assert tk.option_for(opt.resolution) == opt
            assert tk.option_by_digest(opt.digest) == opt
            q = quorum_for_resolution(mandate, tk, opt.resolution)
            assert q >= 1
        assert tk.option_by_digest(_h("missing")) is None


def test_resolution_narrowing_rules() -> None:
    tk_ext = e.ticket()
    grant_full = tk_ext.options[0].resolution
    grant_small = Resolution.read({"action": "grant", "quote": "ext-91", "amount": "5.00000000"})
    grant_big = Resolution.read({"action": "grant", "quote": "ext-91", "amount": "25.00000000"})
    grant_other = Resolution.read({"action": "grant", "quote": "ext-92", "amount": "5.00000000"})
    decline = tk_ext.options[1].resolution

    assert grant_small.narrows(grant_full)
    assert grant_full.narrows(grant_full)
    assert not grant_big.narrows(grant_full)
    assert not grant_other.narrows(grant_full)
    assert not grant_small.narrows(decline)
    assert tk_ext.option_for(grant_small) == tk_ext.options[0]
    assert tk_ext.option_for(grant_big) is None

    tk_held = Ticket.from_json(_held_ticket_json(), signed=False)
    rel_all = tk_held.options[0].resolution
    rel_one = Resolution.read(
        {
            "action": "release",
            "messages": [{"id": "11111111-2222-4333-8444-555555555555", "head": _h("h1")}],
            "reset": "support-agent",
        }
    )
    rel_wrong_head = Resolution.read(
        {
            "action": "release",
            "messages": [{"id": "11111111-2222-4333-8444-555555555555", "head": _h("wrong")}],
            "reset": "support-agent",
        }
    )
    rel_wrong_reset = Resolution.read(
        {
            "action": "release",
            "messages": [{"id": "11111111-2222-4333-8444-555555555555", "head": _h("h1")}],
            "reset": None,
        }
    )
    assert rel_one.narrows(rel_all)
    assert not rel_wrong_head.narrows(rel_all)
    assert not rel_wrong_reset.narrows(rel_all)
    assert tk_held.option_for(rel_one) == tk_held.options[0]
    assert tk_held.option_for(rel_wrong_head) is None


def test_warrant_not_after_minimum_calculation() -> None:
    tk_ext = e.ticket()
    ap1 = e.approval(
        "head-ops",
        subject="ticket",
        digest=tk_ext.digest,
        resolution=tk_ext.options[0].digest,
        not_after="2026-10-18T10:20:00.000000Z",
    )
    ap2 = e.approval(
        "cfo",
        subject="ticket",
        digest=tk_ext.digest,
        resolution=tk_ext.options[0].digest,
        not_after="2026-10-18T10:15:00.000000Z",
    )
    assert warrant_not_after(tk_ext, tk_ext.options[0].resolution, (ap1, ap2)) == datetime(
        2026, 10, 18, 10, 15, tzinfo=UTC
    )

    tk_undo = Ticket.from_json(_undo_ticket_json(late=False), signed=False)
    assert warrant_not_after(tk_undo, tk_undo.options[0].resolution, ()) == datetime(
        2026, 10, 18, 10, 30, tzinfo=UTC
    )
    tk_late = Ticket.from_json(_undo_ticket_json(late=True), signed=False)
    assert warrant_not_after(tk_late, tk_late.options[0].resolution, ()) == tk_late.not_after


def test_ticket_and_warrant_agreement_refusals() -> None:
    _refused(
        Ticket,
        e.ticket_json(not_after="2026-10-18T09:00:00.000000Z"),
        "agreement",
        "$.not_after",
    )
    _refused(Ticket, e.ticket_json(options=[]), "range", "$.options")

    bad_refused = _refused_ticket_json()
    bad_refused["evidence"]["blocked_by"] = []
    _refused(Ticket, bad_refused, "range", "$.evidence.blocked_by")

    bad_refused_kept = _refused_ticket_json()
    bad_refused_kept["evidence"]["kept"] = []
    _refused(Ticket, bad_refused_kept, "range", "$.evidence.kept")

    bad_refused_dropped = _refused_ticket_json()
    bad_refused_dropped["evidence"]["dropped"] = []
    _refused(Ticket, bad_refused_dropped, "range", "$.evidence.dropped")

    bad_refused_prop = _refused_ticket_json()
    bad_refused_prop["options"][0]["resolution"] = {
        "action": "accept_repair",
        "proposal": _h("other"),
    }
    bad_refused_prop["options"][0]["digest"] = fidu1.digest(
        bad_refused_prop["options"][0]["resolution"]
    )
    _refused(Ticket, bad_refused_prop, "agreement", "$.options[0].resolution.proposal")

    bad_held = _held_ticket_json()
    bad_held["evidence"]["messages"] = []
    _refused(Ticket, bad_held, "range", "$.evidence.messages")

    bad_held_reset = _held_ticket_json(with_halt=False)
    bad_held_reset["options"][0]["resolution"]["reset"] = "support-agent"
    bad_held_reset["options"][0]["digest"] = fidu1.digest(
        bad_held_reset["options"][0]["resolution"]
    )
    _refused(Ticket, bad_held_reset, "agreement", "$.options[0].resolution.reset")

    bad_dead = _dead_ticket_json()
    bad_dead["options"][0]["resolution"]["messages"] = [
        {"id": "11111111-2222-4333-8444-555555555555", "head": _h("other")}
    ]
    bad_dead["options"][0]["digest"] = fidu1.digest(bad_dead["options"][0]["resolution"])
    _refused(Ticket, bad_dead, "agreement", "$.options[0].resolution.messages")

    bad_undo_msgs = _undo_ticket_json()
    bad_undo_msgs["evidence"]["messages"] = []
    _refused(Ticket, bad_undo_msgs, "range", "$.evidence.messages")

    bad_undo_plan = _undo_ticket_json()
    bad_undo_plan["options"][1]["resolution"] = {"action": "reject", "plan_id": "other"}
    bad_undo_plan["options"][1]["digest"] = fidu1.digest(bad_undo_plan["options"][1]["resolution"])
    _refused(Ticket, bad_undo_plan, "agreement", "$.options[1].resolution.plan_id")

    bad_undo_not_late = _undo_ticket_json(late=False)
    bad_undo_not_late["options"][0]["resolution"]["action"] = "compensate_late"
    bad_undo_not_late["options"][0]["digest"] = fidu1.digest(
        bad_undo_not_late["options"][0]["resolution"]
    )
    _refused(Ticket, bad_undo_not_late, "agreement", "$.options[0].resolution.action")

    bad_amend_same = _amend_ticket_json()
    same_digest = e.mandate().digest
    bad_amend_same["evidence"]["proposed"]["digest"] = same_digest
    for idx, act in enumerate(("amend", "reject")):
        bad_amend_same["options"][idx]["resolution"] = {"action": act, "mandate": same_digest}
        bad_amend_same["options"][idx]["digest"] = fidu1.digest(
            bad_amend_same["options"][idx]["resolution"]
        )
    _refused(Ticket, bad_amend_same, "agreement", "$.evidence.proposed.digest")

    bad_amend_opt = _amend_ticket_json()
    bad_amend_opt["options"][0]["resolution"] = {"action": "amend", "mandate": _h("other")}
    bad_amend_opt["options"][0]["digest"] = fidu1.digest(bad_amend_opt["options"][0]["resolution"])
    _refused(Ticket, bad_amend_opt, "agreement", "$.options[0].resolution.mandate")

    with pytest.raises(MalformedError) as caught:
        Resolution.read("not-a-dict")
    assert caught.value.code == "type"

    with pytest.raises(MalformedError) as caught:
        Resolution.read({"action": "cancel", "messages": []})
    assert caught.value.code == "range"

    _refused(Warrant, e.warrant_json(basis="not-a-dict"), "type", "$.basis")
    _refused(
        Warrant,
        e.warrant_json(not_after="2026-10-18T10:00:00.000000Z"),
        "agreement",
        "$.not_after",
    )

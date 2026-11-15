"""Undo's refusals (1.18.0): what an undo warrant does when what was kept changed, a row
moved, or nothing is kept any more; what the rules refuse to record; what a ticket and a
resolution hold; and what V8 and V14 say of a bundle that does not hold together."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import fiduciary
from fiduciary import cli, undo
from fiduciary.bridge import Fiduciary
from fiduciary.documents.ticket import (
    MessageTarget,
    Resolution,
    UndoEvidence,
    UndoMessage,
)
from fiduciary.documents.warrant import execution_steps
from fiduciary.exceptions import AuthorityError, FiduciaryError, MalformedError
from tests.test_undo import ROWS, _committed_plans, _govern, project, tickets  # noqa: F401

HASH = "a" * 64


def _commit_one(project: Path, ticket_id: int = 2) -> str:  # noqa: F811
    with fiduciary.open() as gov:
        close_ticket, _ = _govern(gov)
        close_ticket(ticket_id=ticket_id)
    return _committed_plans(project)[-1]


def _fid(project: Path) -> Fiduciary:  # noqa: F811
    return Fiduciary.from_config(project / ".fiduciary" / "fiduciary.toml")


def _ask_then(project: Path, plan_id: str, meddle: Any) -> dict[str, Any] | None:  # noqa: F811
    """Raise the undo ticket, meddle, then approve it as the owner: what the warrant did."""
    fid = _fid(project)
    try:
        ticket = undo.ask(fid, undo.find(fid, plan_id))
        meddle()
        assert undo.approve_solo(fid, project / ".fiduciary", ticket)
        return undo.outcome(fid, ticket)
    finally:
        fid.close()


def _sql(project: Path, statement: str) -> None:  # noqa: F811
    with closing(sqlite3.connect(project / "app.db")) as conn:
        conn.execute(statement)
        conn.commit()


def test_an_undo_warrant_puts_back_only_what_it_approved(project: Path) -> None:  # noqa: F811
    plan = _commit_one(project, 1)
    done = _ask_then(project, plan, lambda: _sql(project, "DELETE FROM _interlock_undo"))
    assert done is not None and done["event"] != "applied"
    assert "nothing is kept to undo" in json.dumps(done)

    plan = _commit_one(project, 2)
    done = _ask_then(
        project,
        plan,
        lambda: _sql(project, 'UPDATE _interlock_undo SET after = \'{"id": 2, "status": "x"}\''),
    )
    assert done is not None and "not what the ticket approved" in json.dumps(done)

    plan = _commit_one(project, 3)
    done = _ask_then(
        project, plan, lambda: _sql(project, "UPDATE tickets SET status = 'held' WHERE id = 3")
    )
    assert done is not None and "was not put back" in json.dumps(done)
    assert tickets(project)[2] == (3, "held")


def test_two_undos_each_find_their_own_record(project: Path) -> None:  # noqa: F811
    first, second = _commit_one(project, 1), _commit_one(project, 3)
    config = str(project / ".fiduciary" / "fiduciary.toml")
    assert cli.main(["--config", config, "undo", first]) == 0
    assert cli.main(["--config", config, "undo", second]) == 0
    assert tickets(project) == ROWS


def test_what_the_command_says_of_requests_and_of_other_principals(
    project: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from fiduciary import solo

    plan_id = _commit_one(project)
    config = str(project / ".fiduciary" / "fiduciary.toml")
    real = undo.find

    def find(fid: Any, plan: str, mandate: str | None = None) -> undo.Plan:
        found = real(fid, plan, mandate)
        found.compensate, found.cancel = ["m-1"], ["m-2"]
        found.cannot = [("m-3", "e3")]
        return found

    monkeypatch.setattr(undo, "find", find)
    assert cli.main(["--config", config, "undo", plan_id]) == 1
    said = capsys.readouterr()
    assert "request m-1: compensated" in said.out and "m-2: not sent yet" in said.out
    assert "was sent, and nothing undoes it" in said.out and "--partial" in said.err
    monkeypatch.undo()
    fid = _fid(project)
    try:
        solo.add_principals(fid, project / ".fiduciary", ["alice", "bob"])
    finally:
        fid.close()
    assert cli.main(["--config", config, "undo", plan_id]) == 0
    assert "the principals approve it in fiduciary console" in capsys.readouterr().out


def test_the_rules_refuse_an_undo_or_a_turn_record_that_does_not_hold(
    project: Path,  # noqa: F811
) -> None:
    mandate = json.loads((project / ".fiduciary" / "mandate.json").read_text())["mandate_id"]
    digest = ["tickets", "1", HASH, "b" * 64]
    inverse = ["tickets", "1", "b" * 64, HASH]
    good = {
        "mandate_id": mandate,
        "plan_id": "p1",
        "undo_plan_id": "p2",
        "original": [digest],
        "undo": [inverse],
        "why": "a test",
    }
    fid = _fid(project)
    try:
        decide, state = fid._authority.decide, fid.log.state()
        assert decide.undo_plan(state, good)["undo"] == [inverse]
        for bad, words in (
            ({"mandate_id": "nope"}, "no lineage"),
            ({"undo_plan_id": 7}, "names the plan"),
            ({"undo": "x"}, "a list of change digests"),
            ({"undo": [digest]}, "not the inverse"),
            ({"why": ""}, "says why"),
        ):
            with pytest.raises(AuthorityError, match=words):
                decide.undo_plan(state, {**good, **bad})
        turn = {"turn": "turn-1", "mandate_id": mandate, "outcome": "cancelled", "messages": []}
        with pytest.raises(AuthorityError, match="lists the requests"):
            decide.close_turn(state, turn)
        with pytest.raises(AuthorityError, match="did not finish puts"):
            decide.close_turn(
                state, {**turn, "outcome": "released", "kept": [{"plan": "p", "why": "w"}]}
            )
        with pytest.raises(AuthorityError, match="are \\{plan, undo, record\\}"):
            decide.close_turn(state, {**turn, "undone": [{"plan": "p"}]})
        with pytest.raises(AuthorityError, match=r"no plan\.undone of plan p"):
            decide.close_turn(state, {**turn, "undone": [{"plan": "p", "undo": "u", "record": 1}]})
        with pytest.raises(AuthorityError, match="not put back are"):
            decide.close_turn(state, {**turn, "kept": [{"plan": 5}]})
        kept = decide.close_turn(state, {**turn, "kept": [{"plan": "p", "why": "moved"}]})
        assert kept["kept"] == [{"plan": "p", "why": "moved"}]
    finally:
        fid.close()


def test_an_undo_ticket_and_resolution_keep_what_they_say() -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    target = MessageTarget("00000000-0000-0000-0000-000000000001", HASH)
    evidence = UndoEvidence(
        plan_id="p",
        messages=(UndoMessage(target.message_id, HASH, now, "refund"),),
        late=True,
        changes=(HASH, 2),
        pending=(MessageTarget("00000000-0000-0000-0000-000000000002", HASH),),
        cannot=(("00000000-0000-0000-0000-000000000003", "e9"),),
    )
    assert UndoEvidence.read(evidence.to_json(), "$") == evidence
    resolution = Resolution(
        action="undo",
        plan_id="p",
        changes=HASH,
        messages=(target,),
        cancels=(MessageTarget("00000000-0000-0000-0000-000000000002", HASH),),
        late=True,
    )
    assert Resolution.read(resolution.to_json()) == resolution
    with pytest.raises(MalformedError, match="only when true"):
        Resolution.read({**resolution.to_json(), "late": False})
    steps = execution_steps(
        SimpleNamespace(resolution=resolution),  # type: ignore[arg-type]
        SimpleNamespace(evidence=evidence),  # type: ignore[arg-type]
    )
    assert [s["kind"] for s in steps] == ["undo", "compensate_late", "cancel"]
    roster = SimpleNamespace(quorum=lambda name: {"compensate": 1, "compensate_late": 2}[name])
    from fiduciary.documents.ticket import resolution_quorum

    mandate = SimpleNamespace(roster=roster)
    assert resolution_quorum(mandate, evidence, resolution) == 2  # type: ignore[arg-type]


def test_an_undo_ticket_refuses_a_request_nothing_undoes(project: Path) -> None:  # noqa: F811
    from fiduciary.escalation import EscalationError

    mandate = json.loads((project / ".fiduciary" / "mandate.json").read_text())["mandate_id"]
    sent = SimpleNamespace(
        message_id="00000000-0000-0000-0000-000000000003",
        effect_id="e3",
        state="delivered",
        compensation=None,
        compensated_by=None,
    )
    fid = _fid(project)
    try:
        with pytest.raises(EscalationError, match="nothing undoes"):
            fid.escalation._raise_undo_plan(mandate, "p", (HASH, 1), [sent], [], None, None, False)
    finally:
        fid.close()


def test_an_engine_that_no_longer_governs_undoes_nothing(project: Path) -> None:  # noqa: F811
    from fiduciary.binding import BindingError

    plan = _commit_one(project)
    fid = _fid(project)
    try:
        (engine,) = fid.binder.engines().values()
        engine._clock = lambda: datetime.now(UTC) + timedelta(days=3650)
        with pytest.raises(BindingError, match="outside its term"):
            engine.undo(plan)
        engine.close()
        with pytest.raises(BindingError, match="no longer bound"):
            engine.undo(plan)
    finally:
        fid.close()


def test_v8_and_v14_say_what_does_not_hold() -> None:
    from interlock.chain import EscrowRecord, RecordType

    from fiduciary.bundle.verify import _Checks
    from fiduciary.documents import Record

    found: list[tuple[str, str]] = []

    def escrow(kind: RecordType, plan: str, note: str) -> EscrowRecord:
        return SimpleNamespace(record_type=kind, plan_id=plan, note=note, record_hash="h")  # type: ignore[return-value]

    def record(seq: int, body: dict[str, Any]) -> Record:
        return Record(
            log="fiduciary",
            seq=seq,
            kind="plan.undone",
            issued_at=datetime.now(UTC),
            content=body,
            prev="0" * 64,
        )

    digest = [["tickets", "1", HASH, "b" * 64]]
    checks = SimpleNamespace(
        lineages={"m"},
        escrow={
            "m": [
                escrow(RecordType.PLAN_ADMITTED, "u", "undo of other: put it back"),
                escrow(RecordType.COMMITTED, "u", "1 row committed"),
                escrow(RecordType.PLAN_ADMITTED, "v", "undo of p: put it back"),
                escrow(RecordType.COMMITTED, "v", "1 row committed"),
            ]
        },
        chain=[
            record(
                1,
                {
                    "mandate_id": "m",
                    "plan_id": "p",
                    "undo_plan_id": "u",
                    "original": digest,
                    "undo": digest,
                    "why": "w",
                },
            ),
            record(
                2,
                {
                    "mandate_id": "elsewhere",
                    "plan_id": "p",
                    "undo_plan_id": "x",
                    "original": digest,
                    "undo": digest,
                    "why": "w",
                },
            ),
        ],
        add=lambda check, kind, message, label="": found.append((check, message)),
    )
    _Checks._v14(checks)  # type: ignore[arg-type]
    said = " | ".join(m for _, m in found)
    assert "the plan p did not commit" in said
    assert "the undo u's change digests are not the ones its commit names" in said
    assert "plan u does not say it undoes p" in said
    assert "is not the inverse" in said
    assert "plan v committed as the undo of p, and no plan.undone record explains it" in said

    found.clear()
    v8 = SimpleNamespace(
        by_seq={3: record(3, {"why": "FIDU1 warrant w", "plan_id": "q"})}, add=checks.add
    )
    step = {"plan_id": "p"}
    _Checks._v8_step(v8, "w", 9, step, {"step": 0, "kind": "undo", "record": "r"}, {}, {}, set())  # type: ignore[arg-type]
    _Checks._v8_step(
        v8,  # type: ignore[arg-type]
        "w",
        9,
        step,
        {"step": 0, "kind": "undo", "record": "r", "undone": 3},
        {},
        {},
        {"r"},
    )
    assert [m for _, m in found] == [
        "record 9: step 0 (undo): the undo's commit is not in the escrow chain",
        "record 9: step 0 (undo): no plan.undone record of this warrant's",
    ]


def test_a_failed_undo_is_said(project: Path) -> None:  # noqa: F811
    with pytest.raises(FiduciaryError):
        fid = _fid(project)
        try:
            undo.find(fid, "plan-none")
        finally:
            fid.close()

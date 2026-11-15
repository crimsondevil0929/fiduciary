"""Undo (1.18.0): what a plan changed, put back exactly, by a plan of its own.

A solo deployment on a SQLite database, which ``fiduciary init`` writes with ``[undo] days =
7``: each commit keeps its before and after images in that database, for a week.

- A turn that does not finish puts back what its committed calls changed, latest first,
  each by an undo that is exactly the inverse of its plan; each writes a ``plan.undone``
  record, and the ``turn.closed`` record lists them.
- A row someone changed since cannot be put back exactly: it stays as it is, and the
  ``turn.closed`` record names it, with why.
- The audit bundle verifies offline: V14 holds every undo to the change digests its plan's
  and its own commits named in the escrow chain.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

import fiduciary
from fiduciary import cli
from fiduciary.bridge import Fiduciary
from fiduciary.init import Target, init
from fiduciary.sdk import Governor

ROWS = [(1, "open"), (2, "open"), (3, "open")]


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "helpdesk"
    root.mkdir()
    with closing(sqlite3.connect(root / "app.db")) as conn:
        conn.executescript("CREATE TABLE tickets (id INTEGER PRIMARY KEY, status TEXT NOT NULL);")
        conn.executemany("INSERT INTO tickets VALUES (?, ?)", ROWS)
        conn.commit()
    monkeypatch.chdir(root)
    monkeypatch.delenv("FIDUCIARY_CONFIG", raising=False)
    init(root / ".fiduciary", target=Target("sqlite", database=str(root / "app.db")), solo=True)
    return root


def tickets(project: Path) -> list[tuple[Any, ...]]:
    with closing(sqlite3.connect(project / "app.db")) as conn:
        return list(conn.execute("SELECT id, status FROM tickets ORDER BY id"))


def records(project: Path, kind: str) -> list[dict[str, Any]]:
    fid = Fiduciary.from_config(project / ".fiduciary" / "fiduciary.toml")
    try:
        return [r.content for r in fid.log.records() if r.kind == kind]
    finally:
        fid.close()


def _govern(gov: Governor) -> tuple[Any, Any]:
    @gov.tool
    def close_ticket(db: fiduciary.Sql, ticket_id: int) -> str:
        """Close one ticket."""
        db.execute("UPDATE tickets SET status = 'closed' WHERE id = :id", {"id": ticket_id})
        return f"closed {ticket_id}"

    @gov.tool
    def reopen_all(db: fiduciary.Sql) -> str:
        """Reopen every ticket."""
        db.execute("UPDATE tickets SET status = 'reopened'")
        return "reopened"

    return close_ticket, reopen_all


def test_init_keeps_a_week_of_undo_in_the_database(project: Path) -> None:
    config = (project / ".fiduciary" / "interlock.toml").read_text()
    assert "[undo]\ndays = 7" in config


def test_a_turn_that_does_not_finish_puts_its_rows_back(project: Path) -> None:
    with fiduciary.open() as gov:
        close_ticket, reopen_all = _govern(gov)
        with pytest.raises(fiduciary.Refused, match="blast_radius"), gov.turn() as turn:
            close_ticket(ticket_id=1)
            close_ticket(ticket_id=3)
            assert tickets(project) == [(1, "closed"), (2, "open"), (3, "closed")]
            reopen_all()  # three rows, where the mandate allows one: refused
        assert turn.closed is not None and turn.closed.outcome == "cancelled"
        assert len(turn.closed.undone) == 2 and turn.closed.kept == ()
    assert tickets(project) == ROWS
    undone = records(project, "plan.undone")
    assert len(undone) == 2 and all("did not finish" in u["why"] for u in undone)
    assert [u["plan_id"] for u in undone] == list(turn.closed.undone)  # latest first
    (closed,) = records(project, "turn.closed")
    assert [u["plan"] for u in closed["undone"]] == list(turn.closed.undone)
    assert closed["messages"] == []


def test_a_row_changed_since_stays_and_is_named(project: Path) -> None:
    with fiduciary.open() as gov:
        close_ticket, _ = _govern(gov)
        with pytest.raises(RuntimeError), gov.turn() as turn:
            close_ticket(ticket_id=2)
            with closing(sqlite3.connect(project / "app.db")) as conn:
                conn.execute("UPDATE tickets SET status = 'escalated' WHERE id = 2")
                conn.commit()
            raise RuntimeError("stopped")
        assert turn.closed is not None and turn.closed.undone == ()
        ((plan, why),) = turn.closed.kept
        assert "tickets 2 was not put back" in why
    assert tickets(project) == [(1, "open"), (2, "escalated"), (3, "open")]
    (closed,) = records(project, "turn.closed")
    assert closed["kept"][0]["plan"] == plan


def test_a_turn_that_finishes_keeps_what_it_changed(project: Path) -> None:
    with fiduciary.open() as gov:
        close_ticket, _ = _govern(gov)
        with gov.turn() as turn:
            close_ticket(ticket_id=3)
        assert turn.closed is None  # no request held, nothing put back: nothing recorded
    assert tickets(project) == [(1, "open"), (2, "open"), (3, "closed")]
    assert records(project, "plan.undone") == []


def _verdicts(project: Path, name: str) -> tuple[int, dict[str, Any]]:
    """The deployment's bundle, exported and verified offline: ``verify-bundle``'s exit, and
    (with Node) the browser's checks. Kept for the golden bundles under
    ``FIDUCIARY_WRITE_BUNDLES`` (``scripts/make_bundles.py``)."""
    root = project / ".fiduciary"
    mandate = json.loads((root / "mandate.json").read_text())["mandate_id"]
    bundle = project / f"{name}.fidb"
    argv = ["--config", str(root / "fiduciary.toml"), "export-bundle", "--mandate", mandate]
    ledger_key = str(root / "keys" / "ledger-checkpoints.key")
    assert cli.main([*argv, "--ledger-key", ledger_key, "--out", str(bundle)]) == 0
    charter = (root / "charter.digest").read_text().strip()
    keys = root / "pinned-keys.json"
    code = cli.main(["verify-bundle", str(bundle), "--charter", charter, "--keys", str(keys)])
    out = os.environ.get("FIDUCIARY_WRITE_BUNDLES")
    if out:
        test = os.environ["PYTEST_CURRENT_TEST"].split(" ")[0].rsplit("::", 1)[-1]
        shutil.copyfile(bundle, Path(out) / f"{test}.fidb")
        sidecar = {
            "charter": charter,
            "keys": json.loads(keys.read_text()),
            "witness": None,
            "skew_seconds": 5,
        }
        (Path(out) / f"{test}.json").write_text(json.dumps(sidecar, sort_keys=True))
    node = shutil.which("node")
    if node is None:
        return code, {}
    harness = Path(__file__).resolve().parent / "js" / "verify_vectors.cjs"
    listing = project / f"{name}.json"
    listing.write_text(json.dumps([{"name": name, "file": bundle.name, "keys": None}]))
    done = subprocess.run(  # noqa: S603
        [node, str(harness), str(listing)], capture_output=True, text=True, check=False, timeout=120
    )
    assert done.returncode == 0, done.stderr
    browser: dict[str, Any] = json.loads(done.stdout)[name]
    return code, browser


def test_the_bundle_holds_every_undo_to_its_plan_v14(project: Path) -> None:
    with fiduciary.open() as gov:
        close_ticket, reopen_all = _govern(gov)
        with pytest.raises(fiduciary.Refused), gov.turn():
            close_ticket(ticket_id=1)
            reopen_all()
    code, browser = _verdicts(project, "undone")
    assert code == 0
    if browser:
        assert browser["checks"].get("undo") == "pass", browser["failing"]
        assert not browser["failing"]


def test_an_undo_whose_digests_are_not_its_commits_fails_v14(project: Path) -> None:
    """A service that wrote an undo record whose digests are each other's inverse, but not
    the ones the plans' commits named: the rules it is replayed under pass it; V14 does not."""
    with fiduciary.open() as gov:
        close_ticket, reopen_all = _govern(gov)
        with pytest.raises(fiduciary.Refused), gov.turn():
            close_ticket(ticket_id=2)
            reopen_all()
    fid = Fiduciary.from_config(project / ".fiduciary" / "fiduciary.toml")
    try:
        (real,) = [r.content for r in fid.log.records() if r.kind == "plan.undone"]
        made_up = "e" * 64
        forged = {
            **real,
            "original": [[d[0], d[1], d[2], made_up] for d in real["original"]],
            "undo": [[d[0], d[1], made_up, d[3]] for d in real["undo"]],
        }
        with fid.log.store.transaction():
            body = fid._authority.decide.undo_plan(fid.log.state(), forged)
            fid.log.append("plan.undone", body, issued_at=fid.log.now())
    finally:
        fid.close()
    code, browser = _verdicts(project, "forged")
    assert code == 6
    if browser:
        assert browser["checks"].get("undo") == "fail"
        assert any("are not the ones its commit names" in f for f in browser["failing"])


def test_fiduciary_undo_puts_a_plan_back_by_the_owners_warrant(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with fiduciary.open() as gov:
        close_ticket, _ = _govern(gov)
        close_ticket(ticket_id=2)
        (plan_id,) = [o for o in _committed_plans(project)]
    assert tickets(project)[1] == (2, "closed")
    config = str(project / ".fiduciary" / "fiduciary.toml")
    assert cli.main(["--config", config, "undo", plan_id]) == 0
    said = capsys.readouterr().out
    assert "1 change put back exactly as it was" in said and "undone      plan" in said
    assert tickets(project) == ROWS
    (record,) = records(project, "plan.undone")
    assert record["plan_id"] == plan_id and record["why"].startswith("FIDU1 warrant ")
    # asked again: the plan's images are still kept, but the rows no longer hold its after
    # image, so the undo is not exact and is refused
    assert cli.main(["--config", config, "undo", plan_id]) == 1
    assert "not undone" in capsys.readouterr().err
    assert cli.main(["--config", config, "undo", "plan-nothing"]) == 1
    assert "nothing is kept to undo plan plan-nothing" in capsys.readouterr().err
    code, browser = _verdicts(project, "warranted")
    assert code == 0
    if browser:
        assert browser["checks"].get("undo") == "pass"


def _committed_plans(project: Path) -> list[str]:
    """Every plan the lineage's escrow chain committed, in order."""
    from interlock.chain import EscrowChain, RecordType

    chains = sorted((project / ".fiduciary" / "chains").glob("*.chain"))
    found: list[str] = []
    for path in chains:
        chain = EscrowChain.load(path)
        found += [str(r.plan_id) for r in chain.records() if r.record_type is RecordType.COMMITTED]
    return found


def test_the_console_asks_an_undo_and_the_owner_approves_it_on_the_desk(
    project: Path, tmp_path: Path
) -> None:
    from fiduciary.control import ConfiguredDeployment, ControlError
    from fiduciary.keys import load_key

    with fiduciary.open() as gov:
        close_ticket, _ = _govern(gov)
        close_ticket(ticket_id=1)
    root = project / ".fiduciary"
    desk = ConfiguredDeployment("d", root / "fiduciary.toml", scratch=tmp_path, accepts=True)
    try:
        (row,) = [r for r in desk.profile()["receipts"] if r.get("plan_id")]
        asked = desk.undo({"plan_id": row["plan_id"]})
        assert asked["changes"] == 1 and asked["cannot"] == []
        with pytest.raises(ControlError, match="nothing is kept"):
            desk.undo({"plan_id": "plan-unknown"})
        vote = desk.draft({"ticket_id": asked["ticket_id"], "principal": "owner", "option": "undo"})
        key = load_key(root / "keys" / "owner.key")
        signature = key.sign(bytes.fromhex(vote["signing_input"])).hex()
        queued = desk.accept(
            {"approval": vote["approval"], "key_id": vote["key_id"], "signature": signature}
        )
        assert queued["intake"] > 0
    finally:
        desk.close()
    fid = Fiduciary.from_config(root / "fiduciary.toml")
    try:
        fid.serve_once()  # the writer records the approval, forms the warrant, executes it
    finally:
        fid.close()
    assert tickets(project) == ROWS
    assert len(records(project, "plan.undone")) == 1

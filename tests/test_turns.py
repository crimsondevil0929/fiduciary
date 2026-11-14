"""Turns (1.17.0): the requests a turn's calls enqueue wait for its end.

A solo deployment with an MCP server's write sink, and a tool in this process that
writes a row and enqueues one request per call. Within ``gov.turn()``:

- every request waits, held for the turn, while the turn is open;
- a turn that ends with every call committed releases them, under one operator intent
  that cites its ``turn.closed`` record;
- one that ends in an exception (or a refused call) cancels them: none is ever made;
- the audit bundle verifies offline: V7 holds each release and cancel to its record.

Then the same through a protected server in another process: ``fiduciary turn open``
and ``close``, as Claude Code's hooks run them.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

import fiduciary
from fiduciary import cli, turns
from fiduciary.exceptions import FiduciaryError
from fiduciary.init import init
from fiduciary.sdk import Turn

TOOLS = [
    {
        "name": "create_note",
        "description": "Create a note.",
        "inputSchema": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    }
]


@pytest.fixture
def deployment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "notes"
    init(root, None, discovered=TOOLS, solo=True, name="notes")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FIDUCIARY_CONFIG", str(root / "fiduciary.toml"))
    return root


def _states(root: Path) -> dict[str, str]:
    with closing(sqlite3.connect(root / "back_office.db")) as conn:
        return {
            str(m): str(s)
            for m, s in conn.execute("SELECT message_id, state FROM _interlock_outbox_state")
        }


def _govern(gov: Any) -> Any:
    @gov.tool
    def note(db: fiduciary.Sql, title: str) -> str:
        """Ask the notes server to create a note."""
        db.request("upstream", "create_note", {"title": title})
        return f"asked for {title}"

    return note


def test_a_turn_releases_its_requests_only_when_it_ends_well(deployment: Path) -> None:
    with fiduciary.open() as gov:
        note = _govern(gov)
        with gov.turn() as turn:
            note(title="first")
            note(title="second")
            assert set(_states(deployment).values()) == {"held"}, "nothing goes out mid-turn"
        assert turn.closed is not None and turn.closed.outcome == "released"
        assert len(turn.closed.messages) == 2
        assert set(_states(deployment).values()) == {"pending"}, "released: the relay makes them"
        with pytest.raises(RuntimeError), gov.turn() as failed:
            note(title="third")
            raise RuntimeError("the agent loop failed after the call")
        assert failed.closed is not None and failed.closed.outcome == "cancelled"
        (cancelled,) = failed.closed.messages
        assert _states(deployment)[cancelled] == "cancelled"
        with gov.turn() as empty:
            pass
        assert empty.closed is None, "a turn that held nothing records nothing"
        with pytest.raises(FiduciaryError, match="do not nest"), gov.turn():
            with gov.turn():
                pass
    _verifies(deployment)


def _verifies(root: Path) -> None:
    mandate = json.loads((root / "mandate.json").read_text())["mandate_id"]
    bundle = root.parent / "turns.fidb"
    argv = ["--config", str(root / "fiduciary.toml"), "export-bundle", "--mandate", mandate]
    ledger_key = str(root / "keys" / "ledger-checkpoints.key")
    assert cli.main([*argv, "--ledger-key", ledger_key, "--out", str(bundle)]) == 0
    charter = (root / "charter.digest").read_text().strip()
    keys = str(root / "pinned-keys.json")
    assert cli.main(["verify-bundle", str(bundle), "--charter", charter, "--keys", keys]) == 0


def test_a_turn_file_is_opened_once_and_closed_by_what_its_calls_did(
    deployment: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    turn = turns.open_turn(deployment)
    assert turns.open_turn(deployment) == turn, "a prompt submitted again is the same turn"
    with fiduciary.open() as gov:
        note = _govern(gov)
        gov._turn = Turn(turn)  # as a proxy does, from the turn file
        note(title="queued")
        gov._turn = None
    turns.note_refused(deployment, "create_note was refused")
    config = str(deployment / "fiduciary.toml")
    assert cli.main(["--config", config, "turn", "close"]) == 0
    said = capsys.readouterr().out
    assert "cancelled" in said and turn in said
    assert turns.current(deployment) is None
    assert set(_states(deployment).values()) == {"cancelled"}
    assert cli.main(["--config", config, "turn", "close"]) == 0  # nothing open: nothing done

"""Incident replays (1.20.0): each runs through ``fiduciary protect`` against its stand-ins,
ends as the public pattern would not have, and is what the site's ``/replays/`` page shows,
word for word (``scripts/record_replays.py`` recorded it)."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

import pytest

from fiduciary.cli import main
from fiduciary.replays import REPLAYS, run

RECORDED = {
    r["key"]: r for r in json.loads((files("fiduciary.report") / "replays.json").read_text())
}


@pytest.mark.parametrize("key", list(REPLAYS))
def test_each_replay_is_what_the_page_shows(key: str, tmp_path: Path) -> None:
    replay = run(key, tmp_path)
    assert replay.to_json() == RECORDED[key], "run scripts/record_replays.py and read the diff"
    assert replay.bundles and all(v.startswith("verifies") for v in replay.bundles.values())
    said = [s.text for s in replay.steps if s.actor == "fiduciary"]
    if key == "destructive-call":
        assert any("waits for its owner's approval" in t for t in said)
        assert any("carries a credential this session read" in t for t in said)
        volumes = json.loads((tmp_path / key / "volumes.json").read_text())
        assert volumes == ["prod-db", "staging-db"], "the volume was not deleted"
        config = (tmp_path / key / ".mcp.json").read_text()
        assert "cld_" not in config, "the token left the client's file"
    if key == "toxic-flow":
        assert any(t.startswith("Held for the principals") for t in said)
        assert any("untrusted input and sensitive data" in t for t in said)
        assert not (tmp_path / key / "published.txt").exists()
    if key == "rug-pull":
        assert sum("changed how it describes" in t for t in said) == 2
        owner = [s.text for s in replay.steps if s.actor == "owner"]
        assert any("bcc: added" in t for t in owner) and any(
            "flagged delete_thread" in t for t in owner
        )


def test_the_replay_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["replay", "rug-pull", "--out", str(tmp_path)]) == 0
    shown = capsys.readouterr().out
    assert "A silent MCP server update" in shown and "bundle     mailer: verifies" in shown
    assert main(["replay", "rug-pull", "--out", str(tmp_path)]) == 1, "never over a replay"
    capsys.readouterr()
    assert main(["replay", "toxic-flow", "--out", str(tmp_path / "j"), "--json"]) == 0
    (found,) = json.loads(capsys.readouterr().out)
    assert found["key"] == "toxic-flow" and found["steps"]

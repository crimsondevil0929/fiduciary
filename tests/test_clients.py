"""Client files (1.20.0): VS Code's ``servers`` and Codex CLI's ``config.toml``, read and
rewritten by ``fiduciary protect``, with everything else in each file kept."""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest

from fiduciary import clients, protect
from fiduciary.clients import ClientFileError, read_entries, remote_of, toml_table, write_entries
from fiduciary.protect import read_servers
from tests.http_kit import StandIn
from tests.test_protect import STUB, Client

CODEX = """# my Codex settings
model = "gpt-5"

[mcp_servers.notes]
command = "@PYTHON@"
args = ["@STUB@"]
env = { NOTES_FILE = "@NOTES@", PHASE_FILE = "@PHASE@" }
startup_timeout_sec = 20
enabled_tools = ["search_notes", "createNote", "delete_note", "list_notes"]

# the docs server is remote
[mcp_servers.docs]
url = "@URL@"
bearer_token_env_var = "DOCS_TOKEN"
http_headers = { "X-Team" = "blue" }

[profiles.fast]
model = "gpt-5-mini"
"""


def _codex(tmp_path: Path, url: str) -> Path:
    (tmp_path / "notes_server.py").write_text(STUB)
    config = tmp_path / "config.toml"
    config.write_text(
        CODEX.replace("@PYTHON@", sys.executable)
        .replace("@STUB@", str(tmp_path / "notes_server.py"))
        .replace("@NOTES@", str(tmp_path / "notes.json"))
        .replace("@PHASE@", str(tmp_path / "phase"))
        .replace("@URL@", url)
    )
    return config


def test_codex_servers_are_read_from_their_tables(tmp_path: Path) -> None:
    config = _codex(tmp_path, "https://mcp.example.com/mcp")
    entries = {name: entry for _, name, entry in read_entries(config)}
    assert set(entries) == {"notes", "docs"}
    remote = remote_of(entries["docs"])
    assert remote is not None and remote.variables == ("DOCS_TOKEN",)
    assert remote.headers == {"X-Team": "blue", "Authorization": "Bearer ${DOCS_TOKEN}"}
    assert remote_of(entries["notes"]) is None
    assert remote_of({"type": "sse", "url": "https://x"}).transport == "sse"  # type: ignore[union-attr]
    (tmp_path / "bad.toml").write_text("[unclosed")
    with pytest.raises(ClientFileError, match="not TOML"):
        read_entries(tmp_path / "bad.toml")
    (tmp_path / "none.toml").write_text('model = "x"\n')
    assert read_entries(tmp_path / "none.toml") == []


def test_protect_rewrites_codex_tables_and_keeps_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "tok_" + "Hq3mZ8vB1xL6nC4tW9yR2"
    with StandIn(token=token) as stand_in:
        config = _codex(tmp_path, stand_in.url)
        monkeypatch.setenv("DOCS_TOKEN", token)
        steps = protect.protect(
            read_servers(config, "codex"),
            root=tmp_path / "protect",
            apply=True,
            runner=[sys.executable, "-m", "fiduciary"],
        )
        assert [(s.server.name, s.action) for s in steps] == [("notes", "wrap"), ("docs", "wrap")]
        text = config.read_text()
        assert text.startswith('# my Codex settings\nmodel = "gpt-5"\n')
        assert "# the docs server is remote" in text and "[profiles.fast]" in text
        data = tomllib.loads(text)
        notes, docs = data["mcp_servers"]["notes"], data["mcp_servers"]["docs"]
        assert notes["args"][-1] == "--protected" and notes["startup_timeout_sec"] == 20
        assert notes["enabled_tools"] == [
            "search_notes",
            "createNote",
            "delete_note",
            "list_notes",
        ]
        assert docs["env_vars"] == ["DOCS_TOKEN"], "Codex passes the token's variable on"
        assert "url" not in docs and "bearer_token_env_var" not in docs
        assert data["profiles"]["fast"]["model"] == "gpt-5-mini"
        client = Client({"command": docs["command"], "args": docs["args"]})
        try:
            made = client.tool("create_doc", {"title": "from codex"})
            assert not made["isError"]
        finally:
            client.close()
        assert stand_in.docs == ["from codex"]
        protect.undo(read_servers(config, "codex"))
        back = tomllib.loads(config.read_text())["mcp_servers"]
        assert back["docs"]["bearer_token_env_var"] == "DOCS_TOKEN"
        assert back["docs"]["http_headers"] == {"X-Team": "blue"}
        assert back["notes"]["env"]["NOTES_FILE"] == str(tmp_path / "notes.json")
        assert (tmp_path / "config.toml.before-fiduciary").read_text().startswith("# my Codex")


def test_a_codex_server_kept_some_other_way_is_refused(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[mcp_servers]\ninline = { command = "x" }\n')
    with pytest.raises(ClientFileError, match="its own"):
        write_entries(config, {("mcp_servers", "inline"): {"command": "y"}})
    table = toml_table("odd name", {"args": ["a"], "env": {"K": "v"}, "on": True, "n": 1.5})
    assert table.startswith('[mcp_servers."odd name"]\n')
    assert tomllib.loads(table)["mcp_servers"]["odd name"] == {
        "args": ["a"],
        "env": {"K": "v"},
        "on": True,
        "n": 1.5,
    }
    with pytest.raises(ClientFileError, match="no TOML form"):
        toml_table("x", {"bad": object()})
    weird = tmp_path / "weird.toml"
    weird.write_text(
        '[mcp_servers.a]\ncommand = "x"\n[mcp_servers.\'b c\']\ncommand = "y"\n[[arr]]\nk = 1\n'
    )
    write_entries(weird, {("mcp_servers", "a"): {"command": "z"}})
    assert tomllib.loads(weird.read_text())["mcp_servers"] == {
        "a": {"command": "z"},
        "b c": {"command": "y"},
    }
    assert clients._path_of('a."b.c".d') == ["a", "b.c", "d"]
    assert clients._path_of("a b") is None and clients._path_of("'open") is None


def test_vscode_entries_keep_their_type(tmp_path: Path) -> None:
    (tmp_path / "notes_server.py").write_text(STUB)
    config = tmp_path / ".vscode" / "mcp.json"
    config.parent.mkdir()
    entry = {
        "type": "stdio",
        "command": sys.executable,
        "args": [str(tmp_path / "notes_server.py")],
        "env": {"NOTES_FILE": str(tmp_path / "n.json"), "PHASE_FILE": str(tmp_path / "p")},
    }
    config.write_text(json.dumps({"inputs": [], "servers": {"notes": entry}}))
    (step,) = protect.protect(
        read_servers(config, "vscode"),
        root=tmp_path / "protect",
        apply=True,
        runner=[sys.executable, "-m", "fiduciary"],
    )
    written = json.loads(config.read_text())
    assert written["inputs"] == [] and written["servers"]["notes"]["type"] == "stdio"
    assert step.action == "wrap"


def test_client_paths_cover_every_client(monkeypatch: pytest.MonkeyPatch) -> None:
    for platform, part in (
        ("darwin", "Application Support"),
        ("linux", ".config"),
        ("win32", "Code"),
    ):
        monkeypatch.setattr(sys, "platform", platform)
        found = protect.client_paths()
        assert set(found) == {"claude-desktop", "cursor", "vscode", "codex"}
        assert part in str(found["vscode"]) or platform == "win32"
        assert found["codex"].name == "config.toml"
    monkeypatch.undo()

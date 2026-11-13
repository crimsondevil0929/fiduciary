"""Claude Code on a repository a mandate governs (``fiduciary init --claude-code``, 1.15.0).

Each session works in a Git worktree interlock opens (``WorktreeCreate``), Claude Code's own
tools act there unchanged, and when the session ends what it changed is judged by the
deployment's mandate and committed to the branch once, or kept for the agent to fix.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest

from fiduciary import cli

GIT_ENV = {
    "GIT_AUTHOR_NAME": "dev",
    "GIT_AUTHOR_EMAIL": "dev@example.com",
    "GIT_COMMITTER_NAME": "dev",
    "GIT_COMMITTER_EMAIL": "dev@example.com",
}


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603 - git, with the test's own arguments
        ["git", "-c", "init.defaultBranch=main", *args],  # noqa: S607
        cwd=cwd,
        env={**os.environ, **GIT_ENV},
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "service"
    (root / "src").mkdir(parents=True)
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "src" / "app.py").write_text("print('hi')\n")
    (root / ".github" / "workflows" / "ci.yml").write_text("on: push\n")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "first")
    monkeypatch.chdir(root)
    monkeypatch.delenv("FIDUCIARY_CONFIG", raising=False)
    return root


def _hook(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    **payload: Any,
) -> tuple[int, str, str]:
    capsys.readouterr()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    config = str(repo / ".fiduciary" / "fiduciary.toml")
    code = cli.main(["--config", config, "hook", "claude-code"])
    said = capsys.readouterr()
    return code, said.out, said.err


def test_init_writes_the_hooks_the_sandbox_and_a_coding_mandate(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["init", "--claude-code"]) == 0
    said = capsys.readouterr().out
    assert "claude --worktree" in said and "committed to main once" in said
    settings = json.loads((repo / ".claude" / "settings.local.json").read_text())
    commands = {h["command"] for e in settings["hooks"].values() for g in e for h in g["hooks"]}
    (command,) = commands
    assert command.endswith("hook claude-code") and str(repo / ".fiduciary") in command
    assert set(settings["hooks"]) >= {"WorktreeCreate", "Stop", "SessionEnd", "PreToolUse"}
    sandbox = settings["sandbox"]
    assert sandbox["enabled"] and sandbox["failIfUnavailable"]
    assert not sandbox["allowUnsandboxedCommands"]
    assert sandbox["network"]["allowedDomains"] == []
    assert sandbox["filesystem"]["denyRead"] == [str((repo / ".fiduciary" / "keys").resolve())]
    exclude = (repo / ".git" / "info" / "exclude").read_text().splitlines()
    assert ".fiduciary/" in exclude and ".claude/settings.local.json" in exclude
    assert _git(repo, "status", "--porcelain") == ""
    interlock = tomllib.loads((repo / ".fiduciary" / "interlock.toml").read_text())
    assert interlock["git"]["checked_out"] == "follow" and interlock["git"]["branch"] == "main"
    assert interlock["sessions"]["hidden"] == [str((repo / ".fiduciary" / "keys").resolve())]
    mandate = json.loads((repo / ".fiduciary" / "mandate.json").read_text())
    assert mandate["tools"]["statements"] == "any"
    kinds = mandate["data"].get("kinds") or mandate["data"]["tables"]
    assert sorted(kinds) == ["docs", "other", "src", "tests"]
    assert all(sorted(ops) == ["delete", "insert", "update"] for ops in kinds.values())


def test_init_for_claude_code_needs_a_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init", "--claude-code"]) != 0
    assert "not in a Git repository" in capsys.readouterr().err
    assert cli.main(["init", "--claude-code", "--substrate", "git"]) != 0
    assert "no --mcp or --substrate" in capsys.readouterr().err


def test_a_session_is_judged_by_the_mandate_and_promoted_to_the_branch(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["init", "--claude-code"]) == 0
    base = _git(repo, "rev-parse", "HEAD")
    code, out, _ = _hook(
        repo, monkeypatch, capsys, hook_event_name="WorktreeCreate", name="fix", cwd=str(repo)
    )
    tree = Path(out.strip().splitlines()[-1])
    assert code == 0 and (tree / "src" / "app.py").is_file()
    assert tree.parent.parent == repo / ".git" / "interlock" / "sessions"

    edit = {"hook_event_name": "PreToolUse", "tool_name": "Write", "cwd": str(tree)}
    code, out, _ = _hook(
        repo, monkeypatch, capsys, **edit, tool_input={"file_path": str(repo / "src" / "app.py")}
    )
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"

    (tree / "src" / "app.py").write_text("print('fixed')\n")
    (tree / ".github" / "workflows" / "ci.yml").write_text("on: [push, pull_request]\n")
    code, out, _ = _hook(
        repo, monkeypatch, capsys, hook_event_name="Stop", cwd=str(tree), stop_hook_active=False
    )
    stop = json.loads(out)
    assert stop["decision"] == "block"
    assert "kind_allowlist" in stop["reason"] and "workflows" in stop["reason"]
    assert _git(repo, "rev-parse", "HEAD") == base  # a check commits nothing

    (tree / ".github" / "workflows" / "ci.yml").write_text("on: push\n")
    code, out, _ = _hook(
        repo, monkeypatch, capsys, hook_event_name="Stop", cwd=str(tree), stop_hook_active=False
    )
    assert out == ""
    code, _, err = _hook(
        repo, monkeypatch, capsys, hook_event_name="SessionEnd", cwd=str(tree), reason="other"
    )
    assert code == 0 and "1 file promoted to main" in err
    assert _git(repo, "rev-parse", "HEAD^") == base
    assert (repo / "src" / "app.py").read_text() == "print('fixed')\n"  # the checkout followed
    assert _git(repo, "status", "--porcelain") == ""
    assert _git(repo, "log", "-1", "--format=%s").startswith("interlock promote fix")

    receipt = json.loads((tree.parent / "promotion.json").read_text())
    assert receipt["outcome"] == "committed" and receipt["commit"] == _git(
        repo, "rev-parse", "HEAD"
    )
    from agentgov.receipts.signing import verifier_of
    from interlock.session import verify_receipt

    from fiduciary.keys import load_key

    receipts_key = load_key(repo / ".fiduciary" / "keys" / "receipts.key")
    assert verify_receipt(receipt, verifier_of(receipts_key))

    code, _, _ = _hook(repo, monkeypatch, capsys, hook_event_name="WorktreeRemove",
                       worktree_path=str(tree))  # fmt: skip
    assert code == 0 and not tree.exists()


def test_a_shadow_session_commits_and_the_bundle_lists_it(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = repo / ".fiduciary"
    assert cli.main(["init", "--claude-code", "--shadow"]) == 0
    _, out, _ = _hook(
        repo, monkeypatch, capsys, hook_event_name="WorktreeCreate", name="ci", cwd=str(repo)
    )
    tree = Path(out.strip().splitlines()[-1])
    (tree / ".github" / "workflows" / "ci.yml").write_text("on: [push, pull_request]\n")
    _code, out, _ = _hook(
        repo, monkeypatch, capsys, hook_event_name="Stop", cwd=str(tree), stop_hook_active=False
    )
    assert out == ""  # shadow: nothing would be refused, only observed
    _, _, err = _hook(
        repo, monkeypatch, capsys, hook_event_name="SessionEnd", cwd=str(tree), reason="other"
    )
    assert "promoted to main" in err
    assert (repo / ".github" / "workflows" / "ci.yml").read_text() == "on: [push, pull_request]\n"
    capsys.readouterr()
    assert cli.main(["--config", str(root / "fiduciary.toml"), "shadow", "report"]) == 0
    report = capsys.readouterr().out
    assert "kind_allowlist: 1 plan" in report
    # The next version enforces; the bundle cut after it carries the session's plan, and
    # the verifier says what made it.
    assert cli.main(["--config", str(root / "fiduciary.toml"), "solo", "enforce"]) == 0
    mandate = json.loads((root / "mandate.json").read_text())["mandate_id"]
    bundle = repo.parent / "lineage.fidb"
    argv = ["--config", str(root / "fiduciary.toml"), "export-bundle", "--mandate", mandate]
    key = str(root / "keys" / "ledger-checkpoints.key")
    assert cli.main([*argv, "--ledger-key", key, "--out", str(bundle)]) == 0
    capsys.readouterr()
    charter = (root / "charter.digest").read_text().strip()
    keys = str(root / "pinned-keys.json")
    assert cli.main(["verify-bundle", str(bundle), "--charter", charter, "--keys", keys]) == 0
    said = capsys.readouterr().out
    assert "coding sessions were promoted as plans the mandate admits" in said
    assert ": ci" in said

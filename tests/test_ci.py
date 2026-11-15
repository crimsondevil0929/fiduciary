"""``fiduciary ci`` (1.20.0): an agent in a CI job, behind the CI proxy, end to end.

The proxy runs as its own process, as in a job. A comment by someone the repository does not
trust labels every session ``untrusted`` before the first call; the job's secrets reach the
proxy on its standard input and nowhere else; a workflow edit and two credential posts are
refused; a reply waits for an approval nobody gives in CI. ``finish`` writes the summary and
the comment file, and exports bundles that verify.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from fiduciary import ci
from fiduciary.ci import CiError, Source, untrusted
from fiduciary.cli import main

_spec = importlib.util.spec_from_file_location(
    "ci_check", Path(__file__).resolve().parent.parent / "scripts" / "ci_check.py"
)
assert _spec is not None and _spec.loader is not None
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


def test_the_events_untrusted_text_is_found() -> None:
    found = untrusted("issue_comment", check.EVENT)
    assert [s.words() for s in found] == [
        "the issue's title and body by @mallory (NONE)",
        "the comment by @mallory (NONE)",
    ]
    owner = {**check.EVENT, "comment": {**check.EVENT["comment"], "author_association": "OWNER"}}
    assert [s.what for s in untrusted("issue_comment", owner)] == ["the issue's title and body"]
    assert untrusted("issue_comment", owner, trust=("OWNER", "NONE")) == []
    pull = {
        "pull_request": {
            "title": "Fix",
            "body": "x",
            "user": {"login": "bob"},
            "author_association": "MEMBER",
            "head": {"repo": {"full_name": "bob/site"}},
            "base": {"repo": {"full_name": "acme/site"}},
        },
        "review": {"body": "ok", "user": {"login": "carol"}, "author_association": "OWNER"},
    }
    (fork,) = untrusted("pull_request", pull)
    assert fork.fork == "bob/site" and "from the fork bob/site" in fork.words()
    assert len(untrusted("pull_request_review", pull)) == 2, "a fork's review thread is a fork's"
    assert untrusted("push", {}) == [] and untrusted("workflow_dispatch", {}) == []
    (odd,) = untrusted("release", {})
    assert "which this does not know" in odd.words()
    run = {
        "workflow_run": {
            "head_repository": {"full_name": "eve/site"},
            "repository": {"full_name": "acme/site"},
        }
    }
    assert untrusted("workflow_run", run) == [
        Source("the run that started this job", fork="eve/site")
    ]
    assert untrusted("workflow_run", {"workflow_run": {}}) == []
    talk = {"discussion": {"title": "t", "user": {"login": "d"}}, "comment": {"body": "c"}}
    assert len(untrusted("discussion_comment", talk)) == 2
    assert untrusted("issues", {"issue": {"title": "", "body": None}}) == []


def test_the_variables_an_mcp_file_names_are_filled_in() -> None:
    entries: list[dict[str, Any]] = [
        {"command": "x", "env": {"A": "${A_TOKEN}", "B": "plain ${env:B_KEY} ${C:-dflt}"}},
        {"url": "https://x", "bearer_token_env_var": "T", "env_http_headers": {"X-K": "K"}},
        {"command": "y", "env_vars": ["PASSED"]},
    ]
    assert ci.referenced(entries) == ["A_TOKEN", "B_KEY", "C", "K", "PASSED", "T"]
    values = {"A_TOKEN": "a", "B_KEY": "b", "T": "t", "K": "k", "PASSED": "p"}
    assert ci._expanded(entries[0], values)["env"] == {"A": "a", "B": "plain b dflt"}
    remote = ci._expanded(entries[1], values)
    assert remote["headers"] == {"Authorization": "Bearer t", "X-K": "k"}
    assert "bearer_token_env_var" not in remote
    assert ci._expanded(entries[2], values)["env"] == {"PASSED": "p"}


def test_an_agent_in_ci_is_refused_and_explained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    work = tmp_path / "work"
    check.prepare(work)
    monkeypatch.setenv("REPO_TOKEN", check.REPO_TOKEN)
    monkeypatch.setenv("DEPLOY_KEY", check.DEPLOY_KEY)
    root = tmp_path / "ci"
    runner = [sys.executable, "-m", "fiduciary"]
    state = ci.start(
        work / "agent-mcp.json",
        root,
        event="issue_comment",
        event_path=work / "event.json",
        secret_names=["DEPLOY_KEY"],
        runner=runner,
    )
    try:
        assert set(state["servers"]) == {"repo"} and state["secrets"] == ["DEPLOY_KEY"]
        agent_file = root / "mcp.json"
        written = agent_file.read_text()
        for secret in (check.REPO_TOKEN, check.DEPLOY_KEY):
            assert secret not in written, "the agent's file holds no secret"
            for path in root.rglob("*"):
                if path.is_file() and path.suffix in (".json", ".toml", ".log"):
                    assert secret not in path.read_text(errors="replace"), path
        with pytest.raises(CiError, match="has a CI proxy already"):
            ci.start(work / "agent-mcp.json", root, runner=runner)
        check.agent(work, agent_file)
    finally:
        summary = tmp_path / "summary.md"
        comment = tmp_path / "comment.md"
        found = ci.finish(root, bundles=tmp_path / "bundles", summary=summary, comment=comment)
    assert found.refused == 3 and found.held == 1
    assert all(s.verified for s in found.servers)
    check.check(work, summary, tmp_path / "bundles")
    assert comment.read_text() == summary.read_text()
    assert json.loads((root / "ci.json").read_text())["stopped"]
    # finishing twice reads the records again, the proxy long gone
    again = ci.finish(root)
    assert again.refused == 3 and again.servers[0].bundle == ""


def test_the_ci_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    work = tmp_path / "work"
    check.prepare(work)
    root = tmp_path / "ci"
    config = str(work / "agent-mcp.json")
    monkeypatch.delenv("REPO_TOKEN", raising=False)
    assert main(["ci", "start", "--mcp-config", config, "--root", str(root)]) == 1
    assert "names REPO_TOKEN, which this step's environment does not set" in (
        capsys.readouterr().err
    )
    monkeypatch.setenv("REPO_TOKEN", check.REPO_TOKEN)
    monkeypatch.setenv("DEPLOY_KEY", check.DEPLOY_KEY)
    outputs = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    started = [
        "ci",
        "start",
        "--mcp-config",
        config,
        "--root",
        str(root),
        "--secret",
        "DEPLOY_KEY",
        "--event-name",
        "issue_comment",
        "--event-path",
        str(work / "event.json"),
    ]
    assert main(started) == 0
    shown = capsys.readouterr().out
    assert "serving     repo at http://127.0.0.1:" in shown
    assert "untrusted   the comment by @mallory (NONE)" in shown
    assert "kept        DEPLOY_KEY" in shown and check.DEPLOY_KEY not in shown
    assert f"mcp-config={root.resolve() / 'mcp.json'}" in outputs.read_text()
    summary = tmp_path / "summary.md"
    finished = ["ci", "finish", "--root", str(root), "--summary", str(summary)]
    assert main([*finished, "--fail-on", "held"]) == 0, "no call was made: nothing to fail on"
    assert "refused=0\nheld=0" in outputs.read_text()
    assert "0 writes made, 0 sends judged, 0 refused, 0 held." in summary.read_text()
    assert main(["ci", "finish", "--root", str(tmp_path / "none")]) == 1


def test_the_ci_proxy_refuses_what_is_not_the_agents(tmp_path: Path) -> None:
    import threading
    import urllib.error
    import urllib.request

    served: dict[str, Any] = {}
    stop = threading.Event()
    listener = ci._listener(served, stop, "127.0.0.1:0")
    threading.Thread(target=listener.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{listener.server_address[1]}"

    def post(path: str, headers: dict[str, str], data: bytes = b"{}") -> int:
        request = urllib.request.Request(  # noqa: S310 - the proxy under test, on loopback
            url + path, data=data, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as answer:  # noqa: S310
                return int(answer.status)
        except urllib.error.HTTPError as exc:
            return int(exc.code)

    bearer = {"Authorization": f"Bearer {listener.token}"}
    try:
        assert post("/mcp/x", {}) == 401
        assert post("/mcp/x", {**bearer, "Origin": "https://evil.example"}) == 403
        assert post("/mcp/x", bearer) == 404
        get = urllib.request.Request(url + "/mcp/x", method="GET")  # noqa: S310
        with pytest.raises(urllib.error.HTTPError, match="405"):
            urllib.request.urlopen(get, timeout=10)  # noqa: S310
        assert post("/shutdown", bearer) == 204 and stop.is_set()
    finally:
        listener.shutdown()
        listener.server_close()
    with pytest.raises(CiError, match="loopback only"):
        ci._listener(served, stop, "0.0.0.0:0")
    assert os.environ.get("RUNNER_TEMP") is None or True

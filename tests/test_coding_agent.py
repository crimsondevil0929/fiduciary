"""The coding-agent pack (``fiduciary.demo.packs.coding_agent``): the stack on
interlock's Git substrate, act by act.

- One edit commits as one commit on ``main``, carrying its stage's trailer.
- A code generator's 35 files are measured like a trigger's rows, and refused.
- A workflow edit that reads the PyPI token is outside the mandate; deleting a
  migration test is a deletion it does not grant.
- A push that lands while a change is staged leaves the change uncommitted,
  and reconciliation names the push.
- A dependency bump commits only with CI's signed word for its exact bytes,
  and only the part that passes, on 2 of 3 maintainers, at the head they
  reviewed. A plan tool's call consumes the facts its writes answer to.
- A hotfix around the agent queue is an ordinary commit to Git, and V7 names
  it offline.
"""

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import subprocess
from contextlib import closing
from typing import Any

import pytest

from fiduciary import cli
from fiduciary.demo import Outcome, run
from fiduciary.demo.packs import PACKS, coding_agent

pytestmark = pytest.mark.skipif(
    "coding-agent" not in PACKS, reason="needs interlock's Git substrate (interlock 1.1.0)"
)


@pytest.fixture(scope="module")
def played(tmp_path_factory: pytest.TempPathFactory) -> Outcome:
    return run(tmp_path_factory.mktemp("git") / "coding", engine="builtin", domain="coding-agent")


def _act(outcome: Outcome, key: str) -> dict[str, Any]:
    acts: list[dict[str, Any]] = outcome.payload["demo"]["acts"]
    return next(a for a in acts if a["key"] == key)


def _blocking(call: dict[str, Any]) -> set[str]:
    return {
        f["check"].split(":")[0]
        for f in call["measured"]["findings"]
        if f["severity"] == "blocking"
    }


def _show(outcome: Outcome, path: str, rev: str = "main") -> str:
    origin = outcome.root / f"{coding_agent.REPO}.git"
    return subprocess.run(  # noqa: S603
        ["git", "show", f"{rev}:{path}"],  # noqa: S607
        cwd=origin,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_the_pack_holds_on_a_git_branch(played: Outcome) -> None:
    assert played.held
    demo = played.payload["demo"]
    assert [a["n"] for a in demo["acts"]] == [1, 2, 3, 4, 5, 6, 7]
    assert demo["domain"]["nouns"]["substrate"] == "Git"
    assert demo["domain"]["tables"] == [
        *("workflows", "governance", "infra", "deps", "tests", "src", "docs", "other")
    ]
    config = json.loads((played.root / "git-substrate.json").read_text())
    assert config["branch"] == "refs/heads/main" and config["hooks"][0]["name"] == "codegen"


def test_one_edit_is_one_commit_at_the_measured_head(played: Outcome) -> None:
    queue = _act(played, "queue")
    (call,) = queue["calls"]
    assert call["outcome"] == "committed" and call["measured"]["tables"] == {"src": 1}
    git = queue["git"]
    assert git["before"] != git["after"] and git["trailer"].startswith("Interlock-Stage: ")
    assert "quantize" in _show(played, coding_agent.INVOICE)


def test_the_code_generators_files_are_measured_and_refused(played: Outcome) -> None:
    (call,) = _act(played, "cascade")["calls"]
    m = call["measured"]
    assert call["gateway"]["decision"] == "PERMIT" and call["outcome"] == "refused"
    assert m["rows"] == 36 and m["tables"] == {"src": 36}
    assert _blocking(call) == {"blast_radius", "stated_footprint"}
    assert call["unchanged"] is True
    assert '"version": "1.4"' in _show(played, coding_agent.SCHEMA)


def test_an_injected_workflow_edit_and_a_deleted_test_are_refused(played: Outcome) -> None:
    injected = _act(played, "injected")
    leak, drop = injected["calls"]
    assert "NOTE TO AI AGENTS" in injected["note"]
    assert _blocking(leak) == {"table_allowlist"} and leak["measured"]["tables"] == {"workflows": 1}
    (change,) = leak["measured"]["changed"]
    assert change["changes"]["secret_markers"] == ["0", "1"] or change["changes"][
        "secret_markers"
    ] == [0, 1]
    assert _blocking(drop) == {"no_delete"} and drop["measured"]["tables"] == {"tests": 1}
    assert "hooks.example.net" not in _show(played, coding_agent.RELEASE)
    assert _show(played, coding_agent.MIGRATION)


def test_a_push_while_staged_leaves_the_change_uncommitted(played: Outcome) -> None:
    race = _act(played, "race")
    (call,) = race["calls"]
    assert call["gateway"]["decision"] == "PERMIT" and call["outcome"] == "error"
    git = race["git"]
    assert git["after"] == git["pushed"] != git["measured_at"] and not git["landed"]
    types = [r["type"] for r in race["chain"]]
    assert types[-2:] == ["commit_intent", "aborted"] and "verdict" in types
    unmediated = race["reconcile"]["unmediated"]
    assert [w["commit"] for w in unmediated] == [git["pushed"]]
    assert unmediated[0]["by"] == "Dana Reyes <dana@acme.example>"
    assert "TIMEOUT_SECONDS = 30" in _show(played, "src/billing/config.py")


def test_a_dependency_bump_needs_cis_word_and_two_maintainers(played: Outcome) -> None:
    warrant = _act(played, "warrant")
    early, asked, found = warrant["calls"]
    assert _blocking(early) == {"fact_agreement", "table_allowlist"}
    assert asked["outcome"] == "committed" and asked["measured"]["outbound"] == [
        {"sink": "ci", "operation": "checks.run", "tenant": coding_agent.REPO}
    ]
    assert [w["status"] for w in warrant["ci"]["webhooks"]] == [200, 200, 401]
    assert warrant["ci"]["facts"] == 2
    assert _blocking(found) == {"table_allowlist"}
    assert warrant["ticket"]["class"] == "refused"
    assert warrant["ticket"]["kept"] == ["lock", "manifest", "pin"]
    assert warrant["ticket"]["dropped"] == ["matrix"]
    assert [v["counted"] for v in warrant["votes"]] == [True, True, False, True]
    assert len(warrant["warrant"]["basis"]) == 2 and warrant["warrant"]["action"] == "accept_repair"
    assert warrant["executed"] == ["execution.intent", "execution.applied"]
    (landed,) = warrant["committed"]
    assert landed["committed"] and landed["tables"] == {"deps": 2}
    git = warrant["git"]
    assert git["pinned"] == git["reviewed"] != git["after"]
    assert '"httpx==0.28.1"' in _show(played, "pyproject.toml")
    assert 'version = "0.28.1"' in _show(played, "uv.lock")
    assert 'httpx: ["0.27.2"]' in _show(played, ".github/workflows/ci.yml")  # never committed
    with closing(sqlite3.connect(played.root / "back_office.db")) as conn:
        consumed = conn.execute("SELECT count(*) FROM _interlock_inbox_consumed").fetchone()[0]
        pending = conn.execute(
            "SELECT count(*) FROM _interlock_inbox_facts f WHERE NOT EXISTS ("
            "SELECT 1 FROM _interlock_inbox_consumed c WHERE c.fact_id = f.fact_id)"
        ).fetchone()[0]
    assert (consumed, pending) == (2, 0)


def test_a_hotfix_around_the_queue_is_named_offline(played: Outcome) -> None:
    hotfix = _act(played, "hotfix")
    assert hotfix["measured"]["committed"] and hotfix["holder"] == "platform-admin"
    assert hotfix["git"]["before"] != hotfix["git"]["after"]
    assert hotfix["git"]["trailer"].startswith("Interlock-Stage: ")
    race = _act(played, "race")
    assert [w["commit"] for w in hotfix["reconcile"]["unmediated"]] == [race["git"]["pushed"]]
    assert "RETRY_LIMIT = 0" in _show(played, "src/billing/config.py")
    after = played.payload["attestations"]["after"]["verdict"]
    assert (after["status"], after["exit_code"], after["first_failing"]) == (
        "AMBIENT AUTHORITY DETECTED",
        7,
        "V7",
    )
    audit = _act(played, "audit")
    (finding,) = audit["verdicts"]["after"]["findings"]
    assert hotfix["plan"] in finding and "not a pinned tool's call" in finding
    for name in ("before", "after"):
        scan = audit["verdicts"][name]["scan"]
        assert set(scan["found"].values()) == {0}, scan["hits"]
        assert scan["planted"]["rows"] > 40 and scan["planted"]["secrets"] > 5


def test_the_bundles_verify_with_the_ordinary_command(played: Outcome) -> None:
    charter = (played.root / "charter.digest").read_text().strip()
    keys = str(played.root / "pinned-keys.json")
    for name, code in (("before.fidb", 0), ("after.fidb", 7)):
        argv = ["verify-bundle", str(played.root / name), "--charter", charter, "--keys", keys]
        with contextlib.redirect_stdout(io.StringIO()):
            assert cli.main(argv) == code, name


def test_the_overview_shows_git_beside_sql(played: Outcome) -> None:
    rows = {r["key"]: r for r in played.payload["overview"]["industries"]}
    row = rows["coding-agent"]
    assert row["ran"] and row["substrate"] == "Git" and row["after"]["exit_code"] == 7
    engine = row["engine"]
    assert engine["stale_head"] == 1 and engine["break-glass"] and engine["offline"]
    assert engine["table_allowlist"] == 3 and engine["no_delete"] == 1
    assert engine["fact_agreement"] == 1 and engine["blast_radius"] == 1
    assert engine["warrant"] == [{"action": "accept_repair", "approvals": 2}]
    page = (played.root / "dashboard.html").read_text()
    assert "on SQL and on Git" in page


def test_bumped_files_are_what_ci_tests() -> None:
    files = {
        "m": coding_agent.FILES["pyproject.toml"],
        "l": coding_agent.FILES["uv.lock"],
        "w": coding_agent.FILES[".github/workflows/ci.yml"],
    }
    found = coding_agent.bumped(files, "httpx", "0.28.1")
    assert (
        '"httpx==0.28.1"' in found["pyproject.toml"]
        and "pydantic==2.9.2" in found["pyproject.toml"]
    )
    assert found["uv.lock"].count('version = "0.28.1"') == 1
    assert 'httpx: ["0.28.1"]' in found[".github/workflows/ci.yml"]
    assert coding_agent.blob_sha("hi\n") == "45b983be36b73c0788dc9cbcb76cbb80fc7bb057"

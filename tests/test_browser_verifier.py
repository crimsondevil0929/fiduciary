"""The console's in-browser bundle checks (``report/verify.js``) against every
golden bundle, run by Node's Web Crypto, held to the verifier's own verdicts.

The page calls what it runs a partial check, never a verdict. What this holds:

- **No false alarm.** Every bundle the verifier passes passes every check here.
- **No silent pass on what it checks.** With the auditor's pinned keys, every
  bundle the verifier fails on V1, V9 or V10 (the checks the browser runs part
  of) fails at least one check here. Every bundle that passes here fails only
  on checks the browser does not claim to run.
- **No drift.** Each bundle's outcome, check by check, with and without pinned
  keys, is the one recorded in ``tests/js/browser_vectors.json``: a change to
  what the page says about a bundle is a deliberate one.

Node is required in CI (``FIDUCIARY_REQUIRE_NODE=1``); elsewhere the test is
skipped without it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from fiduciary import report

ROOT = Path(__file__).resolve().parent.parent
VECTORS = ROOT / "vectors" / "fidu1" / "bundles"
HARNESS = Path(__file__).resolve().parent / "js" / "verify_vectors.cjs"
SNAPSHOT = Path(__file__).resolve().parent / "js" / "browser_vectors.json"
COVERED = frozenset({"V1", "V9", "V10"})
"""The checks the browser runs part of."""


def _node() -> str:
    found = shutil.which("node")
    if found is None:
        if os.environ.get("FIDUCIARY_REQUIRE_NODE") == "1":
            pytest.fail("FIDUCIARY_REQUIRE_NODE=1 and node is not on PATH")
        pytest.skip("node is not installed: the browser's checks run under Node's Web Crypto")
    return found


def _run(*mode: str) -> dict[str, Any]:
    done = subprocess.run(  # noqa: S603 - node and the repository's own harness
        [_node(), str(HARNESS), str(VECTORS), *mode],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert done.returncode == 0, done.stderr
    found: dict[str, Any] = json.loads(done.stdout)
    return found


def _index() -> dict[str, dict[str, Any]]:
    return {e["name"]: e for e in json.loads((VECTORS / "index.json").read_text())}


def _failing(entry: dict[str, Any]) -> set[str]:
    return {str(f).split()[0] for f in entry["findings"]}


@pytest.fixture(scope="module")
def outcomes() -> dict[str, dict[str, Any]]:
    return {"bundle's own keys": _run(), "pinned keys": _run("--pinned")}


def test_every_golden_bundle_is_checked(outcomes: dict[str, dict[str, Any]]) -> None:
    index = _index()
    for found in outcomes.values():
        assert set(found) == set(index)


def test_no_false_alarm(outcomes: dict[str, dict[str, Any]]) -> None:
    for mode, found in outcomes.items():
        for name, entry in _index().items():
            if entry["exit_code"] == 0:
                assert found[name]["failing"] == [], (mode, name)
                assert set(found[name]["checks"].values()) == {"pass"}, (mode, name)


def test_no_silent_pass_on_what_it_checks(outcomes: dict[str, dict[str, Any]]) -> None:
    found = outcomes["pinned keys"]
    for name, entry in _index().items():
        verifier = _failing(entry)
        browser_failed = bool(found[name]["failing"])
        if entry["exit_code"] == 2 or verifier & COVERED:
            assert browser_failed, (
                f"{name}: the verifier fails {sorted(verifier)}, the browser passes"
            )
        if not browser_failed:
            assert not verifier & COVERED, name


def test_own_record_withheld_is_named(outcomes: dict[str, dict[str, Any]]) -> None:
    """The sealed bundle's headline guarantee, checked in the browser too."""
    for found in outcomes.values():
        (said,) = found["v10-own-record-withheld"]["failing"]
        assert said.startswith("anchors:") and "it is the bundle's own" in said


def test_each_outcome_is_the_one_recorded(outcomes: dict[str, dict[str, Any]]) -> None:
    current = {
        mode: {name: v["checks"] for name, v in sorted(found.items())}
        for mode, found in outcomes.items()
    }
    if os.environ.get("FIDUCIARY_UPDATE_BROWSER_VECTORS") == "1":
        SNAPSHOT.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n")
    assert current == json.loads(SNAPSHOT.read_text())


def test_the_page_carries_the_checks() -> None:
    """The console runs this very file: it is inlined before the page's script."""
    page = report.console_html("checks")
    checks = (ROOT / "src" / "fiduciary" / "report" / "verify.js").read_text()
    assert checks in page
    assert page.index("root.FidbVerify = api") < page.index("window.FidbVerify.check(")


def test_what_a_bundle_leaves_out_is_named_in_the_browser_too(
    outcomes: dict[str, dict[str, Any]],
) -> None:
    """1.11.0: an unsigned manifest fails, the ledger entries anchors are read
    from are proven, the cut is the anchor of the range's last record, and a
    receipt an escrow chain names is held; each with or without pinned keys."""
    for found in outcomes.values():
        assert found["v9-tail-cut-off-unsigned"]["checks"]["manifest"] == "fail"
        assert found["v6-cut-past-the-range"]["checks"]["ledger"] == "fail"
        assert found["v5-receipt-left-out"]["checks"]["receipts"] == "fail"
        for name in ("v9-withheld-dropped", "v10-withheld-rewritten"):
            assert found[name]["checks"]["manifest"] == "fail"
    assert outcomes["pinned keys"]["v6-other-ledger-key"]["checks"]["ledger"] == "fail"

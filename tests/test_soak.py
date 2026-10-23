"""The soak, scaled down (§11 Phase 6): ``scripts/live_stress_test.py`` for
forty seconds, two nodes, on live PostgreSQL. Every invariant holds, and the
run is seen to have done what it claims: forgeries submitted, a node killed
and one frozen, edits made around the service, warrants executed."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.conftest import need_postgres

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def stress(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "live_stress_test", ROOT / "scripts" / "live_stress_test.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_a_short_soak_holds_every_invariant(stress: ModuleType, tmp_path: Path) -> None:
    said: list[str] = []
    settings = stress.Settings(
        need_postgres(), seconds=40.0, nodes=2, seed=11, out=tmp_path / "soak", say=said.append
    )
    report = stress.soak(settings)
    assert report.ok, (report.held, report.details)
    details = report.details
    assert details["forgeries"] and details["replays"] > 0
    assert sum(details["forgeries"].values()) > 0
    faults = details["faults"]
    assert any(f.startswith("killed") for f in faults)
    assert any(f.startswith("froze") for f in faults)
    assert sorted(details["every edit named, and nothing else"]["edits"]) == [
        "approvals",
        "tickets",
    ]
    assert details["warrants"].get("applied", 0) > 0
    assert details["executions"]["plans committed"] > 0
    assert (tmp_path / "soak" / "soak-report.json").exists()

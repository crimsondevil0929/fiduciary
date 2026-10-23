"""The security audit (§11 Phase 6; F9-1, F4-27): ``scripts/security_audit.py``
runs its scenario on each store, and no secret, row, payload, transcript or
agent text reaches a hash, a document, a log or a bundle of fiduciary's, but
the clipped ``agent.statement``. And the audit is seen to catch a leak: one
planted in a column, one in a hash."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from fiduciary.store import Store
from tests.conftest import reopen

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def audit(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "security_audit", ROOT / "scripts" / "security_audit.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def _run(audit: ModuleType, store: Store, **kw: Any) -> tuple[Any, str]:
    said: list[str] = []
    run = audit.audit(reopen(store), audit.Canaries.make(), say=said.append, **kw)
    audit.report(run, said.append)
    return run, "\n".join(said)


def test_nothing_sensitive_reaches_a_hash_a_log_or_a_bundle(
    audit: ModuleType, store: Store
) -> None:
    run, said = _run(audit, store)
    assert run.clean, said
    # Each kind of hash fiduciary computes was computed, and recorded.
    for family in ("fidu1", "documents", "bundle", "mcp", "compiler"):
        assert run.families.get(family), (family, run.families)
    # Every canary was found where it is kept: the audit looked.
    assert {name for name, where in run.found.items() if where} == set(audit.KEPT)
    assert len(run.commitments) == 1
    assert run.places > 1000


def test_a_leak_in_a_column_or_a_hash_is_caught(audit: ModuleType, store: Store) -> None:
    def plant(fid: Any, canaries: Any) -> None:
        from fiduciary import sqlite as fiduciary_sqlite

        fid.log.store.submit(canaries.values["a model call's message"].encode())
        fiduciary_sqlite._sha256(canaries.values["a refused argument"].encode())

    run, said = _run(audit, store, plant=plant)
    assert not run.clean
    assert any(
        leak.startswith("a model call's message in") and "intake" in leak for leak in run.leaks
    ), said
    assert "a refused argument in sha256 by fiduciary.sqlite" in run.leaks, said

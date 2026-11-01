"""The SRE pack's admission-policy analyzer (1.5.0): a sensor that reads a
measured diff and signs what it finds."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from interlock.types import SensorFact

from fiduciary.demo import analyzer
from fiduciary.demo.packs import sre


def _document(*rows: Any) -> dict[str, Any]:
    return {"subject": "a" * 64, "rows": list(rows)}


def test_the_policy_finds_what_it_should() -> None:
    clean = {
        "table": "deployments",
        "key": "p/api",
        "operation": "update",
        "after": {"replicas": 6, "image": "registry.internal/api:prod-east-r41"},
    }
    assert analyzer.findings(_document(clean), max_replicas=20) == []
    found = analyzer.findings(
        _document(
            {**clean, "after": {"replicas": 60, "image": "registry.internal/api:latest"}},
            {**clean, "after": {"image": "registry.internal/api"}},
            {"table": "statefulsets", "key": "p/orders-pg", "operation": "delete", "after": None},
        ),
        max_replicas=20,
    )
    assert len(found) == 4
    assert "60 replicas" in found[0] and "no pinned tag" in found[1]
    assert "deletes a stateful workload" in found[3]


def test_the_analyzer_signs_its_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    key = tmp_path / "policy-check.key"
    key.write_text(sre.ANALYZER_SEED.hex() + "\n")
    row = {"table": "deployments", "key": "p/api", "operation": "update", "after": {"replicas": 60}}
    monkeypatch.setattr(
        sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(_document(row)).encode()))
    )
    assert analyzer.main(["--key", str(key)]) == 0
    fact = SensorFact.from_json(json.loads(capsys.readouterr().out))
    assert (fact.sensor, fact.verdict, fact.findings) == ("policy-check", "fail", 1)
    assert fact.key_id == sre.ANALYZER.public_key().key_id
    assert sre.ANALYZER.public_key().verify(fact.message(), bytes.fromhex(fact.signature))

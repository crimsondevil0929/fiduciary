"""The golden bundles (``vectors/fidu1/bundles/``): each, verified with what
``index.json`` records for it, reaches its recorded verdict, its exit code,
the checks that fail and every finding's text (``scripts/make_bundles.py``).

Between them they pin every check and every exit code of §9.3: three valid
bundles, and for each check at least one that fails it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from fiduciary import fidu1
from fiduciary.bundle import CHECKS, BundleError, TrustedKeys, verify_bundle

BUNDLES = Path(__file__).resolve().parent.parent / "vectors" / "fidu1" / "bundles"
INDEX: list[dict[str, Any]] = json.loads((BUNDLES / "index.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("vector", INDEX, ids=lambda v: v["name"])
def test_a_golden_bundle_reaches_its_recorded_verdict(vector: dict[str, Any]) -> None:
    data = (BUNDLES / vector["file"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() == vector["sha256"]
    keys = None if vector["keys"] is None else TrustedKeys(vector["keys"])
    witness = None if vector["witness"] is None else fidu1.key_of(vector["witness"])
    if vector["exit_code"] == 2:
        with pytest.raises(BundleError):
            verify_bundle(data, charter=vector["charter"], keys=keys)
        return
    report = verify_bundle(
        data,
        charter=vector["charter"],
        keys=keys,
        witness=witness,
        skew_seconds=vector["skew_seconds"],
    )
    assert report.exit_code == vector["exit_code"], report.render()
    assert [f.render() for f in report.findings] == vector["findings"]


def test_the_golden_bundles_pin_every_check_and_exit_code() -> None:
    failing = {check for v in INDEX for check in v["checks"]}
    assert failing == {check for check, _ in CHECKS}
    assert {v["exit_code"] for v in INDEX} == {0, 2, 3, 4, 5, 6, 7, 8, 9}
    assert sum(v["exit_code"] == 0 for v in INDEX) >= 3
    labeled = [v for v in INDEX if any("[break-glass]" in f for f in v["findings"])]
    assert [v["name"] for v in labeled] == ["v7-break-glass-release"]

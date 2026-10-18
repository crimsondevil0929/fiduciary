"""``compile`` gives one answer in every process (F6-1, F6-2, F6-8).

A mandate holding every checker type is compiled in child processes under
eight ``PYTHONHASHSEED`` values. Its compiled digest, fiduciary's own encoding,
is one value everywhere. The checker records a receipt carries are too, with
one exception that is Interlock's: before U-1, the five checkers whose
configuration holds a set digest in hash order, which is why verification
checks those by name only (F6-8). Against an Interlock with U-1 (its main
branch since crimsondevil0929/interlock#20), every record, all 17 built-in
checker types', is one value everywhere.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from fiduciary.compiler import SET_BEARING, canonical_digests
from tests.compiled_digests import digests

ROOT = Path(__file__).resolve().parent.parent
SEEDS = ("0", "1", "2", "3", "4", "5", "6", "7")


def _under(seed: str) -> dict[str, Any]:
    done = subprocess.run(
        [sys.executable, "-m", "tests.compiled_digests"],
        cwd=ROOT,
        env={**os.environ, "PYTHONHASHSEED": seed},
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    found: dict[str, Any] = json.loads(done.stdout)
    return found


def test_compile_gives_one_digest_and_records_under_every_hash_seed() -> None:
    runs = {seed: _under(seed) for seed in SEEDS}
    first = runs["0"]
    assert len(first["records"]) >= 20
    for seed, found in runs.items():
        assert found["digest"] == first["digest"], f"PYTHONHASHSEED={seed}: another compiled digest"
        assert found["child"] == first["child"], f"PYTHONHASHSEED={seed}: another child digest"
        assert found["canonical"] == first["canonical"]
    set_bearing = {name for name, cls in first["classes"].items() if cls in SET_BEARING}
    assert {first["classes"][n] for n in set_bearing} == set(SET_BEARING)
    for name in first["records"]:
        values = {found["records"][name] for found in runs.values()}
        if first["canonical"] or name not in set_bearing:
            assert len(values) == 1, f"{name} digests {len(values)} ways across hash seeds"
    if not first["canonical"]:
        varied = [n for n in set_bearing if len({f["records"][n] for f in runs.values()}) > 1]
        assert varied, "Interlock without U-1, yet no set-bearing digest moved with the seed"


def test_the_digests_in_process_are_the_childrens() -> None:
    assert digests()["digest"] == _under("3")["digest"]
    assert digests()["canonical"] == canonical_digests()

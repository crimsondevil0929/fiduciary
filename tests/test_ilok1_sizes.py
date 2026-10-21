"""The ILOK1 sizes of F3-9, measured, and the chunking they justify.

``scripts/ilok1_sizes.py`` lets interlock's own operator write releases and
compensations under fiduciary's reasons, and measures what each record
signs. The spec's table must say what it measures, and the executor's chunk
of :data:`MAX_OUTBOX_TARGETS` must fit the costliest intent in what AWS KMS
signs (U-2's fallback).
"""

from __future__ import annotations

import dataclasses
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from fiduciary.documents import MAX_OUTBOX_TARGETS, Warrant, execution_steps
from fiduciary.documents.ticket import MessageTarget, Resolution
from tests import examples as e

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ilok1_sizes.py"


@pytest.fixture(scope="module")
def sizes() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ilok1_sizes", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def measured(sizes: ModuleType) -> dict[str, tuple[int, int]]:
    found: dict[str, tuple[int, int]] = sizes.measure()
    return found


def test_the_spec_table_is_what_the_script_measures(
    sizes: ModuleType, measured: dict[str, tuple[int, int]]
) -> None:
    lines = [line for line in sizes.table(measured) if not line.startswith("|---")]
    assert sizes.spec_table() == lines


def test_a_chunk_of_outbox_targets_fits_what_kms_signs(
    sizes: ModuleType, measured: dict[str, tuple[int, int]]
) -> None:
    for key in ("operator.intent", "compensate"):
        first, each = measured[key]
        assert first + (MAX_OUTBOX_TARGETS - 1) * each <= sizes.LIMIT, key
    assert sizes.fits(*measured["compensate"]) >= MAX_OUTBOX_TARGETS


def test_outbox_resolutions_are_chunked_to_the_bound() -> None:
    targets = tuple(
        MessageTarget(f"00000000-0000-4000-8000-{n:012d}", f"{n:064x}") for n in range(17)
    )
    for resolution in (
        Resolution(action="release", messages=targets),
        Resolution(action="compensate", plan_id="plan-1", messages=targets),
    ):
        steps = execution_steps(_with(resolution), e.ticket())
        assert [len(s["targets"]) for s in steps] == [8, 8, 1], resolution.action
    cancel = Resolution(action="cancel", messages=targets[:3])
    assert [len(s["targets"]) for s in execution_steps(_with(cancel), e.ticket())] == [1, 1, 1]


def _with(resolution: Resolution) -> Warrant:
    return dataclasses.replace(
        e.warrant(), resolution=resolution, resolution_digest=resolution.digest
    )

"""Audit bundles (§9): export, and offline verification V1 to V12, on SQLite
and PostgreSQL."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from fiduciary.store import Store
from tests import bound
from tests import bundle_kit as bk
from tests.test_bundle_export import _work


@pytest.fixture
def world(store: Store, tmp_path: Path) -> Iterator[bound.World]:
    yield from bk.world(store, tmp_path)


def test_a_lineage_at_work_exports_a_bundle_that_verifies(world: bound.World) -> None:
    mandate, _ = _work(world)
    data = bk.export(world, mandate.mandate_id)
    report = bk.verify(world, data)
    assert report.passed, report.render()
    assert report.exit_code == 0
    again = bk.export(world, mandate.mandate_id)
    assert again == data  # one lineage over one range: identical bytes (§9.2)


def test_the_phase_4_lifecycle_exports_a_bundle_that_verifies(world: bound.World) -> None:
    """Recovery, a quote, a grant by two approvals, and billing moved to the
    extension: every act on the footprint is explained (V7), the warrant's
    steps match what the systems recorded (V8), and the bundle verifies."""
    from tests.test_bridge_lifecycle import test_a_session_from_commit_to_extension_and_back

    test_a_session_from_commit_to_extension_and_back(world, world.clock)
    (mandate_id,) = world.log.state().lineages
    data = bk.export(world, mandate_id)
    report = bk.verify(world, data)
    assert report.passed, report.render()

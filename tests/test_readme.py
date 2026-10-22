"""The README's session example runs as written (§11 Phase 4), against a
deployment its configuration describes, on SQLite and PostgreSQL."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from fiduciary.store import Store
from tests import bound
from tests import bridge_kit as kit
from tests.conftest import Clock
from tests.test_cli_mcp import _configure, _verified

README = Path(__file__).resolve().parents[1] / "README.md"


@pytest.fixture
def world(store: Store, tmp_path: Path, clock: Clock) -> Iterator[bound.World]:
    yield from kit.world(store, tmp_path, clock)


def _example() -> str:
    section = README.read_text().split("## An agent's session", 1)[1]
    found = re.search(r"```python\n(.*?)```", section, re.DOTALL)
    assert found is not None, "the README has a session example"
    return found.group(1)


def test_the_readme_example_runs_as_written(
    world: bound.World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    example = _example()
    assert len(example.splitlines()) <= 12, "and is short"
    _configure(world, top='in_process = "tool-calls-only"\n')
    monkeypatch.chdir(world.tmp)
    exec(compile(example, str(README), "exec"), {})  # noqa: S102 - the README's own code
    assert capsys.readouterr().out == "committed\n"
    _verified(world)

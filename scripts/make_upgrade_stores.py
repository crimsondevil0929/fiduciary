"""Record a store written by each 0.x release's own code (``tests/upgrades/``).

For each release, its commit's ``src`` and ``tests`` are archived to a scratch
directory, and ``scripts/upgrade_scenario.py`` runs with them first on the path,
against the interlock and AgentGov this repository pins (every 0.x release
pinned the same ones). What it writes is kept as text:

- ``fiduciary.sql`` and ``ledger.sql``: the store and the AgentGov ledger, as
  SQLite's own dump of each;
- ``postgres.json``: that release's PostgreSQL schema, statement by
  statement, for the schema ``fid_upgrade``;
- ``chains/``, ``receipts.jsonl``, ``operators.jsonl``, ``tools/``: what an
  audit bundle of the lineage reads;
- ``manifest.json``: the charter, the lineages, the release.

``tests/test_upgrade.py`` loads each into a store of that version, on SQLite
and on PostgreSQL, and opens it with this release. Run from the repository::

    uv run --managed-python python scripts/make_upgrade_stores.py [--out tests/upgrades]

The bytes differ on every run (the wall clock, uuid4); the test holds what any
run writes.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASES = {
    "0.1.0": "276314c",
    "0.2.0": "1c35d9a",
    "0.3.0": "9ec4e94",
    "0.4.0": "21f403a",
    "0.5.0": "1142bbf",
}
KEPT = ("postgres.json", "manifest.json", "receipts.jsonl", "operators.jsonl")


def dump(database: Path) -> str:
    with sqlite3.connect(database) as conn:
        return "\n".join(conn.iterdump()) + "\n"


def record(version: str, commit: str, scratch: Path, out: Path) -> None:
    archive = scratch / version
    archive.mkdir()
    tar = subprocess.run(  # noqa: S603 - git, on this repository
        ["git", "archive", commit, "src", "tests"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(archive)], input=tar, check=True)  # noqa: S603, S607
    written = scratch / f"{version}-out"
    paths = [str(archive), str(archive / "src")]
    program = (
        f"import sys; sys.path[:0] = {paths!r}; "
        f"sys.argv = ['upgrade_scenario', {version!r}, {str(written)!r}]; "
        f"exec(open({str(ROOT / 'scripts' / 'upgrade_scenario.py')!r}).read())"
    )
    subprocess.run(  # noqa: S603 - this interpreter, an earlier release of this repository
        [sys.executable, "-I", "-c", program],
        cwd=archive,
        check=True,
        env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"},
    )
    target = out / version
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    (target / "fiduciary.sql").write_text(dump(written / "fiduciary.db"))
    (target / "ledger.sql").write_text(dump(written / "ledger.db"))
    for name in KEPT:
        if (written / name).exists():
            shutil.copy(written / name, target / name)
    for folder, pattern in (("chains", "*.chain"), ("tools", "*.json")):
        found = sorted((written / folder).glob(pattern)) if (written / folder).exists() else []
        if found:
            (target / folder).mkdir()
            for path in found:
                shutil.copy(path, target / folder / path.name)
    print(f"{version} ({commit}): {target}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--out", type=Path, default=ROOT / "tests" / "upgrades")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as scratch:
        for version, commit in RELEASES.items():
            record(version, commit, Path(scratch), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

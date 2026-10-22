"""Tampering with a bundle, and verifying one offline (§9.2, §9.3, F9-3).

Every member of a valid bundle is deleted, duplicated, moved out of order,
and has a byte changed, both as found and with the manifest rewritten to
match: each way fails a named check, and none crashes or passes. Then the
CLI verifies the bundle in an interpreter that can open no connection.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from fiduciary import fidu1
from fiduciary.bundle import BundleReport
from fiduciary.store import Store
from tests import bound
from tests import bundle_kit as bk
from tests.test_bundle_checks import _execute, _hold, _later
from tests.test_executor import _approve_ticket


@pytest.fixture
def world(store: Store, tmp_path: Path) -> Iterator[bound.World]:
    yield from bk.world(store, tmp_path)


# -- tampering: every way fails a named check, and none crashes --------------------------


def _members(data: bytes) -> list[tuple[str, bytes]]:
    from fiduciary.bundle.format import unpack

    return unpack(data)


def _tar(members: list[tuple[str, bytes]]) -> bytes:
    """A tar of ``members`` in the order given (``pack`` sorts)."""
    import io
    import tarfile

    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name, body in members:
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return out.getvalue()


def _flip(body: bytes) -> bytes:
    """``body`` with the hex digit nearest its middle changed: most such
    edits still parse, so checks past V1's digests must name them."""
    if not body:
        return b"{}\n"
    middle = len(body) // 2
    hexdigits = b"0123456789abcdef"
    for offset in range(len(body)):
        for at in (middle + offset, middle - offset):
            if 0 <= at < len(body) and body[at] in hexdigits:
                changed = hexdigits[(hexdigits.index(body[at]) + 1) % 16]
                return body[:at] + bytes([changed]) + body[at + 1 :]
    return body[:middle] + bytes([body[middle] ^ 1]) + body[middle + 1 :]


def _remanifest(members: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    """The manifest rewritten to the members' new digests, unsigned: a
    tamperer who cannot sign as the service."""
    import hashlib

    held = dict(members)
    manifest = fidu1.parse(held["manifest.json"])
    manifest.pop("sig", None)
    for entry in manifest["members"]:
        entry["sha256"] = hashlib.sha256(held[entry["path"]]).hexdigest()
    held["manifest.json"] = fidu1.canonical(manifest)
    return sorted(held.items())


@pytest.fixture
def released(world: bound.World) -> bytes:
    """The bundle with the most kinds of member: a held message released by
    warrant, after a session's refund."""
    for held in _hold(world):
        warrant = _approve_ticket(world, held.escalation, held.ticket, "release")
        assert _execute(world, held, warrant)[-1].kind == "execution.applied"
        _later(held.fid)
        data = bk.export(world, held.mandate.mandate_id)
    assert bk.verify(world, data).passed
    return data


def _named(world: bound.World, data: bytes, what: str) -> BundleReport:
    from fiduciary.bundle import BundleError

    try:
        report = bk.verify(world, data)
    except BundleError:  # unreadable: exit 2, a named outcome
        pytest.fail(f"{what}: the tar itself still reads")
    assert not report.passed, f"{what} passed"
    assert 3 <= report.exit_code <= 9, (what, report.render())
    return report


def test_every_member_deleted_fails(world: bound.World, released: bytes) -> None:
    members = _members(released)
    for name, _ in members:
        kept = [m for m in members if m[0] != name]
        report = _named(world, _tar(kept), f"deleting {name}")
        assert report.failed("V1"), (name, report.render())


def test_every_member_duplicated_or_moved_fails(world: bound.World, released: bytes) -> None:
    members = _members(released)
    for i, (name, body) in enumerate(members):
        doubled = [*members[: i + 1], (name, body), *members[i + 1 :]]
        report = _named(world, _tar(doubled), f"duplicating {name}")
        assert any("appears 2 times" in f.message for f in report.failed("V1"))
    for i in range(len(members) - 1):
        moved = [*members[:i], members[i + 1], members[i], *members[i + 2 :]]
        report = _named(world, _tar(moved), f"swapping {members[i][0]}")
        assert any("sorted order" in f.message for f in report.failed("V1"))


def test_a_byte_changed_in_any_member_fails(world: bound.World, released: bytes) -> None:
    """Each member with one hex digit changed: its digest no longer matches
    (V1); and with the manifest rewritten to match and left unsigned, a check
    that reads the member names it."""
    members = _members(released)
    kinds: dict[str, set[str]] = {}
    for i, (name, body) in enumerate(members):
        changed = [*members[:i], (name, _flip(body)), *members[i + 1 :]]
        report = _named(world, _tar(changed), f"changing {name}")
        if name != "manifest.json":
            assert any(name in f.message for f in report.failed("V1")), (name, report.render())
            report = _named(world, _tar(_remanifest(changed)), f"changing {name}, re-listed")
        kinds[name] = {f.check for f in report.findings}
    assert all(kinds.values()), kinds


# -- offline -----------------------------------------------------------------------------


def test_verify_bundle_runs_with_no_network_and_no_database(
    world: bound.World, released: bytes, tmp_path: Path
) -> None:
    """The CLI verifies in a fresh interpreter whose sockets, SQLite and
    PostgreSQL connections all raise (F9-3), to the same verdict."""
    import json
    import subprocess
    import sys

    bundle = tmp_path / "released.fidb"
    bundle.write_bytes(released)
    keys = tmp_path / "keys.json"
    keys.write_text(json.dumps(dict(bk.auditor_keys().raw)))
    script = tmp_path / "offline.py"
    script.write_text(
        "import socket, sqlite3, sys\n"
        "def refuse(*a, **k):\n"
        "    raise RuntimeError('offline: no connections')\n"
        "socket.socket.connect = refuse\n"
        "socket.create_connection = refuse\n"
        "socket.getaddrinfo = refuse\n"
        "sqlite3.connect = refuse\n"
        "try:\n"
        "    import psycopg\n"
        "    psycopg.connect = refuse\n"
        "except ImportError:\n"
        "    pass\n"
        "from fiduciary import cli\n"
        "sys.exit(cli.main(sys.argv[1:]))\n"
    )
    run = subprocess.run(  # noqa: S603 - this interpreter, running fiduciary
        [
            sys.executable,
            str(script),
            "verify-bundle",
            str(bundle),
            "--charter",
            world.charter.digest,
            "--keys",
            str(keys),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "V12 ordering: ok" in run.stdout

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
from fiduciary._upstream import ILOK1_DIGESTS
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


def _signed_at_version_2(released: bytes) -> tuple[str, bytes]:
    """``released`` with its first ILOK1 record's ``sig`` saying signature
    version 2, the manifest rewritten to match: the record as U-2 writes it,
    but for its signature, which covers version 1."""
    members = dict(_members(released))
    path = next(p for p in sorted(members) if p.startswith("ilok1/") and members[p])
    first, rest = members[path].split(b"\n", 1)
    record = fidu1.parse(first)
    record["sig"]["sv"] = 2
    members[path] = fidu1.canonical(record) + b"\n" + rest
    return path, _tar(_remanifest(sorted(members.items())))


def test_a_record_this_interlock_cannot_read_leaves_the_bundle_unreadable(
    world: bound.World, released: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verified where the installed Interlock reads signature version 1 only,
    a record signed at version 2 (U-2: an AWS KMS key's) is no finding about
    the bundle: the verifier cannot read it (exit 2), and says which record,
    and which release can."""
    from fiduciary.bundle import BundleError

    monkeypatch.setattr("fiduciary.bundle.verify.ILOK1_DIGESTS", False)
    path, data = _signed_at_version_2(released)
    with pytest.raises(
        BundleError, match=rf"^{path} line 1 .* version 2, .*interlock-escrow 1\.0\.3"
    ):
        bk.verify(world, data)


@pytest.mark.skipif(not ILOK1_DIGESTS, reason="this Interlock reads signature version 1 only (U-2)")
def test_a_record_an_interlock_with_u2_reads_is_checked(
    world: bound.World, released: bytes
) -> None:
    """With U-2 installed the same record is read, and its signature, made
    over version 1, fails a named check."""
    _, data = _signed_at_version_2(released)
    _named(world, data, "a record whose sig says version 2")


def test_a_member_no_manifest_lists_is_named(world: bound.World, released: bytes) -> None:
    members = _members(released)
    extra = sorted([*members, ("notes/extra.txt", b"an extra member\n")])
    report = _named(world, _tar(extra), "an unlisted member")
    assert any("not in the manifest" in f.message for f in report.failed("V1")), report.render()


def test_a_document_under_another_digest_is_named(world: bound.World, released: bytes) -> None:
    """A mandate moved to another digest's name, listed there, the manifest
    re-signed by no one: its name is not its digest (V1)."""
    members = dict(_members(released))
    old = next(n for n in members if fidu1.HASH.fullmatch(n.removeprefix("mandates/")[:-5]))
    new = f"mandates/{'0' * 64}.json"
    members[new] = members.pop(old)
    manifest = fidu1.parse(members["manifest.json"])
    for entry in manifest["members"]:
        if entry["path"] == old:
            entry["path"] = new
    manifest["members"].sort(key=lambda m: m["path"])
    members["manifest.json"] = fidu1.canonical(manifest)
    report = _named(world, _tar(_remanifest(sorted(members.items()))), "a renamed document")
    renamed = [f for f in report.failed("V1") if "does not match its file name" in f.message]
    assert renamed, report.render()


def test_a_bundle_of_another_ledger_than_the_charters_is_named(
    world: bound.World, released: bytes
) -> None:
    members = dict(_members(released))
    manifest = fidu1.parse(members["manifest.json"])
    manifest["ledger"]["first"] = "c" * 64
    members["manifest.json"] = fidu1.canonical(manifest)
    report = _named(world, _tar(_remanifest(sorted(members.items()))), "another ledger")
    assert any("binds another ledger" in f.message for f in report.failed("V2")), report.render()

"""The bundle's container (1.11.0): a ``.fidb`` is read as :func:`pack` writes it
and as the console's in-browser check reads it, header by header, and nothing
else is read. Each case here was a way to make the two readers disagree, or to
make the Python one allocate far more than the bundle holds."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from fiduciary.bundle import format as fmt
from fiduciary.bundle.format import BundleError, pack, unpack

ROOT = Path(__file__).resolve().parent.parent
VALID = ROOT / "vectors" / "fidu1" / "bundles" / "valid-lineage-at-work.fidb"
VERIFY_JS = ROOT / "src" / "fiduciary" / "report" / "verify.js"
MEMBERS = {"a.json": b'{"a":1}', "b/c.json": b"x" * 700}


def _header(name: bytes, size: int, *, kind: bytes = b"0", mode: bytes = b"0000644") -> bytes:
    """One ustar header, its checksum right."""
    block = bytearray(512)
    block[0 : len(name)] = name
    block[100:108] = mode + b"\x00"
    block[108:116] = b"0000000\x00"
    block[116:124] = b"0000000\x00"
    block[124:136] = b"%011o\x00" % size
    block[136:148] = b"00000000000\x00"
    block[156:157] = kind
    block[257:265] = b"ustar\x0000"
    block[148:156] = b"        "
    block[148:156] = b"%06o\x00 " % sum(block)
    return bytes(block)


def _member(name: bytes, data: bytes, **kw: bytes) -> bytes:
    return _header(name, len(data), **kw) + data + b"\x00" * (-len(data) % 512)


def _end() -> bytes:
    return b"\x00" * 1024


def test_what_pack_writes_is_what_unpack_reads_and_what_tarfile_reads() -> None:
    data = pack(MEMBERS)
    assert unpack(data) == sorted(MEMBERS.items())
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        read = [(m.name, tar.extractfile(m).read()) for m in tar.getmembers()]  # type: ignore[union-attr]
    assert read == unpack(data)
    for path in sorted((ROOT / "vectors" / "fidu1" / "bundles").glob("*.fidb")):
        if path.name == "unreadable-not-a-tar.fidb":
            continue
        with tarfile.open(path) as tar:
            read = [(m.name, tar.extractfile(m).read()) for m in tar.getmembers()]  # type: ignore[union-attr]
        assert unpack(path.read_bytes()) == read, path.name


def test_a_hand_built_header_is_read_as_pack_writes_it() -> None:
    """The cases below are built by hand; this one, built so, is a bundle."""
    assert unpack(_member(b"a.json", b"{}") + _end()) == [("a.json", b"{}")]


def _sparse_bomb() -> bytes:
    """A GNU sparse member: 2 KiB on disk, 600 MiB to a reader that fills its
    holes (tarfile does). The audit's proof of concept, as a fixture."""
    info = tarfile.TarInfo("keys.json")
    info.type = tarfile.GNUTYPE_SPARSE
    info.size = 0
    block = bytearray(info.tobuf(tarfile.GNU_FORMAT))
    realsize = 600 * 1024 * 1024
    block[483:495] = b"%011o\x00" % realsize  # the real size the holes add up to
    block[148:156] = b"        "
    block[148:156] = b"%06o\x00 " % sum(block)
    return bytes(block) + _end()


@pytest.mark.parametrize(
    ("what", "data", "says"),
    [
        ("a GNU sparse member", _sparse_bomb(), "not in a POSIX ustar header"),
        ("a symbolic link", _member(b"keys.json", b"", kind=b"2") + _end(), "not a regular file"),
        ("a hard link", _member(b"keys.json", b"", kind=b"1") + _end(), "not a regular file"),
        ("a contiguous file", _member(b"keys.json", b"{}", kind=b"7") + _end(), "regular file"),
        ("a pax header", _member(b"pax", b"9 a=b\n", kind=b"x") + _end(), "not a regular file"),
        ("a GNU long name", _member(b"././@LongLink", b"a\x00", kind=b"L") + _end(), "regular"),
        ("mode 0755", _member(b"keys.json", b"{}", mode=b"0000755") + _end(), "mode is not 0644"),
        ("an absolute path", _member(b"/etc/keys.json", b"{}") + _end(), "outside the bundle"),
        ("a path up and out", _member(b"../keys.json", b"{}") + _end(), "outside the bundle"),
        ("a name not ASCII", _member("kéys.json".encode(), b"{}") + _end(), "not ASCII"),
        ("data past the end", _member(b"a.json", b"{}") + _end() + b"junk", "follows the end"),
        ("data in the padding", _header(b"a.json", 2) + b"{}" + b"x" * 510 + _end(), "padded"),
        ("a member cut short", _header(b"a.json", 2048) + b"{}", "runs past the end"),
        ("a checksum wrong", b"x" * 512 + _end(), "checksum is wrong"),
        ("no member", _end(), "an empty archive"),
    ],
)
def test_anything_but_what_pack_writes_is_unreadable(what: str, data: bytes, says: str) -> None:
    with pytest.raises(BundleError, match=says):
        unpack(data)


def test_a_mtime_or_a_base_256_size_is_unreadable() -> None:
    late = bytearray(_header(b"a.json", 2))
    late[136:148] = b"14000000000\x00"
    late[148:156] = b"        "
    late[148:156] = b"%06o\x00 " % sum(late)
    with pytest.raises(BundleError, match="mtime is not 0"):
        unpack(bytes(late) + b"{}" + b"\x00" * 510 + _end())
    huge = bytearray(_header(b"a.json", 0))
    huge[124:136] = b"\x80" + b"\x00" * 10 + b"\x02"  # GNU base-256: 2 bytes, as no ustar size
    huge[148:156] = b"        "
    huge[148:156] = b"%06o\x00 " % sum(huge)
    with pytest.raises(BundleError, match="size is not an octal number"):
        unpack(bytes(huge) + _end())


def test_the_bounds_are_held(monkeypatch: pytest.MonkeyPatch) -> None:
    data = pack(MEMBERS)
    monkeypatch.setattr(fmt, "MAX_MEMBER_BYTES", 699)
    with pytest.raises(BundleError, match="700 bytes, more than 699 read"):
        unpack(data)
    monkeypatch.setattr(fmt, "MAX_MEMBER_BYTES", 1 << 30)
    monkeypatch.setattr(fmt, "MAX_MEMBERS", 1)
    with pytest.raises(BundleError, match="more than 1 members"):
        unpack(data)
    monkeypatch.setattr(fmt, "MAX_BUNDLE_BYTES", len(data) - 1)
    with pytest.raises(BundleError, match="more than"):
        unpack(data)


def test_pack_writes_only_ascii_paths() -> None:
    with pytest.raises(BundleError, match="ASCII"):
        pack({"kéys.json": b"{}"})


def test_the_browser_reads_the_container_as_the_verifier_does(tmp_path: Path) -> None:
    """Every case above fails the page's container check too, and what pack
    writes passes it: the two readers hold one rule (``report/verify.js``)."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    cases = {
        "valid": pack(MEMBERS),
        "sparse": _sparse_bomb(),
        "mode": _member(b"keys.json", b"{}", mode=b"0000755") + _end(),
        "pax": _member(b"pax", b"9 a=b\n", kind=b"x") + _end(),
        "trailing": _member(b"a.json", b"{}") + _end() + b"junk",
        "padding": _header(b"a.json", 2) + b"{}" + b"x" * 510 + _end(),
        "ascii": _member("kéys.json".encode(), b"{}") + _end(),
    }
    for name, data in cases.items():
        (tmp_path / f"{name}.tar").write_bytes(data)
    where = json.dumps(str(tmp_path))
    script = (
        f"const v = require({json.dumps(str(VERIFY_JS))});"
        "const fs = require('node:fs');"
        f"for (const n of {json.dumps(sorted(cases))}) {{"
        f"  const b = fs.readFileSync({where} + '/' + n + '.tar');"
        "  const r = v.readTar(new Uint8Array(b));"
        "  console.log(n + ' ' + (r.problems.length ? 'fail' : 'pass'));"
        "}"
    )
    done = subprocess.run(  # noqa: S603 - node and the repository's own script
        [node, "-e", script], capture_output=True, text=True, check=True, timeout=60
    )
    said = dict(line.split() for line in done.stdout.splitlines())
    assert said == {name: "pass" if name == "valid" else "fail" for name in cases}

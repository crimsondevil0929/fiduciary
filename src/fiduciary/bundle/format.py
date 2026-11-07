"""The bundle's format (``docs/FIDUCIARY_SPEC.md`` §9.2, ``docs/BUNDLE_FORMAT.md``).

A bundle is an uncompressed POSIX tar: members in sorted order, regular files
only, mode 0644, owner and group 0 with empty names, and mtime 0. One lineage
over one range therefore exports to identical bytes. ``manifest.json`` names
every other member, its type and its SHA-256.

A check's findings carry the class of failure §9.3 names, and each class its
exit code. A run's exit code is the code of the first check, in the order V1
to V12, that found anything.
"""

from __future__ import annotations

import io
import tarfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

from fiduciary.exceptions import FiduciaryError

__all__ = [
    "CHECKS",
    "EXIT",
    "FORMAT",
    "SEALED",
    "BundleError",
    "BundleReport",
    "Finding",
    "pack",
    "unpack",
]

FORMAT: Final = "fidb/1"
SEALED: Final = "fidb/2"
"""A sealed bundle's format: one lineage's records whole, and of the log's
others, their places and hashes (``withheld.json``). Added in 1.4.0."""
CHECKS: Final = (
    ("V1", "schema"),
    ("V2", "charter"),
    ("V3", "lineage"),
    ("V4", "compiled policy"),
    ("V5", "receipts"),
    ("V6", "ledger"),
    ("V7", "completeness"),
    ("V8", "warrants"),
    ("V9", "records"),
    ("V10", "anchors"),
    ("V11", "revocation"),
    ("V12", "ordering"),
)
EXIT: Final = {
    "malformed": 3,
    "signature": 4,
    "authority": 5,
    "monotonicity": 6,
    "policy": 6,
    "ambient": 7,
    "evidence": 8,
    "revocation": 9,
    "time": 9,
}
"""Each class of failure, and its exit code. 0 passes; 1 is a usage error; 2 a
bundle the verifier could not read."""


class BundleError(FiduciaryError):
    """A bundle that cannot be exported, or read at all (exit 2)."""


@dataclass(frozen=True, slots=True)
class Finding:
    """One way a bundle fails a check.

    :ivar label: ``break-glass`` for an ambient act signed by a key the
        charter declares so (F4-6); otherwise empty.
    """

    check: str
    kind: str
    message: str
    label: str = ""

    def render(self) -> str:
        tag = f" [{self.label}]" if self.label else ""
        return f"{self.check} {self.kind}{tag}: {self.message}"


@dataclass(slots=True)
class BundleReport:
    """What :func:`~fiduciary.bundle.verify_bundle` found: every finding of
    every check, and the notes that qualify a pass (what the bundle does not
    claim, which keys the auditor did not pin)."""

    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, check: str, kind: str, message: str, label: str = "") -> None:
        self.findings.append(Finding(check, kind, message, label))

    def failed(self, check: str) -> list[Finding]:
        return [f for f in self.findings if f.check == check]

    @property
    def passed(self) -> bool:
        return not self.findings

    @property
    def exit_code(self) -> int:
        for check, _ in CHECKS:
            found = self.failed(check)
            if found:
                return EXIT[found[0].kind]
        return 0

    def render(self) -> str:
        lines = []
        for check, name in CHECKS:
            found = self.failed(check)
            status = "ok" if not found else f"FAILED ({len(found)})"
            lines.append(f"{check} {name}: {status}")
            lines.extend(f"  {f.render()}" for f in found)
        lines.extend(f"note: {note}" for note in self.notes)
        return "\n".join(lines)


def pack(members: Mapping[str, bytes]) -> bytes:
    """A deterministic tar of ``members`` (§9.2)."""
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name in sorted(members):
            if not name.isascii():
                raise BundleError(f"member {name!r}: a bundle's paths are ASCII")
            data = members[name]
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.type = tarfile.REGTYPE
            tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


MAX_BUNDLE_BYTES: Final = 2 << 30
"""The largest bundle read, in bytes (2 GiB). A member is a slice of the bundle's
own bytes, so what a bundle unpacks to is never more than it is. Added in 1.11.0."""
MAX_MEMBER_BYTES: Final = 1 << 30
"""The largest member read, in bytes (1 GiB). Added in 1.11.0."""
MAX_MEMBERS: Final = 1_000_000
"""The most members a bundle may hold. Added in 1.11.0."""
_BLOCK: Final = 512


def _field(header: bytes, start: int, length: int) -> bytes:
    """A header field up to its first NUL, as a POSIX tar reader reads it."""
    raw = header[start : start + length]
    end = raw.find(b"\x00")
    return raw if end < 0 else raw[:end]


def _octal(header: bytes, start: int, length: int) -> int | None:
    """An octal header field, or ``None`` when it is not one: a base-256 number
    (a GNU extension) or anything else is not what :func:`pack` writes."""
    text = _field(header, start, length).strip(b" ")
    if not text or any(c not in b"01234567" for c in text):
        return None
    return int(text, 8)


def unpack(data: bytes) -> list[tuple[str, bytes]]:
    """The members of a bundle, in the order the tar holds them, duplicates
    and all (the schema check names a duplicate or one out of order).

    The tar is read as :func:`pack` writes it and as the console's in-browser
    check reads it (``report/verify.js``), header by header, and nothing else is
    accepted (1.11.0): each member a regular file (type ``0``) under a relative
    ASCII path, in a POSIX ``ustar`` header with a correct checksum, mode 0644
    and mtime 0, its data within the bundle and its padding zero; then zeros to
    the end. No sparse, pax, GNU or link entry is read, so no member can
    unpack to more bytes than the bundle holds (``MAX_BUNDLE_BYTES``,
    ``MAX_MEMBER_BYTES``, ``MAX_MEMBERS`` bound the rest).

    :raises BundleError: When it is no such tar.
    """
    if len(data) > MAX_BUNDLE_BYTES:
        raise BundleError(f"not a bundle: {len(data)} bytes, more than {MAX_BUNDLE_BYTES} read")
    found: list[tuple[str, bytes]] = []
    view = memoryview(data)
    offset = 0
    while offset + _BLOCK <= len(data):
        header = bytes(view[offset : offset + _BLOCK])
        if not any(header):
            break
        summed = sum(header[:148]) + 8 * 32 + sum(header[156:])
        if _octal(header, 148, 8) != summed:
            raise BundleError("not a bundle: a member's header checksum is wrong")
        try:
            name = _field(header, 0, 100).decode("ascii")
            prefix = _field(header, 345, 155).decode("ascii")
        except UnicodeDecodeError:
            raise BundleError("not a bundle: a member's name is not ASCII") from None
        name = f"{prefix}/{name}" if prefix else name
        if header[257:265] != b"ustar\x0000":
            raise BundleError(f"not a bundle: member {name!r} is not in a POSIX ustar header")
        if header[156:157] not in (b"0", b"\x00"):
            raise BundleError(f"member {name!r} is not a regular file")
        if not name or name.startswith("/") or ".." in name.split("/"):
            raise BundleError(f"member {name!r} is outside the bundle")
        if _octal(header, 100, 8) != 0o644:
            raise BundleError(f"member {name!r}: its mode is not 0644")
        if _octal(header, 136, 12) != 0:
            raise BundleError(f"member {name!r}: its mtime is not 0")
        size = _octal(header, 124, 12)
        if size is None:
            raise BundleError(f"member {name!r}: its size is not an octal number")
        if size > MAX_MEMBER_BYTES:
            raise BundleError(f"member {name!r}: {size} bytes, more than {MAX_MEMBER_BYTES} read")
        start = offset + _BLOCK
        padded = start + -(-size // _BLOCK) * _BLOCK
        if padded > len(data):
            raise BundleError(f"not a bundle: member {name!r} runs past the end of the file")
        if any(view[start + size : padded]):
            raise BundleError(f"not a bundle: member {name!r} is padded with data")
        if len(found) >= MAX_MEMBERS:
            raise BundleError(f"not a bundle: more than {MAX_MEMBERS} members")
        found.append((name, bytes(view[start : start + size])))
        offset = padded
    if any(view[offset:]):
        raise BundleError("not a bundle: data follows the end of the archive")
    if not found:
        raise BundleError("an empty archive is no bundle")
    return found

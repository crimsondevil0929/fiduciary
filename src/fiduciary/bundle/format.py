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


def unpack(data: bytes) -> list[tuple[str, bytes]]:
    """The members of a bundle, in the order the tar holds them, duplicates
    and all (the schema check names a duplicate or one out of order).

    :raises BundleError: When it is no tar, or holds anything but regular files
        under relative paths.
    """
    found: list[tuple[str, bytes]] = []
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
            for info in tar.getmembers():
                name = info.name
                if not info.isreg():
                    raise BundleError(f"member {name!r} is not a regular file")
                if name.startswith("/") or ".." in name.split("/"):
                    raise BundleError(f"member {name!r} is outside the bundle")
                handle = tar.extractfile(info)
                assert handle is not None
                found.append((name, handle.read()))
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise BundleError(f"not a bundle: {exc}") from exc
    if not found:
        raise BundleError("an empty archive is no bundle")
    return found

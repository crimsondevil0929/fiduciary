"""Credentials an agent could read in a workspace (``fiduciary protect --scan-secrets``, 1.20.0).

An agent reads files in passing: a ``.env``, a deploy script, a config with a token someone
pasted into it. A token it read can then leave in a call's arguments, or be used to call a
system directly, as in the incidents where an agent found a token in an unrelated file and
deleted a volume with it.

:func:`scan` walks a directory and finds each credential with the same rules a protected
session applies to what it reads (:func:`fiduciary.egress.credentials_in`): providers'
published formats, passwords in connection strings, values given to names like ``api_key``,
long random-looking tokens. It is deterministic: no model, and a file is read whole or not
at all. It skips what an agent seldom reads and a scan cannot read well: version control,
dependency and build directories, files larger than :data:`MAX_FILE`, and binary files.

What is found is not moved: a token in a ``.env`` belongs to whatever reads that file, and
rewriting it would break that program. Two things are done instead:

- each value is **seeded**: its keyed digest is given to every protected server's session as
  ``secret`` from its first call (:func:`fiduciary.flow.seed`), so no call carries it out,
  whichever tool the agent read it with, the client's own file tools among them;
- a value in an MCP client's file (a server's ``env``, a remote server's headers) is moved by
  ``protect --move-secrets``, which says so.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

__all__ = ["MAX_FILE", "SKIPPED", "Finding", "preview", "scan"]

MAX_FILE: Final = 1_048_576
"""Files larger than this, in bytes, are not read."""
SKIPPED: Final = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".tox",
        "dist",
        "build",
        "target",
        ".next",
        ".fiduciary",
    }
)
"""Directories a scan does not enter."""
CLIENT_FILES: Final = frozenset({".mcp.json", "mcp.json", "claude_desktop_config.json"})
"""MCP client files, by name: a value in one is moved by ``--move-secrets``."""
MAX_FILES: Final = 20000
"""At most this many files are read in one scan."""


@dataclass(frozen=True, slots=True)
class Finding:
    """One credential in one file."""

    path: Path
    line: int
    kind: str
    value: str = field(repr=False)
    """The credential itself: seeded as a digest, never printed (:func:`preview`)."""

    @property
    def in_client_file(self) -> bool:
        return self.path.name in CLIENT_FILES or (
            self.path.name == "config.toml" and self.path.parent.name == ".codex"
        )


def preview(value: str) -> str:
    """``value`` as a person may see it: its first four and last two characters, and its
    length; the dots alone for a short one."""
    if len(value) < 12:
        return f"…, {len(value)} characters"
    return f"{value[:4]}…{value[-2:]}, {len(value)} characters"


def _files(root: Path) -> Iterator[Path]:
    for directory, folders, names in os.walk(root):
        folders[:] = sorted(f for f in folders if f not in SKIPPED)
        for name in sorted(names):
            yield Path(directory) / name


def scan(root: Path) -> list[Finding]:
    """Every credential in the files under ``root`` (module docstring), in path order, each
    value once per file."""
    from fiduciary.egress import credentials_in

    found: list[Finding] = []
    for count, path in enumerate(_files(root)):
        if count >= MAX_FILES:
            break
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue  # a binary file
        text = data.decode("utf-8", "replace")
        seen: set[str] = set()
        for number, line in enumerate(text.splitlines(), 1):
            for value, kind in credentials_in(line):
                if value not in seen:
                    seen.add(value)
                    found.append(Finding(path, number, kind, value))
    return found

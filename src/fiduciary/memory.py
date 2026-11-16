"""What an agent writes into its memory keeps the labels of the session that wrote it (1.21.0).

An agent that reads an untrusted issue today and saves a note to its memory (a
``MEMORY.md``, a ``CLAUDE.md``, a memory server's entities) reads that note back tomorrow, in
a new session that has seen nothing untrusted. Without more, the note arrives clean: OWASP's
ASI06, memory and context poisoning. Here, labels survive the night:

- **A write to memory is recorded with its labels.** When a session that holds a label others
  may take up (``untrusted``, ``secret``, ``private``, a partition's) writes to memory, a
  ``session.remembered`` record names the tool, the plan, what was written (the paths or
  names), the labels the session held (from its records, never the caller's word), and a
  keyed fingerprint of each line written. A memory write is a call whose binding says so
  (``memory``): a memory server's write tools, a file tool writing to a memory file.
- **A read that carries a remembered line takes its labels.** Every result a session reads is
  fingerprinted the same way. A line some ``session.remembered`` record wrote labels the new
  session with that record's labels, in a ``session.labeled`` record that cites it (its
  sequence number and hash) and the fingerprints that matched. V13 checks the citation offline
  against the cited record, when the bundle holds it.
- **What the client loads by itself is read too.** A coding client loads ``CLAUDE.md`` or
  ``AGENTS.md`` into each session without a tool call. Under ``fiduciary protect``, a
  session's first call reads the memory files at the top of the workspace and takes their
  labels, as if a tool had returned them (source ``fiduciary_memory``).
- **What a held memory server stores waits.** ``protect`` holds every write of a server whose
  tools are all memory once the session holds ``untrusted`` (its mandate's ``untrusted``
  sinks), so a poisoned note waits for its owner's approval before it is stored at all.

A fingerprint is a keyed digest (HMAC-SHA-256, cut to 128 bits) of one line, read as
:func:`fiduciary.egress.plain` reads it, with list markers and repeated spaces removed. The
key is the deployment's (``memory/key``, or a protect root's, shared by its servers), so a
line matches across sessions and days, and a record discloses nothing a reader without the key
could test a guess against. Lines shorter than :data:`MIN_LINE` characters are not
fingerprinted: they are too common to tell one memory from another.

What this does not do: decide whether a memory's content is malicious (no model reads it); see
a memory the agent read through a channel no binding covers (the client's own file tools,
outside ``protect``'s first-call read and the frameworks' hooks); or match a memory rewritten
in other words. Labels only ever join: a citation can add a label, and nothing here removes
one.
"""

from __future__ import annotations

import contextlib
import fcntl
import fnmatch
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final

__all__ = [
    "KEY_ARGUMENTS",
    "MEMORY_FILES",
    "MEMORY_TOOLS",
    "MIN_LINE",
    "SOURCE",
    "Cited",
    "Memory",
    "is_memory_path",
    "keys_of",
    "lines_of",
    "memory_of",
]

SOURCE: Final = "fiduciary_memory"
"""The source a session's records name for memory files the client loaded by itself."""

MIN_LINE: Final = 16
"""The shortest line fingerprinted, in characters, as it reads."""

PER_WRITE: Final = 4096
"""At most this many lines are fingerprinted from one write or one read."""

MAX_READ: Final = 1_048_576
"""A memory file larger than this, in bytes, is not read at a session's start."""

MEMORY_FILES: Final = (
    "MEMORY.md",
    "CLAUDE.md",
    "CLAUDE.local.md",
    "AGENTS.md",
    "GEMINI.md",
    ".cursorrules",
    ".windsurfrules",
    ".clinerules",
    ".github/copilot-instructions.md",
    ".claude/*.md",
    ".claude/memory/*",
    ".cursor/rules/*",
    "memory/*",
    "memories/*",
    "*.memory.md",
)
"""Where coding agents keep what they remember, as paths under a workspace: a write to one of
these is a memory write, and the first of them at a workspace's top are read at a session's
start."""

MEMORY_TOOLS: Final = frozenset(
    {
        "add_memories",
        "add_memory",
        "add_observations",
        "create_entities",
        "create_memory",
        "create_relations",
        "memorize",
        "remember",
        "save_memory",
        "store_memory",
        "update_memory",
        "upsert_memory",
        "write_memory",
    }
)
"""Write tools of memory servers by their names: the reference server's
(``create_entities``, ``add_observations``, ``create_relations``) and the common ones."""

KEY_ARGUMENTS: Final = (
    "path",
    "file_path",
    "filename",
    "key",
    "name",
    "entityName",
    "entity_name",
    "title",
    "memory_id",
)
"""The arguments that name what a memory write writes, where a tool has them."""

_MARKER: Final = re.compile(r"^(?:[-*+>•]|\d{1,3}[.)]|#{1,6})\s+")
_SPACES: Final = re.compile(r"\s+")


def is_memory_path(path: str) -> bool:
    """Whether ``path`` is one of :data:`MEMORY_FILES`, at any depth."""
    parts = PurePosixPath(path.replace("\\", "/")).parts
    for start in range(len(parts)):
        tail = "/".join(parts[start:])
        if any(fnmatch.fnmatchcase(tail, pattern) for pattern in MEMORY_FILES):
            return True
    return False


def lines_of(value: Any) -> list[str]:
    """The lines a value holds as a memory is compared: each string's lines, read plain, with
    a list marker or heading mark removed and runs of spaces made one, and the strings of a
    string that is JSON (a memory server's answer, in a text block); those of
    :data:`MIN_LINE` characters or more, once each, at most :data:`PER_WRITE`."""
    from fiduciary.egress import plain, strings_in

    found: dict[str, None] = {}
    pending: list[tuple[Any, int]] = [(value, 0)]
    while pending:
        item, depth = pending.pop(0)
        for text in strings_in(item, limit=PER_WRITE):
            stripped = text.strip()
            if depth < 3 and stripped[:1] in ("{", "[") and len(stripped) <= MAX_READ:
                # a memory server's answer is often JSON in a text block: its strings
                with contextlib.suppress(ValueError):
                    pending.append((json.loads(stripped), depth + 1))
                    continue
            for line in plain(text).splitlines():
                line = _SPACES.sub(" ", _MARKER.sub("", line.strip())).strip()
                if len(line) >= MIN_LINE:
                    found[line] = None
                    if len(found) >= PER_WRITE:
                        return list(found)
    return list(found)


def keys_of(arguments: Mapping[str, Any], names: Sequence[str]) -> list[str]:
    """What ``arguments`` say a memory write writes: the values of ``names`` (or, with none
    given, of :data:`KEY_ARGUMENTS`), and the same arguments of the objects in a list (the
    entities a memory server's ``create_entities`` takes), sorted, each at most 256
    characters, at most 64."""
    wanted = tuple(names) or KEY_ARGUMENTS
    found: set[str] = set()

    def visit(item: Any, depth: int) -> None:
        if depth > 2 or len(found) >= 64:
            return
        if isinstance(item, Mapping):
            for name in wanted:
                value = item.get(name)
                if isinstance(value, str) and value.strip():
                    found.add(value.strip()[:256])
            for value in item.values():
                if isinstance(value, list | tuple | Mapping):
                    visit(value, depth + 1)
        elif isinstance(item, list | tuple):
            for value in item[:64]:
                visit(value, depth + 1)

    visit(arguments, 0)
    return sorted(found)[:64]


def memory_of(binding: Any, arguments: Mapping[str, Any]) -> list[str] | None:
    """What a call of ``binding`` writes to memory, by its keys; ``None`` for a call that
    writes none: a binding with no ``memory``, or one whose ``paths`` no key matches."""
    spec = getattr(binding, "memory", None)
    if spec is None:
        return None
    keys = keys_of(arguments, spec.keys)
    if spec.paths and not any(_matches(spec.paths, key) for key in keys):
        return None
    return keys


def _matches(patterns: Sequence[str], key: str) -> bool:
    if tuple(patterns) == ("*",):
        return True
    parts = PurePosixPath(key.replace("\\", "/")).parts
    tails = ["/".join(parts[i:]) for i in range(len(parts))]
    return any(fnmatch.fnmatchcase(t, p) for t in tails for p in patterns)


@dataclass(frozen=True, slots=True)
class Cited:
    """A ``session.remembered`` record a read matched."""

    seq: int
    hash: str
    labels: tuple[str, ...]
    matched: tuple[str, ...]
    """The fingerprints the read and the record share."""


class Memory:
    """One deployment's memory key, and the index of what its sessions remembered, shared by
    every log that uses the same directory (a protect root's servers).

    The index holds, for each ``session.remembered`` record, its sequence number, hash, labels
    and fingerprints: what a session of another log needs to take up a memory one server's
    session wrote. The records themselves stay in their logs; the index is rebuilt from them
    at no cost by writing them again (:meth:`publish` keeps each once)."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._lock = threading.Lock()
        self._loaded: tuple[int, int] | None = None
        self._index: dict[str, list[Cited]] = {}
        self._records: dict[str, Cited] = {}
        self._key_bytes: bytes | None = None

    @property
    def key(self) -> bytes:
        """The key every fingerprint is made under: made once, on first use."""
        if self._key_bytes is None:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._key_bytes = self._key()
        return self._key_bytes

    def _key(self) -> bytes:
        path = self.directory / "key"
        with self._file_lock():
            if not path.is_file():
                temporary = self.directory / f"key.{os.getpid()}.tmp"
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(descriptor, "w") as out:
                    out.write(secrets.token_hex(32))
                temporary.replace(path)
            return bytes.fromhex(path.read_text(encoding="utf-8").strip())

    @contextlib.contextmanager
    def _file_lock(self) -> Iterator[None]:
        with open(self.directory / "lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def fingerprint(self, line: str) -> str:
        return hmac.new(self.key, line.encode("utf-8"), hashlib.sha256).hexdigest()[:32]

    def fingerprints(self, value: Any) -> list[str]:
        """The fingerprints of what ``value`` holds (:func:`lines_of`), sorted, each once."""
        return sorted({self.fingerprint(line) for line in lines_of(value)})

    # -- the shared index ----------------------------------------------------------------

    @property
    def _path(self) -> Path:
        return self.directory / "remembered.jsonl"

    def publish(
        self, seq: int, record_hash: str, labels: Sequence[str], fingerprints: Sequence[str]
    ) -> None:
        """Add one ``session.remembered`` record to the index, once."""
        if record_hash in self._records:
            return
        line = json.dumps(
            {
                "seq": seq,
                "hash": record_hash,
                "labels": sorted(labels),
                "fingerprints": sorted(fingerprints),
            },
            sort_keys=True,
        )
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self._file_lock():
            descriptor = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(descriptor, "a", encoding="utf-8") as out:
                out.write(line + "\n")
        self._add(Cited(seq, record_hash, tuple(sorted(labels)), tuple(sorted(fingerprints))))

    def _add(self, record: Cited) -> None:
        with self._lock:
            if record.hash in self._records:
                return
            self._records[record.hash] = record
            for fingerprint in record.matched:
                self._index.setdefault(fingerprint, []).append(record)

    def _refresh(self) -> None:
        """Read what other processes added to the index since it was last read."""
        try:
            stat = self._path.stat()
        except OSError:
            return
        mark = (stat.st_size, stat.st_mtime_ns)
        if mark == self._loaded:
            return
        with self._file_lock(), open(self._path, encoding="utf-8") as found:
            for line in found:
                with contextlib.suppress(ValueError, KeyError, TypeError):
                    item = json.loads(line)
                    self._add(
                        Cited(
                            int(item["seq"]),
                            str(item["hash"]),
                            tuple(str(x) for x in item["labels"]),
                            tuple(str(x) for x in item["fingerprints"]),
                        )
                    )
        self._loaded = mark

    @property
    def empty(self) -> bool:
        self._refresh()
        return not self._records

    def recall(self, fingerprints: Iterable[str]) -> list[Cited]:
        """Each remembered record any of ``fingerprints`` matches, with what matched, in the
        order the records were written."""
        self._refresh()
        hits: dict[str, set[str]] = {}
        with self._lock:
            for fingerprint in fingerprints:
                for record in self._index.get(fingerprint, ()):
                    hits.setdefault(record.hash, set()).add(fingerprint)
            found = [
                Cited(r.seq, r.hash, r.labels, tuple(sorted(hits[r.hash])))
                for r in self._records.values()
                if r.hash in hits
            ]
        return sorted(found, key=lambda c: (c.seq, c.hash))


def workspace_memory(workspace: Path) -> dict[str, str]:
    """The memory files at the top of ``workspace`` that a client loads by itself, by path
    relative to it: each one of :data:`MEMORY_FILES` without a ``/`` in it but ``.claude``'s
    and ``.github``'s, at most :data:`MAX_READ` bytes, read as text."""
    found: dict[str, str] = {}
    candidates = [
        workspace / name
        for name in MEMORY_FILES
        if "*" not in name or name.startswith((".claude/", ".cursor/"))
    ]
    for pattern in candidates:
        paths = sorted(pattern.parent.glob(pattern.name)) if "*" in pattern.name else [pattern]
        for path in paths:
            try:
                if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_READ:
                    continue
                found[path.relative_to(workspace).as_posix()] = path.read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                continue
    return dict(sorted(found.items()))

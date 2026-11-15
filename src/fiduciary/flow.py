"""What one client's protected servers have read, shared between their sessions (1.19.0).

``fiduciary protect`` gives each MCP server a proxy of its own, and each proxy its own session.
An agent that reads a token through one server (a database, a repository) and sends it out
through another (a fetch tool, a chat tool) would cross from one session to the other, and
neither would see the whole of it. The servers one client started share its process as their
parent, so their proxies share a small file under the protect root, named for that process:

- each session publishes the labels it holds and the keyed digests of the values it tracked
  (:class:`fiduciary.egress.Tracker`), all under one key kept beside the file;
- before each call, a session takes up what its siblings published: it records the labels in a
  ``session.labeled`` record and the digests in a ``session.read`` record of its own, both
  naming ``fiduciary_sibling`` as their source, so each server's bundle is verified on its own
  (V13), and its sends are judged by everything the client's servers have read.

The file holds labels and digests, never a value. A client that exits leaves its file behind;
the next proxy started under the root removes files whose client is gone. The scope is the
client process: a restarted client starts with nothing shared.

**Seeds (1.20.0).** Some of what a session must know comes from outside every server: the
credentials ``protect --scan-secrets`` found in the workspace, and in CI the event's untrusted
text and the job's secrets. :func:`seed` writes them beside the clients' files, under the same
key (``seeded.json``), by source: ``fiduciary_workspace`` and ``fiduciary_ci``. Every session
under the root takes them up before its first call, as it takes up its siblings', and its
records name the source. Seeds outlive any one client.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import secrets
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

__all__ = ["CI", "OUTSIDE", "SIBLING", "WORKSPACE", "Flow", "for_client", "seed"]

SIBLING: Final = "fiduciary_sibling"
"""The source a session's records name for what it took up from its siblings."""
WORKSPACE: Final = "fiduciary_workspace"
"""The source of what ``protect --scan-secrets`` found in the workspace (1.20.0)."""
CI: Final = "fiduciary_ci"
"""The source of what ``fiduciary ci`` knows of the job: its event, its secrets (1.20.0)."""
OUTSIDE: Final = frozenset({SIBLING, WORKSPACE, CI})
"""Every source a session's records may name that no binding pins (1.20.0)."""
SEEDS: Final = "seeded.json"

SHARED: Final = frozenset({"untrusted", "secret", "private"})
"""The labels a session shares: ``quarantined:`` and ``cleared`` are its own."""

MAX_DIGESTS: Final = 65536
"""At most this many digests are kept in one client's file."""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _shared(label: str) -> bool:
    return label in SHARED or label.startswith("partition:")


class Flow:
    """One client's shared file, as one session reads and writes it."""

    def __init__(self, directory: Path, scope: str, source: str) -> None:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory = directory
        self.path = directory / f"{scope}.json"
        self.source = source
        self.key = self._key()

    def _key(self) -> bytes:
        """The key every session of the root digests under: made once, under the lock and
        whole (written beside, then moved), so a sibling starting at the same moment never
        reads it half written."""
        path = self.directory / "key"
        with open(self.directory / "lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                if not path.is_file():
                    temporary = self.directory / f"key.{os.getpid()}.tmp"
                    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                    with os.fdopen(descriptor, "w") as out:
                        out.write(secrets.token_hex(32))
                    temporary.replace(path)
                return bytes.fromhex(path.read_text(encoding="utf-8").strip())
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    @contextlib.contextmanager
    def _locked(self) -> Iterator[dict[str, Any]]:
        with open(self.directory / "lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                try:
                    found = json.loads(self.path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    found = {}
                sources = found.get("sources")
                state: dict[str, Any] = {
                    "sources": dict(sources) if isinstance(sources, dict) else {}
                }
                yield state
                temporary = self.path.with_suffix(".tmp")
                temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
                os.chmod(temporary, 0o600)
                temporary.replace(self.path)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def publish(self, labels: Sequence[str], digests: Mapping[str, Sequence[str]]) -> None:
        """Add ``labels`` (those a sibling may take up) and ``digests`` to this session's
        share of the file."""
        with self._locked() as state:
            mine = state["sources"].setdefault(self.source, {"labels": [], "digests": {}})
            mine["labels"] = sorted({*mine.get("labels", ()), *filter(_shared, labels)})
            kept = mine.setdefault("digests", {})
            total = sum(len(d) for s in state["sources"].values() for d in s["digests"].values())
            for label, found in digests.items():
                if not _shared(label):
                    continue
                new = sorted(set(found) - set(kept.get(label, ())))[: max(0, MAX_DIGESTS - total)]
                total += len(new)
                if new:
                    kept[label] = sorted({*kept.get(label, ()), *new})

    def seeded(self) -> dict[str, tuple[set[str], dict[str, set[str]]]]:
        """What :func:`seed` gave every session under the root (1.20.0), by source: its
        labels, and its digests by label."""
        try:
            found = json.loads((self.directory / SEEDS).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        sources = found.get("sources") if isinstance(found, dict) else None
        out: dict[str, tuple[set[str], dict[str, set[str]]]] = {}
        for source, share in sorted((sources or {}).items()):
            if source not in OUTSIDE or source == SIBLING or not isinstance(share, dict):
                continue
            labels = {x for x in share.get("labels", ()) if isinstance(x, str) and _shared(x)}
            digests: dict[str, set[str]] = {}
            for label, values in (share.get("digests") or {}).items():
                if _shared(str(label)):
                    digests[str(label)] = {v for v in values if isinstance(v, str)}
            out[str(source)] = (labels, digests)
        return out

    def others(self) -> tuple[set[str], dict[str, set[str]]]:
        """What every other session of the client published: its labels, and its digests by
        label."""
        try:
            found = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set(), {}
        labels: set[str] = set()
        digests: dict[str, set[str]] = {}
        sources = found.get("sources") if isinstance(found, dict) else None
        for source, share in (sources or {}).items():
            if source == self.source or not isinstance(share, dict):
                continue
            labels.update(x for x in share.get("labels", ()) if isinstance(x, str) and _shared(x))
            for label, values in (share.get("digests") or {}).items():
                if _shared(str(label)):
                    digests.setdefault(str(label), set()).update(
                        v for v in values if isinstance(v, str)
                    )
        return labels, digests


def for_client(root: Path, server: str, session: str) -> Flow:
    """The flow of the client that started this process (its parent), under a protect
    ``root``; files of clients that are gone are removed first."""
    directory = root / "flows"
    if directory.is_dir():
        for path in directory.glob("client-*.json"):
            pid = path.stem.removeprefix("client-")
            if pid.isdigit() and not _alive(int(pid)):
                with contextlib.suppress(OSError):
                    path.unlink()
    return Flow(directory, f"client-{os.getppid()}", f"{server}:{session}")


def seed(
    root: Path,
    source: str,
    *,
    labels: Sequence[str] = (),
    secrets: Sequence[str] = (),
    notes: Sequence[str] = (),
) -> int:
    """Give every session under a protect ``root`` ``labels``, and ``secrets`` as ``secret``
    values (their digests under the root's key), from ``source`` (1.20.0): ``WORKSPACE`` or
    ``CI``. Joined with what that source seeded before. ``notes`` say where they came from,
    in words, and are kept beside them (never a value). How many digests the source holds.

    :raises ValueError: For a source that is not one of them.
    """
    import hashlib
    import hmac

    if source not in (WORKSPACE, CI):
        raise ValueError(f"{source!r} is not a source a seed comes from")
    flow = Flow(root / "flows", "seed", source)
    digests = {
        hmac.new(flow.key, v.encode("utf-8"), hashlib.sha256).hexdigest()[:32] for v in secrets
    }
    path = flow.directory / SEEDS
    with open(flow.directory / "lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            try:
                found = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                found = {}
            sources = found.get("sources") if isinstance(found, dict) else None
            state = {"sources": dict(sources) if isinstance(sources, dict) else {}}
            mine = state["sources"].setdefault(source, {"labels": [], "digests": {}, "notes": []})
            mine["labels"] = sorted({*mine.get("labels", ()), *filter(_shared, labels)})
            kept = sorted({*mine.get("digests", {}).get("secret", ()), *digests})[:MAX_DIGESTS]
            mine["digests"] = {"secret": kept} if kept else {}
            mine["notes"] = list(dict.fromkeys([*mine.get("notes", ()), *notes]))[:256]
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(state, sort_keys=True, indent=1), encoding="utf-8")
            os.chmod(temporary, 0o600)
            temporary.replace(path)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    return len(kept)

"""The SQLite store (§10.2).

One file holds the log, the documents, the intake and the projections.
Triggers refuse any update or deletion of the log, the documents or the
intake, refuse a record that is not the next one at the head of its log, and
refuse a document or record whose bytes do not hash to the digest it is stored
under (F10-2). The file has no roles: its permissions are the operating
system's. A second writer of a log is refused by a lock beside the file
(F10-5).
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from fiduciary import fidu1
from fiduciary.exceptions import StoreError
from fiduciary.store import PROJECTIONS, Store, StoredRecord

__all__ = ["SCHEMA_VERSION", "SqliteStore"]

SCHEMA_VERSION: Final = 2
"""1: 0.1.0. 2: 0.2.0, the lineage events (bound, rebased, suspended, revoked,
expired). Upgrading adds the table, which a version 1 log holds nothing for."""
_PROTECTED: Final = ("documents", "log", "intake")


def _sha256(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def _schema() -> str:
    statements = [
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS documents ("
        " digest TEXT PRIMARY KEY, type TEXT NOT NULL, body BLOB NOT NULL)",
        "CREATE TABLE IF NOT EXISTS log ("
        " log_id TEXT NOT NULL, seq INTEGER NOT NULL, hash TEXT NOT NULL UNIQUE,"
        " prev TEXT NOT NULL, kind TEXT NOT NULL, record BLOB NOT NULL,"
        " PRIMARY KEY (log_id, seq))",
        "CREATE TABLE IF NOT EXISTS intake ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, received_at TEXT NOT NULL, body BLOB NOT NULL)",
    ]
    for table, columns in PROJECTIONS.items():
        statements.append(f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(columns)})")
    for table in _PROTECTED:
        for event in ("UPDATE", "DELETE"):
            statements.append(
                f"CREATE TRIGGER IF NOT EXISTS {table}_no_{event.lower()} BEFORE {event} ON {table}"
                f" BEGIN SELECT RAISE(ABORT, 'fiduciary: {table} is append-only'); END"
            )
    statements.append(
        "CREATE TRIGGER IF NOT EXISTS documents_digest BEFORE INSERT ON documents"
        " WHEN NEW.digest IS NOT fiduciary_sha256(NEW.body)"
        " BEGIN SELECT RAISE(ABORT, 'fiduciary: a document is stored under its digest'); END"
    )
    statements.append(
        "CREATE TRIGGER IF NOT EXISTS log_next BEFORE INSERT ON log WHEN"
        " NEW.seq IS NOT COALESCE((SELECT MAX(seq) FROM log WHERE log_id = NEW.log_id), 0) + 1"
        " OR NEW.prev IS NOT COALESCE((SELECT hash FROM log WHERE log_id = NEW.log_id"
        f" ORDER BY seq DESC LIMIT 1), '{fidu1.GENESIS}')"
        " OR NEW.hash IS NOT fiduciary_sha256(NEW.record)"
        " BEGIN SELECT RAISE(ABORT, 'fiduciary: a record is appended at the head of its log,"
        " linked to it, under its own hash'); END"
    )
    return ";\n".join(statements) + ";"


class _FileLock:
    """An exclusive, non-blocking lock on a file beside the database."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None

    def acquire(self) -> None:
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise StoreError(
                f"{self._path.name}: another process writes this log; one writer at a time"
            ) from exc
        self._fd = fd

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)  # closing the descriptor releases the lock
            self._fd = None


class SqliteStore(Store):
    """A store in one SQLite file, created on first open.

    :param path: The database file.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        try:
            self._conn = sqlite3.connect(self._path, isolation_level=None, check_same_thread=False)
        except sqlite3.Error as exc:
            raise StoreError(f"cannot open {self._path}: {exc}") from exc
        self._conn.create_function("fiduciary_sha256", 1, _sha256, deterministic=True)
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = FULL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._depth = 0
        with self.transaction():
            self._install()

    @property
    def path(self) -> Path:
        return self._path

    def _install(self) -> None:
        for statement in _schema().split(";\n"):
            self._conn.execute(statement.rstrip(";"))
        row = self._conn.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema', ?)", (str(SCHEMA_VERSION),)
            )
        elif int(row[0]) < SCHEMA_VERSION:
            self._conn.execute(
                "UPDATE meta SET value = ? WHERE key = 'schema'", (str(SCHEMA_VERSION),)
            )
        elif int(row[0]) != SCHEMA_VERSION:
            raise StoreError(
                f"{self._path} holds a version {row[0]} store; this release reads version "
                f"{SCHEMA_VERSION}"
            )

    # -- transactions ---------------------------------------------------------

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        if self._depth:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return
        try:
            self._conn.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            raise StoreError(f"cannot begin a write: {exc}") from exc
        self._depth = 1
        try:
            yield
        except BaseException:
            self._depth = 0
            self._conn.execute("ROLLBACK")
            raise
        self._depth = 0
        self._conn.execute("COMMIT")

    def transaction(self) -> AbstractContextManager[None]:
        return self._transaction()

    @contextmanager
    def _claim(self, log: str) -> Iterator[None]:
        lock = _FileLock(self._path.with_name(f"{self._path.name}.{log.replace('/', '_')}.lock"))
        lock.acquire()
        try:
            yield
        finally:
            lock.release()

    def claim(self, log: str) -> AbstractContextManager[None]:
        return self._claim(log)

    def _write(self, sql: str, args: tuple[Any, ...]) -> None:
        try:
            self._conn.execute(sql, args)
        except sqlite3.Error as exc:
            raise StoreError(f"the store refused the write: {exc}") from exc

    # -- documents ------------------------------------------------------------

    def put_document(self, digest: str, kind: str, data: bytes) -> None:
        if self.document(digest) is not None:
            return
        self._write(
            "INSERT INTO documents (digest, type, body) VALUES (?, ?, ?)", (digest, kind, data)
        )

    def document(self, digest: str) -> tuple[str, bytes] | None:
        row = self._conn.execute(
            "SELECT type, body FROM documents WHERE digest = ?", (digest,)
        ).fetchone()
        return None if row is None else (str(row[0]), bytes(row[1]))

    def documents(self) -> Iterator[tuple[str, str, bytes]]:
        for digest, kind, body in self._conn.execute(
            "SELECT digest, type, body FROM documents ORDER BY digest"
        ):
            yield str(digest), str(kind), bytes(body)

    # -- the log --------------------------------------------------------------

    def head(self, log: str) -> tuple[int, str]:
        row = self._conn.execute(
            "SELECT seq, hash FROM log WHERE log_id = ? ORDER BY seq DESC LIMIT 1", (log,)
        ).fetchone()
        return (0, fidu1.GENESIS) if row is None else (int(row[0]), str(row[1]))

    def insert_record(self, record: StoredRecord) -> None:
        self._write(
            "INSERT INTO log (log_id, seq, hash, prev, kind, record) VALUES (?, ?, ?, ?, ?, ?)",
            (record.log, record.seq, record.hash, record.prev, record.kind, record.data),
        )

    def records(self, log: str) -> Iterator[StoredRecord]:
        for row in self._conn.execute(
            "SELECT log_id, seq, hash, prev, kind, record FROM log WHERE log_id = ? ORDER BY seq",
            (log,),
        ):
            yield StoredRecord(
                str(row[0]), int(row[1]), str(row[2]), str(row[3]), str(row[4]), bytes(row[5])
            )

    # -- projections ------------------------------------------------------------

    def insert_row(self, table: str, row: tuple[Any, ...]) -> None:
        columns = PROJECTIONS[table]
        marks = ", ".join("?" for _ in columns)
        self._write(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({marks})", row)

    def rows(self, table: str) -> list[tuple[Any, ...]]:
        columns = PROJECTIONS[table]
        return self.sort_rows(
            [tuple(r) for r in self._conn.execute(f"SELECT {', '.join(columns)} FROM {table}")]
        )

    # -- intake ---------------------------------------------------------------

    def submit(self, data: bytes) -> int:
        with self.transaction():
            cursor = self._conn.execute(
                "INSERT INTO intake (received_at, body) VALUES (?, ?)",
                (fidu1.format_instant(datetime.now(UTC)), data),
            )
        assert cursor.lastrowid is not None
        return int(cursor.lastrowid)

    def intake(self) -> Iterator[tuple[int, bytes]]:
        for row in self._conn.execute("SELECT id, body FROM intake ORDER BY id"):
            yield int(row[0]), bytes(row[1])

    def close(self) -> None:
        self._conn.close()

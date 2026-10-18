"""The stores (``docs/FIDUCIARY_SPEC.md`` §10.2), each test on SQLite and PostgreSQL.

What the store itself refuses (F10-2): an update or deletion of the log, the
documents or the intake; a record that is not the next at its head, or not
stored under its own hash; a document not stored under its digest. And one
writer per log (F10-5).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from fiduciary import fidu1
from fiduciary.documents import Record
from fiduciary.exceptions import StoreError
from fiduciary.log import Log
from fiduciary.sqlite import SqliteStore
from fiduciary.store import PROJECTIONS, Store, StoredRecord, fold
from tests import examples as e
from tests.conftest import Clock


def _record(
    seq: int, prev: str, kind: str = "approval.refused", body: dict[str, object] | None = None
) -> Record:
    return Record.make(
        e.SERVICE,
        log="fiduciary",
        seq=seq,
        kind=kind,
        body=body or {"intake": seq, "digest": "a" * 64, "why": "test"},
        prev=prev,
        issued_at=e.at("2026-10-18T10:00:00"),
    )


def _stored(record: Record) -> StoredRecord:
    return StoredRecord(
        record.log, record.seq, record.hash, record.prev, record.kind, record.canonical()
    )


def test_a_new_store_is_empty(store: Store) -> None:
    assert store.head("fiduciary") == (0, fidu1.GENESIS)
    assert list(store.records("fiduciary")) == []
    assert list(store.documents()) == []
    assert list(store.intake()) == []
    for table in PROJECTIONS:
        assert store.rows(table) == []


def test_records_append_at_the_head_only(store: Store) -> None:
    first = _record(1, fidu1.GENESIS)
    with store.transaction():
        store.insert_record(_stored(first))
    assert store.head("fiduciary") == (1, first.hash)
    second = _record(2, first.hash)
    for wrong in (
        _stored(_record(3, first.hash)),  # skips a sequence
        _stored(_record(2, "f" * 64)),  # links elsewhere
        _stored(
            _record(1, fidu1.GENESIS, body={"intake": 9, "digest": "b" * 64, "why": "x"})
        ),  # replaces
    ):
        with pytest.raises(StoreError), store.transaction():
            store.insert_record(wrong)
    forged = _stored(second)
    with pytest.raises(StoreError), store.transaction():
        store.insert_record(
            StoredRecord(forged.log, forged.seq, "e" * 64, forged.prev, forged.kind, forged.data)
        )
    with store.transaction():
        store.insert_record(_stored(second))
    assert [r.seq for r in store.records("fiduciary")] == [1, 2]
    assert [r.hash for r in store.records("fiduciary")] == [first.hash, second.hash]


def test_two_logs_have_two_heads(store: Store) -> None:
    other = Record.make(
        e.SERVICE,
        log="other",
        seq=1,
        kind="approval.refused",
        body={"intake": 1, "digest": "a" * 64, "why": "x"},
        prev=fidu1.GENESIS,
        issued_at=e.at("2026-10-18T10:00:00"),
    )
    with store.transaction():
        store.insert_record(_stored(_record(1, fidu1.GENESIS)))
        store.insert_record(_stored(other))
    assert store.head("other") == (1, other.hash)
    assert len(list(store.records("fiduciary"))) == 1


def test_a_failed_transaction_writes_nothing(store: Store) -> None:
    first = _record(1, fidu1.GENESIS)
    with pytest.raises(StoreError), store.transaction():
        store.insert_record(_stored(first))
        store.insert_row("refusals", (1, "a" * 64, 1))
        store.insert_record(_stored(_record(5, first.hash)))
    assert store.head("fiduciary") == (0, fidu1.GENESIS)
    assert store.rows("refusals") == []
    with pytest.raises(RuntimeError), store.transaction():
        store.insert_record(_stored(first))
        raise RuntimeError("the caller failed")
    assert store.head("fiduciary") == (0, fidu1.GENESIS)


def test_documents_are_stored_under_their_digest(store: Store) -> None:
    charter = e.charter()
    with store.transaction():
        store.put_document(charter.digest, "charter", charter.canonical())
        store.put_document(charter.digest, "charter", charter.canonical())  # again: nothing changes
    assert store.document(charter.digest) == ("charter", charter.canonical())
    assert store.document("0" * 64) is None
    assert [d for d, _, _ in store.documents()] == [charter.digest]
    with pytest.raises(StoreError), store.transaction():
        store.put_document("0" * 64, "charter", charter.canonical())


def test_the_intake_takes_any_bytes_in_order(store: Store) -> None:
    first = store.submit(b"one")
    second = store.submit(e.approval().canonical())
    assert second > first
    assert [data for _, data in store.intake()] == [b"one", e.approval().canonical()]


def test_nested_transactions_are_one(store: Store) -> None:
    with store.transaction(), store.transaction():
        store.insert_record(_stored(_record(1, fidu1.GENESIS)))
    assert store.head("fiduciary")[0] == 1


def test_one_writer_per_log(store: Store, tmp_path: Path, request: pytest.FixtureRequest) -> None:
    if isinstance(store, SqliteStore):
        second: Store = SqliteStore(store.path)
    else:
        from fiduciary.postgres import PostgresStore

        dsn, schema = request.getfixturevalue("pg_schema")
        second = PostgresStore(dsn, schema=schema)
    try:
        with store.claim("fiduciary"):
            with pytest.raises(StoreError, match="one writer"), second.claim("fiduciary"):
                pass  # pragma: no cover
            with second.claim("another-log"):
                pass
        with second.claim("fiduciary"):
            pass
    finally:
        second.close()


def test_log_append_writes_the_record_its_documents_and_its_rows(
    store: Store, clock: Clock
) -> None:
    log = Log(store, e.SERVICE, clock=clock)
    approval = e.approval()
    record = log.append(
        "approval.recorded",
        {
            "intake": 1,
            "approval": approval.digest,
            "principal": "head-ops",
            "decision": "approve",
            "subject": approval.subject.to_json(),
            "resolution": None,
        },
        documents=[approval],
    )
    assert record.issued_at == clock.now
    assert log.records() == [record]
    assert store.document(approval.digest) == ("approval", approval.canonical())
    assert store.rows("approvals") == fold([record])["approvals"]
    assert log.state().approvals[0].principal == "head-ops"
    assert log.anchor_pending() == 0  # no ledger: nothing to anchor


def test_a_projection_row_sorts_as_the_fold_does(store: Store) -> None:
    with store.transaction():
        store.insert_row("refusals", (10, "b" * 64, 2))
        store.insert_row("refusals", (9, "a" * 64, 1))
    assert store.rows("refusals") == [(9, "a" * 64, 1), (10, "b" * 64, 2)]


def test_sqlite_refuses_an_update_or_a_deletion(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "f.db")
    record = _record(1, fidu1.GENESIS)
    with store.transaction():
        store.insert_record(_stored(record))
        store.put_document(hashlib.sha256(b"x").hexdigest(), "charter", b"x")
    store.submit(b"y")
    import sqlite3

    raw = sqlite3.connect(tmp_path / "f.db")
    for statement in (
        "UPDATE log SET kind = 'x'",
        "DELETE FROM log",
        "UPDATE documents SET type = 'x'",
        "DELETE FROM documents",
        "UPDATE intake SET body = x''",
        "DELETE FROM intake",
    ):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            raw.execute(statement)
    with pytest.raises(sqlite3.OperationalError, match="fiduciary_sha256"):
        raw.execute("INSERT INTO documents VALUES ('z', 'charter', x'00')")
    raw.close()
    store.close()


def test_sqlite_refuses_a_store_of_another_version(tmp_path: Path) -> None:
    SqliteStore(tmp_path / "f.db").close()
    import sqlite3

    raw = sqlite3.connect(tmp_path / "f.db")
    raw.execute("UPDATE meta SET value = '99' WHERE key = 'schema'")
    raw.commit()
    raw.close()
    with pytest.raises(StoreError, match="version 99"):
        SqliteStore(tmp_path / "f.db")


def test_sqlite_reports_a_file_it_cannot_open(tmp_path: Path) -> None:
    with pytest.raises(StoreError, match="cannot open"):
        SqliteStore(tmp_path / "missing-dir" / "f.db")

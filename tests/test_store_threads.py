"""A store's one connection, shared by the service's threads (1.11.0).

The writer holds a transaction open while it writes a record and what follows
from it; the notifier, the console's intake and the bridge's sessions run on
threads of their own. Before 1.11.0 a read on another thread saw the
transaction's rows before they committed (and after they rolled back: a notice
was sent for a record that never stood), and a write on another thread joined
the transaction and went with its rollback. The store's lock now holds a
transaction against every other read and write, on both stores, and the
SQLite store reads for the notifier on a read-only connection of its own.
"""

from __future__ import annotations

import contextlib
import hashlib
import threading
from collections.abc import Callable
from typing import Any

import pytest

from fiduciary import fidu1
from fiduciary.exceptions import StoreError
from fiduciary.sqlite import SqliteStore
from fiduciary.store import Store, StoredRecord

LOG = "threads"


def _record(seq: int = 1, prev: str = fidu1.GENESIS) -> StoredRecord:
    data = fidu1.canonical({"seq": seq})
    return StoredRecord(LOG, seq, hashlib.sha256(data).hexdigest(), prev, "x", data)


class _Open:
    """A transaction held open on a thread of its own, until released; then
    rolled back, or committed."""

    def __init__(self, store: Store, *, commit: bool) -> None:
        self.inside, self.release = threading.Event(), threading.Event()
        self._store, self._commit = store, commit
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        assert self.inside.wait(10)

    def _run(self) -> None:
        with contextlib.suppress(RuntimeError), self._store.transaction():
            self._store.insert_record(_record())
            self.inside.set()
            assert self.release.wait(10)
            if not self._commit:
                raise RuntimeError("rolled back")

    def end(self) -> None:
        self.release.set()
        self.thread.join(10)
        assert not self.thread.is_alive()


def _on_a_thread(call: Callable[[], Any]) -> tuple[threading.Thread, list[Any]]:
    out: list[Any] = []
    thread = threading.Thread(target=lambda: out.append(call()), daemon=True)
    thread.start()
    return thread, out


def test_a_read_on_another_thread_waits_for_the_transaction(store: Store) -> None:
    held = _Open(store, commit=False)
    reader, seen = _on_a_thread(lambda: store.head(LOG))
    reader.join(0.3)
    assert reader.is_alive()  # it waits for the outcome, and never sees the row before it
    held.end()
    reader.join(10)
    assert seen == [(0, fidu1.GENESIS)]
    assert list(store.records(LOG)) == []


def test_a_write_on_another_thread_is_a_transaction_of_its_own(store: Store) -> None:
    """The console's intake, during a writer's transaction that then rolls
    back: before 1.11.0, the submission joined it and was lost with it."""
    held = _Open(store, commit=False)
    writer, ids = _on_a_thread(lambda: store.submit(b"an approval, as submitted"))
    writer.join(0.3)
    assert writer.is_alive()
    held.end()
    writer.join(10)
    assert store.intake_ids() == ids and len(ids) == 1
    assert store.head(LOG) == (0, fidu1.GENESIS)


def test_what_commits_on_another_thread_is_read_once_it_has(store: Store) -> None:
    held = _Open(store, commit=True)
    reader, seen = _on_a_thread(lambda: store.head(LOG))
    held.end()
    reader.join(10)
    assert seen == [(1, _record().hash)]


def test_a_commit_action_on_another_thread_is_not_the_open_transactions(store: Store) -> None:
    held = _Open(store, commit=False)
    ran: list[str] = []
    other, _ = _on_a_thread(lambda: store.after_commit(lambda: ran.append("other")))
    other.join(0.3)
    held.end()
    other.join(10)
    assert ran == ["other"]  # run once the other transaction ended, not dropped with it


def test_the_sqlite_reader_reads_only_what_committed_and_never_waits(store: Store) -> None:
    if not isinstance(store, SqliteStore):
        assert store.reader() is store  # serialized by the store's lock instead
        return
    reader = store.reader()
    try:
        assert reader is not store
        held = _Open(store, commit=True)
        assert reader.head(LOG) == (0, fidu1.GENESIS)  # at once: the row has not committed
        held.end()
        assert reader.head(LOG) == (1, _record().hash)
        with pytest.raises(StoreError, match="read only"), reader.transaction():
            pass
    finally:
        reader.close()


def test_the_notifier_reads_through_a_reader_of_its_own(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The service's notifier part is made over ``store.reader()``, made again
    when the writer's store is, and closed with it."""
    from types import SimpleNamespace

    from fiduciary import notify, service

    made: list[Any] = []

    def for_store(config: Any, read: Store) -> Any:
        made.append(read)
        return SimpleNamespace(turn=lambda: {})

    monkeypatch.setattr(notify, "for_store", for_store)
    writer = SimpleNamespace(
        current=SimpleNamespace(log=SimpleNamespace(store=store)),
        config=SimpleNamespace(notify=object()),
    )
    part = service.NotifierService(writer, every=1.0)  # type: ignore[arg-type]
    part.step()
    part.step()
    assert len(made) == 1
    if isinstance(store, SqliteStore):
        assert made[0] is not store and made[0]._read_only
    part.close()
    if isinstance(store, SqliteStore):
        with pytest.raises(Exception, match="closed"):
            made[0].head(LOG)
    assert store.head(LOG) == (0, fidu1.GENESIS)  # the writer's own store stays open

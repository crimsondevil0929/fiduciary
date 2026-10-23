"""The FIDU1 log over a store, and its anchors in AgentGov (§4.7).

:meth:`Log.append` signs the next record with the service key, and writes it,
the documents it names and the projection rows it implies in one store
transaction. It then anchors the record into the AgentGov ledger as a
zero-value ``ANCHOR`` entry on the scope ``fiduciary``, memo
``FIDU1 <log> <seq> <hash>`` (F4-26). When the ledger cannot be written, the
record stands and :meth:`Log.anchor_pending` anchors it the next time the log
is opened, as Interlock's operator log does.

:func:`check_anchors` holds a log to every anchor the ledger has of it: a log
truncated or rewritten after its records were anchored disagrees with the
ledger even though it verifies on its own.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from agentgov.core import EntryType, LedgerEntry
from agentgov.receipts.signing import Signer

from fiduciary import fidu1
from fiduciary.documents import Document, Record, anchor_memo
from fiduciary.documents.record import ANCHOR_PREFIX, Linked
from fiduciary.exceptions import AnchorError
from fiduciary.store import State, Store, StoredRecord, rows_for

if TYPE_CHECKING:
    from agentgov import BudgetManager

__all__ = ["ANCHOR_SCOPE", "LOG_ID", "Log", "check_anchors", "ensure_anchor_scope", "parse_memo"]

logger = logging.getLogger("fiduciary.log")

LOG_ID: Final = "fiduciary"
ANCHOR_SCOPE: Final = "fiduciary"
_ANCHOR_ENVELOPE: Final = Decimal("0.00000001")


def ensure_anchor_scope(governor: BudgetManager, scope: str = ANCHOR_SCOPE) -> bool:
    """Open the root scope the log's anchors are written to, if the ledger
    lacks it. AgentGov registers a root only with an envelope, so it gets the
    ledger's smallest amount, which nothing spends. Returns whether it opened one."""
    if scope in governor.scopes():
        return False
    governor.open_root(scope, _ANCHOR_ENVELOPE, memo="FIDU1 anchors: this scope spends nothing")
    return True


def parse_memo(memo: str) -> tuple[str, int, str] | None:
    """``(log, seq, hash)`` from a ``FIDU1 <log> <seq> <hash>`` memo, or ``None``."""
    parts = memo.split(" ")
    if len(parts) != 4 or f"{parts[0]} " != ANCHOR_PREFIX:
        return None
    log, seq, digest = parts[1], parts[2], parts[3]
    if not seq.isdigit() or not fidu1.HASH.fullmatch(digest):
        return None
    return log, int(seq), digest


def check_anchors(
    records: Sequence[Linked], entries: Iterable[LedgerEntry], log: str = LOG_ID
) -> tuple[list[str], int, list[int]]:
    """Hold a log to the ledger's anchors of it.

    :returns: The problems (an anchor naming a record the log does not hold, or
        holds with another hash), how many anchors agreed, and the sequences of
        the records no anchor names.
    """
    by_seq = {record.seq: record for record in records}
    problems: list[str] = []
    anchored: set[int] = set()
    for entry in entries:
        if entry.entry_type is not EntryType.ANCHOR:
            continue
        found = parse_memo(entry.memo)
        if found is None or found[0] != log:
            continue
        _, seq, digest = found
        record = by_seq.get(seq)
        if record is None:
            problems.append(
                f"the ledger anchors record {seq} of log {log!r} (entry {entry.sequence}), "
                f"which the log does not hold: it was truncated"
            )
        elif record.hash != digest:
            problems.append(
                f"the ledger anchors record {seq} of log {log!r} as {digest[:16]}, but the log "
                f"holds {record.hash[:16]}: it was rewritten"
            )
        else:
            anchored.add(seq)
    unanchored = sorted(set(by_seq) - anchored)
    return problems, len(anchored), unanchored


class Log:
    """The FIDU1 log a service writes (one writer at a time, F10-5).

    :param store: Where the log lives.
    :param signer: The service key. Ed25519, local or remote.
    :param ledger: An AgentGov governor to anchor every record into, or none.
    :param clock: The time source; a test supplies its own.
    """

    def __init__(
        self,
        store: Store,
        signer: Signer,
        *,
        log_id: str = LOG_ID,
        ledger: BudgetManager | None = None,
        scope: str = ANCHOR_SCOPE,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._signer = signer
        self._log_id = log_id
        self._ledger = ledger
        self._scope = scope
        self._clock = clock
        self._state = State()
        """The state as of the last record read: a record never changes, so
        only those appended since are read, and folded into a copy."""
        self._reading = threading.Lock()

    @property
    def store(self) -> Store:
        return self._store

    @property
    def log_id(self) -> str:
        return self._log_id

    @property
    def signer(self) -> Signer:
        return self._signer

    def now(self) -> datetime:
        return self._clock()

    def records(self, *, after: int = 0) -> list[Record]:
        """The log's records past sequence ``after``, each read strictly from
        the store."""
        return [
            Record.from_json(fidu1.parse(row.data))
            for row in self._store.records(self._log_id, after=after)
        ]

    def state(self) -> State:
        """The state as of the log's last record. The state read before is
        kept, and only the records appended since are read and folded, into
        a copy: a state once returned never changes. The record the kept
        state ends at is read again with them, and must be the one it ends
        at, and the first new one must follow it, by sequence and hash; a
        log that is not so (rewritten or cut around the service) is read
        again whole."""
        with self._reading:
            kept = self._state
            rows = list(self._store.records(self._log_id, after=max(kept.head_seq - 1, 0)))
            if kept.head_seq:
                last = (kept.head_seq, kept.head_hash)
                follows = bool(rows) and (rows[0].seq, rows[0].hash) == last
                rows = rows[1:]
                if follows and rows:
                    follows = (rows[0].seq, rows[0].prev) == (last[0] + 1, last[1])
                if not follows:
                    kept, rows = State(), list(self._store.records(self._log_id))
            if not rows:
                return kept
            state = kept.copy()
            for row in rows:
                state.apply(Record.from_json(fidu1.parse(row.data)))
            self._state = state
            return state

    def put(self, document: Document) -> str:
        """Store a document; return its digest."""
        self._store.put_document(document.digest, document.TYPE, document.canonical())
        return document.digest

    def append(
        self,
        kind: str,
        body: dict[str, Any],
        *,
        documents: Sequence[Document] = (),
        issued_at: datetime | None = None,
    ) -> Record:
        """Sign and write the next record, the documents it names and its
        projection rows, together; then anchor it."""
        (record,) = self.append_all([(kind, body)], documents=documents, issued_at=issued_at)
        return record

    def append_all(
        self,
        entries: Sequence[tuple[str, dict[str, Any]]],
        *,
        documents: Sequence[Document] = (),
        issued_at: datetime | None = None,
    ) -> list[Record]:
        """Write several records in one transaction, in order: all of them, or
        none. A parent's amendment and what it does to its descendants are
        written so (F5-7). They are anchored once the outermost transaction
        commits, so the ledger never anchors a record the log rolled back."""
        written: list[Record] = []
        when = issued_at or self._clock()
        with self._store.transaction():
            seq, head = self._store.head(self._log_id)
            for document in documents:
                self.put(document)
            for kind, body in entries:
                record = Record.make(
                    self._signer,
                    log=self._log_id,
                    seq=seq + 1,
                    kind=kind,
                    body=body,
                    prev=head,
                    issued_at=when,
                )
                self._store.insert_record(
                    StoredRecord(
                        self._log_id, record.seq, record.hash, record.prev, kind, record.canonical()
                    )
                )
                for table, row in rows_for(record):
                    self._store.insert_row(table, row)
                written.append(record)
                seq, head = record.seq, record.hash
            self._store.after_commit(lambda: self._anchor_all(written))
        return written

    def _anchor_all(self, records: Sequence[Record]) -> None:
        for record in records:
            self._anchor(record)

    # -- anchors ---------------------------------------------------------------

    def _anchor(self, record: Linked) -> bool:
        if self._ledger is None:
            return False
        try:
            self._ledger.anchor(self._scope, anchor_memo(record))
        except Exception as exc:  # the record stands; anchored on the next open
            logger.warning(
                "record %d could not be anchored (%s: %s); it will be on the next open",
                record.seq,
                type(exc).__name__,
                exc,
            )
            return False
        return True

    def anchor_pending(self) -> int:
        """Anchor every record the ledger does not hold an anchor of. Returns how many."""
        if self._ledger is None:
            return 0
        self._ledger.refresh()
        rows = list(self._store.records(self._log_id))
        problems, _, unanchored = check_anchors(
            rows, self._ledger.audit_trail(self._scope), self._log_id
        )
        if problems:
            raise AnchorError("; ".join(problems))
        by_seq = {row.seq: row for row in rows}
        return sum(1 for seq in unanchored if self._anchor(by_seq[seq]))

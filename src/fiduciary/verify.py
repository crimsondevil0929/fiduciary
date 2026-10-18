"""``fiduciary verify``: a store held to its log, its log to its keys and its ledger.

:func:`verify_store` reads one store and reports every way it does not hold
(F10-1, F10-2, F4-26). It finds:

- a record that is not strictly a FIDU1 record, whose bytes do not hash to the
  hash it is stored under, that breaks the sequence or the links, that names
  another log, or whose signature does not verify under the service key: the
  log was edited, reordered or cut;
- a document whose bytes do not hash to its digest or do not parse as its
  type, and a record naming a document the store does not hold;
- a projection that differs from the fold of the log: it was edited around
  the service;
- a decision the log records that its rules, taken again from the state before
  it, would not have written (:func:`fiduciary.authority.replay`);
- with the ledger's entries: an anchor naming a record the log does not hold,
  or holds with another hash (the log was truncated or rewritten), and every
  record no anchor names.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass

from agentgov.core import LedgerEntry
from agentgov.receipts.signing import Verifier

from fiduciary import documents, fidu1
from fiduciary.authority import replay
from fiduciary.documents import Record
from fiduciary.exceptions import FiduciaryError
from fiduciary.log import LOG_ID, check_anchors
from fiduciary.store import PROJECTIONS, Store, fold

__all__ = ["Report", "verify_store"]

_NAMED = {
    "charter.adopted": ("charter", "charter"),
    "mandate.issued": ("mandate", "mandate"),
    "approval.recorded": ("approval", "approval"),
}


@dataclass(frozen=True, slots=True)
class Report:
    """What :func:`verify_store` found."""

    problems: tuple[str, ...]
    records: int
    """Records that verified."""
    documents: int
    anchored: int
    """Records an anchor in the ledger agrees with."""
    unanchored: tuple[int, ...]
    """Records no anchor names, when the ledger was given."""

    @property
    def ok(self) -> bool:
        return not self.problems


def verify_store(
    store: Store,
    *,
    service_key: Verifier,
    pinned: str,
    log_id: str = LOG_ID,
    ledger: Iterable[LedgerEntry] | None = None,
    require_anchors: bool = True,
) -> Report:
    """Verify everything a store holds (module docstring).

    :param service_key: The key the log's records are signed with.
    :param pinned: Charter version 1's digest, as configuration pins it.
    :param ledger: The AgentGov ledger's entries, when the log is anchored.
    :param require_anchors: Count a record no anchor names as a problem.
    """
    problems: list[str] = []
    records: list[Record] = []
    previous = fidu1.GENESIS
    expected = 1
    for row in store.records(log_id):
        where = f"record {row.seq}"
        if hashlib.sha256(row.data).hexdigest() != row.hash:
            problems.append(f"{where}: its bytes do not hash to the hash it is stored under")
        try:
            record = Record.loads(row.data)
        except FiduciaryError as exc:
            problems.append(f"{where}: not a FIDU1 record: {exc}")
            break
        try:
            record.verify(service_key)
        except FiduciaryError as exc:
            problems.append(f"{where}: {exc}")
        if (record.log, record.seq, record.kind, record.hash) != (row.log, row.seq, row.kind, row.hash):
            problems.append(f"{where}: its columns disagree with the record they hold")
        if record.log != log_id:
            problems.append(f"{where}: names log {record.log!r}, not {log_id!r}")
        if record.seq != expected:
            problems.append(f"{where}: expected record {expected}: the log lost or reordered records")
        if record.prev != previous:
            problems.append(f"{where}: does not link to the record before it")
        previous, expected = record.hash, record.seq + 1
        records.append(record)
    count = 0
    for digest, kind, data in store.documents():
        count += 1
        if hashlib.sha256(data).hexdigest() != digest:
            problems.append(f"document {digest[:16]}: its bytes do not hash to its digest")
            continue
        try:
            found = documents.loads(data, signed=True)
        except FiduciaryError as exc:
            problems.append(f"document {digest[:16]}: not a FIDU1 {kind}: {exc}")
            continue
        if found.TYPE != kind:
            problems.append(f"document {digest[:16]}: is a {found.TYPE}, stored as a {kind}")
    for record in records:
        named = _NAMED.get(record.kind)
        if named is not None:
            field, kind = named
            held = store.document(str(record.content.get(field)))
            if held is None or held[0] != kind:
                problems.append(f"record {record.seq}: names a {kind} the store does not hold")
    implied = fold(records)
    for table in PROJECTIONS:
        if store.rows(table) != implied[table]:
            problems.append(
                f"projection {table}: differs from what the log implies: it was edited around "
                f"the service"
            )
    problems.extend(replay(records, store, pinned=pinned, service_key=service_key))
    anchored, unanchored = 0, ()
    if ledger is not None:
        found_problems, anchored, missing = check_anchors(records, ledger, log_id)
        problems.extend(found_problems)
        unanchored = tuple(missing)
        if require_anchors:
            problems.extend(f"record {seq}: no anchor in the ledger names it" for seq in missing)
    return Report(tuple(problems), len(records), count, anchored, unanchored)

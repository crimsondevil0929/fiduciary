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
- with the ledger's entries and a skew: every instant a record says that
  disagrees with the ledger order of its anchor by more than the skew (V12,
  §9.4);
- with the ledger's entries: an anchor naming a record the log does not hold,
  or holds with another hash (the log was truncated or rewritten), every
  record no anchor names, and a ticket whose ``observed.ledger`` is no entry of
  the ledger, or one written after the ticket's own anchor. (Its
  ``observed.log`` is the replay's to check: the head its record follows.)
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta

from agentgov.core import EntryType, LedgerEntry
from agentgov.receipts.signing import Verifier

from fiduciary import documents, fidu1
from fiduciary.authority import replay
from fiduciary.documents import Record
from fiduciary.exceptions import FiduciaryError
from fiduciary.log import LOG_ID, check_anchors, parse_memo
from fiduciary.store import PROJECTIONS, Store, fold

__all__ = ["Report", "ordering_problems", "verify_store"]

_NAMED = {
    "charter.adopted": ("charter", "charter"),
    "mandate.issued": ("mandate", "mandate"),
    "approval.recorded": ("approval", "approval"),
    "ticket.raised": ("ticket", "ticket"),
    "warrant.formed": ("warrant", "warrant"),
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
    skew_seconds: int | None = None,
) -> Report:
    """Verify everything a store holds (module docstring).

    :param service_key: The key the log's records are signed with.
    :param pinned: Charter version 1's digest, as configuration pins it.
    :param ledger: The AgentGov ledger's entries, when the log is anchored.
    :param require_anchors: Count a record no anchor names as a problem.
    :param skew_seconds: With the ledger, hold every record's instant to the
        ledger order of its anchor, within this many seconds (§9.4).
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
        if (record.log, record.seq, record.kind, record.hash) != (
            row.log,
            row.seq,
            row.kind,
            row.hash,
        ):
            problems.append(f"{where}: its columns disagree with the record they hold")
        if record.log != log_id:
            problems.append(f"{where}: names log {record.log!r}, not {log_id!r}")
        if record.seq != expected:
            problems.append(
                f"{where}: expected record {expected}: the log lost or reordered records"
            )
        if record.prev != previous:
            problems.append(f"{where}: does not link to the record before it")
        previous, expected = record.hash, record.seq + 1
        records.append(record)
    count = 0
    stored: dict[str, tuple[str, bytes]] = {}
    for digest, kind, data in store.documents():
        count += 1
        stored[digest] = (kind, data)
        if hashlib.sha256(data).hexdigest() != digest:
            problems.append(f"document {digest[:16]}: its bytes do not hash to its digest")
            continue
        if kind == "resolution":
            try:
                raw_res = fidu1.parse(data)
                res = documents.Resolution.read(raw_res)
                if fidu1.canonical(res.to_json()) != data:
                    problems.append(f"document {digest[:16]}: not canonical FIDU1 resolution")
            except FiduciaryError as exc:
                problems.append(f"document {digest[:16]}: not a FIDU1 resolution: {exc}")
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
            held = stored.get(str(record.content.get(field)))
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
    anchored = 0
    unanchored: tuple[int, ...] = ()
    if ledger is not None:
        entries = list(ledger)
        found_problems, anchored, missing = check_anchors(records, entries, log_id)
        problems.extend(found_problems)
        problems.extend(_observed_problems(stored, records, entries, log_id))
        if skew_seconds is not None:
            anchors: dict[int, LedgerEntry] = {}
            for entry in entries:
                memo = parse_memo(entry.memo) if entry.entry_type is EntryType.ANCHOR else None
                if memo is not None and memo[0] == log_id:
                    anchors.setdefault(memo[1], entry)
            problems.extend(ordering_problems(records, anchors, timedelta(seconds=skew_seconds)))
        unanchored = tuple(missing)
        if require_anchors:
            problems.extend(f"record {seq}: no anchor in the ledger names it" for seq in missing)
    return Report(tuple(problems), len(records), count, anchored, unanchored)


def ordering_problems(
    records: Sequence[Record], anchors: Mapping[int, LedgerEntry], skew: timedelta
) -> list[str]:
    """V12 (§9.4): each record's instant is no later than its anchor's ledger
    time, nor earlier than the record before it, by more than ``skew``; and
    anchors follow the log's order. An anchor proves a lower bound only: the
    ledger time of the next anchor is the record's upper bound."""
    problems = []
    previous: Record | None = None
    last = 0
    for record in records:
        anchor = anchors.get(record.seq)
        if anchor is not None:
            if record.issued_at > anchor.timestamp + skew:
                problems.append(
                    f"record {record.seq} says {fidu1.format_instant(record.issued_at)}, after its "
                    f"anchor's ledger time {fidu1.format_instant(anchor.timestamp)}"
                )
            if anchor.sequence < last:
                problems.append(f"record {record.seq} is anchored before its predecessor")
            last = anchor.sequence
        if previous is not None and record.issued_at < previous.issued_at - skew:
            problems.append(
                f"record {record.seq} says {fidu1.format_instant(record.issued_at)}, before "
                f"record {previous.seq}"
            )
        previous = record
    return problems


def _observed_problems(
    stored: Mapping[str, tuple[str, bytes]],
    records: Iterable[Record],
    entries: list[LedgerEntry],
    log_id: str,
) -> list[str]:
    """Each ticket's ``observed.ledger`` held to the ledger: the entry it names
    has the hash it names, and precedes the anchor of the ticket's record."""
    by_seq = {entry.sequence: entry for entry in entries}
    anchors: dict[int, int] = {}
    for entry in entries:
        found = parse_memo(entry.memo) if entry.entry_type is EntryType.ANCHOR else None
        if found is not None and found[0] == log_id:
            anchors.setdefault(found[1], entry.sequence)
    problems = []
    for record in records:
        if record.kind != "ticket.raised":
            continue
        held = stored.get(str(record.content.get("ticket")))
        try:
            ticket = documents.loads(held[1]) if held is not None else None
        except FiduciaryError:
            continue  # named above, as a document that does not parse
        if not isinstance(ticket, documents.Ticket):
            continue
        seen = ticket.observed.ledger
        at = by_seq.get(seen.sequence)
        if at is None or at.entry_hash != seen.head:
            problems.append(
                f"record {record.seq}: its ticket observed ledger entry {seen.sequence} as "
                f"{seen.head[:16]}, which the ledger does not hold"
            )
        anchor = anchors.get(record.seq)
        if anchor is not None and anchor <= seen.sequence:
            problems.append(
                f"record {record.seq}: its ticket observed ledger entry {seen.sequence}, which "
                f"is not before the record's own anchor ({anchor})"
            )
    return problems

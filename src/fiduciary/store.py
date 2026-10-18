"""What a store holds, and the projections that follow from the log (§10.2).

A store holds four things. The log's records and the documents they name are
append-only, and the database refuses an update or a deletion of either
(F10-2). The intake holds approvals as principals submitted them, untrusted
(F10-4). The projections are tables that follow from the log alone:
:func:`rows_for` says what each record adds to them, written in the same
transaction as the record, and :func:`fold` recomputes them from the log, so
``fiduciary verify`` names a projection edited around the service (F10-1).

:class:`State` reads the log the same way: the charter in force, the keys
suspended, the approvals recorded, each lineage's versions. Every decision
the service takes, it takes from the state.
"""

from __future__ import annotations

import abc
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any, Final

from fiduciary.documents import Record

__all__ = [
    "PROJECTIONS",
    "ApprovalRow",
    "State",
    "Store",
    "StoredRecord",
    "fold",
    "rows_for",
]

PROJECTIONS: Final[Mapping[str, tuple[str, ...]]] = {
    "charters": ("charter_id", "version", "digest", "seq"),
    "suspensions": ("charter_id", "principal", "key", "seq"),
    "mandates": ("mandate_id", "version", "digest", "issuance", "seq"),
    "approvals": (
        "digest",
        "intake",
        "principal",
        "decision",
        "subject_type",
        "subject_digest",
        "resolution",
        "seq",
    ),
    "refusals": ("intake", "digest", "seq"),
    "lineage": ("mandate_id", "event", "digest", "detail", "seq"),
}
"""Each projection table and its columns, in order. Every column is text or an
integer; ``seq`` is the record a row follows from."""


@dataclass(frozen=True, slots=True)
class StoredRecord:
    """A record as a store holds it: its columns, and its canonical bytes."""

    log: str
    seq: int
    hash: str
    prev: str
    kind: str
    data: bytes


def rows_for(record: Record) -> list[tuple[str, tuple[Any, ...]]]:
    """What ``record`` adds to the projections: ``(table, row)`` pairs."""
    body = record.content
    kind = record.kind
    if kind == "charter.adopted":
        return [("charters", (body["charter_id"], body["version"], body["charter"], record.seq))]
    if kind == "principal.suspended":
        return [("suspensions", (body["charter_id"], body["principal"], body["key"], record.seq))]
    if kind == "mandate.issued":
        return [
            (
                "mandates",
                (
                    body["mandate_id"],
                    body["version"],
                    body["mandate"],
                    body["issuance"],
                    record.seq,
                ),
            )
        ]
    if kind == "approval.recorded":
        subject = body["subject"]
        return [
            (
                "approvals",
                (
                    body["approval"],
                    body["intake"],
                    body["principal"],
                    body["decision"],
                    subject["type"],
                    subject["digest"],
                    body["resolution"],
                    record.seq,
                ),
            )
        ]
    if kind == "approval.refused":
        return [("refusals", (body["intake"], body["digest"], record.seq))]
    event = _LINEAGE_EVENTS.get(kind)
    if event is not None:
        detail = str(body.get(_DETAIL[event]) or "")
        return [("lineage", (body["mandate_id"], event, body["mandate"], detail, record.seq))]
    return []


_LINEAGE_EVENTS: Final = {
    "mandate.bound": "bound",
    "mandate.rebased": "rebased",
    "mandate.suspended": "suspended",
    "mandate.revoked": "revoked",
    "mandate.expired": "expired",
}
_DETAIL: Final = {
    "bound": "compiled",
    "rebased": "parent",
    "suspended": "cause",
    "revoked": "cascade",
    "expired": "cascade",
}
TERMINAL: Final = frozenset({"suspended", "revoked", "expired"})
"""Lineage events after which a lineage governs nothing more."""


def fold(records: Sequence[Record]) -> dict[str, list[tuple[Any, ...]]]:
    """Every projection row the log implies, by table, sorted."""
    tables: dict[str, list[tuple[Any, ...]]] = {name: [] for name in PROJECTIONS}
    for record in records:
        for table, row in rows_for(record):
            tables[table].append(row)
    return {name: sorted(rows, key=_order) for name, rows in tables.items()}


def _order(row: tuple[Any, ...]) -> tuple[str, ...]:
    return tuple(
        "" if value is None else f"{value:020d}" if isinstance(value, int) else str(value)
        for value in row
    )


# --------------------------------------------------------------------------
# the state
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ApprovalRow:
    digest: str
    intake: int
    principal: str
    decision: str
    subject_type: str
    subject_digest: str
    resolution: str | None
    seq: int


@dataclass(slots=True)
class State:
    """What the log says, as of its last record."""

    charters: list[tuple[int, str, int]] = field(default_factory=list)
    """Each adopted charter version: ``(version, digest, seq)``, in order."""
    suspended: dict[tuple[str, str], int] = field(default_factory=dict)
    """``(principal, key)`` to the record that suspended it."""
    approvals: list[ApprovalRow] = field(default_factory=list)
    lineages: dict[str, list[tuple[int, str, str, int]]] = field(default_factory=dict)
    """Each lineage's issued versions: ``(version, digest, issuance, seq)``."""
    processed: set[int] = field(default_factory=set)
    """Intake ids a record answers, recorded or refused."""
    issued: dict[str, str] = field(default_factory=dict)
    """Mandate digest to its ``mandate_id``, for every issued version."""
    parents: dict[str, str] = field(default_factory=dict)
    """Each lineage's parent version in force: the digest its first version
    named, or the one it was last rebased onto."""
    events: dict[str, list[tuple[str, str, str, int]]] = field(default_factory=dict)
    """Each lineage's events: ``(event, mandate digest, detail, seq)``, in order."""
    bound: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Each bound lineage's last ``mandate.bound`` body."""
    head_seq: int = 0

    @classmethod
    def of(cls, records: Sequence[Record]) -> State:
        state = cls()
        for record in records:
            state.apply(record)
        return state

    def apply(self, record: Record) -> None:
        self.head_seq = record.seq
        body = record.content
        for table, row in rows_for(record):
            if table == "charters":
                self.charters.append((row[1], row[2], row[3]))
            elif table == "suspensions":
                self.suspended[(row[1], row[2])] = row[3]
            elif table == "mandates":
                self.lineages.setdefault(row[0], []).append((row[1], row[2], row[3], row[4]))
                self.issued[row[2]] = row[0]
                if body.get("parent"):
                    self.parents[row[0]] = str(body["parent"])
            elif table == "lineage":
                self.events.setdefault(row[0], []).append((row[1], row[2], row[3], row[4]))
                if row[1] == "rebased":
                    self.parents[row[0]] = row[3]
                elif row[1] == "bound":
                    self.bound[row[0]] = dict(body)
            elif table == "approvals":
                self.approvals.append(ApprovalRow(*row))
                self.processed.add(int(body["intake"]))
            elif table == "refusals":
                self.processed.add(int(body["intake"]))

    @property
    def charter(self) -> tuple[int, str] | None:
        """The charter in force: ``(version, digest)``, or ``None`` before one is adopted."""
        if not self.charters:
            return None
        version, digest, _ = self.charters[-1]
        return version, digest

    def head(self, mandate_id: str) -> tuple[int, str] | None:
        """A lineage's latest version: ``(version, digest)``."""
        versions = self.lineages.get(mandate_id)
        if not versions:
            return None
        version, digest, _, _ = versions[-1]
        return version, digest

    def status(self, mandate_id: str) -> str | None:
        """``issued``, ``bound``, or the terminal event (``suspended``,
        ``revoked``, ``expired``); ``None`` for no lineage."""
        if mandate_id not in self.lineages:
            return None
        events = [e[0] for e in self.events.get(mandate_id, ())]
        terminal = [e for e in events if e in TERMINAL]
        if terminal:
            return terminal[0]
        return "bound" if "bound" in events else "issued"

    def in_force(self, mandate_id: str) -> bool:
        """Issued, and neither suspended, revoked nor expired."""
        return self.status(mandate_id) in ("issued", "bound")

    def parent_lineage(self, mandate_id: str) -> str | None:
        """The ``mandate_id`` of a lineage's parent, or ``None`` for a root."""
        parent = self.parents.get(mandate_id)
        return None if parent is None else self.issued.get(parent)

    def children(self, mandate_id: str) -> list[str]:
        """The lineages derived or issued below ``mandate_id``, in issue order."""
        found = [
            (versions[0][3], child)
            for child, versions in self.lineages.items()
            if self.parent_lineage(child) == mandate_id
        ]
        return [child for _, child in sorted(found)]

    def descendants(self, mandate_id: str) -> list[str]:
        """Every lineage below ``mandate_id``, depth first."""
        found: list[str] = []
        for child in self.children(mandate_id):
            found.append(child)
            found.extend(self.descendants(child))
        return found

    def decisions(self, subject_type: str, subject_digest: str) -> dict[str, ApprovalRow]:
        """Each principal's latest recorded decision on a subject (F7-3, rule 6)."""
        latest: dict[str, ApprovalRow] = {}
        for row in self.approvals:
            if (row.subject_type, row.subject_digest) == (subject_type, subject_digest):
                latest[row.principal] = row
        return latest


# --------------------------------------------------------------------------
# a store
# --------------------------------------------------------------------------


class Store(abc.ABC):
    """A store: the log, the documents it names, the intake and the projections.

    Every write the service makes happens inside :meth:`transaction`, so a
    record and the projection rows it implies commit together or not at all.
    """

    @abc.abstractmethod
    def transaction(self) -> AbstractContextManager[None]:
        """One write transaction, serialized against every other writer."""

    @abc.abstractmethod
    def claim(self, log: str) -> AbstractContextManager[None]:
        """Hold the log's single writer for the duration (F10-5).

        :raises StoreError: If another process holds it.
        """

    @abc.abstractmethod
    def put_document(self, digest: str, kind: str, data: bytes) -> None:
        """Store a document's canonical bytes under its digest. Storing it again
        changes nothing."""

    @abc.abstractmethod
    def document(self, digest: str) -> tuple[str, bytes] | None:
        """``(type, canonical bytes)``, or ``None``."""

    @abc.abstractmethod
    def documents(self) -> Iterator[tuple[str, str, bytes]]:
        """Every document: ``(digest, type, bytes)``."""

    @abc.abstractmethod
    def head(self, log: str) -> tuple[int, str]:
        """The log's last ``(seq, hash)``; ``(0, GENESIS)`` when empty."""

    @abc.abstractmethod
    def insert_record(self, record: StoredRecord) -> None:
        """Append a record. The store refuses one that is not the next."""

    @abc.abstractmethod
    def records(self, log: str) -> Iterator[StoredRecord]:
        """The log's records, in order."""

    @abc.abstractmethod
    def insert_row(self, table: str, row: tuple[Any, ...]) -> None:
        """Add a projection row."""

    @abc.abstractmethod
    def rows(self, table: str) -> list[tuple[Any, ...]]:
        """A projection's rows, sorted as :func:`fold` sorts them."""

    @abc.abstractmethod
    def submit(self, data: bytes) -> int:
        """Put an approval, as submitted, into the intake. Returns its id."""

    @abc.abstractmethod
    def intake(self) -> Iterator[tuple[int, bytes]]:
        """Every submission: ``(id, bytes)``, oldest first."""

    @abc.abstractmethod
    def close(self) -> None: ...

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @staticmethod
    def sort_rows(rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
        return sorted(rows, key=_order)

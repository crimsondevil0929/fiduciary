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
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final, TypeVar

from fiduciary import fidu1
from fiduciary.documents import Record

__all__ = [
    "PROJECTIONS",
    "TICKET_TERMINAL",
    "ApprovalRow",
    "State",
    "Store",
    "StoredRecord",
    "TicketRow",
    "WarrantRow",
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
    "tickets": ("ticket_id", "digest", "mandate_id", "class", "event", "detail", "seq"),
    "warrants": (
        "warrant_id",
        "digest",
        "ticket_id",
        "ticket_digest",
        "action",
        "resolution_digest",
        "basis",
        "seq",
    ),
    "executions": ("warrant", "event", "detail", "seq"),
    "sessions": ("session_id", "mandate_id", "event", "detail", "seq"),
}
"""Each projection table and its columns, in order. Every column is text or an
integer; ``seq`` is the record a row follows from."""

K = TypeVar("K")
T = TypeVar("T")


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
    if kind == "ticket.raised":
        return [
            (
                "tickets",
                (
                    body["ticket_id"],
                    body["ticket"],
                    body["mandate_id"],
                    body["class"],
                    "raised",
                    body["mandate"],
                    record.seq,
                ),
            )
        ]
    if kind == "warrant.formed":
        basis = body["basis"]
        basis_label = f"rule:{basis['rule']['id']}" if "rule" in basis else "quorum"
        return [
            (
                "tickets",
                (
                    body["ticket_id"],
                    body["ticket"],
                    body["mandate_id"],
                    body["class"],
                    "warranted",
                    body["warrant"],
                    record.seq,
                ),
            ),
            (
                "warrants",
                (
                    body["warrant_id"],
                    body["warrant"],
                    body["ticket_id"],
                    body["ticket"],
                    body["action"],
                    body["resolution"],
                    basis_label,
                    record.seq,
                ),
            ),
        ]
    if kind in ("ticket.lapsed", "ticket.declined"):
        ticket_event = kind.split(".")[1]
        return [
            (
                "tickets",
                (
                    body["ticket_id"],
                    body["ticket"],
                    body["mandate_id"],
                    body["class"],
                    ticket_event,
                    body["mandate"],
                    record.seq,
                ),
            )
        ]
    if kind in (
        "execution.intent",
        "execution.applied",
        "execution.refused",
        "execution.abandoned",
    ):
        exec_event = kind.split(".")[1]
        if exec_event == "intent":
            exec_detail = str(len(body.get("steps", ())))
        elif exec_event == "applied":
            exec_detail = "mismatch" if body.get("mismatches") else "ok"
        else:
            exec_detail = str(body.get("why") or "")
        return [
            (
                "executions",
                (body["warrant"], exec_event, exec_detail, record.seq),
            )
        ]
    if kind in ("session.opened", "session.refused", "session.closed"):
        session_event = kind.split(".")[1]
        if session_event == "opened":
            session_detail = str(body["mandate"])
        elif session_event == "refused":
            session_detail = str(body["code"])
        else:
            session_detail = " ".join(str(s) for s in body["scopes"])
        return [
            (
                "sessions",
                (body["session"], body["mandate_id"], session_event, session_detail, record.seq),
            )
        ]
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
TICKET_TERMINAL: Final = frozenset({"warranted", "lapsed", "declined"})
"""Ticket events after which a ticket accepts no new warrant or decline."""


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
    recorded_at: datetime | None = None
    """When the record that recorded it was written: quorum holds an approval
    to its deadlines at this instant (§7.3)."""


@dataclass(frozen=True, slots=True)
class TicketRow:
    ticket_id: str
    digest: str
    mandate_id: str
    mandate_digest: str
    ticket_class: str
    seq: int


@dataclass(frozen=True, slots=True)
class WarrantRow:
    warrant_id: str
    digest: str
    ticket_id: str
    ticket_digest: str
    mandate_id: str
    mandate_digest: str
    ticket_class: str
    action: str
    resolution_digest: str
    basis: dict[str, Any]
    amount: str | None
    seq: int


def _added(table: dict[K, list[T]], key: K, item: T) -> None:
    """Add ``item`` to ``table[key]``, a new list: a list a copy shares is
    never changed in place (:meth:`State.copy`)."""
    table[key] = [*table.get(key, ()), item]


@dataclass(slots=True)
class State:
    """What the log says, as of its last record.

    It is folded from the log one record at a time (:meth:`apply`), and holds
    an index for each question a decision asks: what is open, what is
    unexecuted, who decided what. So a question costs what its answer holds,
    not what the log does. :meth:`copy` is cheap: no list a dictionary here
    holds is changed in place, only replaced, so a copy shares them.
    """

    charters: list[tuple[int, str, int]] = field(default_factory=list)
    """Each adopted charter version: ``(version, digest, seq)``, in order."""
    suspended: dict[tuple[str, str], int] = field(default_factory=dict)
    """``(principal, key)`` to the record that suspended it."""
    approvals: dict[tuple[str, str], list[ApprovalRow]] = field(default_factory=dict)
    """Each subject's recorded approvals, by ``(subject type, digest)``, in order."""
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
    tickets: dict[str, TicketRow] = field(default_factory=dict)
    """Raised tickets keyed by ``ticket_id``."""
    tickets_by_digest: dict[str, str] = field(default_factory=dict)
    """Ticket digest to ``ticket_id``."""
    ticket_events: dict[str, list[tuple[str, str, int]]] = field(default_factory=dict)
    """Each ticket's events: ``(event, detail, seq)``, in order."""
    warrants: dict[str, WarrantRow] = field(default_factory=dict)
    """Formed warrants keyed by warrant digest."""
    warrants_by_id: dict[str, str] = field(default_factory=dict)
    """``warrant_id`` to warrant digest."""
    executions: dict[str, list[tuple[str, dict[str, Any], int]]] = field(default_factory=dict)
    """Each warrant's execution records: ``(event, body, seq)``, in order."""
    sessions: dict[str, list[tuple[str, dict[str, Any], int]]] = field(default_factory=dict)
    """Each bridge session's records: ``(event, body, seq)``, in order."""
    head_seq: int = 0
    head_hash: str | None = None
    """The last record's hash: what a ticket's ``observed.log`` names. ``None``
    before the first record, and after a record :meth:`after` simulates."""
    head_time: datetime | None = None
    """The last record's ``issued_at``."""
    # -- indexes, each kept by apply() ---------------------------------------------
    ticket_ends: dict[str, tuple[int, str]] = field(default_factory=dict)
    """Each ticket's terminal events: the first one's ``seq``, and the last event."""
    open_ticket_ids: dict[str, None] = field(default_factory=dict)
    """Tickets raised with no terminal event."""
    unexecuted: dict[str, None] = field(default_factory=dict)
    """Warrants formed with no execution record."""
    open_intent_ids: dict[str, None] = field(default_factory=dict)
    """Warrants whose one execution record is its intent."""
    warrants_by_action: dict[str, dict[str, None]] = field(default_factory=dict)
    """Each resolution action's warrants, by digest."""

    @classmethod
    def of(cls, records: Sequence[Record]) -> State:
        state = cls()
        for record in records:
            state.apply(record)
        return state

    def copy(self) -> State:
        """A state that :meth:`apply` may advance while this one stays as it is."""
        values: dict[str, Any] = {}
        for item in fields(State):
            value = getattr(self, item.name)
            values[item.name] = value.copy() if isinstance(value, (dict, list, set)) else value
        values["warrants_by_action"] = {a: dict(d) for a, d in self.warrants_by_action.items()}
        return State(**values)

    def after(self, kind: str, body: dict[str, Any], *, issued_at: datetime | None = None) -> State:
        """A copy of this state with a record of ``kind`` applied, written at
        ``issued_at`` (the last record's instant by default): what the next
        decision written in the same log transaction is taken from."""
        found = self.copy()
        found.apply(
            Record(
                log="fiduciary",
                seq=self.head_seq + 1,
                kind=kind,
                issued_at=issued_at or self.head_time or datetime.now(UTC),
                content=body,
                prev=fidu1.GENESIS,
            )
        )
        return found

    def apply(self, record: Record) -> None:
        self.head_seq = record.seq
        self.head_time = record.issued_at
        # a record simulated by after() has no hash yet
        self.head_hash = record.hash if record.signature is not None else None
        body = record.content
        for table, row in rows_for(record):
            if table == "charters":
                self.charters.append((row[1], row[2], row[3]))
            elif table == "suspensions":
                self.suspended[(row[1], row[2])] = row[3]
            elif table == "mandates":
                _added(self.lineages, row[0], (row[1], row[2], row[3], row[4]))
                self.issued[row[2]] = row[0]
                if body.get("parent"):
                    self.parents[row[0]] = str(body["parent"])
            elif table == "lineage":
                _added(self.events, row[0], (row[1], row[2], row[3], row[4]))
                if row[1] == "rebased":
                    self.parents[row[0]] = row[3]
                elif row[1] == "bound":
                    self.bound[row[0]] = dict(body)
            elif table == "approvals":
                digest, intake, principal, decision, kind, subject, resolution, seq = row
                approval = ApprovalRow(
                    digest,
                    intake,
                    principal,
                    decision,
                    kind,
                    subject,
                    resolution,
                    seq,
                    recorded_at=record.issued_at,
                )
                _added(self.approvals, (kind, subject), approval)
                self.processed.add(int(body["intake"]))
            elif table == "refusals":
                self.processed.add(int(body["intake"]))
            elif table == "tickets":
                self._ticket(*row)
            elif table == "warrants":
                self._warrant(row, body)
            elif table == "executions":
                warrant_digest, event, _, seq = row
                if warrant_digest in self.executions:
                    self.open_intent_ids.pop(warrant_digest, None)
                elif event == "intent":
                    self.open_intent_ids[warrant_digest] = None
                self.unexecuted.pop(warrant_digest, None)
                _added(self.executions, warrant_digest, (event, dict(body), seq))
            elif table == "sessions":
                session_id, _, event, _, seq = row
                _added(self.sessions, session_id, (event, dict(body), seq))

    def _ticket(
        self,
        ticket_id: str,
        digest: str,
        mandate_id: str,
        ticket_class: str,
        event: str,
        detail: str,
        seq: int,
    ) -> None:
        if event == "raised":
            self.tickets[ticket_id] = TicketRow(
                ticket_id=ticket_id,
                digest=digest,
                mandate_id=mandate_id,
                mandate_digest=detail,
                ticket_class=ticket_class,
                seq=seq,
            )
            self.tickets_by_digest[digest] = ticket_id
            if ticket_id not in self.ticket_ends:
                self.open_ticket_ids[ticket_id] = None
        else:  # warranted, lapsed or declined: every other event ends a ticket
            first = self.ticket_ends.get(ticket_id, (seq, event))[0]
            self.ticket_ends[ticket_id] = (first, event)
            self.open_ticket_ids.pop(ticket_id, None)
        _added(self.ticket_events, ticket_id, (event, detail, seq))

    def _warrant(self, row: tuple[Any, ...], body: Mapping[str, Any]) -> None:
        warrant_id, digest, ticket_id, ticket_digest, action, res_digest, _, seq = row
        before = self.warrants.get(digest)
        if before is not None and before.action != action:
            self.warrants_by_action[before.action].pop(digest, None)
        self.warrants_by_action.setdefault(action, {})[digest] = None
        self.warrants[digest] = WarrantRow(
            warrant_id=warrant_id,
            digest=digest,
            ticket_id=ticket_id,
            ticket_digest=ticket_digest,
            mandate_id=str(body["mandate_id"]),
            mandate_digest=str(body["mandate"]),
            ticket_class=str(body["class"]),
            action=action,
            resolution_digest=res_digest,
            basis=dict(body["basis"]),
            amount=None if body.get("amount") is None else str(body["amount"]),
            seq=seq,
        )
        self.warrants_by_id[warrant_id] = digest
        if digest not in self.executions:
            self.unexecuted[digest] = None

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
        return {
            row.principal: row for row in self.approvals.get((subject_type, subject_digest), ())
        }

    def ticket_status(self, ticket_id: str) -> str | None:
        """``open``, ``warranted``, ``lapsed`` or ``declined``; ``None`` for no ticket."""
        if ticket_id not in self.tickets:
            return None
        ended = self.ticket_ends.get(ticket_id)
        return "open" if ended is None else ended[1]

    def ticket_terminal_seq(self, ticket_id: str) -> int | None:
        """The first ``seq`` at which ``ticket_id`` became terminal, or ``None``."""
        ended = self.ticket_ends.get(ticket_id)
        return None if ended is None else ended[0]

    def open_tickets(self, mandate_id: str | None = None) -> list[TicketRow]:
        """Open tickets, optionally filtered to ``mandate_id``, in raised order."""
        found = [
            row
            for row in map(self.tickets.__getitem__, self.open_ticket_ids)
            if mandate_id is None or row.mandate_id == mandate_id
        ]
        return sorted(found, key=lambda r: r.seq)

    def ticket_decisions(
        self, ticket_digest: str, *, before_seq: int | None = None
    ) -> dict[str, ApprovalRow]:
        """Each principal's latest decision on ``ticket_digest`` recorded before
        the warrant formed (§7.3 rule 6, F7-6)."""
        ticket_id = self.tickets_by_digest.get(ticket_digest)
        cutoff = before_seq
        if cutoff is None and ticket_id is not None:
            cutoff = self.ticket_terminal_seq(ticket_id)
        return {
            row.principal: row
            for row in self.approvals.get(("ticket", ticket_digest), ())
            if cutoff is None or row.seq < cutoff
        }

    def warrant_of(self, ticket_id: str) -> str | None:
        """The digest of the warrant formed for ``ticket_id``, if one was."""
        return next(
            (
                detail
                for event, detail, _ in self.ticket_events.get(ticket_id, ())
                if event == "warranted"
            ),
            None,
        )

    def execution_status(self, warrant_digest: str) -> str | None:
        """``intent``, ``applied``, ``refused`` or ``abandoned``; ``None`` before intent."""
        events = self.executions.get(warrant_digest)
        if not events:
            return None
        return events[-1][0]

    def unexecuted_warrants(self) -> list[WarrantRow]:
        """Every formed warrant with no execution record yet, oldest first."""
        return sorted(map(self.warrants.__getitem__, self.unexecuted), key=lambda r: r.seq)

    def session_status(self, session_id: str) -> str | None:
        """``open`` or ``closed``; ``None`` for no session."""
        events = self.sessions.get(session_id)
        if not events:
            return None
        return "closed" if any(event == "closed" for event, _, _ in events) else "open"

    def open_intents(self) -> list[tuple[str, dict[str, Any], int]]:
        """Every ``(warrant_digest, intent_body, seq)`` with no outcome yet."""
        found = [(d, *self.executions[d][0][1:]) for d in self.open_intent_ids]
        return sorted(found, key=lambda item: item[2])

    def granted(self, mandate_digest: str, *, rule: bool = False) -> Decimal:
        """What the warrants to grant under ``mandate_digest`` granted: every
        one applied; or, with ``rule``, every one an autonomous rule formed
        and no execution refused or abandoned (F7-11)."""
        total = Decimal("0.00000000")
        for digest in self.warrants_by_action.get("grant", ()):
            warrant = self.warrants[digest]
            if warrant.mandate_digest != mandate_digest or warrant.amount is None:
                continue
            status = self.execution_status(digest)
            if (
                ("rule" in warrant.basis and status not in ("refused", "abandoned"))
                if rule
                else status == "applied"
            ):
                total += Decimal(warrant.amount)
        return total

    def auto_extension_total(self, mandate_digest: str) -> Decimal:
        """Total autonomous extension grant amount recorded for ``mandate_digest`` (F7-11)."""
        return self.granted(mandate_digest, rule=True)


# --------------------------------------------------------------------------
# a store
# --------------------------------------------------------------------------


class Store(abc.ABC):
    """A store: the log, the documents it names, the intake and the projections.

    Every write the service makes happens inside :meth:`transaction`, so a
    record and the projection rows it implies commit together or not at all.
    Transactions nest: only the outermost commits, and what must follow a
    commit (an anchor in AgentGov) waits for it (:meth:`after_commit`).
    """

    def __init__(self) -> None:
        self._depth = 0
        self._committed: list[Callable[[], None]] = []

    @abc.abstractmethod
    def transaction(self) -> AbstractContextManager[None]:
        """One write transaction, serialized against every other writer."""

    def after_commit(self, action: Callable[[], None]) -> None:
        """Run ``action`` once the outermost transaction open now commits, or
        now when none is open. A rollback drops it: a record that was never
        written is never anchored."""
        if self._depth:
            self._committed.append(action)
        else:
            action()

    def _run_committed(self) -> None:
        actions, self._committed = self._committed, []
        for action in actions:
            action()

    def _drop_committed(self) -> None:
        self._committed = []

    @abc.abstractmethod
    def claim(self, log: str) -> AbstractContextManager[None]:
        """Hold the log's single writer for the duration (F10-5).

        :raises StoreError: If another process holds it.
        """

    def holds(self, log: str) -> bool:
        """Whether this process still holds the claim on ``log`` it took. A
        file claim is held for as long as its descriptor is open."""
        return True

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
    def records(self, log: str, *, after: int = 0) -> Iterator[StoredRecord]:
        """The log's records past sequence ``after``, in order."""

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

    def intake_ids(self) -> list[int]:
        """Every submission's id, oldest first: what the intake has, without
        reading what each holds."""
        return [intake for intake, _ in self.intake()]

    def submissions(self, ids: Collection[int]) -> list[tuple[int, bytes]]:
        """The submissions ``ids`` names: ``(id, bytes)``, oldest first."""
        wanted = set(ids)
        return [(intake, data) for intake, data in self.intake() if intake in wanted]

    @abc.abstractmethod
    def close(self) -> None: ...

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @staticmethod
    def sort_rows(rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
        return sorted(rows, key=_order)

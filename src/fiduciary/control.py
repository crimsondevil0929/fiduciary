"""The control plane: a loopback web console over deployments (``fiduciary console``).

It does three things:

- **The approvals desk.** Every ticket of a deployment, from its signed bytes,
  with what was measured (the receipt a refusal cites: the rows, the tables,
  the tenants, the measurement class), the outbound payload a held message
  would send, checked against the hash the ticket signs, the options, the
  quorum the log has recorded, and the warrant once one forms.
- **Votes, signed by the principal.** The console drafts an approval and
  returns the exact bytes a FIDU1 signature covers (at most 96). The principal
  signs them in the browser with their own key file, which never leaves the
  machine, or signs the ticket with ``fiduciary approve``; the console checks
  the signature under the key the charter pins and puts the approval into the
  deployment's intake, as ``fiduciary submit`` does, when it was started with
  ``accept_approvals``. The deployment's own writer (``fiduciary serve``)
  records it, forms the warrant at quorum and releases at the heads it pins.
- **The bundle verifier.** Any ``.fidb`` (``fidb/1``, or sealed ``fidb/2``),
  verified in process, offline, V1 to V13, against a charter digest and keys
  pinned from outside the bundle, with the nine control-framework mappings.

What it never does: hold a principal's key, write a deployment's log, or serve
a file other than the console, bundles and their public pins. A deployment's
SQLite files are read from a copy taken through SQLite's backup API, so a
recorded run on disk is never changed by looking at it.

The HTTP boundary listens on loopback only, answers only requests whose
``Host`` names it (DNS rebinding), takes a write only from its own origin with
the console's header (cross-origin pages cannot send one without a CORS
preflight, which is never granted), and bounds every body.

The demo adds a sandbox, :mod:`fiduciary.demo.sandbox`: deployments built in a
scratch directory, where the console is the writer and holds the demo's keys.
"""

from __future__ import annotations

import contextlib
import hashlib
import http.server
import json
import logging
import shutil
import sqlite3
import tempfile
import threading
import urllib.parse
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from fiduciary import __version__, fidu1, webauthn
from fiduciary.documents import Approval, Charter, Record, Ticket, Warrant, loads
from fiduciary.exceptions import FiduciaryError
from fiduciary.log import LOG_ID
from fiduciary.store import State, Store

__all__ = [
    "RUNGS",
    "ConfiguredDeployment",
    "Console",
    "ConsoleServer",
    "ControlError",
    "Deployment",
    "Pins",
    "serve",
]

_log = logging.getLogger("fiduciary.control")

RUNGS: Final[Mapping[str, str]] = {
    "transactional": "T",
    "plan-bound": "P",
    "predicted": "V",
    "request-only": "R",
}
"""ARC1 ``coverage.measurement`` to its rung of the measurement ladder."""

MAX_JSON: Final = 256 * 1024
MAX_BUNDLE: Final = 64 * 1024 * 1024
APPROVAL_TTL: Final = timedelta(hours=1)
HEADER: Final = "X-Fiduciary-Console"
LOOPBACK: Final = ("127.0.0.1", "localhost", "::1")

_SERVED_NAMES: Final = frozenset({"pinned-keys.json", "charter.digest", "attestation.json"})
_SERVED_SUFFIXES: Final = frozenset({".fidb", ".md"})
_CSP: Final = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)


class ControlError(Exception):
    """A request the console refuses, with the HTTP status it answers."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _bad(message: str) -> ControlError:
    return ControlError(400, message)


# --------------------------------------------------------------------------
# what a deployment holds, read
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Pins:
    """What a bundle of a deployment is verified against: its charter digest,
    and the keys an auditor pinned (``pinned-keys.json``), when known."""

    charter: str
    keys: Mapping[str, Any] | None = None
    witness: str | None = None
    """A ledger witness's key (``ed25519:<hex>``): its cosignature is then required."""


def _read_only(path: Path) -> str:
    """A URI that reads ``path`` and writes nothing beside it. With no
    write-ahead log beside the database, every committed page is in the file,
    so it is read as immutable: SQLite then creates no ``-wal`` or ``-shm``
    either. A live writer's log is there, and read through its own index."""
    live = path.with_name(path.name + "-wal").exists()
    return f"{path.resolve().as_uri()}?{'mode=ro' if live else 'immutable=1'}"


def _sqlite_copy(source: Path, target: Path) -> None:
    """``source`` copied to ``target`` through SQLite's backup API, read-only:
    a consistent snapshot of a database another process may be writing."""
    if not source.is_file():
        raise ControlError(404, f"{source.name} does not exist")
    target.unlink(missing_ok=True)
    with contextlib.closing(sqlite3.connect(_read_only(source), uri=True)) as reader:
        with contextlib.closing(sqlite3.connect(target)) as writer:
            reader.backup(writer)


def _signature(path: Path) -> tuple[int, ...]:
    """What changes when a SQLite database or its write-ahead log does."""
    found: list[int] = []
    for each in (path, path.with_name(path.name + "-wal")):
        try:
            stat = each.stat()
        except OSError:
            found.extend((0, 0))
        else:
            found.extend((stat.st_mtime_ns, stat.st_size))
    return tuple(found)


class _Receipts:
    """A receipt log (``receipts.jsonl``), indexed by receipt id, read again
    when the file changes."""

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._seen: tuple[int, int] | None = None
        self._by_id: dict[str, dict[str, Any]] = {}

    def recent(self, count: int) -> list[dict[str, Any]]:
        """The last ``count`` receipts, newest first."""
        self.get("")
        return list(reversed(list(self._by_id.values())))[:count]

    def get(self, receipt_id: str) -> dict[str, Any] | None:
        if self._path is None or not self._path.is_file():
            return None
        stat = self._path.stat()
        if self._seen != (stat.st_mtime_ns, stat.st_size):
            by_id: dict[str, dict[str, Any]] = {}
            with self._path.open(encoding="utf-8") as lines:
                for line in lines:
                    try:
                        doc = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(doc, dict) and isinstance(doc.get("receipt_id"), str):
                        by_id[doc["receipt_id"]] = doc
            self._by_id, self._seen = by_id, (stat.st_mtime_ns, stat.st_size)
        return self._by_id.get(receipt_id)


def receipt_view(doc: Mapping[str, Any]) -> dict[str, Any]:
    """What a desk shows of an ARC1 receipt: what was measured, on what, how
    strongly, and what became of it. A delivery receipt (ARC1-delivery, 1.9.0)
    is shown as the request it delivered: request-only, on its sink."""
    if isinstance(doc.get("delivery"), Mapping):
        request = doc.get("request") or {}
        delivery = doc["delivery"]
        return {
            "kind": "delivery",
            "receipt_id": doc.get("receipt_id"),
            "issued_at": doc.get("issued_at"),
            "measurement": "request-only",
            "rung": RUNGS.get("request-only", "R"),
            "substrate": request.get("sink"),
            "outcome": "delivered",
            "rows": 0,
            "tables": [],
            "tenants": [],
            "operation": request.get("operation"),
            "remote_ref": delivery.get("remote_ref"),
            "status_code": delivery.get("status_code"),
            "action": (doc.get("action") or {}).get("receipt_id"),
            "gaps": [],
        }
    effect = doc.get("effect") or {}
    coverage = doc.get("coverage") or {}
    outcome = doc.get("outcome") or {}
    decision = doc.get("decision") or {}
    measurement = str(coverage.get("measurement") or "transactional")
    summary = effect.get("summary") or {}
    return {
        "kind": "action",
        "receipt_id": doc.get("receipt_id"),
        "issued_at": doc.get("issued_at"),
        "measurement": measurement,
        "rung": RUNGS.get(measurement, "?"),
        "substrate": effect.get("substrate_id"),
        "admitted": decision.get("admitted"),
        "outcome": outcome.get("status"),
        "substrate_txid": outcome.get("substrate_txid"),
        "rows": effect.get("row_count"),
        "inserted": summary.get("ins", 0),
        "updated": summary.get("upd", 0),
        "deleted": summary.get("del", 0),
        "tables": list(summary.get("tables") or []),
        "tenants": list(summary.get("tenants") or []),
        "other_tenants": summary.get("other_tenants"),
        "truncated": bool(effect.get("truncated")),
        "stated": (doc.get("intent") or {}).get("stated"),
        "checkers": [c.get("name") for c in decision.get("checkers") or () if isinstance(c, dict)],
        "gaps": list(coverage.get("known_gaps") or []),
        "diff_hash": effect.get("diff_hash"),
    }


def _payloads(target: str | None, ids: Sequence[str]) -> dict[str, str]:
    """The outbound payloads of ``ids`` in an Interlock outbox, read-only:
    a SQLite back office, or PostgreSQL's ``interlock.outbox``."""
    if not target or not ids:
        return {}
    from fiduciary.config import is_dsn

    marks = ", ".join("?" for _ in ids)
    try:
        if is_dsn(target):
            import psycopg

            with psycopg.connect(target, autocommit=True) as conn:
                conn.read_only = True
                rows = conn.execute(
                    "SELECT message_id::text, payload FROM interlock.outbox "
                    "WHERE message_id::text = ANY(%s)",
                    (list(ids),),
                ).fetchall()
        else:
            if not Path(target).is_file():
                return {}
            with contextlib.closing(sqlite3.connect(_read_only(Path(target)), uri=True)) as conn:
                query = "SELECT message_id, payload FROM _interlock_outbox WHERE message_id IN "
                rows = conn.execute(query + f"({marks})", tuple(ids)).fetchall()
    except Exception as exc:  # an outbox out of reach is shown, never fatal
        _log.info("outbox %s unreadable: %s", target, exc)
        return {}
    return {str(m): p if isinstance(p, str) else json.dumps(p) for m, p in rows}


def _heads(resolution: Mapping[str, Any]) -> list[str]:
    """The heads a resolution pins: each held message at the delivery-log head
    it was observed at, or each Git ref at its commit."""
    found: list[str] = []
    for message in resolution.get("messages") or ():
        if isinstance(message, Mapping) and "id" in message:
            found.append(f"{message['id']}@{message.get('head', '')}")
    expected = resolution.get("expected_heads")
    if isinstance(expected, Mapping):
        found.extend(f"{k}@{v}" for k, v in sorted(expected.items()))
    return found


class Deployment:
    """One deployment, as the console reads it. A subclass says where its
    store, receipts and outbox are, and whether it takes approvals."""

    kind: str = "deployment"
    signs: bool = False
    """Whether the console signs for this deployment's principals (the demo's
    sandbox only: its keys are the demo's own)."""

    def __init__(self, ident: str, name: str, *, accepts: bool = False) -> None:
        self.ident = ident
        self.name = name
        self.accepts = accepts
        self.lock = threading.RLock()

    # -- what a subclass provides ---------------------------------------------

    def read(self) -> tuple[Store, State]:
        """The store and the state its log folds to now."""
        raise NotImplementedError

    def receipts(self) -> _Receipts:
        return _Receipts(None)

    def outbox(self) -> str | None:
        return None

    def pins(self) -> Pins | None:
        return None

    def holds(self) -> dict[tuple[str, str], bool]:
        """Each sink operation, and whether a request to it waits for a warrant."""
        return {}

    def bindings(self) -> dict[str, Any]:
        """The deployment's tool bindings, by digest."""
        return {}

    def submit(self, approval: Approval) -> dict[str, Any]:
        """Put ``approval`` into the deployment's intake."""
        raise ControlError(403, f"{self.name} is read-only here: it takes no approvals")

    def sign(self, draft: Approval) -> Approval:
        raise ControlError(403, f"the console holds no key of {self.name}'s principals")

    def close(self) -> None:
        return

    def describe(self) -> dict[str, str]:
        """What the console says about where this deployment is."""
        return {}

    # -- what every deployment answers -------------------------------------------

    def charter(self, store: Store, state: State) -> Charter:
        if state.charter is None:
            raise ControlError(409, f"{self.name} has no adopted charter")
        found = store.document(state.charter[1])
        if found is None:
            raise ControlError(409, f"{self.name}'s store lacks its charter document")
        charter = loads(found[1], signed=True)
        assert isinstance(charter, Charter)
        return charter

    registers = False
    """Whether a principal's passkey can be registered here (1.17.0)."""

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.ident,
            "name": self.name,
            "kind": self.kind,
            "accepts": self.accepts,
            "signs": self.signs,
            **self.describe(),
        }
        try:
            with self.lock:
                store, state = self.read()
                charter = self.charter(store, state)
                out["charter"] = {
                    "charter_id": charter.charter_id,
                    "version": state.charter[0] if state.charter else charter.version,
                    "digest": state.charter[1] if state.charter else charter.digest,
                    "quorums": charter.body().get("quorums"),
                }
                out["principals"] = [
                    {
                        "name": p.name,
                        "key": p.key,
                        "key_id": p.public_key.key_id,
                        **({"passkey": p.webauthn.to_json()} if p.webauthn else {}),
                    }
                    for p in charter.principals
                ]
                out["passkeys"] = self.registers
                out["records"] = state.head_seq
                out["tickets"] = len(state.tickets)
                out["open"] = len(state.open_ticket_ids)
        except (ControlError, FiduciaryError, OSError, sqlite3.Error) as exc:
            out["error"] = str(exc)
        return out

    def profile(self) -> dict[str, Any]:
        """What governs the agent: each lineage in force, its budget, quorums
        and windows, every tool binding it pins and how each is governed; and
        the latest receipts."""
        from fiduciary.documents import Mandate, ToolBinding

        with self.lock:
            store, state = self.read()
            holds = self.holds()
            known = self.bindings()
            mandates = []
            for mandate_id in sorted(state.lineages):
                head = state.head(mandate_id)
                if head is None:
                    continue
                found = store.document(head[1])
                mandate = loads(found[1], signed=True) if found is not None else None
                if not isinstance(mandate, Mandate):
                    continue
                body = mandate.body()
                tools = []
                for pin in (body.get("tools") or {}).get("bindings") or ():
                    binding = known.get(str(pin.get("digest")))
                    if binding is None:
                        raw = store.document(str(pin.get("digest")))
                        binding = loads(raw[1], signed=True) if raw is not None else None
                    if not isinstance(binding, ToolBinding):
                        tools.append(
                            {
                                "name": pin.get("name"),
                                "digest": pin.get("digest"),
                                "kind": "missing",
                                "governed": "binding not found here",
                            }
                        )
                        continue
                    tool = binding.body()
                    requests = [
                        {
                            "sink": r["sink"],
                            "operation": r["operation"],
                            "held": holds.get((r["sink"], r["operation"]), False),
                        }
                        for r in tool.get("requests") or ()
                    ]
                    if tool["kind"] == "passthrough":
                        governed = "read · metered"
                    elif any(r["held"] for r in requests):
                        governed = "held · warrant"
                    elif requests and not tool.get("effects"):
                        governed = "write · released"
                    else:
                        governed = "staged · measured"
                    tools.append(
                        {
                            "name": tool["name"],
                            "digest": binding.digest,
                            "kind": tool["kind"],
                            "governed": governed,
                            "description": tool.get("description", ""),
                            "price": tool.get("price"),
                            "effects": len(tool.get("effects") or ()),
                            "requests": requests,
                        }
                    )
                mandates.append(
                    {
                        "mandate_id": mandate_id,
                        "version": head[0],
                        "digest": head[1],
                        "status": state.status(mandate_id),
                        "scope": body["subject"]["scope"],
                        "parent": body.get("parent"),
                        "budget": body.get("budget"),
                        "quorums": (body.get("roster") or {}).get("quorums"),
                        "windows": [
                            {
                                "name": w["name"],
                                "limit": w["limit"],
                                "span_seconds": w["span_seconds"],
                            }
                            for w in body.get("windows") or ()
                        ],
                        "tables": (body.get("data") or {}).get("tables"),
                        "tools": tools,
                    }
                )
            receipts = [receipt_view(r) for r in self.receipts().recent(25)]
        return {"deployment": self.ident, "mandates": mandates, "receipts": receipts}

    def tickets(self) -> list[dict[str, Any]]:
        """Every ticket, newest first, open ones before the rest."""
        with self.lock:
            store, state = self.read()
            receipts = self.receipts()
            rows = sorted(
                state.tickets.values(),
                key=lambda r: (state.ticket_status(r.ticket_id) != "open", -r.seq),
            )
            return [
                self._view(store, state, self._ticket(store, row.digest), receipts, full=False)
                for row in rows
            ]

    def ticket(self, ticket_id: str) -> dict[str, Any]:
        with self.lock:
            store, state = self.read()
            row = state.tickets.get(ticket_id)
            if row is None:
                raise ControlError(404, f"{self.name} has no ticket {ticket_id}")
            ticket = self._ticket(store, row.digest)
            return self._view(store, state, ticket, self.receipts(), full=True)

    @staticmethod
    def _ticket(store: Store, digest: str) -> Ticket:
        found = store.document(digest)
        if found is None:
            raise ControlError(409, f"the store lacks ticket document {digest}")
        ticket = loads(found[1], signed=True)
        assert isinstance(ticket, Ticket)
        return ticket

    def _view(
        self,
        store: Store,
        state: State,
        ticket: Ticket,
        receipts: _Receipts,
        *,
        full: bool,
    ) -> dict[str, Any]:
        document = ticket.to_json()
        evidence = document["evidence"]
        decisions = state.ticket_decisions(ticket.digest)
        options = []
        for option in ticket.options:
            approved = sorted(
                p
                for p, row in decisions.items()
                if row.decision == "approve" and row.resolution == option.digest
            )
            options.append(
                {
                    "action": option.resolution.action,
                    "digest": option.digest,
                    "quorum": option.quorum,
                    "approved_by": approved,
                    "heads": _heads(option.resolution.to_json()),
                    "resolution": option.resolution.to_json(),
                }
            )
        votes = [
            {
                "principal": row.principal,
                "decision": row.decision,
                "resolution": row.resolution,
                "option": next(
                    (o["action"] for o in options if o["digest"] == row.resolution), None
                ),
                "approval": row.digest,
                "seq": row.seq,
                "recorded_at": fidu1.format_instant(row.recorded_at) if row.recorded_at else None,
            }
            for row in sorted(decisions.values(), key=lambda r: r.seq)
        ]
        view: dict[str, Any] = {
            "deployment": self.ident,
            "ticket_id": ticket.ticket_id,
            "digest": ticket.digest,
            "class": ticket.ticket_class,
            "status": state.ticket_status(ticket.ticket_id) or "open",
            "mandate": document["mandate"],
            "raised_at": document["raised_at"],
            "not_after": document["not_after"],
            "options": options,
            "votes": votes,
            "warrant": self._warrant(store, state, ticket.ticket_id),
        }
        measured: dict[str, Any] | None = None
        receipt = evidence.get("receipt") if isinstance(evidence, Mapping) else None
        if isinstance(receipt, Mapping) and isinstance(receipt.get("id"), str):
            found = receipts.get(receipt["id"])
            measured = receipt_view(found) if found is not None else None
        view["measured"] = measured
        if not full:
            return view
        messages = evidence.get("messages") if isinstance(evidence, Mapping) else None
        held: list[dict[str, Any]] = []
        if isinstance(messages, list):
            payloads = _payloads(self.outbox(), [str(m.get("id")) for m in messages])
            for message in messages:
                text = payloads.get(str(message.get("id")))
                parsed: Any = None
                if text is not None:
                    try:
                        parsed = json.loads(text)
                    except ValueError:
                        parsed = None
                held.append(
                    {
                        **message,
                        "payload": parsed if parsed is not None else text,
                        "payload_read": text is not None,
                        "payload_matches": (
                            hashlib.sha256(text.encode("utf-8")).hexdigest()
                            == message.get("payload_hash")
                            if text is not None
                            else None
                        ),
                    }
                )
        view["held"] = held
        view["pending"] = self._pending(store, state, ticket, options)
        view["evidence"] = evidence
        view["observed"] = document["observed"]
        view["statement"] = ticket.statement
        view["document"] = ticket.to_json()
        return view

    @staticmethod
    def _pending(
        store: Store, state: State, ticket: Ticket, options: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Approvals of ``ticket`` in the intake that no record answers yet:
        signed and submitted, waiting for the deployment's writer."""
        waiting = [i for i in store.intake_ids() if i not in state.processed]
        out = []
        for intake, data in store.submissions(waiting) if waiting else ():
            try:
                found = loads(data, signed=True)
            except FiduciaryError:
                continue
            if isinstance(found, Approval) and found.subject.digest == ticket.digest:
                out.append(
                    {
                        "intake": intake,
                        "principal": found.principal,
                        "decision": found.decision,
                        "option": next(
                            (o["action"] for o in options if o["digest"] == found.resolution),
                            None,
                        ),
                    }
                )
        return out

    @staticmethod
    def _warrant(store: Store, state: State, ticket_id: str) -> dict[str, Any] | None:
        digest = state.warrant_of(ticket_id)
        if digest is None:
            return None
        row = state.warrants.get(digest)
        found = store.document(digest)
        warrant = loads(found[1], signed=True) if found is not None else None
        out: dict[str, Any] = {
            "digest": digest,
            "warrant_id": row.warrant_id if row is not None else None,
            "action": row.action if row is not None else None,
            "seq": row.seq if row is not None else None,
            "execution": state.execution_status(digest),
        }
        if isinstance(warrant, Warrant):
            body = warrant.body()
            out["basis"] = list(warrant.basis.approvals)
            out["heads"] = _heads(warrant.resolution.to_json())
            out["formed_at"] = body.get("formed_at")
            out["not_after"] = body.get("not_after")
        return out

    # -- approvals ------------------------------------------------------------------

    def draft(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """An unsigned approval of a ticket's option, and the exact bytes its
        signature covers under the principal's charter key."""
        ticket_id = _text(payload, "ticket_id")
        principal = _text(payload, "principal")
        decision = str(payload.get("decision") or "approve")
        if decision not in ("approve", "decline"):
            raise _bad("decision is approve or decline")
        chosen = str(payload.get("option") or "")
        with self.lock:
            store, state = self.read()
            row = state.tickets.get(ticket_id)
            if row is None:
                raise ControlError(404, f"{self.name} has no ticket {ticket_id}")
            if state.ticket_status(ticket_id) != "open":
                raise ControlError(409, f"ticket {ticket_id} is {state.ticket_status(ticket_id)}")
            ticket = self._ticket(store, row.digest)
            charter = self.charter(store, state)
            member = charter.principal(principal)
            if member is None:
                raise _bad(f"{principal!r} is no principal of {self.name}'s charter")
            option = next(
                (o for o in ticket.options if chosen in (o.digest, o.resolution.action)),
                ticket.options[0] if not chosen else None,
            )
            if option is None:
                raise _bad(f"{chosen!r} is no option of ticket {ticket_id}")
            assert state.charter is not None
            now = datetime.now(UTC)
            body = {
                "v": fidu1.VERSION,
                "type": "approval",
                "approval_id": str(uuid.uuid4()),
                "charter": {"charter_id": charter.charter_id, "version": state.charter[0]},
                "principal": member.name,
                "subject": {"type": "ticket", "digest": ticket.digest},
                "resolution": option.digest,
                "decision": decision,
                "issued_at": fidu1.format_instant(now),
                "not_after": fidu1.format_instant(now + APPROVAL_TTL),
            }
            draft = Approval.from_json(body, signed=False)
            # The construction is the principal's key's (1.17.0): a passkey signs its own.
            header = {"alg": member.public_key.alg, "key_id": member.public_key.key_id}
            return {
                "approval": draft.to_json(),
                "alg": header["alg"],
                "key_id": header["key_id"],
                "key": member.key,
                **({"passkey": member.webauthn.to_json()} if member.webauthn else {}),
                "signing_input": fidu1.signing_input("approval", draft.body(), header).hex(),
                "option": option.resolution.action,
                "command": (
                    f"sign it here with {member.name}'s passkey"
                    if member.webauthn
                    else f"fiduciary approve ticket-{ticket.ticket_id[:8]}.json --principal "
                    f"{member.name} --resolution {option.resolution.action}"
                    + (" --decline" if decision == "decline" else "")
                    + " --key <your key file> --submit"
                ),
            }

    def accept(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """A signed approval (or a draft and its signature), checked under the
        principal's charter key, into the intake."""
        approval = self._approval(payload)
        with self.lock:
            store, state = self.read()
            charter = self.charter(store, state)
            member = charter.principal(approval.principal)
            if member is None:
                raise _bad(f"{approval.principal!r} is no principal of {self.name}'s charter")
            if approval.subject.type != "ticket" or approval.subject.digest not in (
                state.tickets_by_digest
            ):
                raise _bad(f"the approval decides no ticket of {self.name}")
            try:
                approval.verify(member.public_key)
            except FiduciaryError as exc:
                raise ControlError(422, f"the signature does not verify: {exc}") from exc
            result = self.submit(approval)
            return {"approval": approval.digest, **result}

    def sign_draft(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Sign a draft with the principal's key the console holds (the sandbox's)."""
        raw = payload.get("approval")
        try:
            draft = Approval.from_json(raw, signed=False)
        except FiduciaryError as exc:
            raise _bad(f"approval: {exc}") from exc
        with self.lock:
            return self.accept({"approval": self.sign(draft).to_json()})

    @staticmethod
    def _approval(payload: Mapping[str, Any]) -> Approval:
        raw = payload.get("approval")
        signature = payload.get("signature")
        assertion = payload.get("assertion")
        try:
            if isinstance(assertion, Mapping):
                if not isinstance(raw, Mapping):
                    raise _bad("approval is the draft the passkey signed")
                raw = {**raw, "sig": _passkey_sig(_text(payload, "key_id"), assertion)}
            elif isinstance(signature, str):
                if not isinstance(raw, Mapping):
                    raise _bad("approval is the draft the signature covers")
                key_id = _text(payload, "key_id")
                raw = {
                    **raw,
                    "sig": {"alg": fidu1.ALG, "key_id": key_id, "signature": signature.lower()},
                }
            approval = Approval.from_json(raw, signed=True)
        except FiduciaryError as exc:
            raise _bad(f"approval: {exc}") from exc
        return approval


def _passkey_sig(key_id: str, assertion: Mapping[str, Any]) -> dict[str, str]:
    """A passkey's assertion, as the browser posted it (hex), as a FIDU1 ``sig``."""
    parts = {}
    for name in ("authenticator_data", "client_data_json", "signature"):
        value = assertion.get(name)
        if not isinstance(value, str):
            raise _bad(f"assertion.{name} is required, in hex")
        parts[name] = value.lower()
    return {"alg": webauthn.ALG, "key_id": key_id, **parts}


def _text(payload: Mapping[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise _bad(f"{name} is required")
    return value.strip()


class ConfiguredDeployment(Deployment):
    """A deployment its ``fiduciary.toml`` names. Read from a snapshot of its
    SQLite store (or from PostgreSQL); approvals go into the live intake only
    when ``accepts``."""

    def __init__(
        self,
        ident: str,
        config_path: Path,
        *,
        accepts: bool = False,
        kind: str = "deployment",
        name: str | None = None,
        scratch: Path,
    ) -> None:
        from fiduciary.config import load_config

        self.config_path = config_path.resolve()
        self.config = load_config(self.config_path)
        super().__init__(ident, name or self.config_path.parent.name, accepts=accepts)
        self.kind = kind
        self._scratch = scratch
        self._cache: tuple[tuple[int, ...], Store, State] | None = None
        self._receipts = _Receipts(self.config.receipts_log)

    registers = True
    """A deployment its configuration names can take a principal's new passkey (1.17.0)."""

    def passkey_draft(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """The charter's next version making ``principal``'s key the passkey the browser
        just made, and what that passkey signs to propose it (1.17.0)."""
        from fiduciary import passkeys
        from fiduciary.bridge import Fiduciary

        principal = _text(payload, "principal")
        try:
            point = bytes.fromhex(_text(payload, "point"))
            policy = passkeys.policy_for(
                _text(payload, "origin"),
                any_port=payload.get("any_port") is True,
                credential=_text(payload, "credential"),
            )
        except (ValueError, FiduciaryError) as exc:
            raise _bad(str(exc)) from exc
        if len(point) != 64:
            raise _bad("point is a P-256 point: 64 bytes, x then y, in hex")
        with self.lock:
            fid = Fiduciary.from_config(self.config_path)
            try:
                draft = passkeys.draft_charter(fid, principal, point, policy)
            except FiduciaryError as exc:
                raise _bad(str(exc)) from exc
            finally:
                fid.close()
        key = webauthn.PasskeyKey(point, policy)
        header = {"alg": webauthn.ALG, "key_id": key.key_id}
        return {
            "charter": draft.to_json(),
            "key_id": key.key_id,
            "passkey": policy.to_json(),
            "signing_input": fidu1.signing_input("charter", draft.body(), header).hex(),
        }

    def passkey_register(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """The charter version the passkey signed: proposed, approved with the
        principal's key file, and adopted when one approval is the quorum (1.17.0)."""
        from fiduciary import passkeys
        from fiduciary.bridge import Fiduciary
        from fiduciary.documents import Charter

        principal = _text(payload, "principal")
        raw = payload.get("charter")
        assertion = payload.get("assertion")
        if not isinstance(raw, Mapping) or not isinstance(assertion, Mapping):
            raise _bad("charter (the draft) and assertion are required")
        try:
            signed = Charter.from_json(
                {**raw, "sig": _passkey_sig(_text(payload, "key_id"), assertion)}, signed=True
            )
        except FiduciaryError as exc:
            raise _bad(f"charter: {exc}") from exc
        with self.lock:
            fid = Fiduciary.from_config(self.config_path)
            try:
                done = passkeys.complete(fid, self.config_path.parent, principal, signed)
            except FiduciaryError as exc:
                raise ControlError(422, str(exc)) from exc
            finally:
                fid.close()
        return {
            "principal": done.principal,
            "key_id": done.key_id,
            "charter_version": done.charter_version,
            "adopted": done.adopted,
            "retired": None if done.retired is None else str(done.retired),
        }

    def describe(self) -> dict[str, str]:
        from fiduciary.config import is_dsn

        db = self.config.database
        return {
            "config": str(self.config_path),
            "store": "postgresql" if is_dsn(db) else "sqlite",
        }

    def read(self) -> tuple[Store, State]:
        from fiduciary.config import is_dsn

        if is_dsn(self.config.database):
            return self._read_postgres()
        source = Path(self.config.database)
        mark = _signature(source)
        if self._cache is not None and self._cache[0] == mark:
            return self._cache[1], self._cache[2]
        from fiduciary.sqlite import SqliteStore

        if self._cache is not None:
            self._cache[1].close()
            self._cache = None
        copy = self._scratch / f"{self.ident}.db"
        _sqlite_copy(source, copy)
        store = SqliteStore(copy)
        state = State.of([Record.loads(r.data) for r in store.records(LOG_ID)])
        self._cache = (mark, store, state)
        return store, state

    def _read_postgres(self) -> tuple[Store, State]:
        if self._cache is None:
            store = self.config.open_store(application_name="fiduciary-console")
            self._cache = ((0,), store, State.of([]))
        _, store, state = self._cache
        head = store.head(LOG_ID)[0]
        if head != state.head_seq:
            state = state.copy()
            for stored in store.records(LOG_ID, after=state.head_seq):
                state.apply(Record.loads(stored.data))
            self._cache = ((head,), store, state)
        return store, state

    def receipts(self) -> _Receipts:
        return self._receipts

    def bindings(self) -> dict[str, Any]:
        from fiduciary.config import load_tool

        found: dict[str, Any] = {}
        for target in self.config.tools:
            with contextlib.suppress(OSError, ValueError, FiduciaryError):
                binding = load_tool(target)
                found[binding.digest] = binding
        return found

    def holds(self) -> dict[tuple[str, str], bool]:
        try:
            sinks = self.config.interlock_config().sinks
        except (OSError, ValueError):
            return {}
        return {(s.name, op.name): op.hold for s in sinks for op in s.operations}

    def outbox(self) -> str | None:
        return self.config.executor_outbox

    def pins(self) -> Pins | None:
        if self.config.charter_digest is None:
            return None
        keys_file = self.config_path.parent / "pinned-keys.json"
        keys = None
        if keys_file.is_file():
            with contextlib.suppress(OSError, ValueError):
                keys = json.loads(keys_file.read_text(encoding="utf-8"))
        return Pins(self.config.charter_digest, keys)

    def submit(self, approval: Approval) -> dict[str, Any]:
        if not self.accepts:
            raise ControlError(
                403,
                f"{self.name} is read-only here: start the console with --accept-approvals, "
                "or run fiduciary submit",
            )
        with contextlib.closing(self.config.open_store(application_name="fiduciary-console")) as s:
            intake = s.submit(approval.canonical())
        return {
            "intake": intake,
            "recorded": [],
            "note": "in the intake; the deployment's writer records it on its next turn",
        }

    def close(self) -> None:
        if self._cache is not None:
            self._cache[1].close()
            self._cache = None


def discover(root: Path) -> list[Path]:
    """``fiduciary.toml`` in ``root`` and in each directory directly under it."""
    found = [root / "fiduciary.toml"] if (root / "fiduciary.toml").is_file() else []
    found.extend(
        sorted(
            p / "fiduciary.toml"
            for p in root.iterdir()
            if p.is_dir() and not p.name.startswith(".") and (p / "fiduciary.toml").is_file()
        )
    )
    return found


# --------------------------------------------------------------------------
# bundles
# --------------------------------------------------------------------------


def verify(data: bytes, pins: Pins) -> dict[str, Any]:
    """V1 to V13 over ``data``, offline, and its attestation (``verify-bundle --json``)."""
    from fiduciary.attest import attest
    from fiduciary.bundle import BundleError, TrustedKeys, verify_bundle

    keys = (
        TrustedKeys.loads(json.dumps(dict(pins.keys), sort_keys=True).encode("utf-8"))
        if pins.keys
        else None
    )
    try:
        witness = fidu1.key_of(pins.witness) if pins.witness else None
    except FiduciaryError as exc:
        raise _bad(f"witness: {exc}") from exc
    try:
        report = verify_bundle(data, charter=pins.charter, keys=keys, witness=witness)
        attestation = attest(
            data,
            report,
            charter=pins.charter,
            pinned=sorted(pins.keys or ()),
            witness=witness is not None,
        )
    except BundleError as exc:
        return {
            "readable": False,
            "exit_code": 2,
            "error": str(exc),
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }
    return {
        "readable": True,
        "exit_code": report.exit_code,
        "attestation": attestation,
        "withheld": sum(1 for n in report.notes if n.startswith("sealed:")),
        "notes": list(report.notes),
        "pins": {
            "charter": pins.charter,
            "keys": sorted(pins.keys or ()),
            "witness": pins.witness,
        },
    }


def _bundle_charter(data: bytes) -> str | None:
    """The digest of the first charter version a bundle carries, to find the
    deployment whose pins verify it. Never itself a pin."""
    from fiduciary.bundle.format import unpack

    try:
        for name, body in unpack(data):
            if name.startswith("charter/") and name.count("/") == 1:
                charter = loads(body, signed=True)
                if isinstance(charter, Charter) and charter.version == 1:
                    return charter.digest
    except Exception:  # an unreadable bundle is verify()'s to report
        return None
    return None


# --------------------------------------------------------------------------
# the console
# --------------------------------------------------------------------------


class Console:
    """Deployments, the files served beside them, and the sandbox if any."""

    def __init__(
        self,
        root: Path,
        *,
        deployments: Sequence[Deployment] = (),
        page: Callable[[], bytes] | None = None,
        sandbox: Any = None,
        title: str = "Fiduciary console",
        scratch: Path | None = None,
    ) -> None:
        self.root = root.resolve()
        self.title = title
        self._deployments = {d.ident: d for d in deployments}
        self._page = page
        self.sandbox = sandbox
        self._scratch = scratch

    @classmethod
    def over(
        cls,
        root: Path,
        configs: Sequence[Path] = (),
        *,
        accepts: bool = False,
        recorded: bool = False,
        sandbox: Any = None,
    ) -> Console:
        """A console over ``configs``, or every deployment found in ``root``.
        ``recorded`` marks them a demo's recorded run: read, never written."""
        scratch = Path(tempfile.mkdtemp(prefix="fiduciary-console-"))
        found = list(configs) or discover(root)
        deployments: list[Deployment] = []
        names: dict[str, int] = {}
        for path in found:
            base = path.resolve().parent
            ident, name = _recorded(base) if recorded else (_slug(base.name), base.name)
            names[ident] = names.get(ident, 0) + 1
            if names[ident] > 1:
                ident = f"{ident}-{names[ident]}"
            try:
                deployments.append(
                    ConfiguredDeployment(
                        ident,
                        path,
                        accepts=accepts and not recorded,
                        kind="recorded" if recorded else "deployment",
                        name=name,
                        scratch=scratch,
                    )
                )
            except (OSError, ValueError, FiduciaryError) as exc:
                deployments.append(_Unreadable(ident, name, str(exc)))
        return cls(
            root,
            deployments=deployments,
            sandbox=sandbox,
            title="Stack demo" if recorded else "Fiduciary console",
            scratch=scratch,
        )

    def deployments(self) -> list[Deployment]:
        out = list(self._deployments.values())
        if self.sandbox is not None:
            out.extend(self.sandbox.deployments())
        return out

    def deployment(self, ident: str) -> Deployment:
        found = self._deployments.get(ident)
        if found is None and self.sandbox is not None:
            found = self.sandbox.deployment(ident)
        if found is None:
            raise ControlError(404, f"no deployment {ident!r}")
        return found

    def status(self) -> dict[str, Any]:
        return {
            "console": "fiduciary",
            "version": __version__,
            "title": self.title,
            "deployments": [d.summary() for d in self.deployments()],
            "bundles": self.bundles(),
            "sandbox": self.sandbox.describe() if self.sandbox is not None else None,
        }

    # -- files ----------------------------------------------------------------------

    def bundles(self) -> list[dict[str, Any]]:
        """Every ``.fidb`` under the root, at most two levels down."""
        out = []
        for pattern in ("*.fidb", "*/*.fidb", "*/*/*.fidb"):
            for path in sorted(self.root.glob(pattern)):
                rel = path.relative_to(self.root)
                if any(part.startswith(".") for part in rel.parts) or not path.is_file():
                    continue
                out.append({"path": rel.as_posix(), "bytes": path.stat().st_size})
        return out

    def file(self, rel: str) -> Path:
        """A file the console serves: a bundle, a report, or a public pin."""
        parts = [p for p in rel.split("/") if p]
        if not parts or any(p.startswith(".") or p in ("..", "~") or "\\" in p for p in parts):
            raise ControlError(404, "not found")
        name = parts[-1]
        if name not in _SERVED_NAMES and Path(name).suffix not in _SERVED_SUFFIXES:
            raise ControlError(404, "not found")
        candidate = self.root.joinpath(*parts)
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.root) or not resolved.is_file():
            raise ControlError(404, "not found")
        return resolved

    def page(self) -> bytes:
        if self._page is not None:
            return self._page()
        from fiduciary import report

        demo = self.root / "demo.json"
        if demo.is_file():
            # The recorded run, with the console's current page; ``served`` tells
            # the page it may ask the console (a file never does).
            data = json.loads(demo.read_text(encoding="utf-8"))
            return report.html({**data, "served": True}).encode("utf-8")
        return report.console_html(self.title, served=True).encode("utf-8")

    # -- bundles --------------------------------------------------------------------

    def verify(self, data: bytes, query: Mapping[str, str]) -> dict[str, Any]:
        """Verify ``data`` against the pins the request names: a deployment's,
        or a charter digest (and keys) given with it, else the deployment
        whose charter the bundle carries."""
        pins: Pins | None = None
        source = ""
        if query.get("charter"):
            keys = json.loads(query["keys"]) if query.get("keys") else None
            if keys is not None and not isinstance(keys, Mapping):
                raise _bad("keys is a pinned-keys.json object")
            pins, source = Pins(query["charter"].strip(), keys, query.get("witness")), "given"
        elif query.get("deployment"):
            pins = self.deployment(query["deployment"]).pins()
            source = query["deployment"]
        else:
            carried = _bundle_charter(data)
            for each in self.deployments():
                found = each.pins()
                if found is not None and carried is not None and found.charter == carried:
                    pins, source = found, each.ident
                    break
        if pins is None:
            raise ControlError(
                422,
                "no charter digest is pinned for this bundle: give the charter digest its "
                "auditor pinned (and pinned-keys.json)",
            )
        result = verify(data, pins)
        result["pinned_by"] = source
        result["filename"] = query.get("name") or "bundle.fidb"
        return result

    def close(self) -> None:
        for each in self.deployments():
            with contextlib.suppress(Exception):
                each.close()
        if self.sandbox is not None:
            self.sandbox.close()
        if self._scratch is not None:
            shutil.rmtree(self._scratch, ignore_errors=True)


class _Unreadable(Deployment):
    """A deployment whose configuration the console cannot open: listed, with why."""

    def __init__(self, ident: str, name: str, error: str) -> None:
        super().__init__(ident, name)
        self.error = error

    def read(self) -> tuple[Store, State]:
        raise ControlError(409, self.error)


def _recorded(base: Path) -> tuple[str, str]:
    """A recorded run's deployment: its industry's key and name (``demo.json``)."""
    try:
        demo = json.loads((base / "demo.json").read_text(encoding="utf-8")).get("demo") or {}
    except (OSError, ValueError):
        demo = {}
    domain = demo.get("domain")
    if isinstance(domain, Mapping) and "key" in domain:
        return str(domain["key"]), str(domain.get("industry") or domain["key"])
    return "fintech", "Fintech"


def _slug(name: str) -> str:
    out = "".join(c if c.isalnum() or c in "-_" else "-" for c in name.lower()).strip("-")
    return out or "deployment"


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


class ConsoleServer(http.server.ThreadingHTTPServer):
    """The console on loopback."""

    daemon_threads = True
    request_queue_size = 32

    def __init__(self, address: tuple[str, int], console: Console) -> None:
        self.console = console
        super().__init__(address, _Handler)
        port = self.server_address[1]
        host = address[0]
        names = ("127.0.0.1", "localhost") if host != "::1" else ("[::1]", "localhost")
        self.hosts = frozenset(f"{n}:{port}" for n in names)
        self.origins = frozenset(f"http://{h}" for h in self.hosts)
        # localhost, not 127.0.0.1: a passkey's relying party is a name, never an
        # address (1.17.0). Both answer; this is the one the console prints and opens.
        self.url = f"http://{'[::1]' if host == '::1' else 'localhost'}:{port}/"

    def server_close(self) -> None:
        try:
            self.console.close()
        finally:
            super().server_close()


class _Handler(http.server.BaseHTTPRequestHandler):
    server: ConsoleServer
    protocol_version = "HTTP/1.1"
    server_version = "fiduciary-console"
    sys_version = ""

    def log_message(self, fmt: str, *args: object) -> None:
        _log.debug("%s " + fmt, self.address_string(), *args)

    # -- responses ------------------------------------------------------------------

    def _send(self, status: int, body: bytes, ctype: str, *, html: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        if html:
            self.send_header("Content-Security-Policy", _CSP)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, body: Any) -> None:
        data = (json.dumps(body, sort_keys=True, default=str) + "\n").encode("utf-8")
        self._send(status, data, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self.close_connection = True
        self._json(status, {"error": message})

    # -- the boundary ---------------------------------------------------------------

    def _guard(self, writes: bool) -> str | None:
        """Why this request is refused, or ``None``."""
        if self.headers.get("Host", "") not in self.server.hosts:
            return "this console answers only on its own loopback address"
        site = self.headers.get("Sec-Fetch-Site")
        navigating = not writes and self.headers.get("Sec-Fetch-Mode") == "navigate"
        api = urllib.parse.urlsplit(self.path).path.startswith("/api/")
        if site is not None and site not in ("same-origin", "none") and (api or not navigating):
            # Opening the console from a link is a navigation, and harmless; what
            # another site's page may not do is call its API or embed its files.
            return "cross-site requests are refused"
        if writes:
            origin = self.headers.get("Origin")
            if origin is not None and origin not in self.server.origins:
                return "cross-origin writes are refused"
            if self.headers.get(HEADER) != "1":
                return f"a write carries the {HEADER} header"
        return None

    def _body(self, limit: int) -> bytes:
        if self.headers.get("Transfer-Encoding"):
            raise ControlError(411, "send a Content-Length, not a chunked body")
        raw = self.headers.get("Content-Length")
        if raw is None:
            raise ControlError(411, "a write carries a Content-Length")
        try:
            length = int(raw)
        except ValueError as exc:
            raise ControlError(400, "Content-Length is a number") from exc
        if length < 0 or length > limit:
            raise ControlError(413, f"a body here is at most {limit} bytes")
        return self.rfile.read(length)

    def _payload(self) -> Mapping[str, Any]:
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            raise ControlError(415, "send application/json")
        body = self._body(MAX_JSON)
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except (UnicodeDecodeError, ValueError) as exc:
            raise _bad(f"the body is no JSON: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise _bad("the body is a JSON object")
        return payload

    # -- routes ---------------------------------------------------------------------

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_OPTIONS(self) -> None:
        self._error(405, "no cross-origin access is granted")

    def do_PUT(self) -> None:
        self._error(405, "not allowed")

    def do_DELETE(self) -> None:
        self._error(405, "not allowed")

    def do_PATCH(self) -> None:
        self._error(405, "not allowed")

    def do_GET(self) -> None:
        self._handle(writes=False)

    def do_POST(self) -> None:
        self._handle(writes=True)

    def _handle(self, *, writes: bool) -> None:
        refused = self._guard(writes)
        if refused is not None:
            self._error(403, refused)
            return
        url = urllib.parse.urlsplit(self.path)
        parts = [urllib.parse.unquote(p) for p in url.path.split("/") if p]
        query = dict(urllib.parse.parse_qsl(url.query))
        console = self.server.console
        try:
            if parts[:1] == ["api"]:
                self._json(200, self._api(console, parts[1:], query, writes))
            elif writes:
                raise ControlError(404, "not found")
            elif not parts or parts == ["dashboard.html"] or parts == ["index.html"]:
                self._send(200, console.page(), "text/html; charset=utf-8", html=True)
            else:
                path = console.file("/".join(parts))
                ctype = {
                    ".fidb": "application/x-tar",
                    ".md": "text/markdown; charset=utf-8",
                    ".json": "application/json; charset=utf-8",
                }.get(path.suffix, "text/plain; charset=utf-8")
                self._send(200, path.read_bytes(), ctype)
        except ControlError as exc:
            self._error(exc.status, str(exc))
        except Exception:
            _log.exception("console request %s %s failed", self.command, url.path)
            self._error(500, "the console failed on this request; its log has the cause")

    def _api(
        self, console: Console, parts: list[str], query: Mapping[str, str], writes: bool
    ) -> Any:
        if not writes:
            if parts == ["status"]:
                return console.status()
            if len(parts) == 3 and parts[0] == "deployments" and parts[2] == "tickets":
                return console.deployment(parts[1]).tickets()
            if len(parts) == 4 and parts[0] == "deployments" and parts[2] == "tickets":
                return console.deployment(parts[1]).ticket(parts[3])
            if len(parts) == 3 and parts[0] == "deployments" and parts[2] == "profile":
                return console.deployment(parts[1]).profile()
            raise ControlError(404, "no such endpoint")
        if parts == ["verify"]:
            ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if ctype != "application/octet-stream":
                raise ControlError(415, "send the bundle as application/octet-stream")
            return console.verify(self._body(MAX_BUNDLE), query)
        payload = self._payload()
        if len(parts) >= 3 and parts[0] == "deployments" and parts[2] == "approvals":
            deployment = console.deployment(parts[1])
            action = parts[3] if len(parts) == 4 else ""
            if action == "draft":
                return deployment.draft(payload)
            if action == "sign":
                return deployment.sign_draft(payload)
            if action == "":
                return deployment.accept(payload)
        if len(parts) >= 3 and parts[0] == "deployments" and parts[2] == "passkeys":
            deployment = console.deployment(parts[1])
            if not isinstance(deployment, ConfiguredDeployment):
                raise ControlError(409, "a passkey is registered on a deployment its config names")
            if len(parts) == 4 and parts[3] == "draft":
                return deployment.passkey_draft(payload)
            if len(parts) == 3:
                return deployment.passkey_register(payload)
        if len(parts) == 3 and parts[0] == "sandbox" and console.sandbox is not None:
            if parts[2] == "open":
                return console.sandbox.open(parts[1])
            if parts[2] == "call":
                return console.sandbox.call(parts[1], payload)
            if parts[2] == "reset":
                return console.sandbox.reset(parts[1])
        raise ControlError(404, "no such endpoint")


def serve(console: Console, where: str = "localhost:8740") -> ConsoleServer:
    """``console`` on loopback ``where`` (``HOST:PORT``; port 0 picks one).

    :raises ValueError: If ``where`` is not a loopback ``HOST:PORT``.
    :raises OSError: If the port cannot be bound.
    """
    host, _, port = where.rpartition(":")
    host = host.strip("[]") or "127.0.0.1"
    if host not in LOOPBACK:
        raise ValueError("the console listens on loopback only: 127.0.0.1:<port>")
    try:
        number = int(port)
    except ValueError as exc:
        raise ValueError("give the address as HOST:PORT") from exc
    try:
        return ConsoleServer(("127.0.0.1" if host == "localhost" else host, number), console)
    except OSError:
        console.close()
        raise


def run(server: ConsoleServer) -> None:
    """Serve until interrupted, then close."""
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    server.server_close()

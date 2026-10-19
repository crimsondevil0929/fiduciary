"""The ticket: a service-signed escalation over typed evidence (§4.4, §7.1).

When an agent reaches a boundary (a refused plan with a repair proposal, a
held or dead outbox message, a plan to undo, an extension quote, or a proposed
mandate amendment), the service raises a :class:`Ticket`. Its ``evidence``
holds only typed ids, digests, counts, amounts and instants read from
Interlock and AgentGov, never prose (F4-17). The agent's own words sit in
``agent.statement``, clipped to 512 characters, and enter no rule, option or
decision (F4-18).

Each option pairs a :class:`Resolution` with its canonical SHA-256 digest and
the quorum its class requires (F4-19). A principal may approve one of the
ticket's options, or a narrowing of one: the same action, a subset of its
targets, and an amount no larger (F4-20).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar, Final

from fiduciary import fidu1
from fiduciary.documents._base import SCOPE, Document, sorted_by
from fiduciary.documents.mandate import (
    DROP_CAUSES,
    OPERATION,
    SINK_NAME,
    TICKET_CLASSES,
    Mandate,
    MandateRef,
)
from fiduciary.documents.record import LOG_ID
from fiduciary.exceptions import MalformedError
from fiduciary.fidu1 import Fields, Signature, check_text

__all__ = [
    "ACTIONS_BY_CLASS",
    "AUTO_ACTIONS",
    "RESOLUTION_ACTIONS",
    "AmendEvidence",
    "DeadEvidence",
    "DeadMessage",
    "DiffItem",
    "DroppedItem",
    "Evidence",
    "ExtensionEvidence",
    "HaltInfo",
    "HeldEvidence",
    "HeldMessage",
    "LedgerObserved",
    "LogObserved",
    "MessageTarget",
    "Observed",
    "ProgressEvidence",
    "ProposedMandate",
    "QuoteEvidence",
    "ReceiptCheckpoint",
    "ReceiptRef",
    "RefusedEvidence",
    "Resolution",
    "Ticket",
    "TicketOption",
    "UndoEvidence",
    "UndoMessage",
    "quorum_for_resolution",
]

SIGNED_MONEY: Final = re.compile(r"-?(0|[1-9][0-9]{0,17})\.[0-9]{8}")
"""A ledger balance that may be negative after an overrun."""

ESTIMATE_METHODS: Final = ("milestones", "next_call")

ACTIONS_BY_CLASS: Final[Mapping[str, tuple[str, ...]]] = {
    "refused": ("accept_repair", "reject"),
    "held": ("release", "reset", "cancel"),
    "dead": ("requeue", "cancel"),
    "undo": ("compensate", "compensate_late", "reject"),
    "extension": ("grant", "decline"),
    "amend": ("amend", "reject"),
}
"""The resolution actions each ticket class admits, in the order ``options``
lists them (§7.1)."""

RESOLUTION_ACTIONS: Final = (
    "accept_repair",
    "amend",
    "cancel",
    "compensate",
    "compensate_late",
    "decline",
    "grant",
    "reject",
    "release",
    "requeue",
    "reset",
)

AUTO_ACTIONS: Final = (
    "accept_repair",
    "cancel",
    "decline",
    "grant",
    "reject",
    "requeue",
)
"""Actions an autonomous escalation rule may form (§7.2, §7.4). ``release``,
``reset``, ``compensate``, ``compensate_late`` and ``amend`` always require a
human quorum."""


def _money(amount: Decimal) -> str:
    return format(amount.quantize(Decimal("0.00000001")), "f")


def _positive_money(fields: Fields, name: str) -> Decimal:
    amount = fields.money(name)
    if amount <= 0:
        raise MalformedError("range", fields.path(name), "must be more than zero")
    return amount


# --------------------------------------------------------------------------
# observed state
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LedgerObserved:
    sequence: int
    head: str

    def to_json(self) -> dict[str, Any]:
        return {"sequence": self.sequence, "head": self.head}

    @classmethod
    def read(cls, fields: Fields) -> LedgerObserved:
        return cls(fields.integer("sequence", 1), fields.hash("head"))


@dataclass(frozen=True, slots=True)
class LogObserved:
    seq: int
    hash: str

    def to_json(self) -> dict[str, Any]:
        return {"seq": self.seq, "hash": self.hash}

    @classmethod
    def read(cls, fields: Fields) -> LogObserved:
        return cls(fields.integer("seq", 1), fields.hash("hash"))


@dataclass(frozen=True, slots=True)
class Observed:
    ledger: LedgerObserved
    log: LogObserved

    def to_json(self) -> dict[str, Any]:
        return {"ledger": self.ledger.to_json(), "log": self.log.to_json()}

    @classmethod
    def read(cls, fields: Fields) -> Observed:
        return cls(
            LedgerObserved.read(fields.object("ledger", ("sequence", "head"))),
            LogObserved.read(fields.object("log", ("seq", "hash"))),
        )


# --------------------------------------------------------------------------
# typed evidence per class (F4-17, §7.1)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReceiptRef:
    receipt_id: str
    digest: str

    def to_json(self) -> dict[str, Any]:
        return {"id": self.receipt_id, "digest": self.digest}


@dataclass(frozen=True, slots=True)
class DroppedItem:
    effect_id: str
    cause: str

    def to_json(self) -> dict[str, Any]:
        return {"id": self.effect_id, "cause": self.cause}


@dataclass(frozen=True, slots=True)
class RefusedEvidence:
    """Evidence for a ``refused`` ticket: a refused plan and its repair proposal."""

    receipt: ReceiptRef
    blocked_by: tuple[str, ...]
    proposal: str
    kept: tuple[str, ...]
    dropped: tuple[DroppedItem, ...]
    exhaustive: bool
    trials: int

    def to_json(self) -> dict[str, Any]:
        return {
            "receipt": self.receipt.to_json(),
            "blocked_by": list(self.blocked_by),
            "proposal": self.proposal,
            "kept": list(self.kept),
            "dropped": [d.to_json() for d in self.dropped],
            "exhaustive": self.exhaustive,
            "trials": self.trials,
        }

    @classmethod
    def read(cls, raw: object, path: str) -> RefusedEvidence:
        fields = Fields(
            raw,
            path,
            ("receipt", "blocked_by", "proposal", "kept", "dropped", "exhaustive", "trials"),
        )
        receipt = fields.object("receipt", ("id", "digest"))
        blocked_by = fields.strings("blocked_by", fidu1.TEXT, "a checker name")
        if not blocked_by:
            raise MalformedError("range", fields.path("blocked_by"), "names at least one checker")
        kept = fields.strings("kept", fidu1.TEXT, "an effect id")
        if not kept:
            raise MalformedError("range", fields.path("kept"), "keeps at least one effect")
        dropped_items = []
        for item_path, item in fields.array("dropped"):
            entry = Fields(item, item_path, ("id", "cause"))
            dropped_items.append(
                DroppedItem(entry.text("id", fidu1.TEXT), entry.choice("cause", DROP_CAUSES))
            )
        if not dropped_items:
            raise MalformedError("range", fields.path("dropped"), "drops at least one effect")
        dropped = sorted_by(dropped_items, lambda d: (d.effect_id,), fields.path("dropped"))
        if set(kept) & {d.effect_id for d in dropped}:
            raise MalformedError(
                "agreement", fields.path("dropped"), "an effect is kept or dropped, not both"
            )
        return cls(
            receipt=ReceiptRef(receipt.text("id", fidu1.TEXT), receipt.hash("digest")),
            blocked_by=blocked_by,
            proposal=fields.hash("proposal"),
            kept=kept,
            dropped=dropped,
            exhaustive=fields.boolean("exhaustive"),
            trials=fields.integer("trials", 1),
        )


@dataclass(frozen=True, slots=True)
class HeldMessage:
    message_id: str
    head: str
    sink: str
    operation: str
    payload_hash: str

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.message_id,
            "head": self.head,
            "sink": self.sink,
            "operation": self.operation,
            "payload_hash": self.payload_hash,
        }


@dataclass(frozen=True, slots=True)
class HaltInfo:
    scope: str
    reason: str
    spent_out: bool

    def to_json(self) -> dict[str, Any]:
        return {"scope": self.scope, "reason": self.reason, "spent_out": self.spent_out}


@dataclass(frozen=True, slots=True)
class HeldEvidence:
    """Evidence for a ``held`` ticket: held outbox messages and the halting scope."""

    messages: tuple[HeldMessage, ...]
    halt: HaltInfo | None

    def to_json(self) -> dict[str, Any]:
        return {
            "messages": [m.to_json() for m in self.messages],
            "halt": None if self.halt is None else self.halt.to_json(),
        }

    @classmethod
    def read(cls, raw: object, path: str) -> HeldEvidence:
        fields = Fields(raw, path, ("messages", "halt"))
        items = []
        for item_path, item in fields.array("messages"):
            entry = Fields(item, item_path, ("id", "head", "sink", "operation", "payload_hash"))
            items.append(
                HeldMessage(
                    message_id=entry.uuid("id"),
                    head=entry.hash("head"),
                    sink=entry.text("sink", SINK_NAME, "a sink name"),
                    operation=entry.text("operation", OPERATION, "an operation"),
                    payload_hash=entry.hash("payload_hash"),
                )
            )
        if not items:
            raise MalformedError("range", fields.path("messages"), "names at least one message")
        messages = sorted_by(items, lambda m: (m.message_id,), fields.path("messages"))
        halt = None
        if not fields.is_null("halt"):
            h = fields.object("halt", ("scope", "reason", "spent_out"))
            halt = HaltInfo(
                scope=h.text("scope", SCOPE, "a scope id"),
                reason=h.text("reason", fidu1.TEXT, "a halt reason"),
                spent_out=h.boolean("spent_out"),
            )
        return cls(messages=messages, halt=halt)


@dataclass(frozen=True, slots=True)
class DeadMessage:
    message_id: str
    head: str
    attempts: int
    requeues: int
    outcome: str

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.message_id,
            "head": self.head,
            "attempts": self.attempts,
            "requeues": self.requeues,
            "outcome": self.outcome,
        }


@dataclass(frozen=True, slots=True)
class DeadEvidence:
    """Evidence for a ``dead`` ticket: one dead message and its delivery history."""

    message: DeadMessage

    def to_json(self) -> dict[str, Any]:
        return {"message": self.message.to_json()}

    @classmethod
    def read(cls, raw: object, path: str) -> DeadEvidence:
        fields = Fields(raw, path, ("message",))
        msg = fields.object("message", ("id", "head", "attempts", "requeues", "outcome"))
        return cls(
            DeadMessage(
                message_id=msg.uuid("id"),
                head=msg.hash("head"),
                attempts=msg.integer("attempts", 1),
                requeues=msg.integer("requeues", 0),
                outcome=msg.text("outcome", fidu1.TEXT, "an outcome class"),
            )
        )


@dataclass(frozen=True, slots=True)
class UndoMessage:
    message_id: str
    head: str
    not_after: datetime
    operation: str

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.message_id,
            "head": self.head,
            "not_after": fidu1.format_instant(self.not_after),
            "operation": self.operation,
        }


@dataclass(frozen=True, slots=True)
class UndoEvidence:
    """Evidence for an ``undo`` ticket: delivered originals that carry compensations."""

    plan_id: str
    messages: tuple[UndoMessage, ...]
    late: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "messages": [m.to_json() for m in self.messages],
            "late": self.late,
        }

    @classmethod
    def read(cls, raw: object, path: str) -> UndoEvidence:
        fields = Fields(raw, path, ("plan_id", "messages", "late"))
        items = []
        for item_path, item in fields.array("messages"):
            entry = Fields(item, item_path, ("id", "head", "not_after", "operation"))
            items.append(
                UndoMessage(
                    message_id=entry.uuid("id"),
                    head=entry.hash("head"),
                    not_after=entry.instant("not_after"),
                    operation=entry.text("operation", OPERATION, "an operation"),
                )
            )
        if not items:
            raise MalformedError("range", fields.path("messages"), "names at least one message")
        messages = sorted_by(items, lambda m: (m.message_id,), fields.path("messages"))
        return cls(
            plan_id=fields.text("plan_id", fidu1.TEXT, "a plan id"),
            messages=messages,
            late=fields.boolean("late"),
        )


@dataclass(frozen=True, slots=True)
class QuoteEvidence:
    quote_id: str
    record_log: str
    record_seq: int
    record_hash: str
    needed: Decimal
    requested: Decimal
    spent: Decimal
    available: Decimal
    estimate: str
    expires_at: datetime

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.quote_id,
            "record": {"log": self.record_log, "seq": self.record_seq, "hash": self.record_hash},
            "needed": _money(self.needed),
            "requested": _money(self.requested),
            "spent": _money(self.spent),
            "available": _money(self.available),
            "estimate": self.estimate,
            "expires_at": fidu1.format_instant(self.expires_at),
        }


@dataclass(frozen=True, slots=True)
class ReceiptCheckpoint:
    log: str
    tree_size: int
    root: str

    def to_json(self) -> dict[str, Any]:
        return {"log": self.log, "tree_size": self.tree_size, "root": self.root}


@dataclass(frozen=True, slots=True)
class ProgressEvidence:
    committed: int
    rows: int
    receipts: ReceiptCheckpoint | None

    def to_json(self) -> dict[str, Any]:
        return {
            "committed": self.committed,
            "rows": self.rows,
            "receipts": None if self.receipts is None else self.receipts.to_json(),
        }


@dataclass(frozen=True, slots=True)
class ExtensionEvidence:
    """Evidence for an ``extension`` ticket (§4.4)."""

    quote: QuoteEvidence
    progress: ProgressEvidence

    def to_json(self) -> dict[str, Any]:
        return {"quote": self.quote.to_json(), "progress": self.progress.to_json()}

    @classmethod
    def read(cls, raw: object, path: str) -> ExtensionEvidence:
        fields = Fields(raw, path, ("quote", "progress"))
        q = fields.object(
            "quote",
            (
                "id",
                "record",
                "needed",
                "requested",
                "spent",
                "available",
                "estimate",
                "expires_at",
            ),
        )
        rec = q.object("record", ("log", "seq", "hash"))
        quote = QuoteEvidence(
            quote_id=q.text("id", fidu1.TEXT, "a quote id"),
            record_log=rec.text("log", LOG_ID, "a log id"),
            record_seq=rec.integer("seq", 1),
            record_hash=rec.hash("hash"),
            needed=_positive_money(q, "needed"),
            requested=_positive_money(q, "requested"),
            spent=q.money("spent"),
            available=Decimal(
                q.text("available", SIGNED_MONEY, "money: a decimal with exactly eight places")
            ),
            estimate=q.choice("estimate", ESTIMATE_METHODS),
            expires_at=q.instant("expires_at"),
        )
        p = fields.object("progress", ("committed", "rows", "receipts"))
        receipts = None
        if not p.is_null("receipts"):
            rc = p.object("receipts", ("log", "tree_size", "root"))
            receipts = ReceiptCheckpoint(
                log=rc.text("log", LOG_ID, "a log id"),
                tree_size=rc.integer("tree_size", 1),
                root=rc.hash("root"),
            )
        progress = ProgressEvidence(
            committed=p.integer("committed", 0),
            rows=p.integer("rows", 0),
            receipts=receipts,
        )
        return cls(quote=quote, progress=progress)


@dataclass(frozen=True, slots=True)
class ProposedMandate:
    version: int
    digest: str

    def to_json(self) -> dict[str, Any]:
        return {"version": self.version, "digest": self.digest}


@dataclass(frozen=True, slots=True)
class DiffItem:
    path: str
    message: str

    def to_json(self) -> dict[str, Any]:
        return {"path": self.path, "message": self.message}


@dataclass(frozen=True, slots=True)
class AmendEvidence:
    """Evidence for an ``amend`` ticket: the proposed version and its two-way diff."""

    proposed: ProposedMandate
    wider: tuple[DiffItem, ...]
    narrower: tuple[DiffItem, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "proposed": self.proposed.to_json(),
            "wider": [d.to_json() for d in self.wider],
            "narrower": [d.to_json() for d in self.narrower],
        }

    @classmethod
    def read(cls, raw: object, path: str) -> AmendEvidence:
        fields = Fields(raw, path, ("proposed", "wider", "narrower"))
        prop = fields.object("proposed", ("version", "digest"))

        def read_diff(name: str) -> tuple[DiffItem, ...]:
            items = []
            for item_path, item in fields.array(name):
                entry = Fields(item, item_path, ("path", "message"))
                items.append(
                    DiffItem(
                        path=entry.text("path", fidu1.TEXT, "a field path"),
                        message=entry.text("message", fidu1.TEXT, "a diff description"),
                    )
                )
            return sorted_by(items, lambda d: (d.path, d.message), fields.path(name))

        return cls(
            proposed=ProposedMandate(prop.integer("version", 1), prop.hash("digest")),
            wider=read_diff("wider"),
            narrower=read_diff("narrower"),
        )


Evidence = (
    RefusedEvidence | HeldEvidence | DeadEvidence | UndoEvidence | ExtensionEvidence | AmendEvidence
)


def _read_evidence(ticket_class: str, raw: object, path: str) -> Evidence:
    if ticket_class == "refused":
        return RefusedEvidence.read(raw, path)
    if ticket_class == "held":
        return HeldEvidence.read(raw, path)
    if ticket_class == "dead":
        return DeadEvidence.read(raw, path)
    if ticket_class == "undo":
        return UndoEvidence.read(raw, path)
    if ticket_class == "extension":
        return ExtensionEvidence.read(raw, path)
    return AmendEvidence.read(raw, path)


# --------------------------------------------------------------------------
# resolutions and options (F4-19, F4-20)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MessageTarget:
    """One outbox message a resolution acts on, at the delivery-log head observed."""

    message_id: str
    head: str

    def to_json(self) -> dict[str, Any]:
        return {"id": self.message_id, "head": self.head}

    @classmethod
    def read(cls, raw: object, path: str) -> MessageTarget:
        fields = Fields(raw, path, ("id", "head"))
        return cls(fields.uuid("id"), fields.hash("head"))


@dataclass(frozen=True, slots=True, kw_only=True)
class Resolution:
    """What an option or a warrant resolves to do (F4-19, F4-20).

    - ``grant``: ``{"action": "grant", "quote": "<id>", "amount": "<money>"}``
    - ``decline``: ``{"action": "decline", "quote": "<id>"}``
    - ``accept_repair``: ``{"action": "accept_repair", "proposal": "<plan_hash>"}``
    - ``amend``: ``{"action": "amend", "mandate": "<digest>"}``
    - ``release``: ``{"action": "release", "messages": [...], "reset": "<scope>" | null}``
    - ``reset``: ``{"action": "reset", "scope": "<scope>"}``
    - ``cancel`` / ``requeue``: ``{"action": ..., "messages": [...]}``
    - ``compensate`` / ``compensate_late``:
      ``{"action": ..., "plan_id": "<id>", "messages": [...]}``
    - ``reject``: ``{"action": "reject", "proposal": "<hash>"}`` or
      ``{"action": "reject", "plan_id": "<id>"}`` or
      ``{"action": "reject", "mandate": "<digest>"}``
    """

    action: str
    quote: str | None = None
    amount: Decimal | None = None
    proposal: str | None = None
    mandate: str | None = None
    plan_id: str | None = None
    messages: tuple[MessageTarget, ...] = ()
    reset: str | None = None
    scope: str | None = None

    def to_json(self) -> dict[str, Any]:
        if self.action == "grant":
            assert self.quote is not None and self.amount is not None
            return {"action": "grant", "quote": self.quote, "amount": _money(self.amount)}
        if self.action == "decline":
            assert self.quote is not None
            return {"action": "decline", "quote": self.quote}
        if self.action == "accept_repair":
            assert self.proposal is not None
            return {"action": "accept_repair", "proposal": self.proposal}
        if self.action == "amend":
            assert self.mandate is not None
            return {"action": "amend", "mandate": self.mandate}
        if self.action == "release":
            return {
                "action": "release",
                "messages": [m.to_json() for m in self.messages],
                "reset": self.reset,
            }
        if self.action == "reset":
            assert self.scope is not None
            return {"action": "reset", "scope": self.scope}
        if self.action in ("cancel", "requeue"):
            return {"action": self.action, "messages": [m.to_json() for m in self.messages]}
        if self.action in ("compensate", "compensate_late"):
            assert self.plan_id is not None
            return {
                "action": self.action,
                "plan_id": self.plan_id,
                "messages": [m.to_json() for m in self.messages],
            }
        # reject: carries exactly one of proposal, plan_id or mandate
        if self.proposal is not None:
            return {"action": "reject", "proposal": self.proposal}
        if self.plan_id is not None:
            return {"action": "reject", "plan_id": self.plan_id}
        assert self.mandate is not None
        return {"action": "reject", "mandate": self.mandate}

    @property
    def digest(self) -> str:
        """``SHA-256(canonical_bytes(resolution))`` (F4-19)."""
        return fidu1.digest(self.to_json())

    def narrows(self, option: Resolution) -> bool:
        """Whether ``self`` is ``option`` or a narrowing of it (F4-20): the
        same action, a non-empty subset of its targets, and an amount no larger."""
        if self.action != option.action:
            return False
        if self.action == "grant":
            assert self.amount is not None and option.amount is not None
            return self.quote == option.quote and Decimal(0) < self.amount <= option.amount
        if self.action in ("release", "cancel", "requeue", "compensate", "compensate_late"):
            if self.reset != option.reset or self.plan_id != option.plan_id:
                return False
            mine = {(m.message_id, m.head) for m in self.messages}
            theirs = {(m.message_id, m.head) for m in option.messages}
            return bool(mine) and mine <= theirs
        return self == option

    @classmethod
    def read(cls, raw: object, path: str = "$.resolution") -> Resolution:
        if not isinstance(raw, dict):
            raise MalformedError("type", path, "must be an object")
        action = check_text(
            raw.get("action"),
            f"{path}.action",
            fidu1.one_of(RESOLUTION_ACTIONS),
            f"one of {', '.join(RESOLUTION_ACTIONS)}",
        )
        if action == "grant":
            fields = Fields(raw, path, ("action", "quote", "amount"))
            return cls(
                action="grant",
                quote=fields.text("quote", fidu1.TEXT, "a quote id"),
                amount=_positive_money(fields, "amount"),
            )
        if action == "decline":
            fields = Fields(raw, path, ("action", "quote"))
            return cls(action="decline", quote=fields.text("quote", fidu1.TEXT, "a quote id"))
        if action == "accept_repair":
            fields = Fields(raw, path, ("action", "proposal"))
            return cls(action="accept_repair", proposal=fields.hash("proposal"))
        if action == "amend":
            fields = Fields(raw, path, ("action", "mandate"))
            return cls(action="amend", mandate=fields.hash("mandate"))
        if action == "release":
            fields = Fields(raw, path, ("action", "messages", "reset"))
            return cls(
                action="release",
                messages=_read_targets(fields),
                reset=None if fields.is_null("reset") else fields.text("reset", SCOPE, "a scope"),
            )
        if action == "reset":
            fields = Fields(raw, path, ("action", "scope"))
            return cls(action="reset", scope=fields.text("scope", SCOPE, "a scope"))
        if action in ("cancel", "requeue"):
            fields = Fields(raw, path, ("action", "messages"))
            return cls(action=action, messages=_read_targets(fields))
        if action in ("compensate", "compensate_late"):
            fields = Fields(raw, path, ("action", "plan_id", "messages"))
            return cls(
                action=action,
                plan_id=fields.text("plan_id", fidu1.TEXT, "a plan id"),
                messages=_read_targets(fields),
            )
        # reject: exactly one target key (proposal, plan_id, or mandate)
        target_keys = [k for k in ("proposal", "plan_id", "mandate") if k in raw]
        chosen = target_keys[0] if len(target_keys) == 1 else "proposal"
        fields = Fields(raw, path, ("action", chosen))
        if chosen == "proposal":
            return cls(action="reject", proposal=fields.hash("proposal"))
        if chosen == "plan_id":
            return cls(action="reject", plan_id=fields.text("plan_id", fidu1.TEXT, "a plan id"))
        return cls(action="reject", mandate=fields.hash("mandate"))


def _read_targets(fields: Fields) -> tuple[MessageTarget, ...]:
    items = [MessageTarget.read(item, p) for p, item in fields.array("messages")]
    if not items:
        raise MalformedError("range", fields.path("messages"), "names at least one message")
    return sorted_by(items, lambda m: (m.message_id,), fields.path("messages"))


@dataclass(frozen=True, slots=True)
class TicketOption:
    resolution: Resolution
    digest: str
    quorum: int

    def to_json(self) -> dict[str, Any]:
        return {
            "resolution": self.resolution.to_json(),
            "digest": self.digest,
            "quorum": self.quorum,
        }

    @classmethod
    def make(cls, resolution: Resolution, quorum: int) -> TicketOption:
        return cls(resolution=resolution, digest=resolution.digest, quorum=quorum)

    @classmethod
    def read(cls, raw: object, path: str) -> TicketOption:
        fields = Fields(raw, path, ("resolution", "digest", "quorum"))
        return cls(
            resolution=Resolution.read(fields.raw("resolution"), fields.path("resolution")),
            digest=fields.hash("digest"),
            quorum=fields.integer("quorum", 1),
        )


# --------------------------------------------------------------------------
# the ticket (§4.4)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Ticket(Document):
    """A signed record of an escalation (F4-17 to F4-20)."""

    TYPE: ClassVar[str] = "ticket"
    FIELDS: ClassVar[tuple[str, ...]] = (
        "ticket_id",
        "mandate",
        "class",
        "raised_at",
        "not_after",
        "observed",
        "evidence",
        "options",
        "agent",
    )

    ticket_id: str
    mandate: MandateRef
    ticket_class: str
    raised_at: datetime
    not_after: datetime
    observed: Observed
    evidence: Evidence
    options: tuple[TicketOption, ...]
    statement: str | None
    """``agent.statement``: clipped to 512 characters, quarantined from every
    rule, option and decision (F4-18, F7-3)."""
    signature: Signature | None = None

    def option_for(self, resolution: Resolution) -> TicketOption | None:
        """The option of this ticket that ``resolution`` equals or narrows (F4-20)."""
        for option in self.options:
            if resolution.narrows(option.resolution):
                return option
        return None

    def option_by_digest(self, digest: str) -> TicketOption | None:
        """The option whose resolution digest is ``digest``, if it is an exact option."""
        for option in self.options:
            if option.digest == digest:
                return option
        return None

    def evidence_json(self) -> dict[str, Any]:
        """The canonical JSON dict of ``evidence``, for deduplication (F7-1)."""
        return self.evidence.to_json()

    def _body(self) -> dict[str, Any]:
        return {
            "ticket_id": self.ticket_id,
            "mandate": self.mandate.to_json(),
            "class": self.ticket_class,
            "raised_at": fidu1.format_instant(self.raised_at),
            "not_after": fidu1.format_instant(self.not_after),
            "observed": self.observed.to_json(),
            "evidence": self.evidence.to_json(),
            "options": [opt.to_json() for opt in self.options],
            "agent": {"statement": self.statement},
        }

    @classmethod
    def _read(cls, fields: Fields) -> dict[str, Any]:
        ticket_class = fields.choice("class", TICKET_CLASSES)
        agent = fields.object("agent", ("statement",))
        options = [TicketOption.read(item, path) for path, item in fields.array("options", 16)]
        if not options:
            raise MalformedError("range", fields.path("options"), "offers at least one option")
        return {
            "ticket_id": fields.uuid("ticket_id"),
            "mandate": MandateRef.read(
                fields.object("mandate", ("mandate_id", "version", "digest"))
            ),
            "ticket_class": ticket_class,
            "raised_at": fields.instant("raised_at"),
            "not_after": fields.instant("not_after"),
            "observed": Observed.read(fields.object("observed", ("ledger", "log"))),
            "evidence": _read_evidence(
                ticket_class, fields.raw("evidence"), fields.path("evidence")
            ),
            "options": tuple(options),
            "statement": (
                None
                if agent.is_null("statement")
                else agent.text("statement", fidu1.TEXT, "an agent statement (at most 512 chars)")
            ),
        }

    def _agree(self) -> None:
        if self.not_after <= self.raised_at:
            raise MalformedError("agreement", "$.not_after", "comes after raised_at")
        allowed = ACTIONS_BY_CLASS[self.ticket_class]
        actions = [opt.resolution.action for opt in self.options]
        for i, opt in enumerate(self.options):
            where = f"$.options[{i}]"
            if opt.digest != opt.resolution.digest:
                raise MalformedError(
                    "agreement",
                    f"{where}.digest",
                    "does not equal the SHA-256 of its resolution's canonical bytes",
                )
            if opt.resolution.action not in allowed:
                raise MalformedError(
                    "agreement",
                    f"{where}.resolution.action",
                    f"{opt.resolution.action!r} is no resolution of a {self.ticket_class} ticket",
                )
            self._agree_option(opt.resolution, f"{where}.resolution")
        if len(set(actions)) != len(actions):
            raise MalformedError("set", "$.options", "holds two options for the same action")
        order = [allowed.index(a) for a in actions]
        if order != sorted(order):
            raise MalformedError(
                "set", "$.options", f"is out of order: options follow {', '.join(allowed)}"
            )
        if isinstance(self.evidence, AmendEvidence):
            if self.evidence.proposed.version != self.mandate.version + 1:
                raise MalformedError(
                    "agreement",
                    "$.evidence.proposed.version",
                    f"is not version {self.mandate.version + 1} of the mandate",
                )
            if self.evidence.proposed.digest == self.mandate.digest:
                raise MalformedError(
                    "agreement",
                    "$.evidence.proposed.digest",
                    "names the current mandate digest",
                )

    def _agree_option(self, res: Resolution, path: str) -> None:
        ev = self.evidence
        if isinstance(ev, ExtensionEvidence):
            if res.quote != ev.quote.quote_id:
                raise MalformedError("agreement", f"{path}.quote", "names another quote")
            if res.action == "grant" and res.amount != ev.quote.requested:
                raise MalformedError(
                    "agreement", f"{path}.amount", "the option offers the quote's requested amount"
                )
        elif isinstance(ev, RefusedEvidence):
            if res.proposal != ev.proposal:
                raise MalformedError("agreement", f"{path}.proposal", "names another proposal")
        elif isinstance(ev, HeldEvidence):
            expected = tuple(MessageTarget(m.message_id, m.head) for m in ev.messages)
            if res.action in ("release", "cancel") and res.messages != expected:
                raise MalformedError(
                    "agreement", f"{path}.messages", "the option names every held message"
                )
            expected_reset = None if ev.halt is None else ev.halt.scope
            if res.action == "release" and res.reset != expected_reset:
                raise MalformedError("agreement", f"{path}.reset", "disagrees with $.evidence.halt")
            if res.action == "reset" and (ev.halt is None or res.scope != ev.halt.scope):
                raise MalformedError("agreement", f"{path}.scope", "disagrees with $.evidence.halt")
        elif isinstance(ev, DeadEvidence):
            expected = (MessageTarget(ev.message.message_id, ev.message.head),)
            if res.messages != expected:
                raise MalformedError(
                    "agreement", f"{path}.messages", "names another message or head"
                )
        elif isinstance(ev, UndoEvidence):
            if res.plan_id != ev.plan_id:
                raise MalformedError("agreement", f"{path}.plan_id", "names another plan")
            if res.action in ("compensate", "compensate_late"):
                expected = tuple(MessageTarget(m.message_id, m.head) for m in ev.messages)
                if res.messages != expected:
                    raise MalformedError(
                        "agreement",
                        f"{path}.messages",
                        "the option names every compensable message",
                    )
                if ev.late and res.action == "compensate":
                    raise MalformedError(
                        "agreement",
                        f"{path}.action",
                        "the undo is late: it requires compensate_late",
                    )
                if not ev.late and res.action == "compensate_late":
                    raise MalformedError(
                        "agreement",
                        f"{path}.action",
                        "the undo is within its deadline: its option is compensate",
                    )
        elif isinstance(ev, AmendEvidence):
            if res.mandate != ev.proposed.digest:
                raise MalformedError(
                    "agreement", f"{path}.mandate", "names another proposed mandate"
                )


def quorum_for_resolution(mandate: Mandate, ticket: Ticket, resolution: Resolution) -> int:
    """The quorum ``mandate`` requires for ``resolution`` on ``ticket`` (§7.2, §7.3)."""
    roster = mandate.roster
    action = resolution.action
    if action in ("reject", "cancel", "decline"):
        return roster.quorum("reduce")
    if action == "release":
        if resolution.reset is None:
            return roster.quorum("release")
        assert isinstance(ticket.evidence, HeldEvidence) and ticket.evidence.halt is not None
        halt_class = "reset" if ticket.evidence.halt.spent_out else "reset_safety"
        return max(roster.quorum("release"), roster.quorum(halt_class))
    if action == "reset":
        if isinstance(ticket.evidence, HeldEvidence) and ticket.evidence.halt is not None:
            return roster.quorum("reset" if ticket.evidence.halt.spent_out else "reset_safety")
        return roster.quorum("reset_safety")
    return roster.quorum(action)

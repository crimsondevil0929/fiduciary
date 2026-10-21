"""The warrant: an execution authorization formed from a quorum or autonomous rule (§4.6)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar, Final

from fiduciary import fidu1
from fiduciary.documents._base import Document
from fiduciary.documents.approval import Approval
from fiduciary.documents.ticket import (
    AUTO_ACTIONS,
    ExtensionEvidence,
    HeldEvidence,
    MessageTarget,
    RefusedEvidence,
    Resolution,
    Ticket,
    UndoEvidence,
)
from fiduciary.exceptions import MalformedError
from fiduciary.fidu1 import Fields, Signature

__all__ = [
    "MAX_OUTBOX_TARGETS",
    "RuleBasis",
    "TicketRef",
    "Warrant",
    "WarrantBasis",
    "execution_steps",
    "warrant_not_after",
]

MAX_OUTBOX_TARGETS: Final = 8
"""The most outbox targets one ILOK1 operator intent names until U-2 (F3-9):
a ``compensate`` intent of 8 targets is the largest that AWS KMS can sign."""


@dataclass(frozen=True, slots=True)
class TicketRef:
    ticket_id: str
    digest: str

    def to_json(self) -> dict[str, str]:
        return {"ticket_id": self.ticket_id, "digest": self.digest}

    @classmethod
    def read(cls, fields: Fields) -> TicketRef:
        return cls(
            ticket_id=fields.uuid("ticket_id"),
            digest=fields.hash("digest"),
        )


@dataclass(frozen=True, slots=True)
class RuleBasis:
    mandate: str
    rule_id: str

    def to_json(self) -> dict[str, str]:
        return {"mandate": self.mandate, "id": self.rule_id}

    @classmethod
    def read(cls, fields: Fields) -> RuleBasis:
        return cls(
            mandate=fields.hash("mandate"),
            rule_id=fields.name("id"),
        )


@dataclass(frozen=True, slots=True)
class WarrantBasis:
    approvals: tuple[str, ...] = ()
    rule: RuleBasis | None = None

    def to_json(self) -> dict[str, Any]:
        if self.rule is not None:
            return {"rule": self.rule.to_json()}
        return {"approvals": list(self.approvals)}

    @classmethod
    def read(cls, raw: object, path: str) -> WarrantBasis:
        if not isinstance(raw, dict):
            raise MalformedError("type", path, "must be an object")
        has_approvals = "approvals" in raw
        has_rule = "rule" in raw
        if has_approvals == has_rule:
            raise MalformedError(
                "fields",
                path,
                "basis has either approvals or rule, not both",
            )
        if has_approvals:
            fields = Fields(raw, path, ("approvals",))
            approvals = fields.strings("approvals", fidu1.HASH, "a sha256:<64 hex> digest")
            if not approvals:
                raise MalformedError(
                    "range",
                    fields.path("approvals"),
                    "names at least one approval",
                )
            return cls(approvals=approvals)
        fields = Fields(raw, path, ("rule",))
        rule = RuleBasis.read(fields.object("rule", ("mandate", "id")))
        return cls(rule=rule)


@dataclass(frozen=True, slots=True, kw_only=True)
class Warrant(Document):
    """Execution authorization formed from a quorum or autonomous rule (§4.6, F4-24, F4-25)."""

    TYPE: ClassVar[str] = "warrant"
    FIELDS: ClassVar[tuple[str, ...]] = (
        "warrant_id",
        "ticket",
        "resolution",
        "resolution_digest",
        "basis",
        "formed_at",
        "not_after",
    )

    warrant_id: str
    ticket: TicketRef
    resolution: Resolution
    resolution_digest: str
    basis: WarrantBasis
    formed_at: datetime
    not_after: datetime
    signature: Signature | None = None

    def _body(self) -> dict[str, Any]:
        return {
            "warrant_id": self.warrant_id,
            "ticket": self.ticket.to_json(),
            "resolution": self.resolution.to_json(),
            "resolution_digest": self.resolution_digest,
            "basis": self.basis.to_json(),
            "formed_at": fidu1.format_instant(self.formed_at),
            "not_after": fidu1.format_instant(self.not_after),
        }

    @classmethod
    def _read(cls, fields: Fields) -> dict[str, Any]:
        return {
            "warrant_id": fields.uuid("warrant_id"),
            "ticket": TicketRef.read(fields.object("ticket", ("ticket_id", "digest"))),
            "resolution": Resolution.read(fields.raw("resolution"), fields.path("resolution")),
            "resolution_digest": fields.hash("resolution_digest"),
            "basis": WarrantBasis.read(fields.raw("basis"), fields.path("basis")),
            "formed_at": fields.instant("formed_at"),
            "not_after": fields.instant("not_after"),
        }

    def _agree(self) -> None:
        if self.resolution_digest != self.resolution.digest:
            raise MalformedError(
                "agreement",
                "$.resolution_digest",
                "does not equal the SHA-256 of its resolution's canonical bytes",
            )
        if self.not_after <= self.formed_at:
            raise MalformedError("agreement", "$.not_after", "comes after formed_at")
        if self.basis.rule is not None and (self.resolution.action not in AUTO_ACTIONS):
            raise MalformedError(
                "agreement",
                "$.resolution.action",
                f"action {self.resolution.action!r} is never auto-resolvable",
            )


def warrant_not_after(
    ticket: Ticket,
    resolution: Resolution,
    approvals: Sequence[Approval] = (),
) -> datetime:
    """Compute the warrant deadline as the minimum of all bounds (F4-25)."""
    deadlines: list[datetime] = [ticket.not_after]
    for approval in approvals:
        deadlines.append(approval.not_after)
    if isinstance(ticket.evidence, ExtensionEvidence):
        deadlines.append(ticket.evidence.quote.expires_at)
    if isinstance(ticket.evidence, UndoEvidence) and resolution.action == "compensate":
        wanted = {m.message_id for m in resolution.messages}
        for msg in ticket.evidence.messages:
            if msg.message_id in wanted:
                deadlines.append(msg.not_after)
    return min(deadlines)


def _targets(chunk: Sequence[MessageTarget]) -> list[dict[str, str]]:
    return [{"message_id": m.message_id, "head": m.head} for m in chunk]


def _chunks(targets: Sequence[MessageTarget]) -> list[Sequence[MessageTarget]]:
    return [targets[i : i + MAX_OUTBOX_TARGETS] for i in range(0, len(targets), MAX_OUTBOX_TARGETS)]


def execution_steps(warrant: Warrant, ticket: Ticket) -> list[dict[str, Any]]:
    """The steps ``warrant`` executes, in order, each with the precondition
    ``ticket`` observed: what ``execution.intent`` records (§7.5, §7.6).

    Outbox actions are split into intents of at most
    :data:`MAX_OUTBOX_TARGETS` targets (F3-9); a ``release`` past a halt
    resets it first; ``cancel`` and ``requeue`` take one message per intent,
    as interlock's operator does. A pure function of the two documents, so a
    verifier writes the same steps.
    """
    res = warrant.resolution
    action = res.action
    ev = ticket.evidence
    spent = isinstance(ev, HeldEvidence) and ev.halt is not None and ev.halt.spent_out
    steps: list[dict[str, Any]] = []

    def add(kind: str, **fields: Any) -> None:
        steps.append({"step": len(steps), "kind": kind, **fields})

    if action == "accept_repair":
        assert isinstance(ev, RefusedEvidence) and res.proposal is not None
        add("accept_repair", proposal=res.proposal, receipt=ev.receipt.receipt_id)
    elif action in ("release", "reset"):
        scope = res.reset if action == "release" else res.scope
        if scope is not None:
            add("reset", scope=scope, spent_out=spent)
        if action == "release":
            for chunk in _chunks(res.messages):
                add("release", targets=_targets(chunk))
    elif action in ("cancel", "requeue"):
        for target in res.messages:
            add(action, targets=_targets([target]))
    elif action in ("compensate", "compensate_late"):
        assert res.plan_id is not None
        for chunk in _chunks(res.messages):
            add(
                action,
                plan_id=res.plan_id,
                late=action == "compensate_late",
                targets=_targets(chunk),
            )
    elif action in ("grant", "decline"):
        assert isinstance(ev, ExtensionEvidence) and res.quote is not None
        if action == "grant":
            assert res.amount is not None
            amount = format(res.amount.quantize(Decimal("0.00000001")), "f")
            add("grant", quote=res.quote, amount=amount, record_hash=ev.quote.record_hash)
        else:
            add("decline", quote=res.quote, record_hash=ev.quote.record_hash)
    elif action == "amend":
        add("amend", mandate=res.mandate)
    else:
        assert action == "reject"
        add("reject")
    return steps

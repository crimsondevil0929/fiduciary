"""Escalation triggers, deduplication, fatigue bounds, quorum intake and
autonomous resolution (``docs/FIDUCIARY_SPEC.md`` §7.1 to §7.4).

When a mandated session reaches a boundary, :class:`Escalation` reads typed
evidence directly from Interlock and AgentGov (never from the agent's prose)
and raises a service-signed :class:`~fiduciary.documents.Ticket`:

- ``refused``: a plan was refused and :meth:`~interlock.engine.EscrowEngine.repair`
  found an admissible sub-plan;
- ``held``: outbox messages of the lineage are held by the relay;
- ``dead``: an outbox message of the lineage is dead;
- ``undo``: a committed plan has delivered originals that carry compensations;
- ``extension``: :meth:`~interlock.extension.BudgetGuard.authorize` issued a
  signed extension quote;
- ``amend``: a next mandate version is proposed, with its two-way refinement diff.

Two invariants bound ticket creation before anything is written:

- **Deduplication (F7-1):** if an open ticket on the lineage already holds the
  same ``(class, evidence)``, the trigger folds into that open ticket and
  writes no new record.
- **Fatigue bound (F7-2):** if the lineage already has ``escalation.max_open``
  open tickets, the trigger writes nothing and leaves the boundary standing.

When a mandate's ``escalation.auto`` rule matches a newly raised ticket
(§7.4, F7-9 to F7-11), ``ticket.raised`` and ``warrant.formed`` (with
``basis.rule``) are written in one log transaction. Otherwise the ticket stays
open for principal approvals through the intake (§7.3, F7-3 to F7-8).
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from agentgov.exceptions import AgentGovError
from interlock.deliveries import OUTCOMES, OutboxOperations
from interlock.extension import ExtensionRequest
from interlock.recovery import breaker_reason, spent_out
from interlock.repair import Repair
from interlock.types import EffectPlan

from fiduciary import fidu1
from fiduciary.authority import Authority, Decide
from fiduciary.documents import (
    Mandate,
    Record,
    Resolution,
    RuleBasis,
    Ticket,
    TicketOption,
    TicketRef,
    Warrant,
    WarrantBasis,
    warrant_not_after,
)
from fiduciary.documents.ticket import (
    AmendEvidence,
    DeadEvidence,
    DeadMessage,
    DiffItem,
    DroppedItem,
    Evidence,
    ExtensionEvidence,
    HaltInfo,
    HeldEvidence,
    HeldMessage,
    LedgerObserved,
    LogObserved,
    MessageTarget,
    Observed,
    ProgressEvidence,
    ProposedMandate,
    QuoteEvidence,
    ReceiptCheckpoint,
    ReceiptRef,
    RefusedEvidence,
    UndoEvidence,
    UndoMessage,
    resolution_quorum,
)
from fiduciary.exceptions import AuthorityError, FiduciaryError
from fiduciary.log import Log
from fiduciary.refines import refines
from fiduciary.store import State, TicketRow

if TYPE_CHECKING:
    from agentgov import BudgetManager
    from agentgov.receipts import ActionReceipt

__all__ = ["Escalation", "EscalationError", "clip_statement"]

_CONTROLS = re.compile(r"[\x00-\x1f\x7f]+")


class EscalationError(FiduciaryError):
    """A trigger whose evidence cannot be read, or that has nothing to escalate."""


def clip_statement(statement: str | None) -> str | None:
    """Clip ``agent.statement`` to 512 printable characters (F4-18)."""
    if statement is None:
        return None
    cleaned = _CONTROLS.sub(" ", statement).strip()[:512]
    return cleaned or None


class Escalation:
    """Raises tickets from Interlock and AgentGov evidence, processes principal
    intake, expires overdue tickets, and forms autonomous or quorum warrants."""

    def __init__(
        self,
        authority: Authority,
        governor: BudgetManager,
        *,
        checkpoint: Callable[[str], None] | None = None,
    ) -> None:
        self._authority = authority
        self._governor = governor
        self._checkpoint = checkpoint or (lambda _: None)
        self.proposals: dict[str, EffectPlan] = {}

    @property
    def authority(self) -> Authority:
        return self._authority

    @property
    def log(self) -> Log:
        return self._authority.log

    @property
    def decide(self) -> Decide:
        return self._authority.decide

    @property
    def governor(self) -> BudgetManager:
        return self._governor

    # -- reading tickets and warrants ------------------------------------------

    def ticket(self, ticket_id_or_digest: str, state: State | None = None) -> Ticket:
        """Load a raised :class:`Ticket` by ``ticket_id`` or ``digest``."""
        state = state or self.log.state()
        row = state.tickets.get(ticket_id_or_digest)
        digest = row.digest if row is not None else ticket_id_or_digest
        if digest not in state.tickets_by_digest:
            raise AuthorityError(f"no raised ticket matches {ticket_id_or_digest!r}")
        return self.decide.load(digest, Ticket)

    def warrant(self, warrant_id_or_digest: str, state: State | None = None) -> Warrant:
        """Load a formed :class:`Warrant` by ``warrant_id`` or ``digest``."""
        state = state or self.log.state()
        digest = state.warrants_by_id.get(warrant_id_or_digest, warrant_id_or_digest)
        if digest not in state.warrants:
            raise AuthorityError(f"no formed warrant matches {warrant_id_or_digest!r}")
        return self.decide.load(digest, Warrant)

    def warrant_for_ticket(
        self, ticket_id_or_digest: str, state: State | None = None
    ) -> Warrant | None:
        """The warrant formed for ``ticket_id_or_digest``, or ``None``."""
        state = state or self.log.state()
        row = state.tickets.get(ticket_id_or_digest)
        ticket_id = (
            row.ticket_id if row is not None else state.tickets_by_digest.get(ticket_id_or_digest)
        )
        if ticket_id is None:
            return None
        for event, detail, _ in state.ticket_events.get(ticket_id, ()):
            if event == "warranted":
                return self.warrant(detail, state)
        return None

    # -- intake, narrowings and expiry -----------------------------------------

    def propose_resolution(self, resolution: Resolution) -> str:
        """Store a narrowed :class:`Resolution` (F4-20) so approvals may name its digest."""
        return self._authority.propose_resolution(resolution)

    def expire_tickets(self, mandate_id: str | None = None) -> list[Record]:
        """Lapse every open ticket whose ``not_after`` is at or before ``now``,
        and every warranted one whose warrant expired with no execution
        (F1-6, F7-7). Nothing due, no lock: what is due is found first, and
        found again under the writer's lock."""
        if not self._expired_entries(self.log.state(), self.log.now(), mandate_id):
            return []
        with self.log.store.transaction():
            now = self.log.now()
            state = self.log.state()
            entries = self._expired_entries(state, now, mandate_id)
            if not entries:
                return []
            return self.log.append_all(entries, issued_at=now)

    def _expired_entries(
        self, state: State, now: datetime, mandate_id: str | None = None
    ) -> list[tuple[str, dict[str, object]]]:
        entries: list[tuple[str, dict[str, object]]] = []
        for open_row in state.open_tickets(mandate_id):
            tk = self.ticket(open_row.digest, state)
            if now >= tk.not_after:
                body = self.decide.lapse_ticket(state, tk.digest, now)
                entries.append(("ticket.lapsed", body))
        # A warrant no process executed by its deadline never will be: its
        # ticket lapses, as silence does (F1-6).
        stale: list[TicketRow] = []
        for formed in state.unexecuted_warrants():
            row = state.tickets.get(formed.ticket_id)
            if (
                row is None
                or (mandate_id is not None and row.mandate_id != mandate_id)
                or state.ticket_status(row.ticket_id) != "warranted"
                or state.warrant_of(row.ticket_id) != formed.digest
            ):
                continue
            if now >= self.warrant(formed.digest, state).not_after:
                stale.append(row)
        for row in sorted(stale, key=lambda r: r.seq):
            entries.append(("ticket.lapsed", self.decide.lapse_ticket(state, row.digest, now)))
        return entries

    def process_intake(self) -> list[Record]:
        """Lapse overdue tickets first, then judge every unprocessed intake submission."""
        expired = self.expire_tickets()
        processed = self._authority.process_intake()
        return [*expired, *processed]

    # -- raising tickets and autonomous resolution -----------------------------

    def _observed(self) -> Observed:
        self._governor.refresh()
        ledger = self._governor.ledger
        ledger_seq = len(ledger)
        ledger_head = ledger.head_hash
        if ledger_seq < 1:
            raise EscalationError("the AgentGov ledger has no entries")
        log_seq, log_head = self.log.store.head(self.log.log_id)
        if log_seq < 1:
            raise EscalationError("the FIDU1 log has no records")
        return Observed(
            ledger=LedgerObserved(sequence=ledger_seq, head=ledger_head),
            log=LogObserved(seq=log_seq, hash=log_head),
        )

    def raise_ticket(
        self,
        mandate_id: str,
        ticket_class: str,
        evidence: Evidence,
        resolutions: Sequence[Resolution],
        *,
        statement: str | None = None,
        ticket_id: str | None = None,
        not_after: datetime | None = None,
    ) -> tuple[Ticket | None, Warrant | None]:
        """Raise a ticket for ``(mandate_id, ticket_class, evidence)``, or fold
        it into an identical open ticket (F7-1), or drop it when
        ``escalation.max_open`` is reached (F7-2).

        When an ``escalation.auto`` rule of the mandate in force matches the
        ticket (§7.4), ``ticket.raised`` and ``warrant.formed`` are written in
        the same log transaction and ``(ticket, warrant)`` is returned.
        """
        self.expire_tickets(mandate_id)
        with self.log.store.transaction():
            now = self.log.now()
            state = self.log.state()
            if not state.in_force(mandate_id):
                raise AuthorityError(
                    f"lineage {mandate_id} is {state.status(mandate_id)}: it cannot raise a ticket"
                )
            mandate = self.decide.head_mandate(state, mandate_id)
            ev_digest = fidu1.digest(evidence.to_json())
            open_rows = state.open_tickets(mandate_id)
            for open_row in open_rows:
                existing = self.ticket(open_row.digest, state)
                if (
                    existing.ticket_class == ticket_class
                    and fidu1.digest(existing.evidence.to_json()) == ev_digest
                ):
                    return existing, None
            if len(open_rows) >= mandate.escalation.max_open:
                return None, None
            observed = self._observed()
            deadline = not_after or (now + timedelta(seconds=mandate.escalation.ttl_seconds))
            options = tuple(
                TicketOption.make(res, resolution_quorum(mandate, evidence, res))
                for res in resolutions
            )
            ticket = Ticket(
                ticket_id=ticket_id or str(uuid.uuid4()),
                mandate=mandate.ref,
                ticket_class=ticket_class,
                raised_at=now,
                not_after=deadline,
                observed=observed,
                evidence=evidence,
                options=options,
                statement=clip_statement(statement),
            ).signed(self.log.signer)
            self.log.put(ticket)
            raised_body = self.decide.raise_ticket(state, ticket.digest, now)
            entries: list[tuple[str, dict[str, object]]] = [("ticket.raised", raised_body)]
            docs: list[Ticket | Warrant] = [ticket]
            # A rule's warrant is taken from the state after the ticket's record.
            raised = state.after("ticket.raised", raised_body, issued_at=now)
            warrant = self._auto_warrant(raised, mandate, ticket, now)
            if warrant is not None:
                self.log.put(warrant)
                warrant_body = self.decide.form_warrant(raised, warrant.digest, now)
                entries.append(("warrant.formed", warrant_body))
                docs.append(warrant)
            self.log.append_all(entries, documents=docs, issued_at=now)
        # The checkpoints follow the commit: what a crash there leaves is written.
        self._checkpoint("raised")
        if warrant is not None:
            self._checkpoint("formed")
        return ticket, warrant

    def _auto_warrant(
        self, raised: State, mandate: Mandate, ticket: Ticket, now: datetime
    ) -> Warrant | None:
        for rule in mandate.escalation.auto:
            if rule.ticket_class != ticket.ticket_class:
                continue
            option = next(
                (opt for opt in ticket.options if opt.resolution.action == rule.resolution),
                None,
            )
            if option is None:
                continue
            if not self.decide.rule_matches(raised, mandate, ticket, rule, option.resolution):
                continue
            not_after = warrant_not_after(ticket, option.resolution, ())
            if now >= not_after:
                continue
            next_seq = raised.head_seq + 1
            warrant_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_OID,
                    f"fidu1:warrant:{ticket.digest}:{option.resolution.digest}:{next_seq}",
                )
            )
            warrant = Warrant(
                warrant_id=warrant_id,
                ticket=TicketRef(ticket.ticket_id, ticket.digest),
                resolution=option.resolution,
                resolution_digest=option.resolution.digest,
                basis=WarrantBasis(rule=RuleBasis(mandate=mandate.digest, rule_id=rule.rule_id)),
                formed_at=now,
                not_after=not_after,
            ).signed(self.log.signer)
            return warrant
        return None

    # -- the six trigger classes (§7.1) ----------------------------------------

    def raise_refused(
        self,
        mandate_id: str,
        repair: Repair,
        *,
        receipt: ActionReceipt | None = None,
        statement: str | None = None,
        ticket_id: str | None = None,
    ) -> tuple[Ticket | None, Warrant | None]:
        """Raise a ``refused`` ticket from an :class:`~interlock.repair.Repair`
        that found a non-empty admissible sub-plan (§7.1)."""
        if repair.proposal is None or repair.already_admissible:
            raise EscalationError("the refused plan has no repair proposal to escalate")
        rcpt = receipt or repair.receipt
        if rcpt is None:
            raise EscalationError("a refused ticket requires the refused plan's ARC1 receipt")
        if repair.whole is not None and repair.whole.verdict.blocking:
            blocked = sorted({v.invariant for v in repair.whole.verdict.blocking})
        else:
            blocked = sorted({inv for d in repair.dropped for inv in d.invariants})
        if not blocked:
            raise EscalationError("the refused plan names no blocking checker")
        kept = tuple(sorted(str(e) for e in repair.kept))
        dropped = tuple(
            sorted(
                (DroppedItem(effect_id=str(d.effect_id), cause=d.cause) for d in repair.dropped),
                key=lambda item: item.effect_id,
            )
        )
        if not kept or not dropped:
            raise EscalationError("a repair proposal must keep at least one step and drop one")
        proposal = repair.proposal.content_hash()
        self.proposals[proposal] = repair.proposal
        receipt_digest = hashlib.sha256(rcpt.canonical()).hexdigest()
        evidence = RefusedEvidence(
            receipt=ReceiptRef(receipt_id=rcpt.receipt_id, digest=receipt_digest),
            blocked_by=tuple(blocked),
            proposal=proposal,
            kept=kept,
            dropped=dropped,
            exhaustive=repair.exhaustive,
            trials=repair.trials,
        )
        resolutions = (
            Resolution(action="accept_repair", proposal=proposal),
            Resolution(action="reject", proposal=proposal),
        )
        return self.raise_ticket(
            mandate_id,
            "refused",
            evidence,
            resolutions,
            statement=statement,
            ticket_id=ticket_id,
        )

    def raise_held(
        self,
        mandate_id: str,
        outbox: OutboxOperations,
        *,
        scope: str | None = None,
        statement: str | None = None,
        ticket_id: str | None = None,
    ) -> tuple[Ticket | None, Warrant | None]:
        """Raise a ``held`` ticket from held outbox messages and the halting scope (§7.1)."""
        state = self.log.state()
        mandate = self.decide.head_mandate(state, mandate_id)
        target_scope = scope or mandate.subject.scope
        held_ids = outbox.held(target_scope)
        if not held_ids:
            raise EscalationError(f"scope {target_scope!r} has no held outbox messages")
        logged, _ = outbox.snapshot(held_ids)
        if not logged:
            raise EscalationError(f"scope {target_scope!r} has no held outbox messages")
        messages = tuple(
            sorted(
                (
                    HeldMessage(
                        message_id=str(m.message_id),
                        head=m.log_head,
                        sink=m.sink,
                        operation=m.operation,
                        payload_hash=m.payload_hash,
                    )
                    for m in logged
                    if m.state == "held"
                ),
                key=lambda item: item.message_id,
            )
        )
        if not messages:
            raise EscalationError(f"scope {target_scope!r} has no held outbox messages")
        self._governor.refresh()
        halted = self._find_halted_scope(target_scope)
        halt = None
        if halted is not None:
            reason = breaker_reason(self._governor, halted) or "halted"
            clean_reason = clip_statement(reason) or "halted"
            halt = HaltInfo(
                scope=halted,
                reason=clean_reason,
                spent_out=spent_out(reason),
            )
        evidence = HeldEvidence(messages=messages, halt=halt)
        targets = tuple(MessageTarget(m.message_id, m.head) for m in messages)
        if halt is None:
            resolutions: tuple[Resolution, ...] = (
                Resolution(action="release", messages=targets, reset=None),
                Resolution(action="cancel", messages=targets),
            )
        else:
            resolutions = (
                Resolution(action="release", messages=targets, reset=halt.scope),
                Resolution(action="reset", scope=halt.scope),
                Resolution(action="cancel", messages=targets),
            )
        return self.raise_ticket(
            mandate_id,
            "held",
            evidence,
            resolutions,
            statement=statement,
            ticket_id=ticket_id,
        )

    def _find_halted_scope(self, scope: str) -> str | None:
        try:
            path = (scope, *self._governor.ancestry(scope))
        except AgentGovError:
            path = (scope,)
        for candidate in path:
            try:
                found = self._governor.halted_by(candidate)
            except AgentGovError:
                continue
            if found is not None:
                return found
        return None

    def raise_dead(
        self,
        mandate_id: str,
        outbox: OutboxOperations,
        message_id: uuid.UUID | str,
        *,
        statement: str | None = None,
        ticket_id: str | None = None,
    ) -> tuple[Ticket | None, Warrant | None]:
        """Raise a ``dead`` ticket for one dead outbox message (§7.1)."""
        mid = message_id if isinstance(message_id, uuid.UUID) else uuid.UUID(message_id)
        logged, events = outbox.snapshot([mid])
        if not logged:
            raise EscalationError(f"outbox message {mid} does not exist")
        msg = logged[0]
        if msg.state != "dead":
            raise EscalationError(f"outbox message {mid} is {msg.state!r}, not 'dead'")
        msg_events = [e for e in events if e.message_id == mid]
        outcomes = [e.event for e in msg_events if e.event in OUTCOMES]
        outcome = outcomes[-1] if outcomes else (msg_events[-1].event if msg_events else "expired")
        log_requeues = sum(1 for e in msg_events if e.event == "requeued")
        state = self.log.state()
        prior_requeues = max(
            log_requeues,
            self.decide._requeues_so_far(state, str(mid)),
        )
        dead_msg = DeadMessage(
            message_id=str(mid),
            head=msg.log_head,
            attempts=max(1, msg.attempts),
            requeues=prior_requeues,
            outcome=outcome,
        )
        evidence = DeadEvidence(message=dead_msg)
        target = (MessageTarget(dead_msg.message_id, dead_msg.head),)
        resolutions = (
            Resolution(action="requeue", messages=target),
            Resolution(action="cancel", messages=target),
        )
        return self.raise_ticket(
            mandate_id,
            "dead",
            evidence,
            resolutions,
            statement=statement,
            ticket_id=ticket_id,
        )

    def raise_undo(
        self,
        mandate_id: str,
        outbox: OutboxOperations,
        plan_id: str,
        *,
        statement: str | None = None,
        ticket_id: str | None = None,
    ) -> tuple[Ticket | None, Warrant | None]:
        """Raise an ``undo`` ticket for the compensable delivered messages of ``plan_id`` (§7.1)."""
        compensables = outbox.compensables(plan_id)
        eligible = [
            c
            for c in compensables
            if c.state == "delivered" and c.compensation is not None and c.compensated_by is None
        ]
        if not eligible:
            raise EscalationError(f"plan {plan_id!r} has no compensable delivered messages")
        now = self.log.now()
        late = any(now >= c.not_after for c in eligible)
        messages = tuple(
            sorted(
                (
                    UndoMessage(
                        message_id=str(c.message_id),
                        head=c.log_head,
                        not_after=c.not_after,
                        operation=str(c.compensation["operation"]),
                    )
                    for c in eligible
                    if c.compensation is not None
                ),
                key=lambda item: item.message_id,
            )
        )
        evidence = UndoEvidence(plan_id=plan_id, messages=messages, late=late)
        targets = tuple(MessageTarget(m.message_id, m.head) for m in messages)
        action = "compensate_late" if late else "compensate"
        resolutions = (
            Resolution(action=action, plan_id=plan_id, messages=targets),
            Resolution(action="reject", plan_id=plan_id),
        )
        return self.raise_ticket(
            mandate_id,
            "undo",
            evidence,
            resolutions,
            statement=statement,
            ticket_id=ticket_id,
        )

    def raise_extension(
        self,
        mandate_id: str,
        request: ExtensionRequest,
        *,
        statement: str | None = None,
        ticket_id: str | None = None,
    ) -> tuple[Ticket | None, Warrant | None]:
        """Raise an ``extension`` ticket from an
        :class:`~interlock.extension.ExtensionRequest` (§7.1)."""
        if request.record is None:
            raise EscalationError(f"extension quote {request.quote_id!r} has no signed record")
        quote = QuoteEvidence(
            quote_id=request.quote_id,
            record_log=request.record.log,
            record_seq=request.record.seq,
            record_hash=request.record.record_hash,
            needed=request.needed,
            requested=request.requested,
            spent=request.spend.spent,
            available=request.spend.available,
            estimate=request.estimate.method,
            expires_at=request.expires_at,
        )
        receipts = None
        if request.progress.checkpoint is not None and request.progress.log is not None:
            tree_size, root = request.progress.checkpoint
            if tree_size >= 1:
                receipts = ReceiptCheckpoint(
                    log=request.progress.log,
                    tree_size=tree_size,
                    root=root,
                )
        progress = ProgressEvidence(
            committed=request.progress.committed,
            rows=request.progress.rows,
            receipts=receipts,
        )
        evidence = ExtensionEvidence(quote=quote, progress=progress)
        resolutions = (
            Resolution(action="grant", quote=request.quote_id, amount=request.requested),
            Resolution(action="decline", quote=request.quote_id),
        )
        return self.raise_ticket(
            mandate_id,
            "extension",
            evidence,
            resolutions,
            statement=statement,
            ticket_id=ticket_id,
        )

    def raise_amend(
        self,
        mandate_id: str,
        proposed: Mandate,
        *,
        statement: str | None = None,
        ticket_id: str | None = None,
    ) -> tuple[Ticket | None, Warrant | None]:
        """Raise an ``amend`` ticket for ``proposed``, computing the two-way
        refinement diff (§7.1)."""
        if proposed.signature is None:
            raise EscalationError("a proposed mandate amendment must be signed")
        state = self.log.state()
        current = self.decide.head_mandate(state, mandate_id)
        if (
            proposed.mandate_id != current.mandate_id
            or proposed.version != current.version + 1
            or proposed.prev != current.digest
        ):
            raise EscalationError(
                f"proposed mandate {proposed.digest[:16]} is not version {current.version + 1} "
                f"of {current.mandate_id}, naming {current.digest[:16]}"
            )
        self.log.put(proposed)
        wider = tuple(
            sorted(
                (DiffItem(p.path, p.message) for p in refines(proposed, current)),
                key=lambda item: (item.path, item.message),
            )
        )
        narrower = tuple(
            sorted(
                (DiffItem(p.path, p.message) for p in refines(current, proposed)),
                key=lambda item: (item.path, item.message),
            )
        )
        evidence = AmendEvidence(
            proposed=ProposedMandate(version=proposed.version, digest=proposed.digest),
            wider=wider,
            narrower=narrower,
        )
        resolutions = (
            Resolution(action="amend", mandate=proposed.digest),
            Resolution(action="reject", mandate=proposed.digest),
        )
        return self.raise_ticket(
            mandate_id,
            "amend",
            evidence,
            resolutions,
            statement=statement,
            ticket_id=ticket_id,
        )

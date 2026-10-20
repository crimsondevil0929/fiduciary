"""Two-phase warrant execution and deterministic crash recovery
(``docs/FIDUCIARY_SPEC.md`` §3.5 F3-9, §7.5, §7.6, §10.3, §10.4).

Every formed :class:`~fiduciary.documents.Warrant` executes in two phases:

1. ``execution.intent`` is appended to the FIDU1 log before any external system
   is touched and before the warrant's ``not_after`` (F7-12). It records the
   warrant's digest, its ordered steps (chunked to at most 8 outbox targets per
   step for U-2 / F3-9), and each step's ticket-observed precondition.
2. Each step runs against Interlock or AgentGov, embedding
   ``FIDU1 warrant <digest>`` in the target system's signed or hashed record
   (§7.6, F2-2).
3. ``execution.applied`` or ``execution.refused`` is appended to the FIDU1 log.

At startup and before every execution, :meth:`Executor.resolve` reconciles any
``execution.intent`` that has no outcome by inspecting the four target systems
(ILOK1 operator records, AgentGov ledger memos, ILOK1 extension records, and
the lineage's escrow chain) and recording ``execution.applied`` or
``execution.abandoned`` without guessing (F7-13, F10-9 to F10-11).
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from agentgov.exceptions import AgentGovError
from agentgov.receipts.kms_aws import AwsKmsSigner
from agentgov.receipts.signing import Signer
from interlock.chain import EscrowChain, RecordType
from interlock.deliveries import OutboxOperations
from interlock.exceptions import (
    ExtensionError,
    InterlockError,
    OutboundRequestError,
)
from interlock.extension import BudgetGuard, ExtensionRequest
from interlock.operators import Operator, OperatorLog, OperatorRefusedError
from interlock.records import Keyring, RecordLog, SignedRecord
from interlock.recovery import breaker_reason, spent_out
from interlock.types import EffectPlan

from fiduciary import fidu1
from fiduciary.authority import Authority, Decide
from fiduciary.binding import Binder
from fiduciary.documents import Mandate, Record, Ticket, Warrant, loads
from fiduciary.documents.ticket import (
    ExtensionEvidence,
    HeldEvidence,
    MessageTarget,
    RefusedEvidence,
)
from fiduciary.escalation import Escalation
from fiduciary.exceptions import AuthorityError, FiduciaryError
from fiduciary.log import Log
from fiduciary.store import State

if TYPE_CHECKING:
    from agentgov import BudgetManager

__all__ = [
    "MAX_OUTBOX_TARGETS",
    "ExecutionError",
    "Executor",
    "check_ilok1_signer",
]

MAX_OUTBOX_TARGETS: Final = 8
"""Maximum outbox targets per ILOK1 operator intent before U-2 lands (F3-9)."""


class ExecutionError(FiduciaryError):
    """A warrant or executor configuration that cannot run."""


def check_ilok1_signer(signer: Signer | None, *, role: str = "executor") -> None:
    """Refuse an :class:`AwsKmsSigner` for an ILOK1 whole-record log (F3-9, U-2)."""
    if isinstance(signer, AwsKmsSigner):
        raise ExecutionError(
            f"the {role} key may not be an AWS KMS signer before U-2 (F3-9): "
            f"ILOK1 signs whole records, which can exceed KMS's 4,096-byte RAW limit"
        )


def _chunks(targets: Sequence[MessageTarget]) -> list[tuple[MessageTarget, ...]]:
    return [
        tuple(targets[i : i + MAX_OUTBOX_TARGETS])
        for i in range(0, len(targets), MAX_OUTBOX_TARGETS)
    ]


def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.00000001")), "f")


class Executor:
    """Executes warrants in two phases and resolves interrupted intents (§7.5, §7.6)."""

    def __init__(
        self,
        binder: Binder,
        *,
        escalation: Escalation | None = None,
        outbox: OutboxOperations | None = None,
        operator_log: OperatorLog | Path | str | None = None,
        executor_signer: Signer | None = None,
        operator_keyring: Keyring | None = None,
        guard: BudgetGuard | None = None,
        runtime_records: RecordLog | None = None,
        runtime_signer: Signer | None = None,
        checkpoint: Callable[[str], None] | None = None,
    ) -> None:
        check_ilok1_signer(executor_signer, role="executor")
        check_ilok1_signer(runtime_signer, role="runtime")
        if isinstance(operator_log, OperatorLog):
            check_ilok1_signer(getattr(operator_log, "_signer", None), role="executor")
        if runtime_records is not None:
            check_ilok1_signer(getattr(runtime_records, "_signer", None), role="runtime")
        if guard is not None and runtime_records is None:
            runtime_records = getattr(guard, "_records", None)
            if runtime_records is not None:
                check_ilok1_signer(getattr(runtime_records, "_signer", None), role="runtime")

        self._binder = binder
        self._escalation = escalation
        self._outbox = outbox
        self._operator_log = operator_log
        self._executor_signer = executor_signer
        self._operator_keyring = operator_keyring
        self._guard = guard
        self._runtime_records = runtime_records
        self._checkpoint = checkpoint or (lambda _: None)
        self._proposals: dict[str, EffectPlan] = {}

    @property
    def binder(self) -> Binder:
        return self._binder

    @property
    def authority(self) -> Authority:
        return self._binder._authority

    @property
    def log(self) -> Log:
        return self._binder.log

    @property
    def decide(self) -> Decide:
        return self._binder.decide

    @property
    def governor(self) -> BudgetManager:
        return self._binder._governor

    def register_proposal(self, plan: EffectPlan) -> str:
        """Register an :class:`EffectPlan` repair proposal by its content hash."""
        digest = plan.content_hash()
        self._proposals[digest] = plan
        return digest

    def _find_proposal(self, digest: str) -> EffectPlan | None:
        if digest in self._proposals:
            return self._proposals[digest]
        if self._escalation is not None and digest in self._escalation.proposals:
            return self._escalation.proposals[digest]
        return None

    # -- loading documents -----------------------------------------------------

    def _warrant(self, warrant_or_digest: Warrant | str, state: State) -> Warrant:
        if isinstance(warrant_or_digest, Warrant):
            return warrant_or_digest
        digest = state.warrants_by_id.get(warrant_or_digest, warrant_or_digest)
        raw = self.log.store.document(digest)
        if raw is None or raw[0] != "warrant":
            raise AuthorityError(f"no formed warrant matches {warrant_or_digest!r}")
        doc = loads(raw[1])
        assert isinstance(doc, Warrant)
        return doc

    def _ticket(self, digest: str) -> Ticket:
        raw = self.log.store.document(digest)
        if raw is None or raw[0] != "ticket":
            raise AuthorityError(f"no ticket has digest {digest[:16]}")
        doc = loads(raw[1])
        assert isinstance(doc, Ticket)
        return doc

    def _mandate(self, digest: str) -> Mandate:
        raw = self.log.store.document(digest)
        if raw is None or raw[0] != "mandate":
            raise AuthorityError(f"no mandate has digest {digest[:16]}")
        doc = loads(raw[1])
        assert isinstance(doc, Mandate)
        return doc

    # -- operator log & Operator lifecycle (F7-16) -----------------------------

    @contextmanager
    def _open_operator_log(self) -> Iterator[OperatorLog]:
        if isinstance(self._operator_log, OperatorLog):
            yield self._operator_log
            return
        if self._operator_log is None:
            raise ExecutionError("an outbox resolution requires an OperatorLog")
        signer = self._executor_signer or self.log.signer
        check_ilok1_signer(signer, role="executor")
        pub = signer.public_key()
        keyring = self._operator_keyring or Keyring({pub.key_id: pub})
        op_log = OperatorLog(self._operator_log, signer, keyring, ledger=self.governor)
        try:
            yield op_log
        finally:
            op_log.close()

    @contextmanager
    def _open_operator(self) -> Iterator[Operator]:
        """Open :class:`Operator` for one execution and close it immediately (F7-16)."""
        if self._outbox is None:
            raise ExecutionError("an outbox resolution requires OutboxOperations")
        with self._open_operator_log() as op_log:
            yield Operator(op_log, self._outbox)

    # -- planning steps from a warrant (§7.5, §7.6, F3-9) ----------------------

    def _plan_steps(self, warrant: Warrant, ticket: Ticket) -> list[dict[str, Any]]:
        res = warrant.resolution
        action = res.action
        steps: list[dict[str, Any]] = []
        if action == "accept_repair":
            assert isinstance(ticket.evidence, RefusedEvidence)
            assert res.proposal is not None
            steps.append(
                {
                    "step": 0,
                    "kind": "accept_repair",
                    "proposal": res.proposal,
                    "receipt": ticket.evidence.receipt.receipt_id,
                }
            )
            return steps
        if action == "release":
            assert res.messages is not None
            if res.reset is not None:
                is_spent = (
                    isinstance(ticket.evidence, HeldEvidence)
                    and ticket.evidence.halt is not None
                    and ticket.evidence.halt.spent_out
                )
                steps.append(
                    {
                        "step": len(steps),
                        "kind": "reset",
                        "scope": res.reset,
                        "spent_out": is_spent,
                    }
                )
            for chunk in _chunks(res.messages):
                steps.append(
                    {
                        "step": len(steps),
                        "kind": "release",
                        "targets": [{"message_id": m.message_id, "head": m.head} for m in chunk],
                    }
                )
            return steps
        if action == "reset":
            assert res.scope is not None
            is_spent = (
                isinstance(ticket.evidence, HeldEvidence)
                and ticket.evidence.halt is not None
                and ticket.evidence.halt.spent_out
            )
            steps.append(
                {
                    "step": 0,
                    "kind": "reset",
                    "scope": res.scope,
                    "spent_out": is_spent,
                }
            )
            return steps
        if action in ("cancel", "requeue"):
            assert res.messages is not None
            for target in res.messages:
                steps.append(
                    {
                        "step": len(steps),
                        "kind": action,
                        "targets": [{"message_id": target.message_id, "head": target.head}],
                    }
                )
            return steps
        if action in ("compensate", "compensate_late"):
            assert res.messages is not None and res.plan_id is not None
            for chunk in _chunks(res.messages):
                steps.append(
                    {
                        "step": len(steps),
                        "kind": action,
                        "plan_id": res.plan_id,
                        "late": action == "compensate_late",
                        "targets": [{"message_id": m.message_id, "head": m.head} for m in chunk],
                    }
                )
            return steps
        if action == "grant":
            assert isinstance(ticket.evidence, ExtensionEvidence)
            assert res.quote is not None and res.amount is not None
            steps.append(
                {
                    "step": 0,
                    "kind": "grant",
                    "quote": res.quote,
                    "amount": _money(res.amount),
                    "record_hash": ticket.evidence.quote.record_hash,
                }
            )
            return steps
        if action == "decline":
            assert isinstance(ticket.evidence, ExtensionEvidence)
            assert res.quote is not None
            steps.append(
                {
                    "step": 0,
                    "kind": "decline",
                    "quote": res.quote,
                    "record_hash": ticket.evidence.quote.record_hash,
                }
            )
            return steps
        if action == "amend":
            assert res.mandate is not None
            steps.append(
                {
                    "step": 0,
                    "kind": "amend",
                    "mandate": res.mandate,
                }
            )
            return steps
        if action == "reject":
            steps.append({"step": 0, "kind": "reject"})
            return steps
        raise ExecutionError(f"unknown warrant resolution action {action!r}")

    # -- executing a warrant (§7.5, §7.6) --------------------------------------

    def execute(
        self,
        warrant_or_digest: Warrant | str,
        *,
        proposal: EffectPlan | None = None,
    ) -> list[Record]:
        """Execute a formed warrant in two phases (``execution.intent`` then
        ``execution.applied`` or ``execution.refused``).

        Runs :meth:`resolve` first (F7-13).
        """
        if proposal is not None:
            self.register_proposal(proposal)
        self.resolve()

        with self.log.store.transaction():
            now = self.log.now()
            state = self.log.state()
            warrant = self._warrant(warrant_or_digest, state)
            self.decide.execution_intent(state, warrant.digest, now)
            ticket = self._ticket(warrant.ticket.digest)
            steps = self._plan_steps(warrant, ticket)
            intent_body: dict[str, Any] = {
                "warrant": warrant.digest,
                "warrant_id": warrant.warrant_id,
                "ticket": ticket.digest,
                "ticket_id": ticket.ticket_id,
                "mandate": ticket.mandate.digest,
                "mandate_id": ticket.mandate.mandate_id,
                "action": warrant.resolution.action,
                "steps": steps,
            }
            intent_record = self.log.append("execution.intent", intent_body, issued_at=now)
        self._checkpoint("intent")

        written: list[Record] = [intent_record]
        results: list[dict[str, Any]] = []
        mismatches: list[dict[str, Any]] = []

        for step in steps:
            idx = int(step["step"])
            ok, result_or_why, step_mismatches, quote_lost = self._run_step(warrant, ticket, step)
            mismatches.extend(step_mismatches)
            if not ok:
                refused_records = self._record_refused(
                    warrant,
                    ticket,
                    intent_record.seq,
                    str(result_or_why),
                    results,
                    mismatches,
                    lapse_ticket=quote_lost,
                )
                written.extend(refused_records)
                return written
            assert isinstance(result_or_why, dict)
            results.append(result_or_why)
            self._checkpoint(f"acted:{idx}")

        with self.log.store.transaction():
            now = self.log.now()
            state = self.log.state()
            self.decide.execution_outcome(state, warrant.digest)
            applied_body: dict[str, Any] = {
                "warrant": warrant.digest,
                "warrant_id": warrant.warrant_id,
                "ticket": ticket.digest,
                "ticket_id": ticket.ticket_id,
                "mandate": ticket.mandate.digest,
                "mandate_id": ticket.mandate.mandate_id,
                "action": warrant.resolution.action,
                "intent": intent_record.seq,
                "results": results,
                "mismatches": mismatches,
            }
            applied_record = self.log.append("execution.applied", applied_body, issued_at=now)
        self._checkpoint("applied")
        written.append(applied_record)
        return written

    def _record_refused(
        self,
        warrant: Warrant,
        ticket: Ticket,
        intent_seq: int,
        why: str,
        results: list[dict[str, Any]],
        mismatches: list[dict[str, Any]],
        *,
        lapse_ticket: bool = False,
    ) -> list[Record]:
        with self.log.store.transaction():
            now = self.log.now()
            state = self.log.state()
            self.decide.execution_outcome(state, warrant.digest)
            refused_body: dict[str, Any] = {
                "warrant": warrant.digest,
                "warrant_id": warrant.warrant_id,
                "ticket": ticket.digest,
                "ticket_id": ticket.ticket_id,
                "mandate": ticket.mandate.digest,
                "mandate_id": ticket.mandate.mandate_id,
                "action": warrant.resolution.action,
                "intent": intent_seq,
                "why": why[:512],
                "results": results,
                "mismatches": mismatches,
            }
            entries: list[tuple[str, dict[str, Any]]] = [("execution.refused", refused_body)]
            if lapse_ticket:
                simulated = copy.deepcopy(state)
                simulated.apply(
                    Record(
                        log=self.log.log_id,
                        seq=state.head_seq + 1,
                        kind="execution.refused",
                        issued_at=now,
                        content=refused_body,
                        prev=fidu1.GENESIS,
                    )
                )
                if simulated.ticket_status(ticket.ticket_id) != "lapsed":
                    lapse_body = self.decide.lapse_ticket(simulated, ticket.digest, now)
                    entries.append(("ticket.lapsed", lapse_body))
            return self.log.append_all(entries, issued_at=now)

    def _run_step(
        self,
        warrant: Warrant,
        ticket: Ticket,
        step: Mapping[str, Any],
    ) -> tuple[bool, dict[str, Any] | str, list[dict[str, Any]], bool]:
        """Run one step of ``warrant``.

        :returns: ``(ok, step_result_or_why, mismatches, quote_lost)``.
        """
        idx = int(step["step"])
        kind = str(step["kind"])
        reason = f"FIDU1 warrant {warrant.digest}"

        if kind == "reject":
            return True, {"step": idx, "kind": "reject"}, [], False

        if kind == "accept_repair":
            proposal_hash = str(step["proposal"])
            plan = self._find_proposal(proposal_hash)
            if plan is None:
                return (
                    False,
                    f"repair proposal {proposal_hash[:16]} is not registered on this executor",
                    [],
                    False,
                )
            if plan.content_hash() != proposal_hash:
                return (
                    False,
                    f"proposal hash {plan.content_hash()[:16]} does not match {proposal_hash[:16]}",
                    [],
                    False,
                )
            try:
                engine = self._binder.engine(ticket.mandate.mandate_id)
                stage_res = engine.execute(plan)
            except (FiduciaryError, InterlockError) as exc:
                return False, str(exc), [], False
            if not stage_res.committed:
                blocking = (
                    [v.invariant for v in stage_res.verdict.blocking]
                    if stage_res.verdict is not None
                    else []
                )
                why = (
                    f"repaired proposal was refused by {', '.join(blocking)}"
                    if blocking
                    else "repaired proposal did not commit"
                )
                return False, why, [], False
            chain_records = engine._chain.for_plan(plan.plan_id)
            committed_rec = next(
                (r for r in reversed(chain_records) if r.record_type == RecordType.COMMITTED),
                None,
            )
            return (
                True,
                {
                    "step": idx,
                    "kind": "accept_repair",
                    "proposal": proposal_hash,
                    "plan_id": str(plan.plan_id),
                    "chain_seq": None if committed_rec is None else committed_rec.sequence,
                    "record": None if committed_rec is None else committed_rec.record_hash,
                    "receipt": None if stage_res.receipt is None else stage_res.receipt.receipt_id,
                },
                [],
                False,
            )

        if kind == "reset":
            scope = str(step["scope"])
            self.governor.refresh()
            try:
                halted = self.governor.halted_by(scope)
            except AgentGovError as exc:
                return False, str(exc), [], False
            if halted is None:
                return False, f"scope {scope!r} is not halted", [], False
            if step.get("spent_out"):
                current_reason = breaker_reason(self.governor, halted) or ""
                if not spent_out(current_reason):
                    return (
                        False,
                        f"scope {scope!r} is halted for a non-monetary safety reason "
                        f"({current_reason!r}), which a spent-out reset warrant cannot lift",
                        [],
                        False,
                    )
            try:
                with self.governor.transaction():
                    anchor_entry = self.governor.anchor("fiduciary", f"{reason} reset {scope}")
                    self.governor.reset(scope)
                after_entries = self.governor.entries(after=anchor_entry.sequence, limit=10)
                reset_entry = next(
                    (
                        e
                        for e in after_entries
                        if e.scope_id == scope and "reset" in str(e.entry_type).lower()
                    ),
                    anchor_entry,
                )
            except AgentGovError as exc:
                return False, str(exc), [], False
            return (
                True,
                {
                    "step": idx,
                    "kind": "reset",
                    "scope": scope,
                    "anchor_seq": anchor_entry.sequence,
                    "anchor_id": str(anchor_entry.entry_id),
                    "reset_seq": reset_entry.sequence,
                    "reset_id": str(reset_entry.entry_id),
                },
                [],
                False,
            )

        if kind in ("release", "cancel", "requeue", "compensate", "compensate_late"):
            targets: Sequence[Mapping[str, Any]] = step["targets"]
            uuids = [uuid.UUID(str(t["message_id"])) for t in targets]
            expected_heads = {str(t["message_id"]): str(t["head"]) for t in targets}
            try:
                with self._open_operator() as op:
                    if kind == "release":
                        outcome = op.release(uuids, reason=reason)
                    elif kind == "cancel":
                        outcome = op.cancel(uuids[0], reason=reason)
                    elif kind == "requeue":
                        outcome = op.requeue(uuids[0], reason=reason)
                    else:
                        state = self.log.state()
                        compiled = self._binder.compile(ticket.mandate.digest, state)
                        outcome = op.compensate(
                            uuids,
                            plan_id=str(step["plan_id"]),
                            late=bool(step.get("late", False)),
                            reason=reason,
                            registry=compiled.sinks,
                            now=self.log.now(),
                        )
            except (
                ExecutionError,
                OperatorRefusedError,
                OutboundRequestError,
                InterlockError,
                FiduciaryError,
            ) as exc:
                return False, str(exc), [], False

            step_mismatches = _head_mismatches(idx, expected_heads, outcome.intent)
            if outcome.record.kind != "operator.applied" or outcome.skipped:
                if outcome.skipped:
                    why = "; ".join(f"{mid}: {msg}" for mid, msg in outcome.skipped)
                else:
                    why = f"operator {kind} produced {outcome.record.kind}"
                return False, why, step_mismatches, False
            return (
                True,
                {
                    "step": idx,
                    "kind": kind,
                    "intent": outcome.intent.record_hash,
                    "outcome": outcome.record.record_hash,
                    "rows": [r.seq for r in outcome.rows],
                },
                step_mismatches,
                False,
            )

        if kind == "grant":
            quote_id = str(step["quote"])
            amount = Decimal(str(step["amount"]))
            quote = self._find_pending_quote(quote_id)
            if quote is None:
                return (
                    False,
                    f"quote_lost: extension quote {quote_id} is not held by BudgetGuard (F7-14)",
                    [],
                    True,
                )
            state = self.log.state()
            mandate = self._mandate(ticket.mandate.digest)
            if mandate.extension is None:
                return False, "mandate allows no extension", [], False
            if amount > mandate.extension.max_grant:
                return (
                    False,
                    f"grant {amount} exceeds extension.max_grant {mandate.extension.max_grant}",
                    [],
                    False,
                )
            already_granted = self._granted_total(state, mandate.digest)
            if already_granted + amount > mandate.extension.max_total:
                return (
                    False,
                    f"grant {amount} after {already_granted} exceeds "
                    f"extension.max_total {mandate.extension.max_total}",
                    [],
                    False,
                )
            assert self._guard is not None
            try:
                grant_res = self._guard.grant(quote, approved_by=reason, amount=amount)
            except (ExtensionError, InterlockError, ValueError) as exc:
                return False, str(exc), [], False
            return (
                True,
                {
                    "step": idx,
                    "kind": "grant",
                    "quote": quote_id,
                    "grant_id": grant_res.grant_id,
                    "scope": grant_res.scope_id,
                    "amount": _money(grant_res.amount),
                    "record": grant_res.record.record_hash,
                },
                [],
                False,
            )

        if kind == "decline":
            quote_id = str(step["quote"])
            quote = self._find_pending_quote(quote_id)
            if quote is None:
                return (
                    False,
                    f"quote_lost: extension quote {quote_id} is not held by BudgetGuard (F7-14)",
                    [],
                    True,
                )
            assert self._guard is not None
            try:
                declined_rec = self._guard.decline(quote, declined_by=reason, reason=reason)
            except (ExtensionError, InterlockError, ValueError) as exc:
                return False, str(exc), [], False
            return (
                True,
                {
                    "step": idx,
                    "kind": "decline",
                    "quote": quote_id,
                    "record": declined_rec.record_hash,
                },
                [],
                False,
            )

        if kind == "amend":
            mandate_digest = str(step["mandate"])
            try:
                issued_records = self._binder.issue(mandate_digest)
            except FiduciaryError as exc:
                return False, str(exc), [], False
            return (
                True,
                {
                    "step": idx,
                    "kind": "amend",
                    "mandate": mandate_digest,
                    "records": [r.seq for r in issued_records],
                },
                [],
                False,
            )

        return False, f"unsupported step kind {kind!r}", [], False

    def _find_pending_quote(self, quote_id: str) -> ExtensionRequest | None:
        if self._guard is None:
            return None
        for pending in self._guard.pending():
            if pending.quote_id == quote_id:
                return pending
        return None

    def _granted_total(self, state: State, mandate_digest: str) -> Decimal:
        """Total extension amount granted under ``mandate_digest`` (manual + auto)."""
        total = Decimal("0.00000000")
        for warrant_row in state.warrants.values():
            if (
                warrant_row.mandate_digest == mandate_digest
                and warrant_row.action == "grant"
                and warrant_row.amount is not None
                and state.execution_status(warrant_row.digest) == "applied"
            ):
                total += Decimal(warrant_row.amount)
        return total

    # -- deterministic crash recovery: resolve() (§7.5 F7-13, §10.4) -----------

    def resolve(self) -> list[Record]:
        """Resolve every open ``execution.intent`` from what the target systems
        hold (F7-13, F10-9 to F10-11).

        Looks in four places:
        - ILOK1 operator intents and outcomes naming ``FIDU1 warrant <digest>``
          (running :meth:`Operator.resolve` first if any operator intent is
          unresolved);
        - AgentGov ledger entries whose memo names ``FIDU1 warrant <digest>``;
        - ILOK1 extension records whose ``approved_by`` / ``declined_by`` names
          ``FIDU1 warrant <digest>``;
        - the lineage's escrow chain for the repair proposal's plan hash.
        """
        state = self.log.state()
        open_intents = state.open_intents()
        if not open_intents:
            return []

        if self._outbox is not None and self._operator_log is not None:
            with self._open_operator() as op:
                op.resolve()

        resolved: list[Record] = []
        for warrant_digest, intent_body, intent_seq in open_intents:
            steps: Sequence[Mapping[str, Any]] = intent_body.get("steps", ())
            mandate_id = str(intent_body["mandate_id"])
            results: list[dict[str, Any]] = []
            mismatches: list[dict[str, Any]] = []
            missing_step: str | None = None

            for step in steps:
                found_result, step_mismatches = self._inspect_step(warrant_digest, mandate_id, step)
                mismatches.extend(step_mismatches)
                if found_result is None:
                    missing_step = f"step {step['step']} ({step['kind']}) left no evidence"
                    break
                results.append(found_result)

            with self.log.store.transaction():
                now = self.log.now()
                current = self.log.state()
                if current.execution_status(warrant_digest) != "intent":
                    continue
                if missing_step is None:
                    applied_body: dict[str, Any] = {
                        "warrant": warrant_digest,
                        "warrant_id": intent_body["warrant_id"],
                        "ticket": intent_body["ticket"],
                        "ticket_id": intent_body["ticket_id"],
                        "mandate": intent_body["mandate"],
                        "mandate_id": mandate_id,
                        "action": intent_body["action"],
                        "intent": intent_seq,
                        "results": results,
                        "mismatches": mismatches,
                    }
                    rec = self.log.append("execution.applied", applied_body, issued_at=now)
                else:
                    abandoned_body: dict[str, Any] = {
                        "warrant": warrant_digest,
                        "warrant_id": intent_body["warrant_id"],
                        "ticket": intent_body["ticket"],
                        "ticket_id": intent_body["ticket_id"],
                        "mandate": intent_body["mandate"],
                        "mandate_id": mandate_id,
                        "action": intent_body["action"],
                        "intent": intent_seq,
                        "why": missing_step,
                        "results": results,
                        "mismatches": mismatches,
                    }
                    rec = self.log.append("execution.abandoned", abandoned_body, issued_at=now)
                resolved.append(rec)
        return resolved

    def _inspect_step(
        self,
        warrant_digest: str,
        mandate_id: str,
        step: Mapping[str, Any],
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        idx = int(step["step"])
        kind = str(step["kind"])
        reason = f"FIDU1 warrant {warrant_digest}"

        if kind == "reject":
            return {"step": idx, "kind": "reject"}, []

        if kind in ("release", "cancel", "requeue", "compensate", "compensate_late"):
            if self._operator_log is None:
                return None, []
            with self._open_operator_log() as op_log:
                records = op_log.records()
            op_action = "compensate" if kind in ("compensate", "compensate_late") else kind
            targets: Sequence[Mapping[str, Any]] = step["targets"]
            target_ids = [str(t["message_id"]) for t in targets]
            expected_heads = {str(t["message_id"]): str(t["head"]) for t in targets}

            matching_intent: SignedRecord | None = None
            for rec in records:
                if (
                    rec.kind == "operator.intent"
                    and rec.body.get("reason") == reason
                    and rec.body.get("action") == op_action
                    and [str(t["message"]) for t in rec.body.get("targets", ())] == target_ids
                ):
                    matching_intent = rec
                    break
            if matching_intent is None:
                return None, []
            step_mismatches = _head_mismatches(idx, expected_heads, matching_intent)
            outcome_rec = next(
                (
                    r
                    for r in records
                    if r.kind in ("operator.applied", "operator.refused", "operator.abandoned")
                    and isinstance(r.body.get("intent"), Mapping)
                    and r.body["intent"].get("hash") == matching_intent.record_hash
                ),
                None,
            )
            if outcome_rec is None or outcome_rec.kind != "operator.applied":
                return None, step_mismatches
            rows = [int(r["seq"]) for r in outcome_rec.body.get("rows", ())]
            return (
                {
                    "step": idx,
                    "kind": kind,
                    "intent": matching_intent.record_hash,
                    "outcome": outcome_rec.record_hash,
                    "rows": rows,
                },
                step_mismatches,
            )

        if kind == "reset":
            scope = str(step["scope"])
            self.governor.refresh()
            trail = list(self.governor.audit_trail())
            expected_memo = f"{reason} reset {scope}"
            for i, entry in enumerate(trail):
                if entry.scope_id == "fiduciary" and entry.memo == expected_memo:
                    reset_entry = next(
                        (
                            later
                            for later in trail[i + 1 :]
                            if later.scope_id == scope and "reset" in str(later.entry_type).lower()
                        ),
                        None,
                    )
                    if reset_entry is not None:
                        return (
                            {
                                "step": idx,
                                "kind": "reset",
                                "scope": scope,
                                "anchor_seq": entry.sequence,
                                "anchor_id": str(entry.entry_id),
                                "reset_seq": reset_entry.sequence,
                                "reset_id": str(reset_entry.entry_id),
                            },
                            [],
                        )
            return None, []

        if kind == "grant":
            quote_id = str(step["quote"])
            if self._runtime_records is not None:
                for rec in self._runtime_records.records():
                    if (
                        rec.kind == "extension.granted"
                        and rec.body.get("approved_by") == reason
                        and isinstance(rec.body.get("quote"), Mapping)
                        and rec.body["quote"].get("id") == quote_id
                    ):
                        return (
                            {
                                "step": idx,
                                "kind": "grant",
                                "quote": quote_id,
                                "grant_id": str(rec.body["grant"]),
                                "scope": str(rec.body["scope"]),
                                "amount": str(rec.body["amount"]),
                                "record": rec.record_hash,
                            },
                            [],
                        )
            self.governor.refresh()
            suffix = f"for quote {quote_id}, approved by {reason}"
            for entry in self.governor.audit_trail():
                if (
                    entry.memo
                    and entry.memo.endswith(suffix)
                    and entry.memo.startswith("extension ")
                ):
                    grant_id = entry.memo.split(" ", 2)[1]
                    return (
                        {
                            "step": idx,
                            "kind": "grant",
                            "quote": quote_id,
                            "grant_id": grant_id,
                            "scope": entry.scope_id,
                            "amount": str(step["amount"]),
                            "record": None,
                        },
                        [],
                    )
            return None, []

        if kind == "decline":
            quote_id = str(step["quote"])
            if self._runtime_records is not None:
                for rec in self._runtime_records.records():
                    if (
                        rec.kind == "extension.declined"
                        and rec.body.get("declined_by") == reason
                        and isinstance(rec.body.get("quote"), Mapping)
                        and rec.body["quote"].get("id") == quote_id
                    ):
                        return (
                            {
                                "step": idx,
                                "kind": "decline",
                                "quote": quote_id,
                                "record": rec.record_hash,
                            },
                            [],
                        )
            return None, []

        if kind == "accept_repair":
            proposal_hash = str(step["proposal"])
            chain_records = self._chain_records(mandate_id)
            committed = next(
                (
                    r
                    for r in reversed(chain_records)
                    if r.record_type == RecordType.COMMITTED and r.payload_hash == proposal_hash
                ),
                None,
            )
            if committed is None:
                return None, []
            return (
                {
                    "step": idx,
                    "kind": "accept_repair",
                    "proposal": proposal_hash,
                    "plan_id": str(committed.plan_id),
                    "chain_seq": committed.sequence,
                    "record": committed.record_hash,
                    "receipt": None,
                },
                [],
            )

        if kind == "amend":
            mandate_digest = str(step["mandate"])
            state = self.log.state()
            if mandate_digest not in state.issued:
                return None, []
            if state.status(mandate_id) == "bound":
                bound_info = state.bound.get(mandate_id)
                if bound_info is None or bound_info.get("mandate") != mandate_digest:
                    self._binder.bind(mandate_digest)
            return (
                {
                    "step": idx,
                    "kind": "amend",
                    "mandate": mandate_digest,
                    "records": [],
                },
                [],
            )

        return None, []

    def _chain_records(self, mandate_id: str) -> tuple[Any, ...]:
        if mandate_id in self._binder._engines:
            mandate_engine = self._binder._engines[mandate_id]
            if mandate_engine._chain.path is not None:
                mandate_engine.engine.recover()
            return mandate_engine._chain.records()
        if self._binder._chains is not None:
            chain_path = self._binder._chains / f"{mandate_id}.chain"
            if chain_path.exists():
                chain = EscrowChain(chain_path)
                try:
                    return chain.records()
                finally:
                    chain.close()
        return ()


def _head_mismatches(
    step_idx: int,
    expected_heads: Mapping[str, str],
    intent_record: SignedRecord,
) -> list[dict[str, Any]]:
    """Compare ILOK1 signed heads with ticket-observed heads (F7-15, U-4)."""
    found: list[dict[str, Any]] = []
    for item in intent_record.body.get("targets", ()):
        mid = str(item.get("message", ""))
        signed_head = str(item.get("head", ""))
        expected = expected_heads.get(mid)
        if expected is not None and signed_head != expected:
            found.append(
                {
                    "step": step_idx,
                    "message_id": mid,
                    "expected_head": expected,
                    "signed_head": signed_head,
                }
            )
    return found

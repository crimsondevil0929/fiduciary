"""Two-phase warrant execution and its resolution after a crash
(``docs/FIDUCIARY_SPEC.md`` §3.5 F3-9, §7.5, §7.6, §10.3, §10.4).

Every formed :class:`~fiduciary.documents.Warrant` executes in two phases:

1. ``execution.intent`` is appended to the FIDU1 log before any other system
   is touched, and before the warrant's ``not_after`` (F7-12). It names the
   warrant's digest and its steps in order, each with the precondition its
   ticket observed (:func:`~fiduciary.documents.execution_steps`): outbox
   actions are chunked to at most 8 targets (F3-9, U-2).
2. Each step acts on Interlock or AgentGov, binding ``FIDU1 warrant <digest>``
   into whatever that system signs or hashes (§7.6, F2-2).
3. ``execution.applied`` or ``execution.refused`` follows.

A warrant's execution holds a claim on it (``execution:<digest>``) from its
intent to its outcome. :meth:`Executor.resolve` runs at start and before every
execution, and resolves each intent no outcome answers and no live process
claims. It reads what the warrant's digest reached in four places (ILOK1
operator records, AgentGov memos, ILOK1 extension records, and the lineage's
escrow chain) and records ``execution.applied`` or ``execution.abandoned``
(F7-13, F10-9). Nothing is guessed: an intent whose evidence is not settled
yet (an operator intent or an escrow commit still open) stays open, for a
later run.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from agentgov.core import EntryType
from agentgov.exceptions import AgentGovError
from agentgov.receipts.kms_aws import AwsKmsSigner
from agentgov.receipts.signing import Signer
from interlock.chain import EscrowChain, EscrowRecord, RecordType
from interlock.deliveries import OutboxOperations
from interlock.exceptions import (
    ChainInUseError,
    ExtensionError,
    InterlockError,
    OutboundRequestError,
)
from interlock.extension import BudgetGuard
from interlock.operators import Operator, OperatorLog, OperatorRefusedError
from interlock.records import Keyring, RecordLog, SignedRecord
from interlock.recovery import breaker_reason, spent_out
from interlock.types import EffectPlan, PlanId

from fiduciary.authority import Authority, Decide
from fiduciary.binding import Binder
from fiduciary.documents import (
    MAX_OUTBOX_TARGETS,
    Mandate,
    Record,
    Ticket,
    Warrant,
    loads,
)
from fiduciary.escalation import Escalation
from fiduciary.exceptions import AuthorityError, FiduciaryError, StoreError
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

_OUTBOX_STEPS: Final = ("release", "cancel", "requeue", "compensate", "compensate_late")
_OPERATOR_OUTCOMES: Final = ("operator.applied", "operator.refused", "operator.abandoned")


class ExecutionError(FiduciaryError):
    """A warrant or executor configuration that cannot run."""


def check_ilok1_signer(signer: Signer | None, *, role: str = "executor") -> None:
    """Refuse an :class:`AwsKmsSigner` for an ILOK1 log, which interlock signs
    whole (F3-9, U-2)."""
    if isinstance(signer, AwsKmsSigner):
        raise ExecutionError(
            f"the {role} key may not be an AWS KMS signer before U-2 (F3-9): "
            f"ILOK1 signs whole records, which can exceed KMS's 4,096-byte RAW limit"
        )


def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.00000001")), "f")


@dataclass(slots=True)
class _Step:
    """What one step did, as :meth:`Executor._run_step` saw it act."""

    ok: bool
    result: dict[str, Any] | None = None
    why: str = ""
    mismatches: list[dict[str, Any]] = field(default_factory=list)
    lost: bool = False
    """What the step needs was held in a memory a restart lost: its ticket lapses."""


@dataclass(slots=True)
class _Found:
    """What one step reached, as :meth:`Executor.resolve` reads it back.

    ``reached``: the step did all it names. ``partial``: some of it, as
    ``result`` says, and nothing after it ran. ``missing``: nothing.
    ``unknown``: not settled yet, so the intent stays open.
    """

    status: str
    result: dict[str, Any] | None = None
    why: str = ""
    mismatches: list[dict[str, Any]] = field(default_factory=list)


class Executor:
    """Executes warrants in two phases, and resolves interrupted intents (§7.5, §7.6).

    :param operator_log: Interlock's operator log, by path: it is opened for
        one step and closed after it (F7-16), and read without a claim when
        resolving. An open :class:`OperatorLog` is used as it is.
    :param operator_keyring: Every operator's public key, as interlock's
        configuration names them; the executor's alone by default.
    :param guard: The :class:`BudgetGuard` that issued the quotes that
        extension tickets name. It holds them in memory (F7-14, U-3).
    :param guard_for: The guard of a lineage, by ``mandate_id``, where each
        lineage quotes through its own (the bridge's: margin and ttl are each
        mandate's). Asked before ``guard``.
    :param runtime_records: The guard's ILOK1 record log: an open
        :class:`RecordLog`, or its path, read without a claim. The guard's own
        by default.
    :param claim_timeout: How long a step waits for an operator log another
        process holds before it gives up and leaves its intent to
        :meth:`resolve` (F7-16).
    :param checkpoint: Called at ``intent``, ``acted:<n>`` and ``applied``:
        the crash tests stop the process there. Does nothing in production.
    """

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
        guard_for: Callable[[str], BudgetGuard | None] | None = None,
        runtime_records: RecordLog | Path | str | None = None,
        runtime_signer: Signer | None = None,
        claim_timeout: float = 30.0,
        checkpoint: Callable[[str], None] | None = None,
    ) -> None:
        if isinstance(operator_log, OperatorLog):
            executor_signer = executor_signer or operator_log.signer
        if runtime_records is None and guard is not None:
            runtime_records = guard.records
        if isinstance(runtime_records, RecordLog):
            runtime_signer = runtime_signer or runtime_records.signer
        check_ilok1_signer(executor_signer, role="executor")
        check_ilok1_signer(runtime_signer, role="runtime")
        self._binder = binder
        self._escalation = escalation
        self._outbox = outbox
        self._operator_log = (
            operator_log
            if operator_log is None or isinstance(operator_log, OperatorLog)
            else Path(operator_log)
        )
        self._executor_signer = executor_signer
        self._operator_keyring = operator_keyring
        self._guard = guard
        self._guard_for = guard_for
        self._runtime_records = (
            runtime_records
            if runtime_records is None or isinstance(runtime_records, RecordLog)
            else Path(runtime_records)
        )
        self._runtime_signer = runtime_signer
        self._claim_timeout = claim_timeout
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
        """Hold a repair proposal by its content hash, for an ``accept_repair``
        warrant to execute. Proposals live in memory: they carry row data,
        which no document holds (F4-27)."""
        digest = plan.content_hash()
        self._proposals[digest] = plan
        return digest

    def _find_proposal(self, digest: str) -> EffectPlan | None:
        if digest in self._proposals:
            return self._proposals[digest]
        if self._escalation is not None:
            return self._escalation.proposals.get(digest)
        return None

    # -- documents ---------------------------------------------------------------

    def _document(self, digest: str, cls: type[Any], what: str) -> Any:
        raw = self.log.store.document(digest)
        if raw is None or raw[0] != cls.TYPE:
            raise AuthorityError(f"no {what} has digest {digest[:16]}")
        found = loads(raw[1])
        assert isinstance(found, cls)
        return found

    def _warrant(self, warrant_or_digest: Warrant | str, state: State) -> Warrant:
        if isinstance(warrant_or_digest, Warrant):
            return warrant_or_digest
        digest = state.warrants_by_id.get(warrant_or_digest, warrant_or_digest)
        found: Warrant = self._document(digest, Warrant, "formed warrant")
        return found

    # -- claims --------------------------------------------------------------------

    def _claim(self, warrant_digest: str) -> Any:
        """The claim on one warrant's execution, held from its intent to its
        outcome. A killed process loses it with its file descriptor or its
        database session."""
        return self.log.store.claim(f"execution:{warrant_digest}")

    @contextmanager
    def _open_operator_log(self) -> Iterator[OperatorLog]:
        """The operator log, claimed for one step (F7-16). A claim held
        elsewhere delays the step, up to ``claim_timeout``."""
        if isinstance(self._operator_log, OperatorLog):
            yield self._operator_log
            return
        if self._operator_log is None:
            raise ExecutionError("an outbox resolution needs interlock's operator log")
        signer = self._executor_signer or self.log.signer
        deadline = time.monotonic() + self._claim_timeout
        while True:
            try:
                op_log = OperatorLog(
                    self._operator_log, signer, self._keyring(signer), ledger=self.governor
                )
                break
            except ChainInUseError as exc:
                if time.monotonic() >= deadline:
                    raise ExecutionError(
                        f"the operator log stayed claimed for {self._claim_timeout:g} seconds: "
                        f"{exc}"
                    ) from exc
                time.sleep(0.05)
        try:
            yield op_log
        finally:
            op_log.close()

    def _keyring(self, signer: Signer) -> Keyring:
        if self._operator_keyring is not None:
            return self._operator_keyring
        public = signer.public_key()
        return Keyring({public.key_id: public})

    def _operator_records(self) -> tuple[SignedRecord, ...]:
        """The operator log as it stands, read without claiming it."""
        if isinstance(self._operator_log, OperatorLog):
            return self._operator_log.records()
        assert self._operator_log is not None
        if not self._operator_log.exists():
            return ()
        signer = self._executor_signer or self.log.signer
        return RecordLog.load(self._operator_log, self._keyring(signer))

    def _extension_records(self) -> tuple[SignedRecord, ...]:
        if isinstance(self._runtime_records, RecordLog):
            return self._runtime_records.records()
        if self._runtime_records is None or not self._runtime_records.exists():
            return ()
        signer = self._runtime_signer or self.log.signer
        return RecordLog.load(self._runtime_records, signer)

    # -- executing (§7.5, §7.6) -------------------------------------------------

    def execute(
        self, warrant_or_digest: Warrant | str, *, proposal: EffectPlan | None = None
    ) -> list[Record]:
        """Execute a formed warrant: ``execution.intent``, its steps, then
        ``execution.applied`` or ``execution.refused``. Resolves interrupted
        intents first (F7-13).

        :raises AuthorityError: When the warrant has an intent already, has
            expired, or its ticket no longer stands.
        :raises ExecutionError: When another process executes it right now.
        """
        if proposal is not None:
            self.register_proposal(proposal)
        self.resolve()
        warrant = self._warrant(warrant_or_digest, self.log.state())
        with ExitStack() as held:
            try:
                held.enter_context(self._claim(warrant.digest))
            except StoreError as exc:
                raise ExecutionError(
                    f"warrant {warrant.digest[:16]} is executing elsewhere: {exc}"
                ) from exc
            with self.log.store.transaction():
                now = self.log.now()
                body = self.decide.execution_intent(self.log.state(), warrant.digest, now)
                intent = self.log.append("execution.intent", body, issued_at=now)
            self._checkpoint("intent")
            ticket: Ticket = self._document(warrant.ticket.digest, Ticket, "ticket")
            results: list[dict[str, Any]] = []
            mismatches: list[dict[str, Any]] = []
            for step in body["steps"]:
                done = self._run_step(warrant, ticket, step)
                mismatches.extend(done.mismatches)
                if not done.ok:
                    return [
                        intent,
                        *self._refused(intent, done.why, results, mismatches, lapse=done.lost),
                    ]
                assert done.result is not None
                results.append(done.result)
                self._checkpoint(f"acted:{step['step']}")
            applied = self._outcome("execution.applied", intent, results, mismatches)
            self._checkpoint("applied")
            return [intent, applied]

    def _outcome(
        self,
        kind: str,
        intent: Record,
        results: list[dict[str, Any]],
        mismatches: list[dict[str, Any]],
        **extra: Any,
    ) -> Record:
        (record,) = self._outcomes([(kind, self._outcome_body(intent, results, mismatches, extra))])
        return record

    @staticmethod
    def _outcome_body(
        intent: Record,
        results: list[dict[str, Any]],
        mismatches: list[dict[str, Any]],
        extra: Mapping[str, Any],
    ) -> dict[str, Any]:
        fixed = ("warrant", "warrant_id", "ticket", "ticket_id", "mandate", "mandate_id", "action")
        return {
            **{name: intent.content[name] for name in fixed},
            "intent": intent.seq,
            **extra,
            "results": results,
            "mismatches": mismatches,
        }

    def _outcomes(self, entries: list[tuple[str, dict[str, Any]]]) -> list[Record]:
        with self.log.store.transaction():
            now = self.log.now()
            state = self.log.state()
            for kind, body in entries:
                if kind.startswith("execution."):
                    self.decide.execution_outcome(state, kind, body)
                    state = state.after(kind, body)
            return self.log.append_all(entries, issued_at=now)

    def _refused(
        self,
        intent: Record,
        why: str,
        results: list[dict[str, Any]],
        mismatches: list[dict[str, Any]],
        *,
        lapse: bool,
    ) -> list[Record]:
        """``execution.refused``; and, for a quote or a repair proposal this
        process no longer holds, the ticket's lapse in the same transaction:
        the agent asks again (F7-14)."""
        body = self._outcome_body(intent, results, mismatches, {"why": why[:512]})
        entries = [("execution.refused", body)]
        if lapse:
            with self.log.store.transaction():
                now = self.log.now()
                after = self.log.state().after("execution.refused", body)
                entries.append(
                    ("ticket.lapsed", self.decide.lapse_ticket(after, body["ticket"], now))
                )
                return self._outcomes(entries)
        return self._outcomes(entries)

    def _run_step(self, warrant: Warrant, ticket: Ticket, step: Mapping[str, Any]) -> _Step:
        idx, kind = int(step["step"]), str(step["kind"])
        reason = f"FIDU1 warrant {warrant.digest}"
        if kind == "reject":
            return _Step(True, {"step": idx, "kind": "reject"})
        if kind == "accept_repair":
            return self._accept_repair(idx, ticket, str(step["proposal"]))
        if kind == "reset":
            return self._reset(idx, reason, str(step["scope"]), bool(step["spent_out"]))
        if kind in _OUTBOX_STEPS:
            return self._outbox_step(idx, kind, reason, ticket, step)
        if kind in ("grant", "decline"):
            return self._extension(idx, kind, reason, ticket, step)
        assert kind == "amend"
        try:
            issued = self._binder.issue(str(step["mandate"]))
        except FiduciaryError as exc:
            return _Step(False, why=str(exc))
        return _Step(
            True,
            {
                "step": idx,
                "kind": "amend",
                "mandate": step["mandate"],
                "records": [r.seq for r in issued],
            },
        )

    def _accept_repair(self, idx: int, ticket: Ticket, proposal: str) -> _Step:
        plan = self._find_proposal(proposal)
        if plan is None or plan.content_hash() != proposal:
            # Like a quote (F7-14): a restart loses it, the ticket lapses, and
            # the agent's next refusal raises a new one.
            return _Step(
                False,
                why=f"proposal_lost: repair proposal {proposal[:16]} is not held by this executor",
                lost=True,
            )
        try:
            staged = self._binder.engine(ticket.mandate.mandate_id).execute(plan)
        except (FiduciaryError, InterlockError) as exc:
            return _Step(False, why=str(exc))
        if not staged.committed:
            blocking = [v.invariant for v in staged.verdict.blocking] if staged.verdict else []
            why = (
                f"the repair proposal was refused by {', '.join(blocking)}"
                if blocking
                else "the repair proposal did not commit"
            )
            return _Step(False, why=why)
        found = self._committed(ticket.mandate.mandate_id, proposal)
        assert found.result is not None
        receipt = None if staged.receipt is None else staged.receipt.receipt_id
        return _Step(True, {**found.result, "step": idx, "receipt": receipt})

    def _reset(self, idx: int, reason: str, scope: str, observed_spent_out: bool) -> _Step:
        governor = self.governor
        governor.refresh()
        try:
            halted = governor.halted_by(scope)
        except AgentGovError as exc:
            return _Step(False, why=str(exc))
        if halted != scope:
            # AgentGov resets a scope's own breaker; one above it would stay
            # tripped, and the reset would write nothing.
            why = (
                f"scope {scope!r} is not halted"
                if halted is None
                else (f"scope {scope!r} is halted by {halted!r}, which the warrant does not name")
            )
            return _Step(False, why=why)
        if observed_spent_out and not spent_out(breaker_reason(governor, scope) or ""):
            return _Step(
                False,
                why=f"scope {scope!r} is now halted for a reason other than money, which a "
                f"warrant to reset a spent-out halt cannot lift",
            )
        try:
            with governor.transaction():  # nothing interleaves: the anchor precedes the reset
                anchor = governor.anchor("fiduciary", f"{reason} reset {scope}")
                governor.reset(scope)
        except AgentGovError as exc:
            return _Step(False, why=str(exc))
        found = self._reset_reached(idx, reason, scope)
        if found.status != "reached":
            return _Step(
                False, why=f"the reset after anchor {anchor.sequence} is not in the ledger"
            )
        return _Step(True, found.result)

    def _outbox_step(
        self, idx: int, kind: str, reason: str, ticket: Ticket, step: Mapping[str, Any]
    ) -> _Step:
        if self._outbox is None:
            return _Step(False, why="an outbox resolution needs interlock's outbox operations")
        targets: Sequence[Mapping[str, Any]] = step["targets"]
        ids = [uuid.UUID(str(t["message_id"])) for t in targets]
        expected = {str(t["message_id"]): str(t["head"]) for t in targets}
        registry = None
        if kind in ("compensate", "compensate_late"):
            # Admitted as the lineage's version in force admits requests now: an
            # amendment since the ticket was raised governs the compensation.
            # Compiled before the operator log is claimed: nothing slow under it.
            state = self.log.state()
            head = state.head(ticket.mandate.mandate_id)
            try:
                if head is None:
                    raise AuthorityError(f"no lineage {ticket.mandate.mandate_id} is issued")
                registry = self._binder.compile(head[1], state).sinks
            except FiduciaryError as exc:
                return _Step(False, why=str(exc))
        try:
            with self._open_operator_log() as op_log:
                op = Operator(op_log, self._outbox)
                if kind == "release":
                    outcome = op.release(ids, reason=reason)
                elif kind == "cancel":
                    outcome = op.cancel(ids[0], reason=reason)
                elif kind == "requeue":
                    outcome = op.requeue(ids[0], reason=reason)
                else:
                    outcome = op.compensate(
                        ids,
                        plan_id=str(step["plan_id"]),
                        late=bool(step["late"]),
                        reason=reason,
                        registry=registry,
                        now=self.log.now(),
                    )
        except (OperatorRefusedError, OutboundRequestError, InterlockError) as exc:
            return _Step(False, why=str(exc))
        mismatches = _head_mismatches(idx, expected, outcome.intent)
        result = _operator_result(idx, kind, outcome.intent, outcome.record)
        if outcome.record.kind != "operator.applied" or outcome.skipped:
            why = (
                "; ".join(f"{mid}: {msg}" for mid, msg in outcome.skipped)
                or f"interlock recorded {outcome.record.kind}"
            )
            return _Step(False, why=why, mismatches=mismatches)
        return _Step(True, result, mismatches=mismatches)

    def _extension(
        self, idx: int, kind: str, reason: str, ticket: Ticket, step: Mapping[str, Any]
    ) -> _Step:
        quote_id = str(step["quote"])
        guard = self._guard_of(ticket.mandate.mandate_id)
        quote = (
            None
            if guard is None
            else next((q for q in guard.pending() if q.quote_id == quote_id), None)
        )
        if guard is None or quote is None:
            return _Step(
                False,
                why=f"quote_lost: the guard holds no quote {quote_id}, which a restart loses "
                f"(F7-14, U-3)",
                lost=True,
            )
        if quote.record.record_hash != step["record_hash"]:
            return _Step(
                False,
                why=f"quote {quote_id} is signed as {quote.record.record_hash[:16]}, not the "
                f"record the ticket observed",
            )
        if kind == "decline":
            try:
                declined = guard.decline(
                    quote, declined_by=reason, reason="declined under a warrant"
                )
            except (ExtensionError, InterlockError, ValueError) as exc:
                return _Step(False, why=str(exc))
            return _Step(
                True,
                {"step": idx, "kind": "decline", "quote": quote_id, "record": declined.record_hash},
            )
        amount = Decimal(str(step["amount"]))
        mandate: Mandate = self._document(ticket.mandate.digest, Mandate, "mandate")
        if mandate.extension is None:
            return _Step(False, why="the mandate allows no extension")
        granted = self._granted(self.log.state(), mandate.digest)
        if amount > mandate.extension.max_grant or granted + amount > mandate.extension.max_total:
            return _Step(
                False,
                why=f"granting {amount} after {granted} passes the mandate's extension bounds "
                f"(max_grant {mandate.extension.max_grant}, max_total "
                f"{mandate.extension.max_total})",
            )
        try:
            grant = guard.grant(quote, approved_by=reason, amount=amount)
        except (ExtensionError, InterlockError, ValueError) as exc:
            return _Step(False, why=str(exc))
        return _Step(
            True,
            {
                "step": idx,
                "kind": "grant",
                "quote": quote_id,
                "grant_id": grant.grant_id,
                "scope": grant.scope_id,
                "amount": _money(grant.amount),
                "record": grant.record.record_hash,
            },
        )

    def _guard_of(self, mandate_id: str) -> BudgetGuard | None:
        found = None if self._guard_for is None else self._guard_for(mandate_id)
        return found if found is not None else self._guard

    @staticmethod
    def _granted(state: State, mandate_digest: str) -> Decimal:
        """What warrants granted under ``mandate_digest`` so far, by quorum or rule."""
        return sum(
            (
                Decimal(row.amount)
                for row in state.warrants.values()
                if row.mandate_digest == mandate_digest
                and row.action == "grant"
                and row.amount is not None
                and state.execution_status(row.digest) == "applied"
            ),
            Decimal("0.00000000"),
        )

    # -- resolving (§7.5 F7-13, §10.4) -------------------------------------------

    def resolve(self) -> list[Record]:
        """Resolve every intent no outcome answers and no process claims, from
        what the warrant's digest reached (F7-13, F10-9): ``execution.applied``
        when every step did all it names, else ``execution.abandoned``, naming
        what the steps before reached. An intent whose evidence is not settled
        yet stays open."""
        resolved: list[Record] = []
        for warrant_digest, body, seq in self.log.state().open_intents():
            with ExitStack() as held:
                try:
                    held.enter_context(self._claim(warrant_digest))
                except StoreError:
                    continue  # its process is alive, and executing it now
                record = self._resolve_one(warrant_digest, body, seq)
                if record is not None:
                    resolved.append(record)
        return resolved

    def _resolve_one(self, warrant_digest: str, body: Mapping[str, Any], seq: int) -> Record | None:
        if (
            self._outbox is not None
            and self._operator_log is not None
            and any(step["kind"] in _OUTBOX_STEPS for step in body["steps"])
        ):
            try:  # an operator intent killed between its phases is the operator's to resolve
                with self._open_operator_log() as op_log:
                    Operator(op_log, self._outbox).resolve()
            except ExecutionError:
                return None
        results: list[dict[str, Any]] = []
        mismatches: list[dict[str, Any]] = []
        why = ""
        for step in body["steps"]:
            found = self._inspect(warrant_digest, str(body["mandate_id"]), step)
            if found.status == "unknown":
                return None
            mismatches.extend(found.mismatches)
            if found.result is not None:
                results.append(found.result)
            if found.status != "reached":
                why = found.why or f"step {step['step']} ({step['kind']}) left no evidence"
                break
        state = self.log.state()
        if state.execution_status(warrant_digest) != "intent":
            return None  # another process resolved it first
        intent = next(r for r in self.log.records() if r.seq == seq)
        if why:
            return self._outcome("execution.abandoned", intent, results, mismatches, why=why)
        return self._outcome("execution.applied", intent, results, mismatches)

    def _inspect(self, warrant_digest: str, mandate_id: str, step: Mapping[str, Any]) -> _Found:
        idx, kind = int(step["step"]), str(step["kind"])
        reason = f"FIDU1 warrant {warrant_digest}"
        if kind == "reject":
            return _Found("reached", {"step": idx, "kind": "reject"})
        if kind in _OUTBOX_STEPS:
            return self._outbox_reached(idx, kind, reason, step)
        if kind == "reset":
            return self._reset_reached(idx, reason, str(step["scope"]))
        if kind == "grant":
            return self._grant_reached(idx, reason, step)
        if kind == "decline":
            for record in self._extension_records():
                if (
                    record.kind == "extension.declined"
                    and record.body.get("declined_by") == reason
                    and record.body["quote"]["id"] == step["quote"]
                ):
                    return _Found(
                        "reached",
                        {
                            "step": idx,
                            "kind": "decline",
                            "quote": step["quote"],
                            "record": record.record_hash,
                        },
                    )
            return _Found("missing")
        if kind == "accept_repair":
            found = self._committed(mandate_id, str(step["proposal"]))
            if found.result is not None:
                found.result = {**found.result, "step": idx, "receipt": None}
            return found
        assert kind == "amend"
        return self._amend_reached(idx, mandate_id, str(step["mandate"]))

    def _outbox_reached(self, idx: int, kind: str, reason: str, step: Mapping[str, Any]) -> _Found:
        if self._operator_log is None:
            return _Found("unknown")
        action = "compensate" if kind == "compensate_late" else kind
        targets: Sequence[Mapping[str, Any]] = step["targets"]
        wanted = {str(t["message_id"]) for t in targets}
        records = self._operator_records()
        # Interlock orders a compensation's targets itself (dependents first):
        # the intent names the same messages, not necessarily in this order.
        intent = next(
            (
                r
                for r in records
                if r.kind == "operator.intent"
                and r.body.get("reason") == reason
                and r.body.get("action") == action
                and {str(t["message"]) for t in r.body.get("targets", ())} == wanted
            ),
            None,
        )
        if intent is None:
            return _Found("missing")
        mismatches = _head_mismatches(
            idx, {str(t["message_id"]): str(t["head"]) for t in targets}, intent
        )
        outcome = next(
            (
                r
                for r in records
                if r.kind in _OPERATOR_OUTCOMES and r.body["intent"]["hash"] == intent.record_hash
            ),
            None,
        )
        if outcome is None:
            return _Found("unknown")  # Operator.resolve settles it on the next run
        result = _operator_result(idx, kind, intent, outcome)
        if outcome.kind != "operator.applied":
            return _Found(
                "missing", why=f"interlock recorded {outcome.kind}", mismatches=mismatches
            )
        if outcome.body.get("skipped"):
            return _Found("partial", result, why="interlock skipped targets", mismatches=mismatches)
        return _Found("reached", result, mismatches=mismatches)

    def _reset_reached(self, idx: int, reason: str, scope: str) -> _Found:
        governor = self.governor
        governor.refresh()
        memo = f"{reason} reset {scope}"
        anchor = next((e for e in governor.audit_trail("fiduciary") if e.memo == memo), None)
        if anchor is None:
            return _Found("missing")
        following = governor.entries(after=anchor.sequence, limit=1)
        if (
            not following
            or following[0].entry_type is not EntryType.CIRCUIT_RESET
            or following[0].scope_id != scope
        ):
            return _Found("missing", why=f"anchor {anchor.sequence} is not followed by its reset")
        return _Found(
            "reached",
            {
                "step": idx,
                "kind": "reset",
                "scope": scope,
                "anchor_seq": anchor.sequence,
                "anchor_id": str(anchor.entry_id),
                "reset_seq": following[0].sequence,
                "reset_id": str(following[0].entry_id),
            },
        )

    def _grant_reached(self, idx: int, reason: str, step: Mapping[str, Any]) -> _Found:
        quote_id = str(step["quote"])
        for record in self._extension_records():
            if (
                record.kind == "extension.granted"
                and record.body.get("approved_by") == reason
                and record.body["quote"]["id"] == quote_id
            ):
                return _Found(
                    "reached",
                    {
                        "step": idx,
                        "kind": "grant",
                        "quote": quote_id,
                        "grant_id": str(record.body["grant"]),
                        "scope": str(record.body["scope"]),
                        "amount": str(record.body["amount"]),
                        "record": record.record_hash,
                    },
                )
        # The guard funds in one ledger transaction, then signs its record: a
        # crash between leaves the memo alone.
        self.governor.refresh()
        suffix = f" for quote {quote_id}, approved by {reason}"
        for entry in self.governor.audit_trail():
            if entry.memo.startswith("extension ") and entry.memo.endswith(suffix):
                return _Found(
                    "reached",
                    {
                        "step": idx,
                        "kind": "grant",
                        "quote": quote_id,
                        "grant_id": entry.memo.split(" ", 2)[1],
                        "scope": entry.scope_id,
                        "amount": str(step["amount"]),
                        "record": None,
                    },
                )
        return _Found("missing")

    def _amend_reached(self, idx: int, mandate_id: str, digest: str) -> _Found:
        state = self.log.state()
        if digest not in state.issued:
            return _Found("missing")
        records: list[int] = []
        bound = state.bound.get(mandate_id)
        if state.status(mandate_id) == "bound" and bound is not None and bound["mandate"] != digest:
            # Issued, and killed before the rebind Binder.issue follows it with.
            records.append(self._binder.bind(digest).seq)
        return _Found(
            "reached", {"step": idx, "kind": "amend", "mandate": digest, "records": records}
        )

    def _committed(self, mandate_id: str, proposal: str) -> _Found:
        """Whether the proposal's plan committed, from the lineage's escrow chain."""
        chain = self._chain(mandate_id)
        admitted = [
            r
            for r in chain
            if r.record_type is RecordType.PLAN_ADMITTED and r.payload_hash == proposal
        ]
        if not admitted:
            return _Found("missing")
        plan_id: PlanId = admitted[-1].plan_id
        mine = [r for r in chain if r.plan_id == plan_id]
        committed = next((r for r in mine if r.record_type is RecordType.COMMITTED), None)
        if committed is not None:
            return _Found(
                "reached",
                {
                    "kind": "accept_repair",
                    "proposal": proposal,
                    "plan_id": str(plan_id),
                    "chain_seq": committed.sequence,
                    "record": committed.record_hash,
                },
            )
        intents = {r.stage_id for r in mine if r.record_type is RecordType.COMMIT_INTENT}
        ended = {
            r.stage_id for r in mine if r.record_type in (RecordType.ABORTED, RecordType.ORPHANED)
        }
        if intents - ended:
            return _Found("unknown")  # the engine's recovery asks the substrate
        return _Found("missing", why=f"plan {plan_id} did not commit")

    def _chain(self, mandate_id: str) -> tuple[EscrowRecord, ...]:
        engine = self._binder._engines.get(mandate_id)
        if engine is not None and not engine.closed:
            engine.engine.recover()
            return engine._chain.records()
        chains = self._binder._chains
        path = None if chains is None else chains / f"{mandate_id}.chain"
        if path is None or not path.exists():
            return ()
        return EscrowChain.load(path).records()


def _operator_result(
    idx: int, kind: str, intent: SignedRecord, outcome: SignedRecord
) -> dict[str, Any]:
    return {
        "step": idx,
        "kind": kind,
        "intent": intent.record_hash,
        "outcome": outcome.record_hash,
        "rows": [int(r["seq"]) for r in outcome.body.get("rows", ())],
    }


def _head_mismatches(
    step: int, expected: Mapping[str, str], intent: SignedRecord
) -> list[dict[str, Any]]:
    """The targets whose head the ILOK1 intent signed is not the one the ticket
    observed: an act on a state no principal saw (F7-15, until U-4)."""
    found = []
    for target in intent.body.get("targets", ()):
        message, signed = str(target["message"]), str(target["head"])
        if message in expected and signed != expected[message]:
            found.append(
                {
                    "step": step,
                    "message_id": message,
                    "expected_head": expected[message],
                    "signed_head": signed,
                }
            )
    return found

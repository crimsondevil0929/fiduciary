"""Binding a mandate to a deployment, and what becomes of a lineage after (§5, §6.4).

:meth:`Binder.bind` checks every precondition before writing anything,
delegates the envelope (``memo="FIDU1 bind <digest>"``, idempotently),
builds the lineage's :class:`MandateEngine` (one escrow chain per lineage,
kept across versions) and records ``mandate.bound``. Binding a version of a
lineage already bound *rebinds* it: the envelope is adjusted, and the engine
takes the new compiled policy at its next plan boundary, while the plan in
flight finishes under the version it was admitted under (F1-3).

:meth:`Binder.issue` issues a version, and when the version is a parent's
amendment, rebases every descendant still ⊑ it and suspends every one that is
not, in the same log transaction (F1-4, F5-7). :meth:`Binder.revoke` and
:meth:`Binder.expire` end a lineage and every descendant (F5-8). Suspension,
revocation and expiry trip, in one AgentGov transaction, each lineage's
subject scope and every satellite scope (``{scope}/recovery``,
``{scope}/ext-N``), so Interlock's own rules stop what is in flight: no plan
of a halted scope commits (E3-8), and a relay holds its messages (§5.5).
"""

from __future__ import annotations

import copy
import dataclasses
import re
import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentgov.exceptions import AgentGovError
from interlock.anchor import LedgerAnchor
from interlock.chain import EscrowChain
from interlock.engine import EscrowEngine, StageResult
from interlock.types import EffectPlan

from fiduciary import fidu1
from fiduciary.authority import Authority, Decide
from fiduciary.checkers import admits_scope
from fiduciary.compiler import CompiledMandate, Deployment, compile
from fiduciary.documents import Mandate, Record
from fiduciary.exceptions import AuthorityError, FiduciaryError
from fiduciary.log import Log
from fiduciary.store import State

if TYPE_CHECKING:
    from agentgov import BudgetManager
    from agentgov.receipts import ReceiptLog
    from interlock.records import Keyring
    from interlock.substrate import ShadowSubstrate

__all__ = ["Binder", "BindingError", "MandateEngine"]

_EXTENSION = re.compile(r"/ext-([1-9][0-9]*)$")


class BindingError(FiduciaryError):
    """A mandate that cannot be bound, or an engine that will not run a plan."""


def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.00000001")), "f")


class MandateEngine:
    """One lineage's escrow engine, built from its compiled policy.

    One plan at a time (Interlock's engine stages one at a time anyway). A
    new compiled policy is taken up between plans: :meth:`rebind` waits for
    the plan in flight, which finishes, and is receipted, under the version
    it was admitted under (F1-3).
    """

    def __init__(
        self,
        mandate: Mandate,
        compiled: CompiledMandate,
        *,
        substrate: ShadowSubstrate,
        chain: EscrowChain,
        anchor: LedgerAnchor | None = None,
        receipts: ReceiptLog | None = None,
        inbox: Keyring | None = None,
        row_secret: bytes | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._lock = threading.RLock()
        self._substrate = substrate
        self._chain = chain
        self._anchor = anchor
        self._receipts = receipts
        self._inbox = inbox
        self._row_secret = row_secret
        self._clock = clock
        self._closed = False
        self._mandate = mandate
        self._compiled = compiled
        self._engine = self._build(compiled)
        self.recovered = self._engine.recover() if chain.path is not None else ()

    def _build(self, compiled: CompiledMandate) -> EscrowEngine:
        return EscrowEngine(
            self._substrate,
            checkers=compiled.checkers,
            chain=self._chain,
            anchor=self._anchor,
            receipts=None
            if self._receipts is None
            else compiled.receipt_issuer(self._receipts, row_secret=self._row_secret),
            sinks=compiled.sinks,
            windows=compiled.windows,
            inbox=self._inbox,
        )

    @property
    def compiled(self) -> CompiledMandate:
        with self._lock:
            return self._compiled

    @property
    def mandate(self) -> Mandate:
        with self._lock:
            return self._mandate

    @property
    def engine(self) -> EscrowEngine:
        with self._lock:
            return self._engine

    @property
    def closed(self) -> bool:
        return self._closed

    def execute(self, plan: EffectPlan, *, settle_cost: Decimal | str | None = None) -> StageResult:
        """Stage, measure, adjudicate and commit or refuse ``plan`` under the
        version bound now.

        :raises BindingError: When the lineage no longer governs (revoked,
            suspended, expired), or the mandate is outside its term.
        """
        with self._lock:
            if self._closed:
                raise BindingError(f"mandate {self._mandate.mandate_id} is no longer bound")
            now = self._clock()
            if not self._mandate.not_before <= now < self._mandate.not_after:
                raise BindingError(
                    f"mandate {self._mandate.mandate_id} version {self._mandate.version} is "
                    f"outside its term at {fidu1.format_instant(now)}"
                )
            return self._engine.execute(plan, settle_cost=settle_cost)

    def rebind(self, mandate: Mandate, compiled: CompiledMandate) -> None:
        """Take up a new version at the next plan boundary."""
        with self._lock:
            self._engine = self._build(compiled)
            self._mandate, self._compiled = mandate, compiled

    def close(self) -> None:
        """Run nothing more; the plan in flight, if any, finishes first."""
        with self._lock:
            self._closed = True


class Binder:
    """Binds mandates to one deployment, and keeps their lineages (module docstring).

    :param authority: The authority over the log.
    :param governor: A write-capable AgentGov governor of the deployment's ledger.
    :param deployment: The deployment snapshot :func:`compile` reads.
    :param substrate: Builds a substrate for an engine; without it, binding
        records and delegates, and builds no engine (the command line).
    :param chains: Where each lineage's escrow chain lives, ``<mandate_id>.chain``.
        In memory when ``None``.
    :param receipts: The receipt log every engine issues into.
    """

    def __init__(
        self,
        authority: Authority,
        governor: BudgetManager,
        *,
        deployment: Deployment,
        substrate: Callable[[], ShadowSubstrate] | None = None,
        chains: Path | None = None,
        receipts: ReceiptLog | None = None,
        inbox: Keyring | None = None,
        row_secret: bytes | None = None,
    ) -> None:
        self._authority = authority
        self._governor = governor
        self._deployment = deployment
        self._substrate = substrate
        self._chains = chains
        self._receipts = receipts
        self._inbox = inbox
        self._row_secret = row_secret
        self._engines: dict[str, MandateEngine] = {}
        self._anchor = LedgerAnchor(governed=governor)

    @property
    def log(self) -> Log:
        return self._authority.log

    @property
    def decide(self) -> Decide:
        return self._authority.decide

    def engine(self, mandate_id: str) -> MandateEngine:
        found = self._engines.get(mandate_id)
        if found is None:
            raise BindingError(f"lineage {mandate_id} has no engine here")
        return found

    # -- compiling -------------------------------------------------------------

    def lineage(self, state: State, mandate: Mandate) -> list[Mandate]:
        """``mandate`` and every ancestor's version in force, root first."""
        found = [mandate]
        current = state.parent_lineage(mandate.mandate_id)
        while current is not None:
            found.append(self.decide.head_mandate(state, current))
            current = state.parent_lineage(current)
        return found[::-1]

    def compile(self, digest: str, state: State | None = None) -> CompiledMandate:
        """Compile the issued version ``digest`` against the deployment."""
        state = state or self.log.state()
        mandate = self.decide.bindable(state, digest) if state.issued.get(digest) else None
        if mandate is None:
            raise BindingError(f"no issued mandate has digest {digest[:16]}")
        lineage = self.lineage(state, mandate)
        root = lineage[0].subject.funded_from
        try:
            depth = self._governor.node(root).depth
        except AgentGovError as exc:
            raise BindingError(
                f"the ledger has no scope {root!r} to fund the lineage from"
            ) from exc
        return compile(lineage, deployment=dataclasses.replace(self._deployment, root_depth=depth))

    # -- binding (§6.4) --------------------------------------------------------------

    def bind(self, digest: str) -> Record:
        """Bind the version ``digest``, or rebind its lineage to it (module docstring).

        :raises BindingError: On any precondition that does not hold; nothing
            is written then.
        :raises CompileError: When the deployment cannot enforce it.
        """
        governor = self._governor
        governor.refresh()
        state = self.log.state()
        try:
            mandate = self.decide.bindable(state, digest)
        except AuthorityError as exc:
            raise BindingError(str(exc)) from exc
        compiled = self.compile(digest, state)
        charter = self.decide.charter(state)
        first = governor.entry(1)
        if (
            charter.deployment.ledger != first.entry_hash
            or self._deployment.ledger != first.entry_hash
        ):
            raise BindingError(
                "the charter, the deployment and the ledger name different ledgers: a charter "
                "binds the ledger whose first entry it names"
            )
        funded = mandate.subject.funded_from
        if funded not in governor.scopes():
            raise BindingError(f"scope {funded!r}, which funds the mandate, is not in the ledger")
        if governor.halted_by(funded) is not None:
            raise BindingError(f"scope {funded!r}, which funds the mandate, is halted")
        policy = governor.policy
        if not (policy.trip_on_overdraft and policy.trip_on_exhaustion):
            raise BindingError(
                "the governor does not trip on overdraft and exhaustion: it is looser than any "
                "mandate"
            )
        depth = compiled.governance.max_depth - (
            mandate.delegation.max_depth if mandate.delegation else 0
        )
        if policy.max_depth < depth:
            raise BindingError(
                f"the governor delegates to depth {policy.max_depth}; the subject sits at {depth}"
            )
        scope = mandate.subject.scope
        memo, key = f"FIDU1 bind {digest}", f"fidu1-bind-{digest}"
        previous = state.bound.get(mandate.mandate_id)
        entries: list[str] = []
        try:
            if previous is None:
                governor.delegate(
                    funded, scope, mandate.budget.envelope, memo=memo, idempotency_key=key
                )
                entries.append(str(governor.find_entries(scope_id=scope, limit=1)[0].entry_id))
                delegated = mandate.budget.envelope
            else:
                delegated = Decimal(str(previous["delegated"]))
                change = mandate.budget.envelope - delegated
                if change > 0:
                    satellite = self._next_extension(scope)
                    governor.delegate(funded, satellite, change, memo=memo, idempotency_key=key)
                    entries.append(
                        str(governor.find_entries(scope_id=satellite, limit=1)[0].entry_id)
                    )
                    delegated += change
                elif change < 0:
                    amount = min(-change, governor.available(scope))
                    if amount > 0:
                        governor.release(scope, amount, memo=memo)
                        entries.append(
                            str(governor.find_entries(scope_id=scope, limit=1)[0].entry_id)
                        )
                        delegated -= amount
        except AgentGovError as exc:
            raise BindingError(f"the ledger refused the envelope: {exc}") from exc
        chain = None if self._chains is None else self._chains / f"{mandate.mandate_id}.chain"
        record = self.log.append(
            "mandate.bound",
            {
                "mandate": digest,
                "mandate_id": mandate.mandate_id,
                "version": mandate.version,
                "compiled": compiled.digest,
                "chain": None if chain is None else chain.name,
                "delegated": _money(delegated),
                "entries": entries,
            },
        )
        if self._substrate is not None:
            engine = self._engines.get(mandate.mandate_id)
            if engine is None or engine.closed:
                self._engines[mandate.mandate_id] = MandateEngine(
                    mandate,
                    compiled,
                    substrate=self._substrate(),
                    chain=EscrowChain(chain) if chain is not None else EscrowChain(),
                    anchor=self._anchor,
                    receipts=self._receipts,
                    inbox=self._inbox,
                    row_secret=self._row_secret,
                    clock=self.log.now,
                )
            else:
                engine.rebind(mandate, compiled)
        return record

    def _next_extension(self, scope: str) -> str:
        taken = set(self._governor.scopes())
        n = 1
        while f"{scope}/ext-{n}" in taken:
            n += 1
        return f"{scope}/ext-{n}"

    # -- issuing, deriving -------------------------------------------------------

    def issue(self, digest: str) -> list[Record]:
        """Issue the version ``digest`` (its approvals in the intake, or a
        derived child's refinement), rebasing or suspending every descendant
        in the same log transaction (F5-7). A bound lineage, and every rebased
        descendant bound, is then rebound to its version in force."""
        self._authority.process_intake()
        state = self.log.state()
        now = self.log.now()
        body = self.decide.issue(state, digest, now)
        mandate_id = str(body["mandate_id"])
        entries: list[tuple[str, dict[str, Any]]] = [("mandate.issued", body)]
        simulated = _after(state, "mandate.issued", body)
        suspended: list[dict[str, Any]] = []
        for child in simulated.descendants(mandate_id):
            parent = simulated.parent_lineage(child)
            assert parent is not None
            head = simulated.head(parent)
            assert head is not None
            kind, found = self.decide.rebase(simulated, child, head[1])
            entries.append((kind, found))
            if kind == "mandate.suspended":
                suspended.append(found)
            simulated = _after(simulated, kind, found)
        if suspended:
            trips = self._trip(state, [str(b["mandate_id"]) for b in suspended], "suspended")
            for found in suspended:
                found["trips"] = trips[str(found["mandate_id"])]
        records = self.log.append_all(entries, issued_at=now)
        for found in suspended:
            self._close(str(found["mandate_id"]))
        final = self.log.state()
        for lineage in [mandate_id, *final.descendants(mandate_id)]:
            # Parents first: a child compiles with its ancestors in force.
            head = final.head(lineage)
            if head is not None and final.status(lineage) == "bound":
                records.append(self.bind(head[1]))
                final = self.log.state()
        return records

    def derive(self, draft: Mandate) -> list[Record]:
        """Sign a derived child with the service key, store it and issue it:
        valid only because it refines its parent (F5-2). No approval is asked."""
        if draft.issuance != "derived":
            raise BindingError("a derived mandate says so: issuance is 'derived'")
        signed = draft.signed(self.log.signer)
        self._authority.propose(signed)
        return self.issue(signed.digest)

    # -- ending a lineage (§5.4, §5.5) ------------------------------------------------

    def revoke(self, digest: str) -> list[Record]:
        """Revoke the version in force ``digest`` and every descendant, on the
        revocation quorum of its roster, an ancestor's, or the charter's (F5-8)."""
        self._authority.process_intake()
        state = self.log.state()
        body = self.decide.revoke(state, digest)
        return self._end(state, [("mandate.revoked", body)], "revoked")

    def expire(self) -> list[Record]:
        """Expire every lineage in force whose term has ended, and its descendants."""
        state = self.log.state()
        now = self.log.now()
        entries: list[tuple[str, dict[str, Any]]] = []
        simulated = state
        for mandate_id in sorted(state.lineages, key=lambda m: state.lineages[m][0][3]):
            if not simulated.in_force(mandate_id):
                continue
            if now >= self.decide.head_mandate(simulated, mandate_id).not_after:
                body = self.decide.expire(simulated, mandate_id, now)
                entries.append(("mandate.expired", body))
                simulated = _after(simulated, "mandate.expired", body)
        if not entries:
            return []
        return self._end(state, entries, "expired", issued_at=now)

    def _end(
        self,
        state: State,
        roots: list[tuple[str, dict[str, Any]]],
        event: str,
        *,
        issued_at: datetime | None = None,
    ) -> list[Record]:
        entries: list[tuple[str, dict[str, Any]]] = []
        simulated = state
        for kind, body in roots:
            entries.append((kind, body))
            simulated = _after(simulated, kind, body)
            for child in simulated.descendants(str(body["mandate_id"])):
                if simulated.in_force(child):
                    found = self.decide.cascade(simulated, child, str(body["mandate"]), kind)
                    entries.append((kind, found))
                    simulated = _after(simulated, kind, found)
        trips = self._trip(state, [str(b["mandate_id"]) for _, b in entries], event)
        for _, body in entries:
            body["trips"] = trips[str(body["mandate_id"])]
        records = self.log.append_all(entries, issued_at=issued_at)
        for _, body in entries:
            self._close(str(body["mandate_id"]))
        return records

    def _trip(self, state: State, lineages: Sequence[str], event: str) -> dict[str, list[int]]:
        """Trip each lineage's subject scope and its satellites, in one AgentGov
        transaction. Returns, per lineage, the ledger sequences of its trips."""
        governor = self._governor
        governor.refresh()
        scopes = list(governor.scopes())
        plan: list[tuple[str, str, str]] = []
        for mandate_id in lineages:
            head = self.decide.head_mandate(state, mandate_id)
            subject = head.subject.scope
            for scope in scopes:
                if admits_scope(subject, scope):
                    plan.append((mandate_id, scope, f"FIDU1 {event} {head.digest}"))
        mark = len(governor.ledger)
        with governor.transaction():
            for _, scope, reason in plan:
                governor.trip(scope, reason)
        written = {e.scope_id: e.sequence for e in governor.entries(after=mark, limit=10_000)}
        found: dict[str, list[int]] = {m: [] for m in lineages}
        for mandate_id, scope, _ in plan:
            if scope in written:
                found[mandate_id].append(written[scope])
        return {m: sorted(seqs) for m, seqs in found.items()}

    def _close(self, mandate_id: str) -> None:
        engine = self._engines.get(mandate_id)
        if engine is not None:
            engine.close()

    def close(self) -> None:
        for engine in self._engines.values():
            engine.close()
        self._anchor.close()


def _after(state: State, kind: str, body: dict[str, Any]) -> State:
    """``state`` with a record of ``kind`` applied: what the next decision in
    the same transaction is taken from."""
    found = copy.deepcopy(state)
    found.apply(
        Record(
            log="fiduciary",
            seq=state.head_seq + 1,
            kind=kind,
            issued_at=datetime.now(UTC),
            content=body,
            prev=fidu1.GENESIS,
        )
    )
    return found

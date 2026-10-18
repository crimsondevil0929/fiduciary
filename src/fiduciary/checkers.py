"""Fiduciary's own checkers (``docs/FIDUCIARY_SPEC.md`` §6.3).

Each is a pure function of ``(plan, diff)``, as Interlock requires of every
checker (E2-12). Each holds only strings, integers and sorted tuples, so its
``repr`` is the same in every process, and so is the configuration digest
Interlock's receipts record for it, with or without U-1 (F6-6). Each
implements ``hint``, so the agent is told which constraint refused it; a
checker outside Interlock's built-ins is never trusted with numbers, and
gives none.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from interlock.feedback import FeedbackHint, Guidance
from interlock.types import EffectDiff, EffectKind, EffectPlan, InvariantViolation, Severity

__all__ = ["BoundStatements", "MandateScope", "TenantAllowlist", "admits_scope"]

_SATELLITE = r"(?:/recovery)?(?:/ext-[1-9][0-9]*)?"


def admits_scope(subject: str, scope: str) -> bool:
    """Whether ``scope`` is ``subject`` or one of its satellites:
    ``{subject}/recovery``, ``{subject}/ext-N``, ``{subject}/recovery/ext-N``."""
    return re.fullmatch(re.escape(subject) + _SATELLITE, scope) is not None


def _blocking(name: str, message: str, **evidence: str) -> InvariantViolation:
    return InvariantViolation(
        invariant=name, severity=Severity.BLOCKING, message=message, evidence=evidence
    )


class MandateScope:
    """``fiduciary_mandate``: a plan is the mandate's subject's, or one of its
    satellites'. Holds the mandate's identity, so the configuration digest a
    receipt records for it commits the receipt to the mandate's digest and the
    compiled policy's (F6-6)."""

    __slots__ = ("_compiled", "_digest", "_mandate_id", "_subject", "_version")

    def __init__(
        self, mandate_id: str, version: int, digest: str, compiled: str, subject: str
    ) -> None:
        self._mandate_id = mandate_id
        self._version = version
        self._digest = digest
        self._compiled = compiled
        self._subject = subject

    @property
    def name(self) -> str:
        return "fiduciary_mandate"

    def check(self, plan: EffectPlan, diff: EffectDiff) -> tuple[InvariantViolation, ...]:
        if admits_scope(self._subject, plan.scope_id):
            return ()
        return (
            _blocking(
                self.name,
                f"plan of scope {plan.scope_id!r} is outside mandate {self._mandate_id} "
                f"version {self._version}, whose subject is {self._subject!r}",
                scope=plan.scope_id,
                mandate=self._digest,
            ),
        )

    def hint(
        self, plan: EffectPlan, diff: EffectDiff, violation: InvariantViolation
    ) -> FeedbackHint:
        return FeedbackHint(kind=Guidance.OPERATOR)

    def __repr__(self) -> str:
        return (
            f"MandateScope({self._mandate_id!r}, {self._version}, {self._digest!r}, "
            f"{self._compiled!r}, {self._subject!r})"
        )


class TenantAllowlist:
    """``tenant_allowlist``: every measured row, and every outbound request,
    that carries a tenant carries one the mandate allows."""

    __slots__ = ("_allowed",)

    def __init__(self, allowed: Iterable[str]) -> None:
        self._allowed = tuple(sorted(set(allowed)))

    @property
    def name(self) -> str:
        return "tenant_allowlist"

    def _outside(self, diff: EffectDiff) -> list[str]:
        found = {d.tenant_id for d in diff.deltas} | {o.tenant_id for o in diff.outbound}
        return sorted(t for t in found if t is not None and t not in self._allowed)

    def check(self, plan: EffectPlan, diff: EffectDiff) -> tuple[InvariantViolation, ...]:
        outside = self._outside(diff)
        if not outside:
            return ()
        return (
            _blocking(
                self.name,
                f"plan reaches tenant(s) {', '.join(outside)} outside the mandate's",
                tenants=",".join(outside),
            ),
        )

    def hint(
        self, plan: EffectPlan, diff: EffectDiff, violation: InvariantViolation
    ) -> FeedbackHint:
        return FeedbackHint(kind=Guidance.TENANT_SCOPE, tenants=tuple(self._outside(diff)))


class BoundStatements:
    """``bound_statements``: every row effect is one a pinned tool binding
    carries: its kind, its table and its statement's SHA-256. An outbound
    request is the sink checkers' to judge."""

    __slots__ = ("_bound",)

    def __init__(self, bound: Iterable[tuple[str, str, str]]) -> None:
        self._bound = tuple(sorted(set(bound)))

    @property
    def name(self) -> str:
        return "bound_statements"

    def _unbound(self, plan: EffectPlan) -> list[str]:
        allowed = set(self._bound)
        found = []
        for effect in plan.effects:
            if effect.kind is EffectKind.ENQUEUE:
                continue
            table = effect.target.rsplit(":", 1)[-1]
            digest = hashlib.sha256(effect.statement.encode("utf-8")).hexdigest()
            if (effect.kind.value, table, digest) not in allowed:
                found.append(str(effect.effect_id))
        return found

    def check(self, plan: EffectPlan, diff: EffectDiff) -> tuple[InvariantViolation, ...]:
        unbound = self._unbound(plan)
        if not unbound:
            return ()
        return (
            _blocking(
                self.name,
                f"effect(s) {', '.join(unbound)} run a statement no pinned tool binding carries",
                effects=",".join(unbound),
            ),
        )

    def hint(
        self, plan: EffectPlan, diff: EffectDiff, violation: InvariantViolation
    ) -> FeedbackHint:
        return FeedbackHint(kind=Guidance.STATEMENT_KIND)

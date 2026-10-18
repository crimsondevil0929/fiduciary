"""Semantic monotonicity (``docs/FIDUCIARY_SPEC.md`` §5.6, F5-10, F5-11).

The §5.2 table is a syntactic condition. What it must imply is semantic: for
every ``child ⊑ parent`` and every ``(plan, diff)``, if the child's compiled
checkers admit the plan, the parent's do. Interlock's checkers are pure
functions of ``(plan, diff)``, so this is tested with no database: parents
and their narrowings are drawn at random (as the order's own property test
draws them), compiled, and every compiled checker is run over plans and
diffs drawn at random around their bounds.

The cognitive variant: on call sequences drawn at random (repeats,
near-duplicates, cycles, interleavings), a child's ``CognitivePolicy`` trips
no later than its parent's, on every trajectory the parent's trips on.
"""

from __future__ import annotations

import hashlib
import random
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from agentgov import CognitiveBreaker, CognitivePolicy
from agentgov.exceptions import AgentThrashingError
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from interlock import PlanBuilder
from interlock.types import (
    EffectDiff,
    EffectKind,
    EffectPlan,
    OutboundDelta,
    RowDelta,
    Severity,
    WindowMeasure,
)
from interlock.windows import RateWindowCheck

from fiduciary.compiler import CompiledMandate, CompileError, compile
from fiduciary.documents import Mandate
from fiduciary.refines import refines
from tests import deploy
from tests import examples as e
from tests.test_refines import narrow, universe

TENANTS = ("acme", "beta", "gamma", None)
TABLES = ("items", "orders", "refunds")
STATEMENTS = [effect.statement for effect in e.tool().effects] + [
    "UPDATE items SET qty = qty - 1 WHERE id = :id"
]
SCOPE = "support-agent"


def _pair(rnd: random.Random) -> tuple[CompiledMandate, CompiledMandate] | None:
    parent_raw = narrow(universe(), rnd, rnd.randint(0, 25))
    child_raw = narrow(parent_raw, rnd, rnd.randint(1, 25))
    parent, child = (
        Mandate.from_json(parent_raw, signed=False),
        Mandate.from_json(child_raw, signed=False),
    )
    assert refines(child, parent) == ()
    try:
        return (
            compile([parent], deployment=deploy.deployment()),
            compile([child], deployment=deploy.deployment()),
        )
    except CompileError:
        return None  # a narrowing that dropped what a pinned binding needs: not a mandate to bind


def _plan(rnd: random.Random) -> EffectPlan:
    builder = PlanBuilder(SCOPE if rnd.random() < 0.9 else "elsewhere")
    for _ in range(rnd.randint(1, 3)):
        builder.add(
            rnd.choice([EffectKind.INSERT, EffectKind.UPDATE, EffectKind.DELETE]),
            table=rnd.choice(TABLES),
            statement=rnd.choice(STATEMENTS),
            tenant_id=rnd.choice(TENANTS),
            stated_rows=rnd.choice([None, 1, 2]),
            independent=True,
        )
    for _ in range(rnd.randint(0, 2)):
        sink, operation = rnd.choice(
            [("stripe", "refunds.create"), ("stripe", "charges.create"), ("mail", "send")]
        )
        payload: dict[str, Any] = {
            "amount": rnd.choice(["5", "50", "500", "900", "1500"]),
            "currency": rnd.choice(["usd", "eur", "gbp"]),
            "to": rnd.choice(["ops@acme.com", "x@beta.org", "y@evil.io", "ops@x.io"]),
            "metadata": {"tenant": rnd.choice(["acme", "beta"])},
        }
        builder.enqueue(
            sink=sink,
            operation=operation,
            payload=payload,
            tenant_id=rnd.choice(TENANTS),
            independent=True,
        )
    return builder.build()


def _rows(rnd: random.Random) -> Iterator[RowDelta]:
    for n in range(rnd.choice([0, 1, 2, 4, 30])):
        table = rnd.choice(TABLES)
        tenant = rnd.choice(TENANTS)
        before = {"id": n, "tenant": tenant, "total": 1000, "amount": "10", "qty": 5}
        after = {
            **before,
            "total": rnd.choice([1000, 900, 600, 50]),
            "amount": rnd.choice(["10", "50"]),
        }
        kind = rnd.choice(["insert", "update", "delete"])
        yield RowDelta(
            table=table,
            primary_key=str(n),
            before=None if kind == "insert" else before,
            after=None if kind == "delete" else after,
            tenant_id=tenant,
        )


def _diff(rnd: random.Random, plan: EffectPlan, child: CompiledMandate) -> EffectDiff:
    outbound = tuple(
        OutboundDelta(
            message_id=uuid.uuid4(),
            effect_id=effect.effect_id,
            sink=effect.request.sink,
            operation=effect.request.operation,
            tenant_id=effect.tenant_id,
            payload=effect.request.payload,
            payload_hash=hashlib.sha256(effect.request.canonical_payload).hexdigest(),
            idempotency_key=f"k-{effect.effect_id}",
        )
        for effect in plan.effects
        if effect.request is not None
    )
    diff = EffectDiff(
        plan_id=plan.plan_id,
        stage_id=uuid.uuid4(),
        substrate_id="main",
        computed_at=datetime.now(UTC),
        deltas=tuple(_rows(rnd)),
        outbound=outbound,
    )
    measures = []
    for window in child.windows:
        amounts, _ = window.amounts(plan, diff)
        for key, amount in amounts.items():
            measures.append(
                WindowMeasure(
                    window.name, key, Decimal(rnd.choice([0, 10, 500, 1990, 5000])), amount
                )
            )
    return EffectDiff(
        plan_id=diff.plan_id,
        stage_id=diff.stage_id,
        substrate_id=diff.substrate_id,
        computed_at=diff.computed_at,
        deltas=diff.deltas,
        outbound=diff.outbound,
        windows=tuple(measures),
    )


def admitted(
    compiled: CompiledMandate, plan: EffectPlan, diff: EffectDiff
) -> tuple[bool, list[str]]:
    """Whether every compiled checker admits the plan, as Interlock decides:
    every checker runs, a raising one blocks (E2-14, E2-15)."""
    refused = []
    for checker in compiled.engine_checkers():
        try:
            violations = checker.check(plan, diff)
        except Exception:
            refused.append(checker.name)
            continue
        if any(v.severity is Severity.BLOCKING for v in violations):
            refused.append(checker.name)
    return not refused, refused


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.integers(min_value=0, max_value=2**32 - 1))
def test_what_a_child_admits_its_parent_admits(seed: int) -> None:
    rnd = random.Random(seed)
    pair = _pair(rnd)
    if pair is None:
        return
    parent, child = pair
    for _ in range(40):
        plan = _plan(rnd)
        diff = _diff(rnd, plan, child)
        by_child, _ = admitted(child, plan, diff)
        by_parent, refused = admitted(parent, plan, diff)
        if by_child:
            assert by_parent, f"the child admits a plan its parent refuses with {refused}"


def test_the_property_is_not_vacuous() -> None:
    """Enough generated plans are admitted by children, and refused by them,
    for the implication to say something."""
    rnd = random.Random(20261018)
    admitted_by_child = refused_by_child_only = pairs = 0
    while pairs < 40:
        pair = _pair(rnd)
        if pair is None:
            continue
        pairs += 1
        parent, child = pair
        for _ in range(60):
            plan = _plan(rnd)
            diff = _diff(rnd, plan, child)
            by_child, _ = admitted(child, plan, diff)
            by_parent, refused = admitted(parent, plan, diff)
            assert by_parent or not by_child, refused
            admitted_by_child += by_child
            refused_by_child_only += by_parent and not by_child
    assert admitted_by_child >= 20, admitted_by_child
    assert refused_by_child_only >= 20, refused_by_child_only


def test_every_checker_type_refuses_something_in_the_generated_cases() -> None:
    """Every compiled checker type is exercised: each refuses at least one
    generated plan, so none passes the property by never deciding."""
    rnd = random.Random(7)
    refusing: set[str] = set()
    seen: set[str] = set()
    for _ in range(60):
        pair = _pair(rnd)
        if pair is None:
            continue
        _, child = pair
        for checker in child.engine_checkers():
            seen.add(type(checker).__name__)
        for _ in range(40):
            plan = _plan(rnd)
            diff = _diff(rnd, plan, child)
            for checker in child.engine_checkers():
                try:
                    if any(v.severity is Severity.BLOCKING for v in checker.check(plan, diff)):
                        refusing.add(type(checker).__name__)
                except Exception:
                    refusing.add(type(checker).__name__)
    # Truncation and schema changes are not generated; facts are never consumed here.
    assert seen - refusing <= {"TruncationGuard", "NoSchemaChange", "FactAgreement"}, (
        seen - refusing
    )
    assert RateWindowCheck.__name__ in refusing


# -- the cognitive variant (F5-11) ----------------------------------------------------------

TOOLS = ("search", "fetch", "poll", "lookup_order")


def _calls(rnd: random.Random) -> list[tuple[str, str, str]]:
    calls: list[tuple[str, str, str]] = []
    for _ in range(rnd.randint(4, 24)):
        pattern = rnd.random()
        tool = rnd.choice(TOOLS)
        if pattern < 0.3 and calls:
            calls.append(calls[-1])  # an exact repeat
        elif pattern < 0.55 and calls:
            last = calls[-1]
            calls.append(
                (last[0], last[1] + rnd.choice(["", " ", "s", "!"]), last[2])
            )  # near-duplicate
        elif pattern < 0.65 and calls:
            calls.extend(
                [("poll", "status", f"tick {rnd.randint(0, 9)}"), calls[-1]]
            )  # an interleaving
        elif pattern < 0.75 and len(calls) >= 3:
            calls.extend(calls[-rnd.randint(2, 3) :])  # a cycle
        else:
            calls.append((tool, f"query about {rnd.randint(0, 9)}", f"result {rnd.randint(0, 3)}"))
    return calls


def _trips_at(policy: CognitivePolicy, calls: list[tuple[str, str, str]]) -> int | None:
    breaker = CognitiveBreaker(policy=policy, observer=None)
    try:
        for index, (tool, arguments, result) in enumerate(calls):
            try:
                breaker.observe(SCOPE, tool, arguments, trajectory="t")
            except AgentThrashingError:
                return index
            breaker.record_result(SCOPE, result, trajectory="t")
    finally:
        breaker.close()
    return None


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.integers(min_value=0, max_value=2**32 - 1))
def test_a_childs_cognitive_policy_trips_no_later_than_its_parents(seed: int) -> None:
    rnd = random.Random(seed)
    pair = _pair(rnd)
    if pair is None:
        return
    parent, child = pair
    for _ in range(20):
        calls = _calls(rnd)
        at_parent = _trips_at(parent.cognitive, calls)
        if at_parent is None:
            continue
        at_child = _trips_at(child.cognitive, calls)
        assert at_child is not None and at_child <= at_parent, (at_parent, at_child, calls)


def test_a_child_judging_a_tool_its_parent_exempts_would_trip_later() -> None:
    """Why ``exempt_tools`` is equal, not a subset, in the order (F5-11)."""
    calls = [("search", "q", "r"), ("poll", "s", "x")] * 3 + [("search", "q", "r")]
    parent = CognitivePolicy(exempt_tools=frozenset({"poll"}))
    child = CognitivePolicy(exempt_tools=frozenset())
    at_parent, at_child = _trips_at(parent, calls), _trips_at(child, calls)
    assert at_parent is not None and at_child is not None and at_child > at_parent

"""The order on mandates: ``child ⊑ parent`` (``docs/FIDUCIARY_SPEC.md`` §5).

:func:`refines` is the field-wise order of the §5.2 table. It returns every
field where ``child`` is wider than ``parent`` and is empty exactly when
``child ⊑ parent``. It is reflexive and transitive (F5-1): each field's rule
is a partial order, and the order is their product.

:func:`delegation_problems` adds what makes a child a child of one parent
(F5-2): it is funded from the parent's scope, names the parent's version,
fits in the parent's delegation, and has one level of derivation less. Those
rules relate a mandate to its parent, not two mandates' bounds, so they are
not part of the order: no mandate is its own child.

The order bounds what one plan may do. Sums across a subtree (money, windows,
children) are bounded by section 5.3, at compile and bind time.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TypeVar

from fiduciary.documents.mandate import (
    GRANTING,
    QUORUM_CLASSES,
    REDUCING,
    AmountCap,
    AutoRule,
    Drawdown,
    Mandate,
    Recipients,
)
from fiduciary.sealing import opens

__all__ = ["Problem", "delegation_problems", "refines"]

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Problem:
    """A field where the child is wider than the parent.

    :ivar path: The field, as the §5.2 table names it: ``budget.envelope``,
        ``outbound.caps[stripe.refunds.create.amount.request]``.
    """

    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


class _Check:
    __slots__ = ("problems",)

    def __init__(self) -> None:
        self.problems: list[Problem] = []

    def fail(self, path: str, message: str) -> None:
        self.problems.append(Problem(path, message))

    def at_most(self, path: str, child: Decimal | int, parent: Decimal | int) -> None:
        if child > parent:
            self.fail(path, f"is {child}, above the parent's {parent}")

    def at_least(self, path: str, child: Decimal | int, parent: Decimal | int) -> None:
        if child < parent:
            self.fail(path, f"is {child}, below the parent's {parent}")

    def subset(self, path: str, child: Sequence[str], parent: Sequence[str]) -> None:
        extra = sorted(set(child) - set(parent))
        if extra:
            self.fail(path, f"adds {', '.join(extra)}, which the parent does not allow")

    def superset(self, path: str, child: Sequence[str], parent: Sequence[str]) -> None:
        missing = sorted(set(parent) - set(child))
        if missing:
            self.fail(path, f"drops {', '.join(missing)}, which the parent holds")

    def implies(self, path: str, child: bool, parent: bool) -> None:
        """A switch that tightens when on: on in the child if on in the parent."""
        if parent and not child:
            self.fail(path, "is off, and the parent's is on")

    def only_if(self, path: str, child: bool, parent: bool) -> None:
        """A switch that loosens when on: on in the child only if on in the parent."""
        if child and not parent:
            self.fail(path, "is on, and the parent's is off")

    def kept(
        self,
        path: str,
        child: Sequence[T],
        parent: Sequence[T],
        identity: Callable[[T], object],
        name: Callable[[T], str],
        compare: Callable[[str, T, T], None] | None = None,
        applies: Callable[[T], bool] = lambda rule: True,
    ) -> None:
        """Every parent rule that still applies is kept, compared with ``compare``.
        Without one, a rule's ``identity`` is the whole rule, so a rule kept
        but changed is one dropped."""
        mine = {identity(rule): rule for rule in child}
        for rule in parent:
            if not applies(rule):
                continue
            where = f"{path}[{name(rule)}]"
            found = mine.get(identity(rule))
            if found is None:
                self.fail(where, "is dropped: a child keeps every rule of its parent")
            elif compare is not None:
                compare(where, found, rule)


def refines(child: Mandate, parent: Mandate) -> tuple[Problem, ...]:
    """Every field where ``child`` is wider than ``parent`` (§5.2). Empty when
    ``child ⊑ parent``."""
    check = _Check()
    _term(check, child, parent)
    _roster(check, child, parent)
    _budget(check, child, parent)
    _budgets(check, child, parent)
    _governance(check, child, parent)
    _data(check, child, parent)
    _outbound(check, child, parent)
    _tools(check, child, parent)
    _recovery(check, child, parent)
    _extension(check, child, parent)
    _escalation(check, child, parent)
    _delegation(check, child, parent)
    return tuple(check.problems)


def delegation_problems(child: Mandate, parent: Mandate) -> tuple[Problem, ...]:
    """Why ``child`` is not a valid child of ``parent`` (F5-2, F4-8): every
    :func:`refines` problem, and every rule that relates a child to its parent.

    The derivation budget's count of children (``max_children``) is the log's
    to count, not this function's (F5-6).
    """
    check = _Check()
    check.problems.extend(refines(child, parent))
    if child.parent is None or (
        child.parent.mandate_id,
        child.parent.version,
        child.parent.digest,
    ) != (parent.mandate_id, parent.version, parent.digest):
        check.fail("parent", "does not name this parent version by id, version and digest")
    if child.mandate_id == parent.mandate_id:
        check.fail("mandate_id", "is the parent's: a child is a lineage of its own")
    if child.subject.funded_from != parent.subject.scope:
        check.fail(
            "subject.funded_from",
            f"is {child.subject.funded_from!r}; a child is funded from its parent's scope, "
            f"{parent.subject.scope!r}",
        )
    if child.subject.scope == parent.subject.scope:
        check.fail("subject.scope", "is the parent's scope: a child governs a scope of its own")
    if parent.delegation is None:
        check.fail("delegation", "the parent allows no derivation")
    else:
        check.at_most("budget.envelope", child.budget.envelope, parent.delegation.envelope)
        if child.delegation is not None:
            check.at_most(
                "delegation.max_depth", child.delegation.max_depth, parent.delegation.max_depth - 1
            )
        if parent.delegation.max_depth < 1:
            check.fail("delegation.max_depth", "the parent allows no further level of derivation")
    return tuple(check.problems)


# -- the fields -----------------------------------------------------------------


def _term(check: _Check, child: Mandate, parent: Mandate) -> None:
    if child.not_before < parent.not_before:
        check.fail("term.not_before", "begins before the parent's term")
    if child.not_after > parent.not_after:
        check.fail("term.not_after", "ends after the parent's term")


def _roster(check: _Check, child: Mandate, parent: Mandate) -> None:
    check.subset("roster.principals", child.roster.principals, parent.roster.principals)
    for name in QUORUM_CLASSES:
        path = f"roster.quorums.{name}"
        mine, theirs = child.roster.quorum(name), parent.roster.quorum(name)
        if name in REDUCING:
            check.at_most(path, mine, theirs)  # a child is never harder to stop
        else:
            assert name in GRANTING or name == "accept_repair"
            check.at_least(path, mine, theirs)


def _budget(check: _Check, child: Mandate, parent: Mandate) -> None:
    check.at_most("budget.envelope", child.budget.envelope, parent.budget.envelope)
    check.at_most("budget.per_call", child.budget.per_call, parent.budget.per_call)
    check.at_most(
        "budget.max_output_tokens", child.budget.max_output_tokens, parent.budget.max_output_tokens
    )
    check.subset("budget.models", child.budget.models, parent.budget.models)


def _governance(check: _Check, child: Mandate, parent: Mandate) -> None:
    mine, theirs = child.governance, parent.governance
    # At most max_calls in any window of `seconds`: a longer window holding
    # fewer calls implies the parent's bound, since any shorter window lies in it.
    check.at_most("governance.max_calls", mine.max_calls, theirs.max_calls)
    check.at_least("governance.seconds", mine.seconds, theirs.seconds)
    check.implies("governance.fleet_velocity", mine.fleet_velocity, theirs.fleet_velocity)
    c, p = mine.cognitive, theirs.cognitive
    base = "governance.cognitive"
    check.at_most(f"{base}.max_identical_repeats", c.max_identical_repeats, p.max_identical_repeats)
    check.at_most(f"{base}.max_similar_streak", c.max_similar_streak, p.max_similar_streak)
    check.at_most(f"{base}.min_cycle_repeats", c.min_cycle_repeats, p.min_cycle_repeats)
    # AgentGov's cycle detector runs only from a period and a repeat count of 2:
    # below either it is off, so a child "narrowing" to 1 drops its parent's check.
    # F5-11's property test found it (tests/test_monotonicity.py).
    if min(p.max_cycle_period, p.min_cycle_repeats) >= 2 and c.min_cycle_repeats < 2:
        check.fail(
            f"{base}.min_cycle_repeats",
            f"is {c.min_cycle_repeats}, which turns off the cycle detection its parent runs "
            f"(it runs from 2 repeats)",
        )
    check.at_most(f"{base}.similarity_threshold", c.similarity_threshold, p.similarity_threshold)
    check.at_most(
        f"{base}.result_similarity_threshold",
        c.result_similarity_threshold,
        p.result_similarity_threshold,
    )
    check.at_least(f"{base}.max_cycle_period", c.max_cycle_period, p.max_cycle_period)
    check.at_least(f"{base}.min_novelty_ratio", c.min_novelty_ratio, p.min_novelty_ratio)
    # Equal, not a subset: a child that judges a call its parent exempts sees
    # that call between two others, and a streak the parent counts breaks.
    # F5-11's property test found it (tests/test_monotonicity.py).
    if c.exempt_tools != p.exempt_tools:
        check.fail(
            f"{base}.exempt_tools", "differs from the parent's: a child exempts exactly its tools"
        )


def _data(check: _Check, child: Mandate, parent: Mandate) -> None:
    mine, theirs = child.data, parent.data
    for table, operations in sorted(mine.tables.items()):
        allowed = theirs.tables.get(table)
        if allowed is None:
            check.fail(f"data.tables.{table}", "is a table the parent does not allow")
        else:
            check.subset(f"data.tables.{table}", operations, allowed)
    if mine.tenants is None:
        if theirs.tenants is not None:
            check.fail("data.tenants", "is any tenant, and the parent names its tenants")
    elif theirs.tenants is not None:
        check.subset("data.tenants", mine.tenants, theirs.tenants)
    check.implies("data.declared_tenants", mine.declared_tenants, theirs.declared_tenants)
    check.at_most("data.max_rows", mine.max_rows, theirs.max_rows)
    check.at_most("data.max_tenants", mine.max_tenants, theirs.max_tenants)
    check.kept(
        "data.drawdown",
        mine.drawdown,
        theirs.drawdown,
        lambda d: d.identity,
        lambda d: ".".join(d.identity),
        lambda path, c, p: _drawdown(check, path, c, p),
    )
    if theirs.receipt_tenants == "declared" and mine.receipt_tenants != "declared":
        check.fail("data.receipt_tenants", "names tenants the parent's receipts do not")
    for dimension, rule in sorted(theirs.partitions.items()):
        path = f"data.partitions.{dimension}"
        kept = mine.partitions.get(dimension)
        if kept is None:
            check.fail(path, "is dropped, and the parent confines plans in it")
        else:
            check.at_most(f"{path}.max", kept.max, rule.max)
            check.implies(f"{path}.declared", kept.declared, rule.declared)
    if theirs.stated_footprint is not None:
        found = mine.stated_footprint
        if found is None:
            check.fail("data.stated_footprint", "is dropped, and the parent's compares claims")
        else:
            check.at_most(
                "data.stated_footprint.tolerance",
                found.tolerance,
                theirs.stated_footprint.tolerance,
            )
            check.implies(
                "data.stated_footprint.blocking",
                found.blocking,
                theirs.stated_footprint.blocking,
            )
    check.kept(
        "data.agreements",
        mine.agreements,
        theirs.agreements,
        lambda a: a,
        lambda a: ".".join(a.identity),
    )
    check.kept("data.facts", mine.facts, theirs.facts, lambda f: f, lambda f: ".".join(f.identity))
    # 1.5.0: a child keeps every analyzer its parent needs, signed by keys
    # the parent trusts; every pinned checker, as pinned; and accepts no
    # unmeasured value the parent does not.
    check.kept(
        "data.sensors",
        mine.sensors,
        theirs.sensors,
        lambda r: r.sensor,
        lambda r: r.sensor,
        lambda path, c, p: check.subset(f"{path}.keys", c.keys, p.keys),
    )
    check.kept(
        "data.checkers",
        mine.checkers,
        theirs.checkers,
        lambda c: c.identity,
        lambda c: f"{c.module}.{c.checker}",
        lambda path, c, p: (
            None
            if c.to_json() == p.to_json()
            else check.fail(path, "is changed: a child runs its parent's checkers as pinned")
        ),
    )
    check.subset("data.acknowledge_unknown", mine.acknowledge_unknown, theirs.acknowledge_unknown)


def _outbound(check: _Check, child: Mandate, parent: Mandate) -> None:
    mine, theirs = child.outbound, parent.outbound

    def allowed(sink: str, operation: str) -> bool:
        bounds = mine.sinks.get(sink)
        return bounds is not None and operation in bounds.operations

    for sink, bounds in sorted(mine.sinks.items()):
        path = f"outbound.sinks.{sink}"
        limit = theirs.sinks.get(sink)
        if limit is None:
            check.fail(path, "is a sink the parent does not allow")
            continue
        check.subset(f"{path}.operations", bounds.operations, limit.operations)
        check.at_most(f"{path}.max_attempts", bounds.max_attempts, limit.max_attempts)
        check.at_most(
            f"{path}.not_after_seconds", bounds.not_after_seconds, limit.not_after_seconds
        )
        check.at_most(
            f"{path}.max_payload_bytes", bounds.max_payload_bytes, limit.max_payload_bytes
        )
    if theirs.max_per_plan is not None:
        if mine.max_per_plan is None:
            check.fail("outbound.max_per_plan", "is unbounded, and the parent's is bounded")
        else:
            check.at_most("outbound.max_per_plan", mine.max_per_plan, theirs.max_per_plan)
    for sink, count in sorted(theirs.per_sink.items()):
        if sink not in mine.sinks:
            continue  # the child sends nothing there
        path = f"outbound.per_sink.{sink}"
        if sink not in mine.per_sink:
            check.fail(path, "is unbounded, and the parent's is bounded")
        else:
            check.at_most(path, mine.per_sink[sink], count)
    check.kept(
        "outbound.caps",
        mine.caps,
        theirs.caps,
        lambda c: c.identity,
        lambda c: ".".join(c.identity),
        lambda path, c, p: _cap(check, path, c, p),
        lambda c: allowed(c.sink, c.operation),
    )
    check.kept(
        "outbound.recipients",
        mine.recipients,
        theirs.recipients,
        lambda r: r.identity,
        lambda r: ".".join(r.identity),
        lambda path, c, p: _recipients(check, path, c, p),
        lambda r: allowed(r.sink, r.operation),
    )
    if theirs.tenants is not None:
        found = mine.tenants
        if found is None:
            check.fail("outbound.tenants", "is dropped, and the parent's holds requests to tenants")
        else:
            check.at_most(
                "outbound.tenants.max_tenants", found.max_tenants, theirs.tenants.max_tenants
            )
            check.implies(
                "outbound.tenants.require_tenant",
                found.require_tenant,
                theirs.tenants.require_tenant,
            )
            for target, field in sorted(theirs.tenants.fields.items()):
                sink, _, operation = target.partition(".")
                if sink not in mine.sinks or (operation and not allowed(sink, operation)):
                    continue  # the child sends nothing there
                if found.fields.get(target) != field:
                    check.fail(
                        f"outbound.tenants.fields.{target}",
                        "is dropped or moved: a child reads each tenant where its parent does",
                    )


def _drawdown(check: _Check, path: str, child: Drawdown, parent: Drawdown) -> None:
    for name, mine, theirs in (
        ("max_drop", child.max_drop, parent.max_drop),
        ("max_rise", child.max_rise, parent.max_rise),
    ):
        if theirs is None:
            continue  # the parent does not bound that way; the child may
        if mine is None:
            check.fail(f"{path}.{name}", "is unbounded, and the parent's is bounded")
        else:
            check.at_most(f"{path}.{name}", mine, theirs)


def _cap(check: _Check, path: str, child: AmountCap, parent: AmountCap) -> None:
    if child.currency_field != parent.currency_field:
        check.fail(f"{path}.currency_field", "reads the currency elsewhere than the parent's")
    if child.unit_field != parent.unit_field:
        check.fail(f"{path}.unit_field", "reads the unit elsewhere than the parent's")
    mine, theirs = child.maximum, parent.maximum
    if isinstance(theirs, Mapping):
        if not isinstance(mine, Mapping):
            check.fail(f"{path}.maximum", "is one cap, and the parent's is per currency")
            return
        for currency, cap in sorted(mine.items()):
            limit = theirs.get(currency)
            if limit is None:
                check.fail(f"{path}.maximum.{currency}", "is a currency the parent refuses")
            else:
                check.at_most(f"{path}.maximum.{currency}", cap, limit)
    elif isinstance(mine, Mapping):
        check.fail(f"{path}.maximum", "is per currency, and the parent's is one cap")
    else:
        check.at_most(f"{path}.maximum", mine, theirs)


def _recipients(check: _Check, path: str, child: Recipients, parent: Recipients) -> None:
    check.superset(f"{path}.fields", child.fields, parent.fields)
    check.subset(f"{path}.domains", child.domains, parent.domains)
    check.subset(f"{path}.addresses", child.addresses, parent.addresses)
    for tenant, domains in sorted(child.tenant_domains.items()):
        allowed = parent.tenant_domains.get(tenant)
        opening = child.openings.get(tenant)
        where = f"{path}.tenant_domains.{tenant}"
        if opening is not None:
            # Opened under the parent's seal: exactly the entry it sealed.
            if parent.sealed is None or not opens(parent.sealed, tenant, domains, opening):
                check.fail(where, "is not an entry its opening puts under the parent's seal")
        elif allowed is None:
            check.fail(where, "is a tenant the parent allows nothing")
        else:
            check.subset(where, domains, allowed)
    if child.sealed is not None and child.sealed != parent.sealed:
        check.fail(f"{path}.sealed", "seals another map than the parent's")
    check.only_if(f"{path}.subdomains", child.subdomains, parent.subdomains)


def _budgets(check: _Check, child: Mandate, parent: Mandate) -> None:
    for unit, theirs in sorted(parent.budgets.items()):
        path = f"budgets.{unit}"
        mine = child.budgets.get(unit)
        if mine is None:
            check.fail(path, "is dropped, and the parent conserves it")
            continue
        if mine.places != theirs.places:
            check.fail(f"{path}.places", "counts the unit otherwise than the parent's ledger")
        if mine.measure != theirs.measure:
            check.fail(f"{path}.measure", "measures the unit otherwise than the parent")
        check.at_most(f"{path}.envelope", mine.envelope, theirs.envelope)
        check.at_most(f"{path}.per_plan", mine.per_plan, theirs.per_plan)


def _tools(check: _Check, child: Mandate, parent: Mandate) -> None:
    if parent.tools.statements == "bound" and child.tools.statements != "bound":
        check.fail("tools.statements", "admits any statement, and the parent's only bound ones")
    pinned = {(b.name, b.digest) for b in parent.tools.bindings}
    for binding in child.tools.bindings:
        if (binding.name, binding.digest) not in pinned:
            check.fail(
                f"tools.bindings[{binding.name}]",
                "is a binding the parent does not pin, by name and digest",
            )


def _recovery(check: _Check, child: Mandate, parent: Mandate) -> None:
    mine, theirs = child.recovery, parent.recovery
    if mine is None:
        return
    if theirs is None:
        check.fail("recovery", "recovers, and the parent's halts stand")
        return
    base = "recovery"
    check.at_most(f"{base}.reserve", mine.reserve, theirs.reserve)
    check.at_most(f"{base}.step_estimate", mine.step_estimate, theirs.step_estimate)
    check.at_most(f"{base}.max_steps", mine.max_steps, theirs.max_steps)
    allowed = {(d.directive_id, d.sha256): d for d in theirs.directives}
    for directive in mine.directives:
        found = allowed.get((directive.directive_id, directive.sha256))
        where = f"{base}.directives[{directive.directive_id}]"
        if found is None:
            check.fail(where, "is a directive the parent does not allow, by id and digest")
        else:
            check.subset(f"{where}.answers", directive.answers, found.answers)
    check.superset(f"{base}.revoke_on", mine.revoke_on, theirs.revoke_on)
    if mine.revocable is not None:  # None is every granted tool, a superset of any set
        if theirs.revocable is None:
            check.fail(f"{base}.revocable", "names its tools, and the parent's may revoke any")
        else:
            check.superset(f"{base}.revocable", mine.revocable, theirs.revocable)
    if theirs.token_ceiling is not None:
        if mine.token_ceiling is None:
            check.fail(f"{base}.token_ceiling", "is off, and the parent's lowers max_tokens")
        else:
            check.at_most(f"{base}.token_ceiling", mine.token_ceiling, theirs.token_ceiling)
    check.only_if(f"{base}.guidance", mine.guidance, theirs.guidance)
    if mine.channel != theirs.channel:
        check.fail(f"{base}.channel", "differs from the parent's")
    if mine.funding != theirs.funding:
        check.fail(f"{base}.funding", "draws the reserve elsewhere than the parent's")


def _extension(check: _Check, child: Mandate, parent: Mandate) -> None:
    mine, theirs = child.extension, parent.extension
    if mine is None:
        return
    if theirs is None:
        check.fail("extension", "may be extended, and the parent's may not")
        return
    check.at_most("extension.max_grant", mine.max_grant, theirs.max_grant)
    check.at_most("extension.max_total", mine.max_total, theirs.max_total)
    check.at_most("extension.margin", mine.margin, theirs.margin)
    check.at_most("extension.ttl_seconds", mine.ttl_seconds, theirs.ttl_seconds)
    if mine.auto is not None:
        if theirs.auto is None:
            check.fail("extension.auto", "grants by rule, and the parent's never does")
        else:
            check.at_most("extension.auto.max_grant", mine.auto.max_grant, theirs.auto.max_grant)
            check.at_most("extension.auto.max_total", mine.auto.max_total, theirs.auto.max_total)


def _escalation(check: _Check, child: Mandate, parent: Mandate) -> None:
    mine, theirs = child.escalation, parent.escalation
    check.at_most("escalation.max_open", mine.max_open, theirs.max_open)
    check.at_most("escalation.ttl_seconds", mine.ttl_seconds, theirs.ttl_seconds)
    rules = {rule.rule_id: rule for rule in theirs.auto}
    for rule in mine.auto:
        where = f"escalation.auto[{rule.rule_id}]"
        found = rules.get(rule.rule_id)
        if found is None:
            check.fail(where, "resolves by a rule the parent does not have")
        else:
            _rule(check, where, rule, found)


def _rule(check: _Check, path: str, child: AutoRule, parent: AutoRule) -> None:
    if (child.ticket_class, child.resolution) != (parent.ticket_class, parent.resolution):
        check.fail(path, "resolves another class, or another way, than the parent's rule")
        return
    for name, bound in sorted(parent.when.items()):
        where = f"{path}.when.{name}"
        if name == "exhaustive":
            check.implies(where, bool(child.when.get(name, False)), bool(bound))
            continue
        if name not in child.when:
            check.fail(where, "is dropped, and the parent's rule holds to it")
        elif name in ("causes", "outcome"):
            check.subset(where, child.when[name], bound)
        else:
            check.at_most(where, child.when[name], bound)


def _delegation(check: _Check, child: Mandate, parent: Mandate) -> None:
    mine, theirs = child.delegation, parent.delegation
    if mine is None:
        return
    if theirs is None:
        check.fail("delegation", "allows derivation, and the parent's does not")
        return
    check.at_most("delegation.max_depth", mine.max_depth, theirs.max_depth)
    check.at_most("delegation.max_children", mine.max_children, theirs.max_children)
    check.at_most("delegation.envelope", mine.envelope, theirs.envelope)

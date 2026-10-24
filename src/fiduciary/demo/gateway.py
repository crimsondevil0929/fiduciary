"""The input gateway the demo compares with: the policy a careful team writes
in front of the same tools.

It decides each call before it runs, from what the call says: the tool, its
arguments, the order's tenant from the gateway's own entity store, and a
running sum per session, as a temporal policy is scoped (AgentCore's temporal
policies count within one session, whose id the caller supplies). Its policies
are Cedar. With ``cedarpy`` installed (the ``cedar`` extra) the real Cedar
engine evaluates them; without it, a built-in evaluator of exactly the forms
:data:`POLICIES` uses decides, and says so. The tests hold both to the same
decision on every request the demo makes.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import time
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

__all__ = ["POLICIES", "Condition", "Decision", "Gateway", "Policy", "cedar_text"]

SESSION_LIMIT_CENTS: Final = 10000
"""The temporal policy: $100.00 of refunds per session."""
REFUND_LIMIT_CENTS: Final = 20000
DISCOUNT_LIMIT: Final = 15


@dataclass(frozen=True, slots=True)
class Condition:
    """``left [+ plus] op right``: each side a path (``context.x``,
    ``principal.x``, ``resource.x``) or a literal."""

    left: str
    op: str
    right: str | int
    plus: str | None = None

    def cedar(self) -> str:
        left = f"{self.left} + {self.plus}" if self.plus else self.left
        right = (
            self.right if isinstance(self.right, int) or "." in self.right else f'"{self.right}"'
        )
        return f"{left} {self.op} {right}"


@dataclass(frozen=True, slots=True)
class Policy:
    policy_id: str
    action: str
    when: tuple[Condition, ...]
    about: str

    def cedar(self) -> str:
        head = f'@id("{self.policy_id}")\npermit (principal, action == Action::"{self.action}", '
        head += "resource)"
        if not self.when:
            return f"// {self.about}\n{head};"
        body = " &&\n    ".join(c.cedar() for c in self.when)
        return f"// {self.about}\n{head}\nwhen {{\n    {body}\n}};"


_OWN: Final = Condition("resource.tenant", "==", "principal.tenant")
POLICIES: Final = (
    Policy("lookup", "lookup_order", (), "Agents may read orders."),
    Policy(
        "refund",
        "refund_order",
        (
            _OWN,
            Condition("context.amount_cents", "<=", REFUND_LIMIT_CENTS),
            Condition(
                "context.session_refunded_cents",
                "<=",
                SESSION_LIMIT_CENTS,
                plus="context.amount_cents",
            ),
        ),
        "Refunds: the agent's own tenant, at most $200.00 each, at most $100.00 per session.",
    ),
    Policy(
        "discount",
        "apply_discount",
        (_OWN, Condition("context.percent", "<=", DISCOUNT_LIMIT)),
        "Loyalty discounts: the agent's own tenant, at most 15%.",
    ),
    Policy("dispute", "flag_dispute", (_OWN,), "Disputes: the agent's own tenant."),
)


def cedar_text(policies: tuple[Policy, ...] = POLICIES) -> str:
    return "\n\n".join(p.cedar() for p in policies) + "\n"


@dataclass(frozen=True, slots=True)
class Decision:
    """What the gateway decided about one call, and what it decided it from."""

    tool: str
    session: str
    decision: str
    """``PERMIT`` or ``DENY``."""
    policies: tuple[str, ...]
    request: Mapping[str, Any]
    engine: str
    micros: int

    @property
    def permitted(self) -> bool:
        return self.decision == "PERMIT"

    def to_json(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "session": self.session,
            "decision": self.decision,
            "policies": list(self.policies),
            "request": dict(self.request),
            "engine": self.engine,
            "micros": self.micros,
        }


def _policy_id(reason: str) -> str:
    """Cedar names a policy by its place in the set (``policy1``): ours by its ``@id``."""
    if reason.startswith("policy") and reason[6:].isdigit() and int(reason[6:]) < len(POLICIES):
        return POLICIES[int(reason[6:])].policy_id
    return reason


def _cents(text: object) -> int:
    return int((Decimal(str(text)) * 100).to_integral_value())


class Gateway:
    """Cedar over each call's input, and a refund sum per session (module docstring).

    :param principal: The agent, as the gateway names it.
    :param tenant: The tenant the agent acts for.
    :param tenants: Each order's tenant: the gateway's entity store.
    :param engine: ``"auto"`` (cedarpy when installed), ``"cedarpy"`` or ``"builtin"``.
    """

    def __init__(
        self,
        principal: str,
        tenant: str,
        tenants: Mapping[int, str],
        *,
        engine: str = "auto",
    ) -> None:
        self._principal = principal
        self._tenant = tenant
        self._tenants = dict(tenants)
        self._sums: dict[str, int] = defaultdict(int)
        self._cedar: Any = None
        if engine not in ("auto", "cedarpy", "builtin"):
            raise ValueError(f"engine is auto, cedarpy or builtin, not {engine!r}")
        if engine != "builtin":
            try:
                self._cedar = importlib.import_module("cedarpy")
            except ImportError:
                if engine == "cedarpy":
                    raise
        if self._cedar is not None:
            self.engine = f"cedarpy {importlib.metadata.version('cedarpy')}"
        else:
            self.engine = "built-in evaluator"
        self.policies = cedar_text()
        self._parsed: Any = self.policies
        if self._cedar is not None and hasattr(self._cedar, "PolicySet"):
            self._parsed = self._cedar.PolicySet.from_str(self.policies)  # parsed once

    def request(self, session: str, tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """The Cedar request the gateway forms from one call."""
        context: dict[str, Any] = {"session": session}
        if "amount" in arguments:
            context["amount_cents"] = _cents(arguments["amount"])
            context["session_refunded_cents"] = self._sums[session]
        if "percent" in arguments:
            context["percent"] = int(arguments["percent"])
        order = int(arguments.get("order_id", 0))
        return {
            "principal": f'Agent::"{self._principal}"',
            "action": f'Action::"{tool}"',
            "resource": f'Order::"{order}"',
            "context": context,
        }

    def _entities(self, order: int) -> list[dict[str, Any]]:
        return [
            {
                "uid": {"type": "Agent", "id": self._principal},
                "attrs": {"tenant": self._tenant},
                "parents": [],
            },
            {
                "uid": {"type": "Order", "id": str(order)},
                "attrs": {"tenant": self._tenants.get(order, "")},
                "parents": [],
            },
        ]

    def decide(self, session: str, tool: str, arguments: Mapping[str, Any]) -> Decision:
        """Decide one call; a permitted refund adds to its session's sum."""
        request = self.request(session, tool, arguments)
        order = int(arguments.get("order_id", 0))
        started = time.perf_counter_ns()
        if self._cedar is not None:
            result = self._cedar.is_authorized(request, self._parsed, self._entities(order))
            permitted = bool(result.allowed)
            reasons = tuple(sorted(_policy_id(str(r)) for r in result.diagnostics.reasons))
        else:
            reasons = self._builtin(tool, request["context"], order)
            permitted = bool(reasons)
        micros = (time.perf_counter_ns() - started) // 1000
        if permitted and "amount_cents" in request["context"]:
            self._sums[session] += int(request["context"]["amount_cents"])
        return Decision(
            tool,
            session,
            "PERMIT" if permitted else "DENY",
            reasons,
            request,
            self.engine,
            micros,
        )

    def _builtin(self, tool: str, context: Mapping[str, Any], order: int) -> tuple[str, ...]:
        """The policies that permit the request: Cedar's semantics for
        :data:`POLICIES`' forms (a missing attribute fails its policy)."""
        attributes = {
            "principal.tenant": self._tenant,
            "resource.tenant": self._tenants.get(order, ""),
            **{f"context.{k}": v for k, v in context.items()},
        }
        missing = object()

        def value(term: str | int) -> Any:
            if isinstance(term, int):
                return term
            return attributes.get(term, missing) if "." in term else term

        def holds(c: Condition) -> bool:
            left, right = value(c.left), value(c.right)
            if c.plus is not None:
                extra = value(c.plus)
                if left is missing or extra is missing:
                    return False
                left = left + extra
            if left is missing or right is missing:
                return False
            if c.op == "==":
                return bool(left == right)
            if c.op == "<=":
                return bool(left <= right)
            raise AssertionError(c.op)

        return tuple(
            p.policy_id for p in POLICIES if p.action == tool and all(holds(c) for c in p.when)
        )

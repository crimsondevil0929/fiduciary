"""The bridge: the sessions an agent acts through (``docs/FIDUCIARY_SPEC.md`` §8).

A :class:`Session` is one mandate version in force, one tenant (or none), one
trajectory and one billing scope. It is recorded (``session.opened``) before
its first call, and its methods are the agent's whole surface (F8-1):
:meth:`Session.call`, :meth:`Session.govern`, :meth:`Session.tool_definitions`,
:meth:`Session.tickets` and :meth:`Session.close`. Nothing reachable from it
submits or relays an approval (F7-4).

**A call** (F8-2) resolves the tool against the bindings the lineage's
version in force pins, by digest (F4-16), validates the arguments against the
binding's schema and the canonical domain before anything is built (F4-13),
and then:

- for a ``plan`` tool, builds the plan with ``PlanBuilder`` from the binding
  alone, the arguments bound only where the binding says (``$.<name>``,
  ``$session.tenant``, ``$session.trajectory``, F4-14), and runs it on the
  lineage's engine. A refused plan is repaired; a proposal raises a
  ``refused`` ticket, and a warrant an autonomous rule forms is executed here,
  by the process that holds the proposal (F7-14).
- for a ``passthrough`` tool, meters the call at the binding's price through
  AgentGov, and runs the handler the deployment registered (F4-15).

Every call, of either kind, counts against the cognitive breaker and the
session's velocity (F6-5). A call refused before anything is built or metered
is recorded (``session.refused``), naming no agent text.

**A result** (:class:`CallResult`) carries interlock's sanitized feedback, and,
when the call escalated, the ticket's id, class and deadline (F8-3). It never
carries the diff, the verdict, another tenant, a bound value or an approver.

**Model calls** go through :meth:`Session.govern`, which meters them on the
session's billing scope with the compiled cognitive breaker, the ``hold`` and
``max_output_tokens`` ceilings and the model allowlist (§8.3). A halt is
answered by interlock's :class:`~interlock.recovery.RecoveryRuntime`: each
step raises :class:`SessionHaltedError` with the transcript to send next. A reserve
spent out, with a quote, raises an ``extension`` ticket; a ``grant`` warrant
executed moves the billing scope to the scope it funded, through
``RecoveryRuntime.extend`` (F8-8). A safety halt is never quoted (F8-9).

**In process** (F8-10), :meth:`Fiduciary.session` refuses to start unless the
configuration says ``in_process = "tool-calls-only"``: an agent that runs code
it writes shares the address space with the credentials, and is bridged out of
process instead (:mod:`fiduciary.mcp`).
"""

from __future__ import annotations

import dataclasses
import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from agentgov import CognitiveBreaker
from agentgov.cognitive import canonical_arguments
from agentgov.core import Authorization
from agentgov.exceptions import (
    AgentGovError,
    BudgetExceededError,
    CircuitBreakerError,
    RunawayLoopDetectedError,
)
from agentgov.interceptor import (
    ModelPricing,
    SpendGuard,
    TokenUsage,
    default_usage_extractor,
    pricing_for,
)
from interlock.builder import PlanBuilder
from interlock.exceptions import (
    InterlockError,
    PlanError,
    RecoveryError,
    RecoveryExhaustedError,
)
from interlock.extension import BudgetGuard, ExtensionGrant, ExtensionRequest
from interlock.feedback import AgentFeedback, feedback_for_error
from interlock.outbound import schema_problems
from interlock.recovery import RecoveryRuntime, RecoveryStep, Trip, TripKind
from interlock.types import EffectId, EffectKind, EffectPlan, OutboundRequest

from fiduciary import fidu1
from fiduciary._upstream import RECOVERY_REOPENS
from fiduciary.authority import BUILTIN_TOOLS, Authority
from fiduciary.binding import Binder, BindingError, MandateEngine
from fiduciary.documents import Mandate, Ticket, ToolBinding
from fiduciary.escalation import Escalation, EscalationError
from fiduciary.exceptions import FiduciaryError
from fiduciary.executor import Executor
from fiduciary.log import Log
from fiduciary.store import State

if TYPE_CHECKING:
    from agentgov import BudgetManager
    from interlock.deliveries import OutboxOperations
    from interlock.records import RecordLog

    from fiduciary.documents import Record

__all__ = [
    "MAX_ARGUMENT_BYTES",
    "REQUEST_SCHEMA",
    "STATUS_SCHEMA",
    "CallResult",
    "Fiduciary",
    "GovernedModel",
    "Session",
    "SessionError",
    "SessionHaltedError",
    "TicketSummary",
    "build_plan",
    "check_arguments",
]

MAX_ARGUMENT_BYTES: Final = 16_384
"""The most a call's arguments may take, as canonical JSON: a bound on the
work a call can ask of the validator, whatever its schema says."""

REQUEST_SCHEMA: Final[Mapping[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", "statement"],
    "properties": {
        "kind": {"type": "string", "enum": ["amend", "undo"]},
        "statement": {"type": "string", "minLength": 1, "maxLength": 512},
        "plan_id": {"type": "string", "pattern": "^plan-[0-9a-f]{12}$"},
        "mandate": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    },
}
"""``fiduciary_request``: an ``undo`` names a plan this session committed; an
``amend`` names a next version a principal proposed (the agent asks for a
bound; it never writes one)."""

STATUS_SCHEMA: Final[Mapping[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ticket_id"],
    "properties": {
        "ticket_id": {
            "type": "string",
            "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        }
    },
}
"""``fiduciary_ticket_status``: read-only, and only this session's tickets."""

_BUILTIN_DESCRIPTIONS: Final = {
    "fiduciary_request": (
        "Ask the principals for something this session's mandate does not allow: undo a plan "
        "this session committed (kind undo, plan_id), or adopt a next mandate version a "
        "principal has proposed (kind amend, mandate). The statement, at most 512 characters, "
        "is shown to them as your claim."
    ),
    "fiduciary_ticket_status": "Report the state of a ticket this session raised.",
}
_SOURCE: Final = re.compile(r"\$(\.[A-Za-z_][A-Za-z0-9_]{0,63}|session\.(tenant|trajectory))")


class SessionError(FiduciaryError):
    """A session that may not start, or a governed call that may not be made."""


@dataclass(frozen=True, slots=True)
class TicketSummary:
    """What a session may say of a ticket (F8-3): its id, class and deadline,
    and its state. Never the bound, the evidence, or who decides."""

    ticket_id: str
    ticket_class: str
    not_after: datetime
    status: str = "open"

    def to_json(self) -> dict[str, Any]:
        return {
            "ticket_id": self.ticket_id,
            "class": self.ticket_class,
            "not_after": fidu1.format_instant(self.not_after),
            "status": self.status,
        }

    def render(self) -> str:
        return (
            f"Ticket {self.ticket_id} ({self.ticket_class}) is {self.status}; principals may "
            f"decide it until {fidu1.format_instant(self.not_after)}."
        )


class SessionHaltedError(FiduciaryError):
    """A model call the session halted.

    :ivar kind: What halted it: ``thrashing``, ``runaway``, ``budget``,
        ``halted``, or ``model`` (a model the mandate does not admit).
    :ivar messages: When a recovery step was taken, the transcript to send
        next, with :attr:`max_tokens` (and :attr:`betas`, when the step needs
        them); the call made with it is billed to the step. ``None`` when
        the halt stands.
    :ivar ticket: The ``extension`` ticket a spent-out reserve raised, when
        one was: the halt stands until its warrant is executed.
    """

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        messages: tuple[object, ...] | None = None,
        max_tokens: int | None = None,
        betas: tuple[str, ...] = (),
        ticket: TicketSummary | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.messages = messages
        self.max_tokens = max_tokens
        self.betas = betas
        self.ticket = ticket


@dataclass(frozen=True, slots=True)
class CallResult:
    """What a call tells the agent (F8-2, F8-3).

    :ivar outcome: ``committed`` or ``refused`` (a plan adjudicated),
        ``served`` (a passthrough tool, or a built-in), ``rejected`` (refused
        before anything was built or metered), ``halted`` (the billing scope
        is halted, or the call halted it), or ``error`` (the plan never
        reached adjudication).
    :ivar feedback: Interlock's sanitized feedback, rendered.
    :ivar repair: A refused plan's repair feedback, rendered: which of its own
        steps would be admitted.
    :ivar ticket: The ticket the call raised or folded into.
    :ivar value: What a passthrough tool or a built-in returned.
    """

    tool: str | None
    outcome: str
    feedback: str
    repair: str | None = None
    ticket: TicketSummary | None = None
    value: Any = None
    repaired: bool = False
    """The repair a rule warranted was committed in this call."""

    @property
    def committed(self) -> bool:
        return self.outcome == "committed"

    @property
    def ok(self) -> bool:
        return self.outcome in ("committed", "served")

    def render(self) -> str:
        """The text an agent is given."""
        lines = [self.feedback]
        if self.repair:
            lines.append(self.repair)
        if self.repaired:
            lines.append("A rule of the mandate accepted that repair, and it committed.")
        if self.ticket is not None:
            lines.append(self.ticket.render())
        return "\n".join(line for line in lines if line)

    def to_json(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "outcome": self.outcome,
            "feedback": self.feedback,
            "repair": self.repair,
            "repaired": self.repaired,
            "ticket": None if self.ticket is None else self.ticket.to_json(),
            "value": self.value,
        }


# --------------------------------------------------------------------------
# arguments and plans (F4-13, F4-14)
# --------------------------------------------------------------------------


def check_arguments(schema: Mapping[str, Any], arguments: object) -> list[str]:
    """Every way ``arguments`` falls outside the canonical domain or
    ``schema``: an object, with no float, no integer past 2**53, nothing
    JSON lacks, at most :data:`MAX_ARGUMENT_BYTES` as canonical JSON, and every
    keyword of interlock's schema subset holding (F4-13). Empty when it holds.
    """
    if not isinstance(arguments, Mapping) or not all(isinstance(k, str) for k in arguments):
        return ["$ is not an object of named arguments"]
    try:
        size = len(fidu1.canonical(dict(arguments)))
    except FiduciaryError as exc:
        return [f"outside the canonical domain (a float, a number past 2**53, or no JSON): {exc}"]
    if size > MAX_ARGUMENT_BYTES:
        return [f"$ takes {size} bytes as canonical JSON, past {MAX_ARGUMENT_BYTES}"]
    return schema_problems(dict(arguments), schema)


def _source(source: str, arguments: Mapping[str, Any], tenant: str | None, trajectory: str) -> Any:
    if source == "$session.tenant":
        return tenant
    if source == "$session.trajectory":
        return trajectory
    return arguments.get(source[2:])


def _fill(value: Any, arguments: Mapping[str, Any], tenant: str | None, trajectory: str) -> Any:
    if isinstance(value, str) and _SOURCE.fullmatch(value):
        return _source(value, arguments, tenant, trajectory)
    if isinstance(value, Mapping):
        return {k: _fill(v, arguments, tenant, trajectory) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, arguments, tenant, trajectory) for v in value]
    return value


def build_plan(
    binding: ToolBinding,
    arguments: Mapping[str, Any],
    *,
    scope: str,
    trajectory: str,
    tenant: str | None,
) -> EffectPlan:
    """The plan a ``plan`` binding builds from validated ``arguments`` (F8-2):
    its statements, tables, sinks and operations the binding's, every
    parameter from ``$.<name>`` or the session, every effect stamped with the
    session's tenant when the binding takes it (F4-14).

    :raises PlanError: When interlock refuses to build it.
    """
    if binding.kind != "plan":
        raise PlanError(f"tool {binding.name} is a passthrough: it builds no plan")
    stamped = tenant if binding.tenant == "session" else None
    builder = PlanBuilder(
        scope, trajectory_id=trajectory, intent=f"FIDU1 tool {binding.name} {binding.digest[:16]}"
    )
    for effect in binding.effects:
        builder.add(
            EffectKind(effect.kind),
            table=effect.table,
            statement=effect.statement,
            parameters={
                name: _source(source, arguments, tenant, trajectory)
                for name, source in sorted(effect.parameters.items())
            },
            tenant_id=stamped,
            stated_rows=effect.stated_rows,
            effect_id=EffectId(effect.effect_id),
            after=[EffectId(a) for a in effect.after],
        )
    for request in binding.requests:
        compensation = None
        if request.compensation is not None:
            compensation = OutboundRequest(
                sink=str(request.compensation["sink"]),
                operation=str(request.compensation["operation"]),
                payload=_fill(request.compensation["payload"], arguments, tenant, trajectory),
            )
        builder.enqueue(
            sink=request.sink,
            operation=request.operation,
            payload=_fill(request.payload, arguments, tenant, trajectory),
            tenant_id=stamped,
            compensation=compensation,
            effect_id=EffectId(request.request_id),
            after=[EffectId(a) for a in request.after],
        )
    return builder.build()


# --------------------------------------------------------------------------
# the service a session runs in
# --------------------------------------------------------------------------

Handler = Callable[[Mapping[str, Any]], Any]
"""A passthrough tool's implementation: validated arguments in, a JSON value out."""


class Fiduciary:
    """Fiduciary in process: the log, the binder, escalation, the executor,
    and the sessions they serve (module docstring).

    Build one with :meth:`from_config`, or from its parts.

    :param in_process: ``"tool-calls-only"`` lets :meth:`session` start in this
        process (F8-10); ``"no"`` refuses it, for a deployment that bridges
        out of process only (:mod:`fiduciary.mcp` passes ``bridged=True``).
    :param handlers: Each passthrough tool's implementation, by tool name.
    :param pricing: Rates for models AgentGov does not price; an allowlisted
        model with no rate is never called (an unmetered model is an
        unmetered budget).
    :param runtime_records: The ILOK1 log of recovery and extension records.
    """

    def __init__(
        self,
        authority: Authority,
        binder: Binder,
        governor: BudgetManager,
        *,
        runtime_records: RecordLog | None = None,
        outbox: OutboxOperations | None = None,
        operator_log: Any = None,
        operator_keyring: Any = None,
        executor_signer: Any = None,
        receipts: Any = None,
        handlers: Mapping[str, Handler] | None = None,
        pricing: Mapping[str, ModelPricing] | None = None,
        in_process: str = "no",
        checkpoint: Callable[[str], None] | None = None,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        if in_process not in ("no", "tool-calls-only"):
            raise SessionError(f'in_process is "no" or "tool-calls-only", not {in_process!r}')
        self._authority = authority
        self._binder = binder
        self._governor = governor
        self._runtime_records = runtime_records
        self._outbox = outbox
        self._receipts = receipts
        self._handlers = dict(handlers or {})
        self._pricing = dict(pricing or {})
        self._in_process = in_process
        self._on_close = on_close
        self._lock = threading.RLock()
        self._guards: dict[str, BudgetGuard] = {}
        self._recoveries: dict[str, RecoveryRuntime] = {}
        self._recovering: dict[str, set[str]] = {}
        """Each lineage's sessions that billed its recovery: the last to close closes it."""
        self._escalation = Escalation(authority, governor, checkpoint=checkpoint)
        self._executor = Executor(
            binder,
            escalation=self._escalation,
            outbox=outbox,
            operator_log=operator_log,
            operator_keyring=operator_keyring,
            executor_signer=executor_signer,
            guard_for=self.guard,
            runtime_records=runtime_records,
            checkpoint=checkpoint,
        )
        self._binder.resume()
        self._executor.resolve()

    @classmethod
    def from_config(cls, path: str | Path, *, bridged: bool = False) -> Fiduciary:
        """Fiduciary as ``fiduciary.toml`` describes it (§10.5): the store and
        its log, the ledger, the deployment ``interlock.toml`` names, the
        receipt log, the runtime and executor keys, and ``[bridge]``.

        :param bridged: Sessions serve an agent out of process (MCP), so
            ``in_process`` does not apply.
        :raises ConfigError: On anything the configuration lacks.
        """
        from fiduciary.config import open_fiduciary

        return open_fiduciary(cls, path, bridged=bridged)

    # -- what sessions share -------------------------------------------------------

    @property
    def log(self) -> Log:
        return self._authority.log

    @property
    def escalation(self) -> Escalation:
        return self._escalation

    @property
    def executor(self) -> Executor:
        return self._executor

    @property
    def outbox(self) -> OutboxOperations | None:
        """Interlock's outbox, as the executor acts on it, or ``None``."""
        return self._outbox

    @property
    def binder(self) -> Binder:
        return self._binder

    @property
    def governor(self) -> BudgetManager:
        return self._governor

    def handler(self, name: str) -> Handler | None:
        return self._handlers.get(name)

    def register(self, name: str, handler: Handler) -> None:
        """Implement passthrough tool ``name``: the deployment's act, never a
        session's (a proxy registers each upstream tool a binding names)."""
        with self._lock:
            self._handlers[name] = handler

    @property
    def lock(self) -> threading.RLock:
        """Held by a bridge around each call, and by the service's turns."""
        return self._lock

    def price(self, model: str) -> ModelPricing | None:
        """A model's rates: the configured ones, else AgentGov's published ones."""
        found = self._pricing.get(model)
        if found is not None:
            return found
        try:
            return pricing_for(model)
        except KeyError:
            return None

    def guard(self, mandate_id: str) -> BudgetGuard | None:
        """The lineage's budget guard, with its version's margin and ttl:
        ``None`` when its mandate allows no extension, or no runtime log is
        configured to sign quotes into."""
        with self._lock:
            found = self._guards.get(mandate_id)
            if found is not None or self._runtime_records is None:
                return found
            try:
                compiled = self._binder.engine(mandate_id).compiled
            except BindingError:
                return None
            if compiled.guard is None:
                return None
            found = BudgetGuard(
                self._governor,
                self._runtime_records,
                receipts=self._receipts,
                margin=compiled.guard.margin,
                ttl=compiled.guard.ttl,
                clock=self.log.now,
            )
            self._guards[mandate_id] = found
            return found

    def recovery(
        self, mandate_id: str, tools: Sequence[str], trajectory: str, *, session: str = ""
    ) -> RecoveryRuntime:
        """The lineage's recovery runtime: opened on its first halt, funding
        ``{scope}/recovery``, and shared by every session that halts while it
        is open, so a step's tightening holds for all of them. ``session``
        names the session that bills it; the last of them to close closes it
        (:meth:`release_recovery`). A halt after that opens it again, on a
        fresh reserve (``{scope}/recovery/ext-N``), where Interlock reopens a
        closed recovery (U-6).

        :raises RecoveryError: When the mandate has no recovery policy, no
            runtime log is configured, the reserve cannot be funded, or the
            lineage's recovery was closed and Interlock does not reopen one
            (§12, U-6).
        """
        with self._lock:
            found = self._recoveries.get(mandate_id)
            if found is not None:
                if session:
                    self._recovering.setdefault(mandate_id, set()).add(session)
                return found
            engine = self._binder.engine(mandate_id)
            policy = engine.compiled.recovery
            if policy is None or self._runtime_records is None:
                raise RecoveryError(f"mandate {mandate_id} names no recovery policy")
            reopen: dict[str, Any] = {"reopen": True} if RECOVERY_REOPENS else {}
            found = RecoveryRuntime(
                self._governor,
                engine.mandate.subject.scope,
                policy,
                self._runtime_records,
                tools=tools,
                trajectory=trajectory,
                guard=self.guard(mandate_id),
                **reopen,
            )
            self._recoveries[mandate_id] = found
            if session:
                self._recovering.setdefault(mandate_id, set()).add(session)
            return found

    def recovering(self, mandate_id: str) -> RecoveryRuntime | None:
        with self._lock:
            return self._recoveries.get(mandate_id)

    def release_recovery(self, mandate_id: str, session: str) -> None:
        """``session`` bills the lineage's recovery no more. When no session
        does, close it: an unsettled step's hold is voided, and the unused
        reserve, and what any grant left, returns to the scope it came from
        rather than standing on ``{scope}/recovery``."""
        with self._lock:
            users = self._recovering.get(mandate_id)
            if users is None or session not in users:
                return
            users.discard(session)
            if users:
                return
            del self._recovering[mandate_id]
            runtime = self._recoveries.pop(mandate_id, None)
        if runtime is not None:
            runtime.close()

    # -- sessions -------------------------------------------------------------------

    def session(
        self,
        mandate: str,
        *,
        tenant: str | None = None,
        trajectory: str | None = None,
        bridged: bool = False,
    ) -> Session:
        """Open a session of the lineage ``mandate`` names (its ``mandate_id``,
        or its subject scope), recorded before its first call.

        :raises SessionError: In process without ``in_process =
            "tool-calls-only"`` (F8-10), or for a lineage that is not bound
            and in force, or a tenant its mandate does not admit.
        """
        if not bridged and self._in_process != "tool-calls-only":
            raise SessionError(
                'a session in this process needs in_process = "tool-calls-only" in the '
                "configuration (F8-10): an agent that runs code it writes is bridged out of "
                "process, over MCP"
            )
        self._binder.follow()
        state = self.log.state()
        mandate_id = self._lineage(state, mandate)
        session_id = str(uuid.uuid4())
        trajectory = trajectory or f"fidu1-session-{session_id}"
        try:
            with self.log.store.transaction():
                now = self.log.now()
                body = self._authority.decide.open_session(
                    self.log.state(), session_id, mandate_id, tenant, trajectory, now
                )
                self.log.append("session.opened", body, issued_at=now)
        except FiduciaryError as exc:
            raise SessionError(str(exc)) from exc
        return Session(self, body)

    def _lineage(self, state: State, name: str) -> str:
        if name in state.lineages:
            return name
        found = [
            mandate_id
            for mandate_id in state.lineages
            if state.in_force(mandate_id)
            and self._authority.decide.head_mandate(state, mandate_id).subject.scope == name
        ]
        if len(found) != 1:
            raise SessionError(f"no lineage in force is {name!r}, or has it as its subject")
        return found[0]

    # -- the service's own loop --------------------------------------------------------

    def serve_once(self) -> list[Record]:
        """One turn of the service: lapse what is due, judge the intake, and
        execute every warrant formed and not yet executed. What
        ``fiduciary serve`` (Phase 6) runs in a loop; never reachable from a
        session."""
        records = self._escalation.process_intake()
        records.extend(self._executor.resolve())
        for warrant in self.log.state().unexecuted_warrants():
            try:
                records.extend(self._executor.execute(warrant.digest))
            except FiduciaryError:
                continue  # expired, or executing elsewhere: lapse and resolve answer it
        return records

    def close(self) -> None:
        self._binder.close()
        if self._on_close is not None:
            self._on_close()


# --------------------------------------------------------------------------
# a session
# --------------------------------------------------------------------------


class Session:
    """One agent's session (module docstring). Open it with
    :meth:`Fiduciary.session`; its methods are its whole surface (F8-1)."""

    __slots__ = (
        "__weakref__",
        "_billed",
        "_breaker",
        "_calls",
        "_closed",
        "_committed",
        "_fid",
        "_granted",
        "_lock",
        "_opened",
        "_pending",
        "_tickets",
    )

    def __init__(self, fid: Fiduciary, opened: Mapping[str, Any]) -> None:
        self._fid = fid
        self._opened = dict(opened)
        self._lock = threading.RLock()
        self._closed = False
        self._billed: list[str] = [str(opened["scope"])]
        self._calls: deque[float] = deque()
        self._tickets: dict[str, TicketSummary] = {}
        self._granted: set[str] = set()
        self._committed: set[str] = set()
        self._pending: RecoveryStep | None = None
        engine = fid.binder.engine(self.mandate_id)
        self._breaker = CognitiveBreaker(policy=engine.compiled.cognitive, manager=fid.governor)

    # -- what the session is ------------------------------------------------------------

    @property
    def session_id(self) -> str:
        return str(self._opened["session"])

    @property
    def mandate_id(self) -> str:
        return str(self._opened["mandate_id"])

    @property
    def tenant(self) -> str | None:
        tenant = self._opened["tenant"]
        return None if tenant is None else str(tenant)

    @property
    def trajectory(self) -> str:
        return str(self._opened["trajectory"])

    @property
    def billing_scope(self) -> str:
        """Where the session's calls and plans are billed now (F8-8)."""
        with self._lock:
            return self._billed[-1]

    # -- the surface (F8-1) --------------------------------------------------------------

    def tool_definitions(self) -> list[dict[str, Any]]:
        """The Messages API tool list: each binding the version in force pins,
        with its description and schema exactly as the binding holds them,
        and the two built-ins (F8-4)."""
        found = [
            {"name": b.name, "description": b.description, "input_schema": dict(b.input)}
            for b in self._bindings().values()
        ]
        found += [
            {
                "name": name,
                "description": _BUILTIN_DESCRIPTIONS[name],
                "input_schema": dict(
                    REQUEST_SCHEMA if name == "fiduciary_request" else STATUS_SCHEMA
                ),
            }
            for name in BUILTIN_TOOLS
        ]
        return found

    def call(self, name: str, arguments: Mapping[str, Any]) -> CallResult:
        """Call a tool (F8-2; module docstring)."""
        self._check_open()
        self._fid.binder.follow()  # what another process bound, at this plan boundary
        self._take_up()
        if name in BUILTIN_TOOLS:
            return self._builtin(name, arguments)
        binding = self._bindings().get(name)
        if binding is None:
            return self._reject(
                None, "unpinned", "No tool of that name is granted to this session."
            )
        problems = check_arguments(binding.input, arguments)
        if problems:
            text = "; ".join(problems)[:1024]
            return self._reject(name, "arguments", f"The arguments were refused: {text}.")
        if binding.tenant == "session" and self.tenant is None:
            return self._reject(
                name, "tenant", "This tool acts for a tenant; this session has none."
            )
        recovery = self._fid.recovering(self.mandate_id)
        if recovery is not None and name in recovery.revoked:
            return self._reject(
                name, "revoked", "Not run: this tool was revoked for the rest of the task."
            )
        if not self._count():
            return self._reject(name, "velocity", "Too many calls in too short a time; wait.")
        try:
            self._breaker.observe(
                self.billing_scope,
                name,
                canonical_arguments(kwargs=dict(arguments)),
                trajectory=self._trajectory(),
            )
        except CircuitBreakerError:
            return CallResult(
                name, "halted", "This task is halted: it repeated calls without progress."
            )
        if binding.kind == "passthrough":
            result = self._passthrough(binding, arguments)
        else:
            result = self._plan(binding, arguments)
        self._breaker.record_result(
            self.billing_scope, result.outcome + result.feedback, trajectory=self._trajectory()
        )
        return result

    def govern(self, client: object, *, model: str | None = None) -> GovernedModel:
        """Wrap a model client so its calls are metered on the billing scope,
        under the compiled breaker, ceilings and allowlist (§8.3). ``model``
        names the model of a client whose calls name none (``DummyLLM``)."""
        self._check_open()
        return GovernedModel(self, client, model)

    def tickets(self) -> list[TicketSummary]:
        """The tickets this session raised or folded into, as they stand."""
        state = self._fid.log.state()
        return [self._summary(t, state) for t in sorted(self._tickets)]

    def close(self) -> None:
        """Record ``session.closed``, naming every scope the session billed;
        the last session billing the lineage's recovery closes it, returning
        its unused reserve."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._breaker.close()
        self._fid.release_recovery(self.mandate_id, self.session_id)
        log = self._fid.log
        with log.store.transaction():
            now = log.now()
            body = self._fid._authority.decide.close_session(
                log.state(), self.session_id, list(self._billed)
            )
            log.append("session.closed", body, issued_at=now)

    def __enter__(self) -> Session:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- internals ---------------------------------------------------------------------

    def _check_open(self) -> None:
        if self._closed:
            raise SessionError(f"session {self.session_id} is closed")

    def _engine(self) -> MandateEngine:
        return self._fid.binder.engine(self.mandate_id)

    def _bindings(self) -> dict[str, ToolBinding]:
        """The bindings the lineage's version in force pins now: an amendment
        takes effect at the next call, as at the engine's next plan (F1-3)."""
        mandate = self._engine().mandate
        tools = self._fid.binder.deployment.tools
        found = {}
        for pin in mandate.tools.bindings:
            binding = tools.get(pin.digest)
            if binding is not None and binding.name == pin.name:
                found[pin.name] = binding
        return found

    def _trajectory(self) -> str:
        recovery = self._fid.recovering(self.mandate_id)
        return self.trajectory if recovery is None else recovery.trajectory

    def _count(self) -> bool:
        """Count a call against ``governance.max_calls`` per ``seconds``: the
        session's own velocity, which the governor's backs (F6-5)."""
        governance = self._engine().mandate.governance
        now = time.monotonic()
        with self._lock:
            while self._calls and now - self._calls[0] >= governance.seconds:
                self._calls.popleft()
            if len(self._calls) >= governance.max_calls:
                return False
            self._calls.append(now)
            return True

    def _reject(self, tool: str | None, code: str, feedback: str) -> CallResult:
        """A call refused before anything was built or metered, recorded."""
        log = self._fid.log
        with log.store.transaction():
            now = log.now()
            body = self._fid._authority.decide.refuse_call(log.state(), self.session_id, tool, code)
            log.append("session.refused", body, issued_at=now)
        return CallResult(tool, "rejected", feedback)

    def _summary(self, ticket_id: str, state: State | None = None) -> TicketSummary:
        found = self._tickets[ticket_id]
        status = (state or self._fid.log.state()).ticket_status(ticket_id) or found.status
        return TicketSummary(found.ticket_id, found.ticket_class, found.not_after, status)

    def _note(self, ticket: Ticket) -> TicketSummary:
        summary = TicketSummary(ticket.ticket_id, ticket.ticket_class, ticket.not_after)
        with self._lock:
            self._tickets[ticket.ticket_id] = summary
        return self._summary(ticket.ticket_id)

    def _execute(self, warrant: Any) -> bool:
        """Execute a warrant a rule formed as this session raised its ticket:
        this process holds the proposal or the quote it names (F7-14)."""
        try:
            records = self._fid.executor.execute(warrant)
        except FiduciaryError:
            return False
        return bool(records) and records[-1].kind == "execution.applied"

    # -- a plan tool (F8-2) ---------------------------------------------------------

    def _plan(self, binding: ToolBinding, arguments: Mapping[str, Any]) -> CallResult:
        name = binding.name
        try:
            plan = build_plan(
                binding,
                arguments,
                scope=self.billing_scope,
                trajectory=self.trajectory,
                tenant=self.tenant,
            )
        except PlanError as exc:
            return CallResult(name, "error", feedback_for_error(_empty_plan(self), exc).render())
        engine = self._engine()
        plan = self._with_facts(engine, binding, plan)
        try:
            result = engine.execute(plan)
        except BindingError:
            return CallResult(name, "halted", "This mandate no longer governs; nothing changed.")
        except InterlockError as exc:
            feedback = (
                exc.feedback
                if isinstance(exc.feedback, AgentFeedback)
                else feedback_for_error(plan, exc)
            )
            return CallResult(name, "error", feedback.render())
        rendered = _lines(result.feedback.render() if result.feedback is not None else "")
        if result.committed:
            plan_id = str(plan.plan_id)
            with self._lock:
                self._committed.add(plan_id)
            return CallResult(
                name,
                "committed",
                _committed(binding, plan_id, rendered),
                value={"plan_id": plan_id},
            )
        try:
            repair = engine.engine.repair(plan)
        except InterlockError:
            return CallResult(name, "refused", rendered)
        if (
            repair.proposal is None
            or repair.already_admissible
            or not _whole(plan, set(repair.kept))
        ):
            # No part of the call stands alone: the binding's steps that wait
            # for a kept one would be dropped (a debit without its refund).
            return CallResult(name, "refused", rendered)
        part = (
            f"Only part of this call would be admitted: keeping {', '.join(repair.kept)}, "
            f"dropping {', '.join(d.effect_id for d in repair.dropped)}."
        )
        try:
            ticket, warrant = self._fid.escalation.raise_refused(
                self.mandate_id, repair, receipt=result.receipt
            )
        except (EscalationError, FiduciaryError):
            return CallResult(name, "refused", rendered)
        if ticket is None:
            self._reject(name, "max_open", "")
            return CallResult(
                name,
                "refused",
                rendered + "\nNot escalated: this mandate has as many open tickets as it allows.",
            )
        repaired = warrant is not None and self._execute(warrant)
        if repaired:
            advice = f"{part} A rule of the mandate accepted that part, and it committed."
        else:
            advice = (
                f"{part} Whether to run that part is the principals' decision, not yours: it "
                f"was sent to them as a ticket. You cannot resubmit part of a call."
            )
        return CallResult(
            name,
            "refused",
            rendered,
            repair=advice,
            ticket=self._note(ticket),
            repaired=repaired,
        )

    def _with_facts(
        self, engine: MandateEngine, binding: ToolBinding, plan: EffectPlan
    ) -> EffectPlan:
        """``plan``, consuming the facts its writes may answer to (F8-2): every
        attested fact pending for the billing scope, of a kind a ``data.facts``
        rule names for a table the binding writes, for the session's tenant or
        none. Unchanged when the engine verifies no facts, or no rule applies."""
        tables = {e.table for e in binding.effects}
        kinds = {
            kind for rule in engine.mandate.data.facts if rule.table in tables for kind in rule.kind
        }
        if not kinds:
            return plan
        try:
            pending = engine.engine.facts(plan.scope_id)
        except InterlockError:
            return plan  # no inbox keys: the engine consumes no fact, so none is named
        found = tuple(
            sorted(
                (
                    f.fact_id
                    for f in pending
                    if f.kind in kinds and f.tenant_id in (None, self.tenant)
                ),
                key=str,
            )
        )
        return dataclasses.replace(plan, facts=found) if found else plan

    # -- a passthrough tool (F4-15) ------------------------------------------------------

    def _passthrough(self, binding: ToolBinding, arguments: Mapping[str, Any]) -> CallResult:
        name = binding.name
        handler = self._fid.handler(name)
        if handler is None:
            return CallResult(name, "error", "This tool has no implementation here; nothing ran.")
        held: Authorization | None = None
        if binding.price > 0:
            try:
                found = self._hold(binding.price, f"FIDU1 session {self.session_id} tool {name}")
            except SessionHaltedError as exc:
                return CallResult(name, "halted", str(exc), ticket=exc.ticket)
            held = found
        try:
            value = handler(dict(arguments))
            fidu1.canonical({"value": value})
        except Exception:
            if held is not None:
                self._fid.governor.void(held, memo=f"tool {name} failed")
            return CallResult(name, "error", "The tool failed; nothing was charged.")
        if held is not None:
            self._capture(held, binding.price, f"FIDU1 session {self.session_id} tool {name}")
        return CallResult(name, "served", "", value=value)

    def _hold(self, amount: Decimal, memo: str) -> Authorization:
        """Hold ``amount`` on the billing scope: through the recovery runtime
        while one bills, through the lineage's guard (a quote in place of an
        overdraft), else through the governor.

        :raises SessionHaltedError: When the scope is halted, or the amount does
            not fit: with the ``extension`` ticket a quote raised.
        """
        recovery = self._fid.recovering(self.mandate_id)
        guard = self._fid.guard(self.mandate_id)
        subject = str(self._opened["scope"])
        waiting = self._waiting()
        if waiting is not None and self._fid.governor.available(self.billing_scope) < amount:
            # Asked once: the quote stands until its ticket is decided.
            raise SessionHaltedError(
                "budget", "This task's budget is spent; principals are asked.", ticket=waiting
            )
        if (
            recovery is not None
            and self.billing_scope != subject
            and self._safety(recovery)
            and self._fid.governor.available(self.billing_scope) < amount
        ):
            # The reserve of a safety halt's recovery is spent: the halt
            # stands, and no quote is asked (F8-9).
            raise SessionHaltedError("halted", "This task is halted, and recovery is over.")
        try:
            if recovery is not None and self.billing_scope != subject:
                found: Authorization | ExtensionRequest = recovery.hold(amount, memo=memo)
            elif guard is not None:
                found = guard.authorize(self.billing_scope, amount, memo=memo, task=subject)
            else:
                found = self._fid.governor.authorize(self.billing_scope, amount, memo=memo)
        except AgentGovError as exc:
            raise SessionHaltedError(
                "budget" if isinstance(exc, BudgetExceededError) else "halted",
                "This task is halted; the call was not made.",
            ) from exc
        if isinstance(found, ExtensionRequest):
            raise SessionHaltedError(
                "budget",
                "This task's budget is spent; the call was not made.",
                ticket=self._extension(found),
            )
        return found

    def _waiting(self) -> TicketSummary | None:
        """The ``extension`` ticket this session waits on: open, or warranted
        and its grant not yet taken up."""
        state = self._fid.log.state()
        for ticket_id, summary in sorted(self._tickets.items()):
            if summary.ticket_class != "extension" or ticket_id in self._granted:
                continue
            status = state.ticket_status(ticket_id)
            if status == "open" or (status == "warranted" and self._unexecuted(state, ticket_id)):
                return self._summary(ticket_id, state)
        return None

    @staticmethod
    def _unexecuted(state: State, ticket_id: str) -> bool:
        warrant = state.warrant_of(ticket_id)
        return warrant is not None and state.execution_status(warrant) in (None, "intent")

    def _capture(self, held: Authorization, cost: Decimal, memo: str) -> None:
        try:
            self._fid.governor.capture(held, cost, memo=memo)
        except BudgetExceededError:
            pass  # recorded by AgentGov, and the scope halted: the next call says so

    # -- built-ins (F8-4) ---------------------------------------------------------------

    def _builtin(self, name: str, arguments: Mapping[str, Any]) -> CallResult:
        schema = REQUEST_SCHEMA if name == "fiduciary_request" else STATUS_SCHEMA
        problems = check_arguments(schema, arguments)
        if problems:
            return self._reject(
                name, "arguments", f"The arguments were refused: {'; '.join(problems)[:1024]}."
            )
        if not self._count():
            return self._reject(name, "velocity", "Too many calls in too short a time; wait.")
        if name == "fiduciary_ticket_status":
            ticket_id = str(arguments["ticket_id"])
            if ticket_id not in self._tickets:
                return CallResult(name, "error", "This session raised no ticket with that id.")
            summary = self._summary(ticket_id)
            return CallResult(name, "served", summary.render(), value=summary.to_json())
        return self._request(arguments)

    def _request(self, arguments: Mapping[str, Any]) -> CallResult:
        name = "fiduciary_request"
        statement = str(arguments["statement"])
        escalation = self._fid.escalation
        try:
            self._breaker.observe(
                self.billing_scope,
                name,
                canonical_arguments(kwargs=dict(arguments)),
                trajectory=self._trajectory(),
            )
        except CircuitBreakerError:
            return CallResult(
                name, "halted", "This task is halted: it repeated calls without progress."
            )
        try:
            if arguments["kind"] == "undo":
                plan_id = arguments.get("plan_id")
                outbox = self._fid._outbox
                if plan_id is None or plan_id not in self._committed or outbox is None:
                    return CallResult(name, "error", "An undo names a plan this session committed.")
                ticket, warrant = escalation.raise_undo(
                    self.mandate_id, outbox, str(plan_id), statement=statement
                )
            else:
                proposed = self._proposed(arguments.get("mandate"))
                if proposed is None:
                    return CallResult(
                        name,
                        "error",
                        "An amendment names the next version a principal proposed for this "
                        "mandate.",
                    )
                ticket, warrant = escalation.raise_amend(
                    self.mandate_id, proposed, statement=statement
                )
        except (EscalationError, FiduciaryError):
            return CallResult(name, "error", "Nothing was escalated: there is nothing to ask for.")
        if ticket is None:
            return self._reject(
                name,
                "max_open",
                "Not escalated: this mandate has as many open tickets as it allows.",
            )
        if warrant is not None:
            self._execute(warrant)
        summary = self._note(ticket)
        return CallResult(name, "served", "", ticket=summary, value=summary.to_json())

    def _proposed(self, digest: object) -> Mandate | None:
        if not isinstance(digest, str):
            return None
        from fiduciary.documents import loads

        raw = self._fid.log.store.document(digest)
        if raw is None or raw[0] != "mandate":
            return None
        found = loads(raw[1])
        if not isinstance(found, Mandate) or found.mandate_id != self.mandate_id:
            return None
        return found

    # -- model calls, halts and recovery (§8.3) ---------------------------------------------

    def _halt(
        self, error: BaseException, messages: Sequence[object], max_tokens: int
    ) -> SessionHaltedError:
        """What a halt becomes: a recovery step, an extension ticket, or a
        halt that stands. A safety halt is never quoted (F8-9)."""
        trip = Trip.of(error, scope_id=self.billing_scope, trajectory=self.trajectory)
        waiting = self._waiting()
        if trip.kind is TripKind.BUDGET and waiting is not None:
            return SessionHaltedError(
                "budget", "This task's budget is spent; principals are asked.", ticket=waiting
            )
        tools = list(self._bindings())
        try:
            recovery = self._fid.recovery(
                self.mandate_id, tools, self.trajectory, session=self.session_id
            )
        except (RecoveryError, AgentGovError) as exc:
            return self._unrecovered(
                trip, f"This task is halted ({trip.kind.value}): {type(exc).__name__}."
            )
        with self._lock:
            if recovery.billing_scope not in self._billed:
                self._billed.append(recovery.billing_scope)
        if not messages:
            return SessionHaltedError(trip.kind.value, f"This task is halted ({trip.kind.value}).")
        try:
            step = recovery.recover(trip, messages, max_tokens=max_tokens)
        except RecoveryExhaustedError as exc:
            quote = exc.quote if isinstance(exc.quote, ExtensionRequest) else None
            if quote is None or self._safety(recovery):
                # A recovery that answers a safety halt ends with its reserve:
                # interlock's quote for more is never escalated (F8-9).
                return SessionHaltedError(
                    trip.kind.value, "This task is halted, and recovery is over."
                )
            return SessionHaltedError(
                "budget",
                "The recovery reserve is spent; principals are asked for more.",
                ticket=self._extension(quote),
            )
        except RecoveryError as exc:
            return SessionHaltedError(trip.kind.value, f"Recovery refused the step: {exc}")
        with self._lock:
            self._pending = step
        return SessionHaltedError(
            trip.kind.value,
            f"This task is halted ({trip.kind.value}); recovery step {step.number} "
            f"({step.rung.value}) is ready: send its messages.",
            messages=step.messages,
            max_tokens=step.max_tokens,
            betas=step.betas,
        )

    def _safety(self, recovery: RecoveryRuntime) -> bool:
        """Whether the lineage's recovery answers a halt that is not money's:
        a cognitive trip, a runaway, an operator's or an ancestor's (F8-9).
        Only this recovery's steps count, not a closed one's (U-6)."""
        subject = str(self._opened["scope"])
        episode: Any = getattr(recovery, "episode", None)
        since = 0 if episode is None else int(episode.seq)
        return any(
            record.kind == "recovery.step"
            and record.scope == subject
            and record.seq > since
            and record.body["trip"]["kind"] != TripKind.BUDGET.value
            for record in recovery.records.records()
        )

    def _unrecovered(self, trip: Trip, message: str) -> SessionHaltedError:
        """A halt no recovery answers: a spent-out one is quoted, on the
        billing scope, when the lineage has a guard; any other stands."""
        guard = self._fid.guard(self.mandate_id)
        if trip.kind is not TripKind.BUDGET or guard is None:
            return SessionHaltedError(trip.kind.value, message)
        engine = self._engine()
        step = engine.compiled.interceptor.per_call
        try:
            quote = guard.quote(self.billing_scope, step, task=str(self._opened["scope"]))
        except (InterlockError, AgentGovError):
            return SessionHaltedError(trip.kind.value, message)
        return SessionHaltedError(
            "budget",
            "This task's budget is spent; principals are asked for more.",
            ticket=self._extension(quote),
        )

    def _extension(self, quote: ExtensionRequest) -> TicketSummary | None:
        """Raise the ``extension`` ticket a quote asks for (§7.1); execute the
        warrant a rule forms with it here, where the quote is held."""
        try:
            ticket, warrant = self._fid.escalation.raise_extension(self.mandate_id, quote)
        except FiduciaryError:
            return None
        if ticket is None:
            self._reject(None, "max_open", "")
            return None
        summary = self._note(ticket)
        if warrant is not None and self._execute(warrant):
            self._take_up()
        return self._summary(summary.ticket_id)

    def _take_up(self) -> None:
        """Move billing to the scope each executed grant funded (F8-8)."""
        state = self._fid.log.state()
        for ticket_id, summary in list(self._tickets.items()):
            if summary.ticket_class != "extension" or ticket_id in self._granted:
                continue
            digest = state.warrant_of(ticket_id)
            if (
                state.ticket_status(ticket_id) != "warranted"
                or digest is None
                or state.execution_status(digest) != "applied"
            ):
                continue
            (_, applied, _) = state.executions[digest][-1]
            for result in applied.get("results", ()):
                if result.get("kind") == "grant":
                    self._extend(str(result["scope"]), str(result["grant_id"]), result)
            with self._lock:
                self._granted.add(ticket_id)

    def _extend(self, scope: str, grant_id: str, result: Mapping[str, Any]) -> None:
        recovery = self._fid.recovering(self.mandate_id)
        if recovery is not None:
            records = self._fid._runtime_records
            record = None
            if records is not None and result.get("record") is not None:
                record = next(
                    (r for r in records.records() if r.record_hash == result["record"]), None
                )
            if record is None:
                return  # the grant's record is lost with its log; billing stays
            recovery.extend(
                ExtensionGrant(
                    grant_id=grant_id,
                    quote_id=str(result["quote"]),
                    scope_id=scope,
                    amount=Decimal(str(result["amount"])),
                    carried=Decimal(str(record.body.get("carried", "0"))),
                    approved_by=str(record.body.get("approved_by", "")),
                    reset=bool(record.body.get("reset", False)),
                    record=record,
                )
            )
            scope = recovery.billing_scope
        with self._lock:
            if scope not in self._billed:
                self._billed.append(scope)


def _lines(text: str) -> str:
    """Feedback with a line said once: two violations of one constraint render alike."""
    return "\n".join(dict.fromkeys(text.splitlines()))


def _whole(plan: EffectPlan, kept: set[str]) -> bool:
    """Whether ``kept`` leaves no step of the binding without a step it was
    written to follow: a part that stands on its own."""
    return not any(
        effect.effect_id not in kept and any(d in kept for d in effect.depends_on)
        for effect in plan.effects
    )


def _committed(binding: ToolBinding, plan_id: str, feedback: str) -> str:
    """What a committed call tells the agent: the plan's id, what it queued,
    and how to ask for it to be undone when its requests can be."""
    lines = [f"Committed: {binding.name} ran as plan {plan_id}."]
    if binding.requests:
        sent = ", ".join(sorted({f"{r.sink} {r.operation}" for r in binding.requests}))
        lines.append(f"Outbound request(s) queued for delivery: {sent}.")
    if any(r.compensation is not None for r in binding.requests):
        lines.append(
            "To ask the principals to undo it, call fiduciary_request with kind undo and this "
            "plan_id."
        )
    extra = [line for line in feedback.splitlines()[1:] if line]  # advisory notes, if any
    return "\n".join(lines + extra)


def _empty_plan(session: Session) -> EffectPlan:
    """A stand-in plan for feedback on a plan interlock refused to build:
    feedback reads only what a plan names, and this one names nothing."""
    return EffectPlan(
        plan_id="plan-000000000000",  # type: ignore[arg-type]
        scope_id=session.billing_scope,
        trajectory_id=session.trajectory,
        created_at=session._fid.log.now(),
        effects=(),
    )


class GovernedModel:
    """A model client governed by a session (§8.3): ``client.messages.create``
    (Anthropic's shape) and ``client.complete`` (AgentGov's ``DummyLLM``)
    are metered; nothing else of the client is reachable through it.

    Each call names an allowlisted model with a known price, its
    ``max_tokens`` capped at the mandate's ceiling (and a recovery step's),
    and holds ``budget.per_call`` on the billing scope. A halt raises
    :class:`SessionHaltedError`.
    """

    __slots__ = ("_client", "_model", "_session")

    def __init__(self, session: Session, client: object, model: str | None) -> None:
        self._session = session
        self._client = client
        self._model = model

    @property
    def messages(self) -> _Messages:
        return _Messages(self)

    def complete(self, prompt: str, **kwargs: Any) -> Any:
        """``DummyLLM.complete``, governed."""
        call = getattr(self._client, "complete")  # noqa: B009 - the client's own method
        return self._invoke(call, (prompt,), kwargs, messages=[{"role": "user", "content": prompt}])

    def _invoke(
        self,
        fn: Callable[..., Any],
        args: tuple[object, ...],
        kwargs: dict[str, Any],
        *,
        messages: Sequence[object] | None = None,
    ) -> Any:
        session = self._session
        session._check_open()
        session._take_up()
        fid = session._fid
        engine = session._engine()
        bounds = engine.compiled.interceptor
        model = str(kwargs.get("model") or self._model or "")
        pricing = fid.price(model) if model in bounds.models else None
        if pricing is None:
            session._reject(None, "model", "")
            raise SessionHaltedError(
                "model", "That model is not one this mandate admits, at a known price."
            )
        transcript = list(messages if messages is not None else kwargs.get("messages") or ())
        requested = kwargs.get("max_tokens")
        ceiling = bounds.max_output_tokens
        if isinstance(requested, int) and not isinstance(requested, bool) and requested > 0:
            ceiling = min(ceiling, requested)
        recovery = fid.recovering(session.mandate_id)
        if recovery is not None:
            ceiling = recovery.max_tokens(ceiling)
        if "max_tokens" in kwargs or "messages" in kwargs:
            kwargs = {**kwargs, "max_tokens": ceiling}
        with session._lock:
            step, session._pending = session._pending, None
        if step is not None:
            return self._step(fn, args, kwargs, step, pricing)
        if not session._count():
            raise session._halt(
                RunawayLoopDetectedError(
                    session.billing_scope,
                    engine.mandate.governance.max_calls,
                    float(engine.mandate.governance.seconds),
                ),
                transcript,
                ceiling,
            )
        scope = session.billing_scope
        memo = f"FIDU1 session {session.session_id} model {model}"
        try:
            if recovery is not None and scope != str(session._opened["scope"]):
                return self._recovering(fn, args, kwargs, pricing, bounds.per_call, memo)
            # Checked before any hold, so a call that trips the breaker costs
            # nothing; metered under a memo that names the session (V7).
            session._breaker.observe_call(
                scope,
                getattr(fn, "__name__", "call"),
                args,
                kwargs,
                trajectory=session._trajectory(),
            )
            with SpendGuard(fid.governor, scope, bounds.per_call, memo=memo) as guard:
                response = fn(*args, **kwargs)
                guard.settle(_cost(pricing, response))
            session._breaker.record_result(scope, response, trajectory=session._trajectory())
            return response
        except SessionHaltedError:
            raise
        except (AgentGovError, InterlockError) as exc:
            if not isinstance(exc, CircuitBreakerError | BudgetExceededError):
                raise
            raise session._halt(exc, transcript, ceiling) from exc

    def _step(
        self,
        fn: Callable[..., Any],
        args: tuple[object, ...],
        kwargs: dict[str, Any],
        step: RecoveryStep,
        pricing: ModelPricing,
    ) -> Any:
        """The call a recovery step enables, billed by its own hold."""
        recovery = self._session._fid.recovering(self._session.mandate_id)
        assert recovery is not None
        try:
            response = fn(*args, **kwargs)
        except BaseException:
            recovery.settle(step, Decimal(0))
            raise
        try:
            recovery.settle(step, _cost(pricing, response))
        except BudgetExceededError:
            pass  # recorded; the recovery scope is halted, and the next step says so
        return response

    def _recovering(
        self,
        fn: Callable[..., Any],
        args: tuple[object, ...],
        kwargs: dict[str, Any],
        pricing: ModelPricing,
        per_call: Decimal,
        memo: str,
    ) -> Any:
        """A call between steps, billed through the recovery runtime: a quote
        in place of a hold that does not fit."""
        session = self._session
        held = session._hold(per_call, memo)
        try:
            response = fn(*args, **kwargs)
        except BaseException:
            session._fid.governor.void(held, memo=f"{memo}: the call failed")
            raise
        session._capture(held, _cost(pricing, response), memo)
        return response


class _Messages:
    __slots__ = ("_model",)

    def __init__(self, model: GovernedModel) -> None:
        self._model = model

    def create(self, **kwargs: Any) -> Any:
        """``client.messages.create``, governed."""
        target = getattr(self._model._client, "messages").create  # noqa: B009
        return self._model._invoke(target, (), dict(kwargs))


def _cost(pricing: ModelPricing, response: object) -> Decimal:
    usage = default_usage_extractor(response)
    return pricing.cost_of(usage if isinstance(usage, TokenUsage) else TokenUsage())

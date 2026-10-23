"""``fiduciary serve``: the service, supervised (``docs/FIDUCIARY_SPEC.md`` §10.1, §10.3).

Each part of the service runs as a service of interlock's supervisor
(:class:`~interlock.supervisor.InterlockSupervisor`). Its step runs on a
thread of its own. A step that raises closes the part, which opens again after
an exponential backoff. ``SIGHUP`` opens every key again, and ``SIGTERM``
drains and closes the parts in order. ``GET /healthz`` and ``GET /metrics``
answer on a listener of their own::

    supervisor = build_service("fiduciary.toml")
    asyncio.run(supervisor.run(handle_signals=True))

The parts:

- ``writer``: the node's :class:`~fiduciary.bridge.Fiduciary`. It holds every
  lineage's engine and its escrow chain, the guards and the quotes they hold in
  memory (U-3), the recoveries and the runtime log, and the sessions the
  bridge serves over Streamable HTTP (``[bridge] http``) to the agents
  ``[bridge.agents]`` names. Each turn, under the lock the sessions share, it:
  - brings its engines up to the log (:meth:`~fiduciary.binding.Binder.follow`);
  - lapses what is due and judges the intake;
  - expires the lineages whose term has ended;
  - executes the formed warrants only this process can execute, the
    :data:`ATTACHED` ones;
  - anchors what the ledger missed;
  - closes the sessions an agent left idle.
- ``triggers``: raises a ``held`` ticket for each lineage scope whose
  messages are held, and a ``dead`` ticket for each dead message (§7.1). A
  message a live ticket names already is left to that ticket.
- ``settler``: interlock's settler over each lineage's chain and receipt
  issuer. It issues the delivery receipts an audit bundle carries (§9.1).
- ``executor``: resolves interrupted executions (F7-13), and executes every
  formed warrant that touches only interlock and AgentGov: an outbox
  action, a reset, a rejection.
- ``metrics``: ``/metrics`` and ``/healthz``, and a sample of the log.

**A cluster** (``[cluster]``, PostgreSQL only, F10-5) is several of these
processes over one store and one ledger. Each process is a node, joined
through interlock's :class:`~interlock.cluster.ClusterNode`. One node leads
the writer role, and the writer, the triggers and the settler run there. One
node leads the executor role, and the executor runs there on a stack of its
own, with no engines. The two may be the same node.

- **A node that loses the writer role lets go of everything it holds:** its
  engines and their chains, the runtime log, the receipt log and the HTTP
  listener. The next leader opens them. A node whose process is killed loses
  its roles when the database ends its session. A node that freezes loses
  them once its session has been silent for ``session_timeout_seconds``. The
  other nodes then end each of its connections, which are named for it
  (``interlock-<part>@<node>``), so a transaction it froze in holds the
  writer's lock no longer.
- **Leadership decides which node works, never what keeps the work
  correct.** Every decision is taken under the writer's lock, and every
  execution under its warrant's claim (F7-12, F10-8). So a node that thaws in
  the middle of a step finds its connections ended at its next write, and
  writes nothing twice.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from interlock.cluster import ClusterNode, Leadership, connection_name
from interlock.supervisor import InterlockSupervisor, MetricsService, Service
from interlock.telemetry import CATALOG, COUNTER, GAUGE, Metric, Metrics

from fiduciary.exceptions import FiduciaryError

if TYPE_CHECKING:
    from agentgov.receipts.signing import Signer
    from interlock.settlement import Settler

    from fiduciary.bridge import Fiduciary
    from fiduciary.config import Config
    from fiduciary.executor import Executor
    from fiduciary.mcp import HttpBridge
    from fiduciary.store import State

__all__ = [
    "ATTACHED",
    "CATALOG",
    "EXECUTOR_ROLE",
    "WRITER_ROLE",
    "HeldSigner",
    "build_service",
]

logger = logging.getLogger("fiduciary.service")

WRITER_ROLE: Final = "fiduciary-writer"
EXECUTOR_ROLE: Final = "fiduciary-executor"
ATTACHED: Final = frozenset({"accept_repair", "amend", "grant", "decline"})
"""Resolutions only the writer's process can execute. A repair stages on the
lineage's engine, and an amendment rebinds it. A grant or a decline answers a
quote the writer's guard holds in memory (F7-14, U-3)."""

SERVICE_CATALOG: Final = (
    *CATALOG,
    Metric("fiduciary_build_info", GAUGE, "Fiduciary's version, as a label: 1.", ("version",)),
    Metric("fiduciary_role", GAUGE, "1 while this node leads the role.", ("role",)),
    Metric("fiduciary_log_records", GAUGE, "Records in the FIDU1 log."),
    Metric("fiduciary_lineages", GAUGE, "Lineages, by status.", ("status",)),
    Metric("fiduciary_tickets", GAUGE, "Tickets, by status.", ("status",)),
    Metric("fiduciary_warrants", GAUGE, "Warrants, by their execution.", ("execution",)),
    Metric("fiduciary_intake_pending", GAUGE, "Submissions no record answers yet."),
    Metric("fiduciary_sessions_open", GAUGE, "Sessions the bridge serves on this node."),
    Metric(
        "fiduciary_records_written_total",
        COUNTER,
        "Records the service's parts wrote.",
        ("part",),
    ),
)
"""Interlock's metrics (the supervisor reports each part through them), and
fiduciary's. Every label comes from a closed set: never a lineage, a tenant or
a key."""

_LINEAGE_STATUSES: Final = ("issued", "bound", "suspended", "revoked", "expired")
_TICKET_STATUSES: Final = ("open", "warranted", "declined", "lapsed")
_EXECUTIONS: Final = ("unexecuted", "intent", "applied", "refused", "abandoned")


# --------------------------------------------------------------------------
# keys opened again in place
# --------------------------------------------------------------------------


class HeldSigner:
    """A signer the service opens again at ``SIGHUP``, in place: whatever
    holds this one signs with the key opened last from its next signature.

    A key file is read again, and a key service is reached through the
    endpoint and credentials the configuration names now. The key itself
    stays, unless ``rotates`` allows it. The log, the runtime records and the
    receipt checkpoints are each held to one key, so only the executor's
    rotates, to a key interlock's operators trust.
    """

    __slots__ = ("_lock", "_name", "_rotates", "_signer")

    def __init__(self, name: str, signer: Signer, *, rotates: bool = False) -> None:
        self._name = name
        self._signer = signer
        self._rotates = rotates
        self._lock = threading.Lock()

    @property
    def held(self) -> Signer:
        with self._lock:
            return self._signer

    def swap(self, signer: Signer) -> None:
        """Sign with ``signer`` from now on.

        :raises ValueError: If it is another key, and this one does not rotate.
        """
        with self._lock:
            if not self._rotates and signer.key_id != self._signer.key_id:
                raise ValueError(
                    f"the {self._name} key is {self._signer.key_id}, not {signer.key_id}: "
                    f"what it signed is verified under that key alone, so a reload keeps it"
                )
            self._signer = signer

    @property
    def alg(self) -> str:
        return self.held.alg

    @property
    def key_id(self) -> str:
        return self.held.key_id

    def sign(self, message: bytes) -> bytes:
        return self.held.sign(message)

    def public_key(self) -> Any:
        return self.held.public_key()

    def verify(self, message: bytes, signature: bytes) -> bool:
        return bool(self.held.verify(message, signature))


@dataclass(frozen=True, slots=True)
class _Keys:
    """The keys the service signs with, held so a reload swaps them."""

    service: HeldSigner
    executor: HeldSigner
    runtime: HeldSigner
    receipts: HeldSigner

    @classmethod
    def of(cls, config: Config) -> _Keys:
        return cls(
            HeldSigner("service", config.service()),
            HeldSigner("executor", config.executor(), rotates=True),
            HeldSigner("runtime", config.runtime()),
            HeldSigner("receipts", config.receipts()),
        )

    def reload(self, config: Config) -> None:
        """Open each key again from ``config``, and swap it in.

        :raises ValueError: On a key that will not open, or may not change.
        """
        opened = {
            "service": config.service(),
            "executor": config.executor(),  # refused unless interlock's operators trust it
            "runtime": config.runtime(),
            "receipts": config.receipts(),
        }
        for name, signer in opened.items():
            held: HeldSigner = getattr(self, name)
            held.swap(signer)

    def as_map(self) -> dict[str, Signer]:
        return {
            "service": self.service,
            "executor": self.executor,
            "runtime": self.runtime,
            "receipts": self.receipts,
        }


# --------------------------------------------------------------------------
# where this process stands
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Membership:
    """This process's place: alone, or a node of a cluster. Its connections
    are named for the node, so another node can end them once this one is
    gone, and a transaction left idle inside is ended after ``bound``
    seconds."""

    cluster: ClusterNode | None = None

    @property
    def node(self) -> str | None:
        return None if self.cluster is None else self.cluster.node

    @property
    def bound(self) -> float | None:
        return None if self.cluster is None else self.cluster.session_timeout

    def name(self, part: str) -> str:
        return connection_name(part, self.node, "fiduciary" if part == "fiduciary" else part)

    def follow(self, service: Service, role: str) -> Service:
        if self.cluster is not None:
            service.leader = self.cluster.leadership(role)
        return service


class _Writer:
    """The node's :class:`Fiduciary` while it leads the writer role, with
    the HTTP bridge it serves sessions through. Opened at the writer's
    first turn as leader, never before: a node on standby holds no chain,
    no runtime log and no listener. Shared by the writer, the triggers and the
    settler, which use it under its lock."""

    def __init__(
        self,
        config_path: str,
        config: Config,
        keys: _Keys,
        membership: Membership,
        *,
        bridge: str | None,
    ) -> None:
        self._path = config_path
        self.config = config
        self.keys = keys
        self.membership = membership
        self._bridge_at = bridge
        self._guard = threading.Lock()
        self._fid: Fiduciary | None = None
        self._bridge: HttpBridge | None = None
        self.opened = 0
        """How many times this node opened its writer: a takeover each."""

    @property
    def current(self) -> Fiduciary | None:
        with self._guard:
            return self._fid

    @property
    def bridge(self) -> HttpBridge | None:
        with self._guard:
            return self._bridge

    def acquire(self) -> Fiduciary:
        """The node's Fiduciary, opened if it is not: the engines resume
        what their chains hold, and interrupted executions are resolved."""
        from fiduciary.bridge import Fiduciary
        from fiduciary.config import open_fiduciary

        with self._guard:
            if self._fid is not None:
                return self._fid
            fid = open_fiduciary(
                Fiduciary,
                self._path,
                bridged=True,
                signers=self.keys.as_map(),
                node=self.membership.node,
                bound=self.membership.bound,
            )
            try:
                self._bridge = self._listen(fid)
            except BaseException:
                fid.close()
                raise
            self._fid = fid
            self.opened += 1
            logger.info("writer: opened%s", self._where())
            return fid

    def _where(self) -> str:
        bridge = self._bridge
        if bridge is None:
            return ""
        host, port = bridge.address
        scheme = "https" if self.config.bridge_tls is not None else "http"
        return f"; bridge on {scheme}://{host}:{port}/mcp"

    def _listen(self, fid: Fiduciary) -> HttpBridge | None:
        from fiduciary.config import _listen
        from fiduciary.mcp import Agent, HttpBridge

        listen = self._bridge_at or self.config.bridge_http
        if listen is None:
            return None
        where = _listen(listen)
        if where is None:  # checked when the configuration was read
            raise FiduciaryError(f"the bridge listens on HOST:PORT, not {listen!r}")
        tls = None
        if self.config.bridge_tls is not None:
            import ssl

            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.load_cert_chain(*self.config.bridge_tls)
        agents = {}
        for name, agent in self.config.agents.items():

            def opener(agent: Any = agent) -> Any:
                return fid.session(agent.mandate, tenant=agent.tenant, bridged=True)

            agents[name] = Agent(name, agent.token_sha256, opener, agent.max_sessions)
        bridge = HttpBridge(where[0], where[1], tls=tls, lock=fid.lock, agents=agents)
        bridge.start()
        return bridge

    def reload(self) -> None:
        """At ``SIGHUP``: the keys are opened again and swapped in, and the
        agents the configuration names now are the ones served. Sessions
        open stay open."""
        from fiduciary.config import load_config

        config = load_config(self._path)
        self.keys.reload(config)
        self.config = config
        bridge = self.bridge
        fid = self.current
        if bridge is not None and fid is not None:
            from fiduciary.mcp import Agent

            agents = {}
            for name, agent in config.agents.items():

                def opener(agent: Any = agent) -> Any:
                    return fid.session(agent.mandate, tenant=agent.tenant, bridged=True)

                agents[name] = Agent(name, agent.token_sha256, opener, agent.max_sessions)
            bridge.set_agents(agents)

    def release(self) -> None:
        """Let go of everything: no session is served, every engine's chain
        and the runtime log are closed, and another node may open them."""
        with self._guard:
            fid, self._fid = self._fid, None
            bridge, self._bridge = self._bridge, None
        if bridge is not None:
            bridge.stop()
        if fid is None:
            return
        with fid.lock:
            if bridge is not None:
                bridge.close_sessions()
            fid.close()
        logger.info("writer: released")


class _Following(Leadership):
    """The writer role, as the writer follows it: once the node does not lead
    it, the writer lets go of what it holds at once, before standing by."""

    __slots__ = ("_lost",)

    def __init__(self, node: ClusterNode, role: str, lost: Callable[[], None]) -> None:
        super().__init__(node, role)
        self._lost = lost

    def lead(self) -> bool:
        if super().lead():
            return True
        self._lost()
        return False


# --------------------------------------------------------------------------
# the parts
# --------------------------------------------------------------------------


class _Part(Service):
    """A part that counts the records it writes, by part."""

    def wrote(self, records: list[Any]) -> None:
        if records:
            self.count("records", len(records))
            self.metrics.inc("fiduciary_records_written_total", len(records), part=self.name)


class WriterService(_Part):
    """``writer`` (module docstring)."""

    phase = 3

    def __init__(
        self,
        writer: _Writer,
        *,
        every: float,
        idle: float,
        anchor_every: float = 30.0,
    ) -> None:
        super().__init__("writer")
        self._writer = writer
        self._every = every
        self._idle = idle
        self._anchor_every = anchor_every
        self._anchored = 0.0

    def step(self) -> float:
        fid = self._writer.acquire()
        with fid.lock:
            self.count("followed", len(fid.binder.follow()))
            self.wrote(fid.escalation.process_intake())
            self.wrote(fid.binder.expire())
            self.wrote(_execute(fid.executor, fid.log.state(), attached=True, part=self))
            if time.monotonic() - self._anchored >= self._anchor_every:
                self.count("anchored", fid.log.anchor_pending())
                self._anchored = time.monotonic()
        bridge = self._writer.bridge
        if bridge is not None:
            self.count("idle_closed", bridge.close_idle(self._idle))
            self.metrics.set("fiduciary_sessions_open", bridge.open_sessions())
        return self._every

    def drain(self) -> None:
        if self._writer.current is not None:
            self.step()

    def close(self) -> None:
        self._writer.release()

    def reopen(self) -> None:
        """Keys swapped in place, agents read again: the engines and the
        sessions keep running. A key that may not change is not swapped in,
        and the writer runs on with the keys it has."""
        from fiduciary.config import ConfigError

        try:
            self._writer.reload()
        except (ConfigError, OSError, ValueError) as exc:
            self.count("reloads_refused")
            logger.warning("writer: reload refused, keeping the keys in use: %s", exc)


class TriggersService(_Part):
    """``triggers`` (module docstring): on the writer's Fiduciary, where it
    is open on this node."""

    phase = 2

    def __init__(self, writer: _Writer, *, every: float) -> None:
        super().__init__("triggers")
        self._writer = writer
        self._every = every

    def step(self) -> float:
        fid = self._writer.current
        if fid is None or fid.outbox is None:
            return self._every
        with fid.lock:
            self.wrote(raise_triggers(fid))
        return self._every

    def reopen(self) -> None:
        """It holds nothing of its own."""


class SettlerService(_Part):
    """``settler`` (module docstring): each lineage's deliveries receipted
    into the log its plans' action receipts are in."""

    phase = 4

    def __init__(self, writer: _Writer, *, every: float) -> None:
        super().__init__("settler")
        self._writer = writer
        self._every = every
        self._settlers: dict[str, tuple[Any, Settler]] = {}
        self._close: Callable[[], None] | None = None
        self._outbox: Any = None
        self._of: Fiduciary | None = None

    def open(self) -> None:
        self._outbox, self._close = _settler_outbox(self._writer)

    def step(self) -> float:
        fid = self._writer.current
        if fid is None:
            self._settlers.clear()
            return self._every
        if fid is not self._of:  # another opening of the writer: other engines
            self._settlers.clear()
            self._of = fid
        with fid.lock:
            for mandate_id, settler in self._lineages(fid):
                report = settler.settle()
                self.count("settled", len(report.settled))
                self.count("receipts", report.receipts)
                self.metrics.inc("interlock_receipts_issued_total", report.receipts)
                for problem in report.problems:
                    logger.warning("settler: lineage %s: %s", mandate_id, problem)
        return self._every

    def _lineages(self, fid: Fiduciary) -> list[tuple[str, Settler]]:
        from interlock.settlement import Settler
        from interlock.wiring import live_keyring

        interlock = self._writer.config.interlock_config()
        relays = live_keyring(interlock, "relay")
        if relays is None:
            return []
        found = []
        for mandate_id, engine in fid.binder.engines().items():
            if engine.closed or engine.engine.receipts is None:
                continue
            kept = self._settlers.get(mandate_id)
            if kept is None or kept[0] is not engine.engine:
                settler = Settler(
                    self._outbox,
                    receipts=engine.engine.receipts,
                    chain=engine.engine.chain,
                    relays=relays,
                    ledger=fid.governor,
                    operator_log=self._writer.config.operators_log(),
                    operators=self._writer.config.operator_keyring(),
                    sinks=engine.compiled.sinks,
                    partition=True,  # each lineage settles what its own chain committed
                )
                kept = (engine.engine, settler)
                self._settlers[mandate_id] = kept
            found.append((mandate_id, kept[1]))
        return found

    def drain(self) -> None:
        if self._writer.current is not None:
            self.step()

    def close(self) -> None:
        self._settlers.clear()
        self._of = None
        close, self._close = self._close, None
        if close is not None:
            close()


class ExecutorService(_Part):
    """``executor`` (module docstring). Alone, it executes on the writer's
    Fiduciary, where the ledger's one SQLite writer is. In a cluster, on a
    stack of its own: its own connections, a binder with no engines, and the
    runtime log read without a claim."""

    phase = 3

    def __init__(self, writer: _Writer, *, every: float) -> None:
        super().__init__("executor")
        self._writer = writer
        self._every = every
        self._own: Executor | None = None
        self._close: Callable[[], None] | None = None

    def open(self) -> None:
        if self._writer.membership.cluster is not None:
            self._own, self._close = _detached_executor(self._writer)

    def step(self) -> float:
        if self._own is not None:
            self._run(self._own)
            return self._every
        fid = self._writer.current
        if fid is not None:
            with fid.lock:
                self._run(fid.executor)
        return self._every

    def _run(self, executor: Executor) -> None:
        self.wrote(executor.resolve())
        self.wrote(_execute(executor, executor.log.state(), attached=False, part=self))

    def drain(self) -> None:
        self.step()

    def close(self) -> None:
        self._own = None
        close, self._close = self._close, None
        if close is not None:
            close()


class _Metrics(MetricsService):
    """``/metrics`` and ``/healthz``, and every ``every`` seconds a sample of
    the log: its lineages, tickets, warrants and intake."""

    def __init__(self, writer: _Writer, health: Callable[[], Any], **kw: Any) -> None:
        super().__init__(**kw)
        self._writer = writer
        self._own_health = health
        self._store: Any = None
        self._log: Any = None

    def bind(self, supervisor: InterlockSupervisor) -> None:
        super().bind(supervisor)
        self._health = self._own_health

    def open(self) -> None:
        from fiduciary.log import Log

        super().open()
        self._store = self._writer.config.open_store(
            application_name=self._writer.membership.name("metrics"),
        )
        # Read only: the sample folds what was appended since the last one.
        self._log = Log(self._store, self._writer.keys.service)

    def step(self) -> float:
        import fiduciary

        self.metrics.set("fiduciary_build_info", 1, version=fiduciary.__version__)
        try:
            self._read_log()
        except Exception as exc:
            self.metrics.inc("interlock_metrics_sample_errors_total")
            self.count("sample_errors")
            logger.warning("metrics: the log could not be sampled: %s", exc)
        return self._every

    def _read_log(self) -> None:
        state = self._log.state()
        metrics = self.metrics
        metrics.set("fiduciary_log_records", state.head_seq)
        statuses = [state.status(m) for m in state.lineages]
        for status in _LINEAGE_STATUSES:
            metrics.set("fiduciary_lineages", statuses.count(status), status=status)
        tickets = [state.ticket_status(t) for t in state.tickets]
        for status in _TICKET_STATUSES:
            metrics.set("fiduciary_tickets", tickets.count(status), status=status)
        executions = [state.execution_status(w) or "unexecuted" for w in state.warrants]
        for execution in _EXECUTIONS:
            metrics.set("fiduciary_warrants", executions.count(execution), execution=execution)
        pending = sum(1 for i in self._store.intake_ids() if i not in state.processed)
        metrics.set("fiduciary_intake_pending", pending)
        self.count("samples")

    def close(self) -> None:
        super().close()
        store, self._store, self._log = self._store, None, None
        if store is not None:
            store.close()


# --------------------------------------------------------------------------
# what the parts do
# --------------------------------------------------------------------------


def _execute(executor: Executor, state: State, *, attached: bool, part: Service) -> list[Any]:
    """Execute every formed warrant with no execution yet whose resolution is
    :data:`ATTACHED` (or is not), oldest first. One another process executes
    now, or that has expired, is left: its claim, and the lapse, answer it."""
    records: list[Any] = []
    for row in state.unexecuted_warrants():
        if (row.action in ATTACHED) != attached:
            continue
        try:
            records.extend(executor.execute(row.digest))
        except FiduciaryError as exc:
            part.count("not_executed")
            logger.info("%s: warrant %s not executed: %s", part.name, row.digest[:16], exc)
            continue
        part.count("executed")
    return records


def raise_triggers(fid: Fiduciary) -> list[Any]:
    """Raise a ``held`` ticket for each scope of a lineage in force whose
    outbox messages are held, and a ``dead`` ticket for each dead message
    (§7.1). A message that a ticket still open, or warranted and not yet
    executed, names is that ticket's: nothing is raised over it. Dedup and
    the fatigue bound hold as for any trigger (F7-1, F7-2)."""
    from interlock.deliveries import messages

    from fiduciary.checkers import admits_scope

    outbox = fid.outbox
    assert outbox is not None
    records: list[Any] = []
    state = fid.log.state()
    named = _named_messages(fid, state)
    scopes = list(fid.governor.scopes())
    for mandate_id in sorted(state.lineages):
        if state.status(mandate_id) != "bound":
            continue
        subject = fid.escalation.decide.head_mandate(state, mandate_id).subject.scope
        for scope in sorted(s for s in scopes if admits_scope(subject, s)):
            held = {str(m) for m in outbox.held(scope)}
            if held and not held & named:
                before = fid.log.store.head(fid.log.log_id)[0]
                try:
                    fid.escalation.raise_held(mandate_id, outbox, scope=scope)
                except FiduciaryError as exc:
                    logger.info("triggers: held on %s not raised: %s", scope, exc)
                records.extend(_since(fid, before))
                named |= held
            for view in messages(outbox, state="dead", scope_id=scope, limit=100):
                if str(view.message_id) in named:
                    continue
                before = fid.log.store.head(fid.log.log_id)[0]
                try:
                    fid.escalation.raise_dead(mandate_id, outbox, view.message_id)
                except FiduciaryError as exc:
                    logger.info("triggers: dead %s not raised: %s", view.message_id, exc)
                records.extend(_since(fid, before))
                named.add(str(view.message_id))
    return records


def _since(fid: Fiduciary, seq: int) -> list[Any]:
    return list(fid.log.records(after=seq))


def _named_messages(fid: Fiduciary, state: State) -> set[str]:
    """Every outbox message a live ticket names: one open, or warranted with
    its execution not finished."""
    from fiduciary.documents import Ticket

    live = {row.digest for row in state.open_tickets()}
    for digest in (*state.unexecuted, *state.open_intent_ids):
        warrant = state.warrants.get(digest)
        if warrant is not None and state.ticket_status(warrant.ticket_id) == "warranted":
            live.add(state.tickets[warrant.ticket_id].digest)
    named: set[str] = set()
    for digest in live:
        try:
            ticket = fid.escalation.decide.load(digest, Ticket)
        except FiduciaryError:
            continue
        for option in ticket.options:
            named.update(m.message_id for m in option.resolution.messages or ())
    return named


def _settler_outbox(writer: _Writer) -> tuple[Any, Callable[[], None]]:
    """The outbox as the settler writes it: the installer's connection, or the
    SQLite back office opened for settlement."""
    from fiduciary.config import is_dsn

    target = writer.config.executor_outbox
    assert target is not None  # the settler is built only beside it
    if is_dsn(target):
        from fiduciary.config import bounded

        conn = bounded(target, writer.membership.name("settler"), writer.membership.bound)
        return conn, conn.close
    from interlock.sqlite_outbox import SETTLER, SqliteOutboxStore

    store = SqliteOutboxStore(target, writes=SETTLER)
    return store, store.close


def _detached_executor(writer: _Writer) -> tuple[Executor, Callable[[], None]]:
    """The executor's own stack, for a node of a cluster: the store, the
    ledger and the outbox through connections of its own, named for the
    node; a binder with no engines; the runtime log read without a claim."""
    from fiduciary.authority import Authority
    from fiduciary.binding import Binder
    from fiduciary.config import _close_all, _outbox
    from fiduciary.executor import Executor
    from fiduciary.log import Log

    config = writer.config
    membership = writer.membership
    opened: list[Callable[[], None]] = []
    try:
        store = config.open_store(
            application_name=membership.name("executor"), idle_timeout=membership.bound
        )
        opened.append(store.close)
        governor = config.open_ledger(
            application_name=membership.name("ledger"), idle_timeout=membership.bound
        )
        assert governor is not None
        opened.append(governor.close)
        log = Log(store, writer.keys.service, ledger=governor)
        authority = Authority(log, pinned=config.pinned())
        binder = Binder(authority, governor, deployment=config.deployment(governor))
        outbox = None
        if config.executor_outbox is not None:
            outbox = _outbox(
                config.executor_outbox, opened, membership.name("outbox"), membership.bound
            )
        executor = Executor(
            binder,
            outbox=outbox,
            operator_log=config.operators_log(),
            operator_keyring=config.operator_keyring(),
            executor_signer=writer.keys.executor,
            runtime_records=config.runtime_records,
            runtime_signer=writer.keys.runtime,
        )
    except BaseException:
        _close_all(opened)
        raise
    return executor, lambda: _close_all(opened)


# --------------------------------------------------------------------------
# the supervisor
# --------------------------------------------------------------------------


def build_service(
    config_path: str,
    *,
    node: str | None = None,
    bridge: str | None = None,
    metrics: str | None = None,
) -> InterlockSupervisor:
    """The supervisor ``fiduciary serve`` runs (module docstring).

    :param node: This node's name, over ``[cluster] node`` and
        ``FIDUCIARY_NODE``.
    :param bridge: Where the bridge listens, over ``[bridge] http``.
    :param metrics: Where ``/metrics`` and ``/healthz`` listen, over
        ``[metrics] listen``.
    :raises ConfigError: On a configuration the service cannot run.
    """
    from fiduciary.config import ConfigError, _listen, load_config

    config = load_config(config_path)
    if config.ledger is None:
        raise ConfigError("the service needs the deployment's AgentGov ledger: set ledger")
    cluster = config.cluster_node(node)
    membership = Membership(cluster)
    keys = _Keys.of(config)
    writer = _Writer(config_path, config, keys, membership, bridge=bridge)
    serve = config.serve
    services: list[Service] = []
    writes = WriterService(writer, every=serve.every, idle=serve.idle)
    if cluster is not None:
        writes.leader = _Following(cluster, WRITER_ROLE, writer.release)
    services.append(writes)
    triggers = TriggersService(writer, every=serve.triggers)
    settler: Service | None = None
    if config.receipts_log is not None and config.executor_outbox is not None:
        settler = SettlerService(writer, every=serve.settle)
    executor = membership.follow(ExecutorService(writer, every=serve.every), EXECUTOR_ROLE)
    services += [triggers, *([settler] if settler is not None else []), executor]
    registry = Metrics(SERVICE_CATALOG)
    running: list[InterlockSupervisor] = []

    def health() -> tuple[int, dict[str, Any]]:
        status, body = running[0].health()
        body["node"] = membership.node
        body["roles"] = _roles(cluster)
        found = writer.bridge
        body["sessions"] = 0 if found is None else found.open_sessions()
        body["bridge"] = None if found is None else "{}:{}".format(*found.address)
        return status, body

    listen = metrics or config.metrics_listen
    if listen is not None:
        where = _listen(listen)
        if where is None:
            raise ConfigError(f"metrics listen on HOST:PORT, not {listen!r}")
        services.append(_Metrics(writer, health, host=where[0], port=where[1], every=serve.sample))
    supervisor = InterlockSupervisor(
        services=services,
        drain_timeout=serve.drain_timeout,
        restart_min=serve.restart_min,
        restart_max=serve.restart_max,
        metrics=registry,
        cluster=cluster,
    )
    running.append(supervisor)

    def roles(into: Metrics) -> None:
        for role, held in _roles(cluster).items():
            into.set("fiduciary_role", int(held), role=role)

    registry.collect(roles)
    return supervisor


def _roles(cluster: ClusterNode | None) -> dict[str, bool]:
    if cluster is None:
        return {WRITER_ROLE: True, EXECUTOR_ROLE: True}
    return {role: cluster.leads(role) for role in (WRITER_ROLE, EXECUTOR_ROLE)}

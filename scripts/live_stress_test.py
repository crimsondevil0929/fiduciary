#!/usr/bin/env python3
"""The soak (``docs/FIDUCIARY_SPEC.md`` §11 Phase 6): a cluster of
``fiduciary serve`` nodes under load and faults, on live PostgreSQL, and every
invariant checked afterwards.

One deployment: a store, an AgentGov ledger and a back office, all on the
server ``--dsn`` names; a parent lineage (whose refused plans an autonomous
rule repairs) and derived children (whose refused plans go to the principals),
sharing the parent's refund window, set low enough to be met; the agents'
look-alike calls are exempt from the cognitive breaker (it has tests of its
own), so the load reaches the window, the tenants and the escalations. For
``--minutes``, these run at once:

- ``--nodes`` nodes, joined in a cluster: one leads the writer and serves the
  bridge, one leads the executor;
- an agent per lineage, over MCP to whichever node serves: refunds (some past
  the window), adjustments across tenants (refused, then repaired), lookups;
- the relay, delivering most requests and failing some for good (dead);
- the principals, approving tickets through the intake, each signing through
  a fake KMS; with forged approvals among theirs (an outsider's key; bytes
  changed after signing; a resolution the ticket does not offer) and replays
  of recorded ones;
- faults: a node killed (and started again), the writer's node frozen past its
  session timeout (and thawed);
- edits made to the store around the service, as its owner: projection rows.

Afterwards, with the nodes stopped, it holds the database, the ledger and the
logs to every invariant:

- no warrant executed twice: one ``execution.intent`` each, one ILOK1 intent
  per outbox step, one ledger reset per reset step, one commit per plan;
- no quorum short: every warrant formed by approvals has as many distinct
  principals as its option's quorum;
- no window passed: the window ledger's sum per key is within the limit;
- the ledger balanced (AgentGov's chain and conservation);
- every forgery refused, and every submission answered (F10-11);
- every edit named by ``fiduciary verify``, and nothing else;
- each root lineage's bundle verifies offline;
- then, an edit of a log record and of a ledger entry, each named.

Run::

    uv run --managed-python python scripts/live_stress_test.py \\
        --dsn postgresql://interlock_admin:interlock@localhost:55432/postgres --minutes 3

``--dsn`` names a role that may create databases, schemas and roles; the soak
drops what it made, unless ``--keep``. It writes ``soak-report.json`` and each
node's log to ``--out``. Exit status: 0 when every invariant held; 1 when one
did not; 2 when it could not run. ``tests/test_soak.py`` runs it scaled down.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import random
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EXIT_HELD, EXIT_BROKEN, EXIT_ERROR = 0, 1, 2
WINDOW_LIMIT = "150"
"""Dollars of refunds per day, per lineage tree: met within a minute or two of load."""
CHILDREN = 2
SESSION_TIMEOUT = 2.0


@dataclass
class Settings:
    dsn: str
    seconds: float = 180.0
    nodes: int = 3
    seed: int = 7
    out: Path = field(default_factory=lambda: Path("soak"))
    keep: bool = False
    say: Callable[[str], None] = print


# --------------------------------------------------------------------------
# the deployment
# --------------------------------------------------------------------------


@dataclass
class Deployment:
    """What the soak built: its files, its databases, its lineages."""

    tmp: Path
    config: Path
    dsn: str
    schema: str
    ledger_schema: str
    world: Any
    parent: Any
    children: list[Any]
    tokens: dict[str, tuple[str, str]]
    cleanups: list[Callable[[], None]] = field(default_factory=list)

    @property
    def lineages(self) -> list[str]:
        return [self.parent.mandate_id, *(c.mandate_id for c in self.children)]


def deploy(settings: Settings, tmp: Path) -> Deployment:
    import psycopg
    from agentgov import BudgetManager
    from tests import bound, conftest
    from tests import bridge_kit as kit
    from tests import examples as e
    from tests import serve_kit as sk

    from fiduciary.documents import Mandate
    from fiduciary.postgres import PostgresStore, install, roles

    dsn = settings.dsn
    schema = f"fid_soak_{uuid.uuid4().hex[:8]}"
    ledger_schema = f"agl_soak_{uuid.uuid4().hex[:8]}"
    cleanups: list[Callable[[], None]] = []
    with psycopg.connect(dsn, autocommit=True) as admin:
        install(admin, schema=schema)

    def drop() -> None:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            admin.execute(f"DROP SCHEMA IF EXISTS {ledger_schema} CASCADE")
            for role in roles(schema):
                admin.execute(f"DROP OWNED BY {role}")
                admin.execute(f"DROP ROLE IF EXISTS {role}")

    cleanups.append(drop)
    store = PostgresStore(dsn, schema=schema)
    built = bound.build(
        "postgres",
        tmp,
        store,
        conftest.LiveClock(),
        ledger=lambda: BudgetManager.open_postgres(dsn, schema=ledger_schema),
        tools=kit.tools("postgres"),
    )
    world = next(built)
    cleanups[:0] = list(world.cleanups)  # the back office's database, dropped before ours
    world.cleanups.clear()
    raw = kit.mandate_json(world)
    raw["windows"] = [{**raw["windows"][0], "limit": WINDOW_LIMIT}]
    # The agents repeat look-alike calls by design; the cognitive breaker, which
    # would halt them for it, has tests of its own. The load stays on the rest.
    cognitive = raw["governance"]["cognitive"]
    cognitive["exempt_tools"] = sorted(["adjust_orders", "lookup_order", "refund_order"])
    parent = Mandate.from_json(raw, signed=False).signed(e.signer("cfo"))
    world.issue_and_bind(parent)
    children = []
    for n in range(1, CHILDREN + 1):
        child_raw = world.child_json(
            parent,
            subject={
                "scope": f"{bound.SCOPE}/child-{n}",
                "funded_from": bound.SCOPE,
                "label": f"child {n}",
            },
            escalation={**raw["escalation"], "auto": []},  # its refusals go to principals
        )
        world.binder.derive(Mandate.from_json(child_raw, signed=False))
        child = world.binder.decide.head_mandate(world.log.state(), child_raw["mandate_id"])
        world.binder.bind(child.digest)
        children.append(child)
    tokens = {
        f"agent-{n}": (lineage, f"soak-token-{n}-{uuid.uuid4().hex}")
        for n, lineage in enumerate([parent.mandate_id, *(c.mandate_id for c in children)])
    }
    config = sk.configure(
        world,
        top=f'ledger_schema = "{ledger_schema}"\n',
        ledger=dsn,
        bind=False,
        agents=tokens,
        serve=(
            "[serve]\nevery_seconds = 0.25\ntriggers_seconds = 1\nsettle_seconds = 1\n"
            "sample_seconds = 1\nidle_seconds = 120\nrestart_min_seconds = 0.2\n"
            "restart_max_seconds = 2\ndrain_timeout_seconds = 10\n"
        ),
        tail=(f"[cluster]\nheartbeat_seconds = 0.5\nsession_timeout_seconds = {SESSION_TIMEOUT}\n"),
    )
    built.close()  # what the world held: the service opens its own
    return Deployment(
        tmp, config, dsn, schema, ledger_schema, world, parent, children, tokens, cleanups
    )


# --------------------------------------------------------------------------
# the nodes
# --------------------------------------------------------------------------


class Node:
    """``fiduciary serve --node <name>``, its log in ``--out``."""

    def __init__(self, deployment: Deployment, name: str, out: Path) -> None:
        self.name = name
        self.deployment = deployment
        self.log = out / f"node-{name}.log"
        self.port: int | None = None
        self.process: subprocess.Popen[bytes] | None = None
        self.starts = 0

    def start(self) -> None:
        self.starts += 1
        with self.log.open("a") as out:
            out.write(f"--- start {self.starts} ---\n")
        self._out = self.log.open("a")
        self.process = subprocess.Popen(  # noqa: S603 - this interpreter, running fiduciary
            [
                sys.executable,
                "-c",
                "from fiduciary.cli import main_entry; main_entry()",
                "--config",
                str(self.deployment.config),
                "serve",
                "--node",
                self.name,
                "--bridge",
                "127.0.0.1:0",
                "--metrics",
                "127.0.0.1:0",
            ],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            stdout=subprocess.DEVNULL,
            stderr=self._out,
        )
        self.port = None
        deadline = time.monotonic() + 60
        while self.port is None:
            text = self.log.read_text().rsplit(f"--- start {self.starts} ---", 1)[-1]
            for line in text.splitlines():
                if "metrics on http://" in line:
                    self.port = int(line.rsplit(":", 1)[1].split("/")[0])
            if self.process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(f"node {self.name} did not start:\n{text[-2000:]}")
            time.sleep(0.05)

    def health(self) -> dict[str, Any]:
        from tests import serve_kit as sk

        if self.port is None or self.process is None or self.process.poll() is not None:
            return {}
        try:
            _, _, body = sk.get(("127.0.0.1", self.port), "/healthz")
        except Exception:  # down, frozen or stopping
            return {}
        return dict(body or {})

    def bridge(self) -> tuple[str, int] | None:
        found = self.health().get("bridge")
        if not found:
            return None
        host, _, port = str(found).rpartition(":")
        return host, int(port)

    def leads(self, role: str) -> bool:
        return bool(self.health().get("roles", {}).get(role))

    def signal(self, signum: int) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.send_signal(signum)

    def kill(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.kill()
            self.process.wait()
        self._out.close()

    def stop(self) -> int | None:
        if self.process is None:
            return None
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGCONT)
            self.process.terminate()
            try:
                self.process.wait(60)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self._out.close()
        return self.process.returncode


# --------------------------------------------------------------------------
# the load
# --------------------------------------------------------------------------


@dataclass
class Tally:
    """What the load did, by kind; and what the principals submitted."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    calls: Counter[str] = field(default_factory=Counter)
    forged: dict[int, str] = field(default_factory=dict)
    replayed: list[int] = field(default_factory=list)
    approved: dict[str, str] = field(default_factory=dict)
    edits: list[str] = field(default_factory=list)
    faults: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    said: dict[str, str] = field(default_factory=dict)

    def count(self, what: str) -> None:
        with self.lock:
            self.calls[what] += 1


def _outcome(text: str, error: bool) -> str:
    """A call's outcome, from what the session said."""
    if text.startswith("Committed"):
        return "committed"
    if not error:
        return "served"
    lowered = text.lower()
    for word in ("window", "too many", "spent", "halted", "repaired", "refused", "rejected"):
        if word in lowered:
            return word.replace(" ", "-")
    return "error"


class Soak:
    def __init__(self, settings: Settings, deployment: Deployment) -> None:
        self.settings = settings
        self.d = deployment
        self.rng = random.Random(settings.seed)  # noqa: S311 - the soak's seeded choices
        self.tally = Tally()
        self.stopping = threading.Event()
        self.relaying = threading.Event()
        self.relaying.set()
        self.nodes = [
            Node(deployment, f"soak-{n}", settings.out) for n in range(1, settings.nodes + 1)
        ]

    # -- who serves -----------------------------------------------------------------------

    def bridge(self) -> tuple[str, int] | None:
        for node in self.nodes:
            found = node.bridge()
            if found is not None:
                return found
        return None

    def leader(self, role: str) -> Node | None:
        for node in self.nodes:
            if node.leads(role):
                return node
        return None

    # -- the agents --------------------------------------------------------------------

    def agent(self, name: str, token: str, seed: int) -> None:
        from tests import serve_kit as sk

        rng = random.Random(seed)  # noqa: S311 - the soak's seeded choices
        client: Any = None
        while not self.stopping.is_set():
            try:
                address = self.bridge()
                if address is None:
                    time.sleep(0.2)
                    continue
                if client is None or client.address != address:
                    client = sk.McpClient(address, token)
                    status, _ = client.request("initialize", {"protocolVersion": "2025-06-18"})
                    if status != 200:
                        client = None
                        time.sleep(0.2)
                        continue
                tool, arguments = self._call(rng)
                status, answer = client.request(
                    "tools/call", {"name": tool, "arguments": arguments}
                )
                if status != 200:
                    self.tally.count(f"{tool}:http-{status}")
                    client = None  # the node changed, or the session closed
                    continue
                text = answer["result"]["content"][0]["text"]
                outcome = _outcome(text, bool(answer["result"].get("isError")))
                self.tally.count(f"{tool}:{outcome}")
                with self.tally.lock:
                    self.tally.said.setdefault(f"{tool}:{outcome}", text[:160])
                if outcome == "halted":  # this task is over: the agent starts another
                    with contextlib.suppress(Exception):
                        client.close()
                    client = None
                    time.sleep(1.0)
            except Exception as exc:  # a node killed under the call
                self.tally.count(f"error:{type(exc).__name__}")
                client = None
                time.sleep(0.2)
            time.sleep(rng.uniform(0.2, 0.6))
        if client is not None:
            with contextlib.suppress(Exception):
                client.close()

    @staticmethod
    def _call(rng: random.Random) -> tuple[str, dict[str, Any]]:
        order = rng.randrange(1, 40, 2)  # acme's orders
        roll = rng.random()
        if roll < 0.5:
            amount = f"{rng.randint(1, 9)}.{rng.randint(0, 99):02d}"
            return "refund_order", {
                "order_id": order,
                "amount": amount,
                "payment_intent": f"pi_{order:010d}",
            }
        if roll < 0.75:
            return "adjust_orders", {
                "first": order,
                "second": rng.randrange(2, 41, 2),  # beta's: refused, then repaired
                "amount": "1.00",
            }
        return "lookup_order", {"order_id": order}

    # -- the relay ----------------------------------------------------------------------

    def relay(self) -> None:
        from agentgov import BudgetManager
        from interlock.relay import DELIVERED, PERMANENT, DeliveryResult, LedgerBreaker, Relay
        from tests import serve_kit as sk

        rng = random.Random(self.settings.seed + 1)  # noqa: S311 - the soak's seeded choices

        class Adapter:
            def send(self, delivery: Any) -> DeliveryResult:
                if rng.random() < 0.15:
                    return DeliveryResult(outcome=PERMANENT, detail="card declined")
                return DeliveryResult(outcome=DELIVERED, detail="ok")

        governor = BudgetManager.open_postgres(
            self.d.dsn, schema=self.d.ledger_schema, read_only=True
        )
        store = self.d.world.relay_store()
        try:
            relay = Relay(
                store,
                adapters={"stripe": Adapter()},
                breaker=LedgerBreaker(governor),
                signer=sk.RELAY,
            )
            while not self.stopping.is_set():
                if not self.relaying.wait(0.3):
                    continue
                try:
                    relay.run_once(limit=20)
                except Exception as exc:
                    self.tally.errors.append(f"relay: {type(exc).__name__}: {exc}")
                time.sleep(0.3)
        finally:
            store.close()
            governor.close()

    # -- the principals -----------------------------------------------------------------

    def principals(self) -> None:
        from agentgov.receipts.kms_aws import AwsKmsSigner
        from tests import bound
        from tests import examples as e
        from tests import serve_kit as sk
        from tests.conftest import LiveClock
        from tests.fakes import FakeKms

        from fiduciary import fidu1
        from fiduciary.documents import Approval
        from fiduciary.postgres import PostgresStore

        rng = random.Random(self.settings.seed + 2)  # noqa: S311 - the soak's seeded choices
        signers = {}
        for name in e.PRINCIPALS:
            kms = FakeKms(e.signer(name), arn=f"arn:aws:kms:us-east-1:1:key/{name}")
            signers[name] = AwsKmsSigner(kms.arn, client=kms)
        outsider = e.signer("mallory")
        intake = PostgresStore(self.d.dsn, schema=self.d.schema, application_name="soak-intake")
        try:
            while not self.stopping.is_set():
                try:
                    state = sk.state_of(intake)
                    for row in sorted(state.tickets.values(), key=lambda r: r.seq):
                        if row.ticket_id in self.tally.approved:
                            continue
                        if state.ticket_status(row.ticket_id) != "open":
                            continue
                        ticket = sk.ticket_of(intake, row.digest)
                        actions = [o.resolution.action for o in ticket.options]
                        wanted = {
                            "dead": ["requeue", "cancel"],
                            "refused": ["accept_repair", "reject"],
                            "held": ["release", "reset", "cancel"],
                        }.get(ticket.ticket_class, actions)
                        choices = [a for a in wanted if a in actions] or actions
                        action = rng.choice(choices)
                        option = next(o for o in ticket.options if o.resolution.action == action)
                        window = bound.window(LiveClock())
                        chosen = rng.sample(list(e.PRINCIPALS), option.quorum)
                        recorded = []
                        for principal in chosen:
                            raw = e.approval_json(
                                principal,
                                subject="ticket",
                                digest=ticket.digest,
                                resolution=option.digest,
                                approval_id=str(uuid.uuid4()),
                                **window,
                            )
                            signed = Approval.from_json(raw, signed=False).signed(
                                signers[principal]
                            )
                            recorded.append(signed.canonical())
                            intake.submit(signed.canonical())
                        self.tally.approved[row.ticket_id] = action
                        if rng.random() < 0.5:  # a replay of a recorded approval
                            self.tally.replayed.append(intake.submit(rng.choice(recorded)))
                        if rng.random() < 0.6:
                            kind, data = self._forgery(
                                rng, e, fidu1, ticket, option, window, outsider, signers
                            )
                            self.tally.forged[intake.submit(data)] = kind
                except Exception as exc:  # the store went away for a moment
                    self.tally.errors.append(f"principals: {type(exc).__name__}: {exc}")
                time.sleep(0.4)
        finally:
            intake.close()

    @staticmethod
    def _forgery(
        rng: random.Random,
        e: Any,
        fidu1: Any,
        ticket: Any,
        option: Any,
        window: dict[str, str],
        outsider: Any,
        signers: dict[str, Any],
    ) -> tuple[str, bytes]:
        from fiduciary.documents import Approval

        principal = rng.choice(list(e.PRINCIPALS))
        raw = e.approval_json(
            principal,
            subject="ticket",
            digest=ticket.digest,
            resolution=option.digest,
            approval_id=str(uuid.uuid4()),
            **window,
        )
        kind = rng.choice(["outsider", "changed", "unoffered"])
        if kind == "outsider":  # a roster name, signed by a key the roster does not hold
            signed = Approval.from_json(raw, signed=False).signed(outsider)
            return kind, signed.canonical()
        if kind == "unoffered":  # a resolution the ticket does not offer
            raw = {**raw, "resolution": "b" * 64}
            signed = Approval.from_json(raw, signed=False).signed(signers[principal])
            return kind, signed.canonical()
        signed = Approval.from_json(raw, signed=False).signed(signers[principal])
        body = fidu1.parse(signed.canonical())
        body["approval_id"] = str(uuid.uuid4())  # changed after signing
        return kind, fidu1.canonical(body)

    # -- faults and edits ---------------------------------------------------------------

    def chaos(self, seconds: float) -> None:
        """At a third of the run, the executor's node killed and started
        again; at three fifths, the writer's node frozen past its session
        timeout, and thawed; projection rows edited at two fifths and at four
        fifths."""
        start = time.monotonic()

        def at(fraction: float) -> bool:
            left = start + seconds * fraction - time.monotonic()
            return not self.stopping.wait(max(0.0, left))

        if at(1 / 3):
            node = self.leader("fiduciary-executor") or self.nodes[0]
            node.kill()
            self.tally.faults.append(f"killed {node.name}")
            if not self.stopping.wait(2.0):
                node.start()
                self.tally.faults.append(f"started {node.name} again")
        if at(2 / 5):
            self.edit("approvals")
        if at(1 / 2):
            self.trip_child()
        if at(3 / 5):
            node = self.leader("fiduciary-writer") or self.nodes[-1]
            node.signal(signal.SIGSTOP)
            self.tally.faults.append(f"froze {node.name}")
            self.stopping.wait(SESSION_TIMEOUT * 3)
            node.signal(signal.SIGCONT)
            self.tally.faults.append(f"thawed {node.name}")
        if at(4 / 5):
            self.edit("tickets")

    def trip_child(self) -> None:
        """The relay paused while a child's agent commits, its scope then
        halted as an operator halts it, and the relay resumed: it holds what
        the child had queued, which the triggers ticket, and the principals
        release (with a reset) or cancel."""
        from agentgov import BudgetManager

        self.relaying.clear()
        self.stopping.wait(4.0)
        scope = self.d.children[0].subject.scope
        governor = BudgetManager.open_postgres(self.d.dsn, schema=self.d.ledger_schema)
        try:
            governor.trip(scope, "spend envelope exhausted")
        finally:
            governor.close()
        self.tally.faults.append(f"halted {scope}")
        self.relaying.set()

    def edit(self, table: str) -> None:
        """Change one row of a projection, as the store's owner: around the
        service, which never reads it back."""
        import psycopg

        with psycopg.connect(self.d.dsn, autocommit=True) as admin:
            column = "principal" if table == "approvals" else "detail"
            done = admin.execute(
                f"UPDATE {self.d.schema}.{table} SET {column} = %s "  # noqa: S608
                f"WHERE ctid = (SELECT ctid FROM {self.d.schema}.{table} ORDER BY seq LIMIT 1)",
                ("edited-around-the-service",),
            ).rowcount
        if done:
            self.tally.edits.append(table)

    # -- the run ------------------------------------------------------------------------

    def run(self) -> None:
        say = self.settings.say
        for node in self.nodes:
            node.start()
        deadline = time.monotonic() + 60
        while self.bridge() is None:
            if time.monotonic() > deadline:
                raise RuntimeError("no node served the bridge within a minute")
            time.sleep(0.2)
        say(f"soak: {len(self.nodes)} nodes up; running {self.settings.seconds:g}s")
        threads = [
            threading.Thread(target=self.agent, args=(name, token, n), daemon=True)
            for n, (name, (_, token)) in enumerate(sorted(self.d.tokens.items()))
        ]
        threads += [
            threading.Thread(target=self.relay, daemon=True),
            threading.Thread(target=self.principals, daemon=True),
        ]
        chaos = threading.Thread(target=self.chaos, args=(self.settings.seconds,), daemon=True)
        for thread in [*threads, chaos]:
            thread.start()
        begun = time.monotonic()
        self.stopping.wait(self.settings.seconds)
        self.stopping.set()
        chaos.join(SESSION_TIMEOUT * 4 + 30)
        for thread in threads:
            thread.join(60)
        say(
            f"soak: load stopped after {time.monotonic() - begun:.0f}s; "
            f"{sum(self.tally.calls.values())} calls; draining"
        )
        self.drain()
        for node in self.nodes:
            node.stop()

    def drain(self) -> None:
        """Until every submission is answered and every warrant formed is
        executed or past executing, a minute at most."""
        from tests import serve_kit as sk

        from fiduciary.postgres import PostgresStore

        store = PostgresStore(self.d.dsn, schema=self.d.schema, application_name="soak-drain")
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                state = sk.state_of(store)
                answered = {i for i, _ in store.intake()} <= state.processed
                settled = all(
                    state.execution_status(w) not in (None, "intent") for w in state.warrants
                )
                if answered and settled:
                    return
                time.sleep(0.5)
        finally:
            store.close()


# --------------------------------------------------------------------------
# the invariants
# --------------------------------------------------------------------------


@dataclass
class Report:
    held: dict[str, bool] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.held) and all(self.held.values())

    def check(self, name: str, holds: bool, detail: Any = None) -> None:
        self.held[name] = holds
        if detail is not None:
            self.details[name] = detail

    def to_json(self) -> dict[str, Any]:
        return {"ok": self.ok, "held": self.held, "details": self.details}


def check(soak: Soak) -> Report:
    import psycopg
    from agentgov import BudgetManager
    from interlock.chain import EscrowChain, RecordType
    from interlock.deliveries import PostgresReader
    from interlock.records import Keyring, RecordLog
    from tests import examples as e
    from tests import serve_kit as sk

    from fiduciary.postgres import PostgresStore
    from fiduciary.verify import verify_store

    d = soak.d
    report = Report()
    store = PostgresStore(d.dsn, schema=d.schema, application_name="soak-check")
    governor = BudgetManager.open_postgres(d.dsn, schema=d.ledger_schema, read_only=True)
    try:
        state = sk.state_of(store)
        records = [r for r in _records(store)]
        report.details["records"] = len(records)
        report.details["calls"] = dict(sorted(soak.tally.calls.items()))
        report.details["faults"] = soak.tally.faults
        report.details["tickets"] = dict(Counter(r.ticket_class for r in state.tickets.values()))
        executions = Counter(state.execution_status(w) or "unexecuted" for w in state.warrants)
        report.details["warrants"] = dict(executions)
        report.details["warrants by action"] = dict(
            Counter(row.action for row in state.warrants.values())
        )
        report.details["executions refused, why"] = dict(
            Counter(
                str(r.content.get("why", ""))[:90]
                for r in records
                if r.kind in ("execution.refused", "execution.abandoned")
            )
        )
        report.details["said"] = dict(sorted(soak.tally.said.items()))
        from interlock.recovery import breaker_reason

        governor.refresh()
        report.details["halted at the end"] = {
            scope: breaker_reason(governor, scope)
            for scope in sorted(governor.scopes())
            if scope.startswith("support-agent") and governor.halted_by(scope) == scope
        }

        # -- no warrant executed twice ------------------------------------------------
        intents = Counter(r.content["warrant"] for r in records if r.kind == "execution.intent")
        public = e.SERVICE.public_key()
        operators = d.tmp / "operators.jsonl"
        ilok1 = (
            RecordLog.load(operators, Keyring({public.key_id: public}))
            if operators.exists()
            else ()
        )
        acts = Counter(
            (r.body["reason"], frozenset(t["message"] for t in r.body["targets"]))
            for r in ilok1
            if r.kind == "operator.intent" and str(r.body["reason"]).startswith("FIDU1 warrant ")
        )
        governor.refresh()
        resets = Counter(
            (en.memo, en.scope_id)
            for en in governor.audit_trail()
            if en.memo.startswith("FIDU1 warrant ") and " reset " in en.memo
        )
        commits: Counter[str] = Counter()
        for path in sorted((d.tmp / "chains").glob("*.chain")):
            for record in EscrowChain.load(path).records():
                if record.record_type is RecordType.COMMITTED:
                    commits[record.plan_id] += 1
        twice = (
            [w for w, n in intents.items() if n > 1]
            + [str(k) for k, n in acts.items() if n > 1]
            + [str(k) for k, n in resets.items() if n > 1]
            + [p for p, n in commits.items() if n > 1]
        )
        report.check("no warrant executed twice", not twice, twice or None)
        report.details["executions"] = {
            "intents": len(intents),
            "operator acts": len(acts),
            "resets": len(resets),
            "plans committed": len(commits),
        }

        # -- no quorum short ---------------------------------------------------------
        short = []
        for digest, row in state.warrants.items():
            warrant = _document(store, digest)
            approvals = list(warrant.basis.approvals)
            if not approvals:
                continue  # an autonomous rule's (V8 recomputes its predicate)
            ticket = _document(store, warrant.ticket.digest)
            option = next(o for o in ticket.options if o.digest == row.resolution_digest)
            principals = {_document(store, a).principal for a in approvals}
            if len(principals) != len(approvals) or len(principals) < option.quorum:
                short.append(digest)
        report.check("no quorum short", not short, short or None)

        # -- no window passed --------------------------------------------------------
        with psycopg.connect(d.world.back_office, autocommit=True) as conn:
            reader = PostgresReader(conn)
            rows = reader.window_rows(reader.database_now())
        sums: dict[tuple[str, str], Decimal] = {}
        for line in rows:
            key = (line.window, line.key)
            sums[key] = sums.get(key, Decimal(0)) + Decimal(str(line.amount))
        refunds = {k: v for k, v in sums.items() if "refund_dollars" in k[0]}
        passed = {f"{k[0]} {k[1]}": str(v) for k, v in refunds.items() if v > Decimal(WINDOW_LIMIT)}
        report.check("no window passed", bool(refunds) and not passed, passed or None)
        report.details["window"] = {f"{k[0]} {k[1]}": str(v) for k, v in refunds.items()}

        # -- the ledger balanced ------------------------------------------------------
        try:
            governor.verify_integrity()
            report.check("the ledger balanced", True)
        except Exception as exc:
            report.check("the ledger balanced", False, f"{type(exc).__name__}: {exc}")

        # -- every forgery refused; every submission answered -------------------------
        refused = {int(r.content["intake"]) for r in records if r.kind == "approval.refused"}
        unrefused = sorted(i for i in soak.tally.forged if i not in refused)
        report.check(
            "every forgery refused", bool(soak.tally.forged) and not unrefused, unrefused or None
        )
        report.details["forgeries"] = dict(Counter(soak.tally.forged.values()))
        report.details["replays"] = len(soak.tally.replayed)
        unanswered = sorted({i for i, _ in store.intake()} - state.processed)
        report.check("every submission answered", not unanswered, unanswered or None)

        # -- every edit named ---------------------------------------------------------
        found = verify_store(
            store, service_key=public, pinned=d.world.charter.digest, ledger=governor.audit_trail()
        )
        named = {
            t for t in soak.tally.edits if any(f"projection {t}:" in p for p in found.problems)
        }
        others = [p for p in found.problems if not p.startswith("projection ")]
        report.check(
            "every edit named, and nothing else",
            bool(soak.tally.edits) and named == set(soak.tally.edits) and not others,
            {"edits": soak.tally.edits, "problems": list(found.problems)},
        )

        # -- each root lineage's bundle verifies offline -------------------------------
        verdicts = _bundles(soak, store, governor)
        report.check(
            "each bundle verifies offline",
            all(v == "passed" for v in verdicts.values()),
            verdicts,
        )
    finally:
        governor.close()
        store.close()

    # -- an edit of the log, and of the ledger: named ---------------------------------------
    report.check("a log record edited is named", _edit_log(soak))
    report.check("a ledger entry edited is named", _edit_ledger(soak))
    report.details["errors"] = soak.tally.errors[:20]
    return report


def _records(store: Any) -> Iterator[Any]:
    from fiduciary import fidu1
    from fiduciary.documents import Record

    for row in store.records("fiduciary"):
        yield Record.from_json(fidu1.parse(row.data))


def _document(store: Any, digest: str) -> Any:
    from fiduciary.documents import loads

    found = store.document(digest)
    assert found is not None, digest
    return loads(found[1])


def _bundles(soak: Soak, store: Any, governor: Any) -> dict[str, str]:
    from agentgov.receipts import ReceiptLog
    from agentgov.receipts.signing import verifier_of
    from interlock.records import Keyring, RecordLog
    from tests import bundle_kit as bk
    from tests import examples as e

    from fiduciary.bundle import Evidence, TrustedKeys, export_bundle, verify_bundle
    from fiduciary.bundle.export import spec
    from fiduciary.config import load_config

    d = soak.d
    config = load_config(d.config)
    public = e.SERVICE.public_key()
    keyring = Keyring({public.key_id: public})
    receipts = ReceiptLog("fiduciary-receipts", e.SERVICE, path=config.receipts_log)
    runtime = d.tmp / "runtime.jsonl"
    try:
        evidence = Evidence(
            ledger=governor,
            ledger_signer=bk.LEDGER_KEY,
            receipts=receipts,
            receipt_keys=[verifier_of(e.SERVICE)],
            operator_records=RecordLog.load(d.tmp / "operators.jsonl", keyring)
            if (d.tmp / "operators.jsonl").exists()
            else (),
            operator_keys={"fiduciary": spec(public)},
            runtime_records=RecordLog.load(runtime, e.SERVICE) if runtime.exists() else (),
            runtime_key=public,
            chains=d.tmp / "chains",
            service=e.SERVICE,
        )
        keys = TrustedKeys(
            {
                "service": spec(public),
                "ledger": spec(verifier_of(bk.LEDGER_KEY)),
                "receipts": [spec(public)],
                "runtime": spec(public),
                "operators": {"fiduciary": spec(public)},
            }
        )
        deployment = config.deployment(governor)
        verdicts = {}
        data = export_bundle(store, d.parent.mandate_id, deployment=deployment, evidence=evidence)
        (soak.settings.out / "parent.fidb").write_bytes(data)
        verdict = verify_bundle(data, keys=keys, charter=d.world.charter.digest)
        verdicts[d.parent.mandate_id] = "passed" if verdict.passed else verdict.render()
        return verdicts
    finally:
        receipts.close()


def _edit_log(soak: Soak) -> bool:
    """Rewrite one record's bytes around the triggers, as the owner can: the
    log then fails to verify, by its links or its anchors."""
    import psycopg
    from tests import examples as e

    from fiduciary.postgres import PostgresStore
    from fiduciary.verify import verify_store

    d = soak.d
    with psycopg.connect(d.dsn, autocommit=True) as admin:
        admin.execute(f"ALTER TABLE {d.schema}.log DISABLE TRIGGER USER")
        try:
            admin.execute(
                f"UPDATE {d.schema}.log SET record = convert_to(replace("  # noqa: S608
                f"convert_from(record, 'UTF8'), '\"acme\"', '\"acmE\"'), 'UTF8') "
                f"WHERE seq = (SELECT max(seq) FROM {d.schema}.log WHERE kind = 'session.opened')"
            )
        finally:
            admin.execute(f"ALTER TABLE {d.schema}.log ENABLE TRIGGER USER")
    store = PostgresStore(d.dsn, schema=d.schema, application_name="soak-check")
    try:
        found = verify_store(
            store, service_key=e.SERVICE.public_key(), pinned=d.world.charter.digest
        )
        return any(not p.startswith("projection ") for p in found.problems)
    except Exception:  # a record that no longer parses is named too
        return True
    finally:
        store.close()


def _edit_ledger(soak: Soak) -> bool:
    """Change one ledger entry's memo around AgentGov, as the database's owner
    can: its hash chain no longer verifies."""
    import psycopg
    from agentgov import BudgetManager

    d = soak.d
    with psycopg.connect(d.dsn, autocommit=True) as admin:
        admin.execute(f"ALTER TABLE {d.ledger_schema}.entries DISABLE TRIGGER USER")
        try:
            edited = admin.execute(
                f"UPDATE {d.ledger_schema}.entries SET memo = memo || ' (edited)' "  # noqa: S608
                f"WHERE ctid = (SELECT ctid FROM {d.ledger_schema}.entries "
                f"WHERE memo LIKE 'FIDU1 bind %' LIMIT 1)"
            ).rowcount
        finally:
            admin.execute(f"ALTER TABLE {d.ledger_schema}.entries ENABLE TRIGGER USER")
    if not edited:
        return False
    try:
        governor = BudgetManager.open_postgres(d.dsn, schema=d.ledger_schema, read_only=True)
    except Exception:  # it refuses to open: named
        return True
    try:
        governor.verify_integrity()
    except Exception:
        return True
    finally:
        governor.close()
    return False


# --------------------------------------------------------------------------
# the command
# --------------------------------------------------------------------------


def soak(settings: Settings) -> Report:
    """Deploy, run, check, and drop what it made (unless ``keep``)."""
    settings.out.mkdir(parents=True, exist_ok=True)
    # The test kit builds the back office on this server; it reads the variable on import.
    os.environ["FIDUCIARY_TEST_POSTGRES_DSN"] = settings.dsn
    tmp = Path(tempfile.mkdtemp(prefix="soak-", dir=settings.out))
    deployment = deploy(settings, tmp)
    run = Soak(settings, deployment)
    try:
        try:
            run.run()
        finally:
            for node in run.nodes:
                node.stop()
        report = check(run)
    finally:
        if not settings.keep:
            for cleanup in deployment.cleanups:
                with contextlib.suppress(Exception):
                    cleanup()
    (settings.out / "soak-report.json").write_text(
        json.dumps(report.to_json(), indent=2, sort_keys=True, default=str) + "\n"
    )
    for name, held in report.held.items():
        settings.say(f"  {'held  ' if held else 'BROKEN'} {name}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--dsn", default=os.environ.get("FIDUCIARY_TEST_POSTGRES_DSN"))
    parser.add_argument("--minutes", type=float, default=3.0)
    parser.add_argument("--nodes", type=int, default=3)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", type=Path, default=Path("soak"))
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    if not args.dsn:
        print("give --dsn, or set FIDUCIARY_TEST_POSTGRES_DSN", file=sys.stderr)
        return EXIT_ERROR
    settings = Settings(
        args.dsn, args.minutes * 60, max(2, args.nodes), args.seed, args.out, args.keep
    )
    try:
        report = soak(settings)
    except Exception:
        traceback.print_exc()
        return EXIT_ERROR
    print(json.dumps(report.details, indent=2, sort_keys=True, default=str))
    return EXIT_HELD if report.ok else EXIT_BROKEN


if __name__ == "__main__":
    sys.exit(main())

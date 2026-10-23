"""A cluster of ``fiduciary serve`` nodes over one PostgreSQL store and ledger
(§10.3, §10.7; F10-5 to F10-8, F10-12).

Each node is a process of its own. One leads the writer role, and serves the
bridge; one leads the executor role; the others stand by. A leader that is
killed hands its roles over at the next ask. A leader that freezes is fenced:
once its session has been silent for the session timeout, another node ends
every connection named for it, takes its roles, and, once the frozen process
lets go of the chains it holds (it thaws, or dies), opens them. Nothing is
executed twice, and the log verifies.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
import pytest
from agentgov import BudgetManager

from fiduciary.postgres import PostgresStore
from fiduciary.verify import verify_store
from tests import bound
from tests import bridge_kit as kit
from tests import examples as e
from tests import serve_kit as sk
from tests.conftest import Clock, need_postgres

ROOT = Path(__file__).resolve().parents[1]
CLUSTER = "[cluster]\nheartbeat_seconds = 0.5\nsession_timeout_seconds = 2\n"
ORDER_9 = {"order_id": 9, "amount": "4.00", "payment_intent": "pi_0000000009"}
ORDER_11 = {"order_id": 11, "amount": "3.00", "payment_intent": "pi_0000000011"}


@dataclass
class Shared:
    world: bound.World
    dsn: str
    ledger_schema: str


@pytest.fixture
def shared(pg_schema: tuple[str, str], tmp_path: Path, clock: Clock) -> Iterator[Shared]:
    dsn, schema = pg_schema
    ledger_schema = f"agl_{uuid.uuid4().hex[:12]}"
    store = PostgresStore(dsn, schema=schema)
    try:
        yield from (
            Shared(world, dsn, ledger_schema)
            for world in kit.world(
                store,
                tmp_path,
                clock,
                ledger=lambda: BudgetManager.open_postgres(dsn, schema=ledger_schema),
            )
        )
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f"DROP SCHEMA IF EXISTS {ledger_schema} CASCADE")


def _config(shared: Shared) -> Path:
    return sk.configure(
        shared.world,
        top=f'ledger_schema = "{shared.ledger_schema}"\n',
        ledger=shared.dsn,
        tail=CLUSTER,
    )


class Node:
    """``fiduciary serve --node <name>``, its log in a file."""

    def __init__(self, config: Path, name: str) -> None:
        self.name = name
        self.log = config.with_name(f"node-{name}.log")
        self._out = self.log.open("w")
        self.process = subprocess.Popen(  # noqa: S603 - this interpreter, running fiduciary
            [
                sys.executable,
                "-c",
                "from fiduciary.cli import main_entry; main_entry()",
                "--config",
                str(config),
                "serve",
                "--node",
                name,
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
        self.port = sk.wait_for(self._port, what=f"node {name}'s metrics", timeout=60)

    def _port(self) -> int | None:
        for line in self.log.read_text().splitlines():
            if "metrics on http://" in line:
                return int(line.rsplit(":", 1)[1].split("/")[0])
        if self.process.poll() is not None:
            raise AssertionError(self.log.read_text()[-3000:])
        return None

    def health(self) -> dict[str, Any]:
        try:
            _, _, body = sk.get(("127.0.0.1", self.port), "/healthz")
        except OSError:
            return {}
        return dict(body)

    def leads(self, role: str) -> bool:
        return bool(self.health().get("roles", {}).get(role))

    def bridge(self) -> tuple[str, int] | None:
        found = self.health().get("bridge")
        if not found:
            return None
        host, _, port = str(found).rpartition(":")
        return host, int(port)

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGCONT)
            self.process.terminate()
            try:
                self.process.wait(60)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self._out.close()


def _commit(node: Node, order: dict[str, Any]) -> None:
    address = sk.wait_for(node.bridge, what=f"node {node.name}'s bridge", timeout=30)
    agent = sk.McpClient(address)
    agent.initialize()
    result = agent.call("refund_order", order)
    assert result["content"][0]["text"].startswith("Committed:"), result
    assert agent.close() == 204


def _sessions_of(dsn: str, node: str) -> list[str]:
    """The node's connections to the cluster's database: the store and the
    ledger. Its back office's are bounded by their idle timeout instead."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT application_name FROM pg_stat_activity WHERE application_name LIKE %s "
            "AND datname = current_database()",
            (f"interlock-%@{node}",),
        ).fetchall()
    return sorted(str(r[0]) for r in rows if not str(r[0]).startswith("interlock-cluster@"))


def _verified(shared: Shared) -> Any:
    world = shared.world
    store = PostgresStore(need_postgres(), schema=world.store.schema)  # type: ignore[attr-defined]
    governor = BudgetManager.open_postgres(shared.dsn, schema=shared.ledger_schema, read_only=True)
    try:
        report = verify_store(
            store,
            service_key=e.SERVICE.public_key(),
            pinned=world.charter.digest,
            ledger=governor.audit_trail(),
        )
        assert report.ok, report.problems
        return sk.state_of(store)
    finally:
        governor.close()
        store.close()


def test_a_killed_leader_hands_its_roles_over(shared: Shared) -> None:
    config = _config(shared)
    first = Node(config, "n1")
    second = None
    try:
        sk.wait_for(lambda: first.leads("fiduciary-writer"), what="n1 leading the writer")
        sk.wait_for(lambda: first.leads("fiduciary-executor"), what="n1 leading the executor")
        second = Node(config, "n2")
        sk.wait_for(lambda: second.health().get("status") == "ok", what="n2 standing by")
        body = second.health()
        assert body["roles"] == {"fiduciary-writer": False, "fiduciary-executor": False}
        assert body["services"]["writer"]["state"] == "standby"
        assert body["bridge"] is None  # a standby node serves no session
        _commit(first, sk.ORDER_7)

        first.process.kill()
        first.process.wait()
        sk.wait_for(lambda: second.leads("fiduciary-writer"), what="n2 taking the writer")
        sk.wait_for(lambda: second.leads("fiduciary-executor"), what="n2 taking the executor")
        _commit(second, ORDER_9)
    finally:
        first.stop()
        if second is not None:
            second.stop()
    state = _verified(shared)
    opened = [e for events in state.sessions.values() for e, _, _ in events if e == "opened"]
    assert len(opened) == 2


def test_a_frozen_leader_is_fenced_and_hands_over(shared: Shared) -> None:
    config = _config(shared)
    first = Node(config, "n1")
    second = None
    try:
        sk.wait_for(lambda: first.leads("fiduciary-writer"), what="n1 leading the writer")
        second = Node(config, "n2")
        sk.wait_for(lambda: second.health().get("status") == "ok", what="n2 standing by")
        _commit(first, sk.ORDER_7)
        assert _sessions_of(shared.dsn, "n1")  # n1's store, ledger, stage... named for it

        first.process.send_signal(signal.SIGSTOP)
        sk.wait_for(lambda: second.leads("fiduciary-executor"), what="n2 taking the executor")
        # n1's session fell silent: n2 ended every connection named for n1.
        try:
            sk.wait_for(lambda: not _sessions_of(shared.dsn, "n1"), what="n1 fenced", timeout=20)
        except AssertionError:
            raise AssertionError(_sessions_of(shared.dsn, "n1")) from None
        fenced = second.health()["services"]["cluster"]["counters"].get("fenced", 0)
        assert fenced >= 1

        # n1 holds its chains' file claims while it is frozen: n2 leads the
        # writer, and its writer waits for them, healthy, asking each turn.
        sk.wait_for(lambda: second.leads("fiduciary-writer"), what="n2 taking the writer")

        def waiting() -> bool:
            body = second.health()
            writer = body.get("services", {}).get("writer", {})
            return int(writer.get("counters", {}).get("claims_held", 0)) >= 2

        sk.wait_for(waiting, what="n2's writer waiting for n1's claims", timeout=30)
        body = second.health()
        assert body["status"] == "ok" and body["services"]["writer"]["state"] == "running"
        assert body["bridge"] is None  # nothing is served before the files are n2's
        # Thawed, n1 finds its session gone and lets go of everything.
        first.process.send_signal(signal.SIGCONT)

        def one_writer() -> Node | None:
            leaders = [n for n in (first, second) if n.leads("fiduciary-writer")]
            return leaders[0] if len(leaders) == 1 and leaders[0].bridge() else None

        leader = sk.wait_for(one_writer, what="one node leading the writer", timeout=30)
        _commit(leader, ORDER_11)
    finally:
        first.stop()
        if second is not None:
            second.stop()
    state = _verified(shared)
    opened = [e for events in state.sessions.values() for e, _, _ in events if e == "opened"]
    assert len(opened) == 2


def test_a_node_in_process_leads_both_roles_and_lets_them_go(shared: Shared) -> None:
    """One node, in this process: it joins, leads the writer and the executor
    (the executor on a stack of its own), serves an agent, and, when the
    writer role is lost, lets go of everything it held."""
    config = _config(shared)
    with sk.serving(config, node="n0") as served:
        body = served.health()[1]
        assert body["node"] == "n0"
        sk.wait_for(
            lambda: (
                served.health()[1]["roles"]
                == {"fiduciary-writer": True, "fiduciary-executor": True}
            ),
            what="both roles",
        )
        agent = sk.McpClient(served.bridge())
        agent.initialize()
        result = agent.call("refund_order", sk.ORDER_7)
        assert result["content"][0]["text"].startswith("Committed:"), result
        text = served.metrics()
        assert 'fiduciary_role{role="fiduciary-executor"} 1' in text
        # The role resigned under the writer: it lets go, and takes it again.
        cluster = served.supervisor._cluster
        assert cluster is not None
        cluster.resign("fiduciary-writer")
        sk.wait_for(lambda: served.health()[1]["bridge"] is not None, what="the writer again")
    state = _verified(shared)
    assert any(e == "opened" for events in state.sessions.values() for e, _, _ in events)

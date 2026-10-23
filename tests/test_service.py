"""``fiduciary serve`` (§10.1, §10.6, §11 Phase 6), on SQLite and PostgreSQL:
every part under the supervisor; an agent served over HTTP; a dead message
ticketed by the service, approved through the intake and requeued by it;
deliveries receipted; a term ended; a part that fails restarted after its
backoff; and the command, reloading its keys on ``SIGHUP`` and draining on
``SIGTERM``."""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from agentgov import BudgetManager
from interlock.relay import DeliveryResult, LedgerBreaker, Relay

from fiduciary import service as service_module
from fiduciary.documents import Mandate
from fiduciary.store import State, Store
from fiduciary.verify import verify_store
from tests import bound
from tests import bridge_kit as kit
from tests import examples as e
from tests import serve_kit as sk
from tests.conftest import Clock, reopen

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def world(store: Store, tmp_path: Path, clock: Clock) -> Iterator[bound.World]:
    yield from kit.world(store, tmp_path, clock)


class _Fixed:
    def __init__(self, result: DeliveryResult) -> None:
        self._result = result

    def send(self, delivery: Any) -> DeliveryResult:
        return self._result


def _deliver(world: bound.World, outcome: str) -> None:
    """The relay's pass over the outbox, every request answered ``outcome``."""
    governor = BudgetManager.open_sqlite(str(world.tmp / "ledger.db"), read_only=True)
    store = world.relay_store()
    try:
        relay = Relay(
            store,
            adapters={"stripe": _Fixed(DeliveryResult(outcome=outcome, detail=outcome))},
            breaker=LedgerBreaker(governor),
            signer=sk.RELAY,
        )
        for _ in range(10):
            if not relay.run_once(limit=20):
                break
    finally:
        store.close()
        governor.close()


def _verified(world: bound.World) -> State:
    store = reopen(world.store)
    governor = BudgetManager.open_sqlite(str(world.tmp / "ledger.db"), read_only=True)
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


def _committed(result: dict[str, Any]) -> None:
    assert not result.get("isError"), result
    assert result["content"][0]["text"].startswith("Committed:"), result


# -- every part, and an agent -------------------------------------------------------------


def test_the_service_runs_every_part_and_serves_its_agents(world: bound.World) -> None:
    config = sk.configure(world)
    with sk.serving(config) as served:
        status, body = served.health()
        assert status == 200, body
        assert body["roles"] == {"fiduciary-writer": True, "fiduciary-executor": True}
        assert {"writer", "triggers", "settler", "executor", "metrics"} <= set(body["services"])
        address = served.bridge()

        # Only a token the configuration's digest names opens a session.
        assert sk.McpClient(address, token="not-the-token").request("initialize")[0] == 401
        stranger = sk.McpClient(address)
        assert stranger.request("tools/list")[0] == 400  # initialize first
        agent = sk.McpClient(address)
        agent.initialize()
        _, listed = agent.request("tools/list")
        names = {tool["name"] for tool in listed["result"]["tools"]}
        assert {"refund_order", "lookup_order", "fiduciary_request"} <= names
        _committed(agent.call("refund_order", sk.ORDER_7))
        other = sk.McpClient(address)
        other.initialize()
        assert other.session != agent.session
        assert served.health()[1]["sessions"] == 2
        assert agent.close() == 204
        assert agent.request("tools/list")[0] == 404  # closed
        sk.wait_for(lambda: served.health()[1]["sessions"] == 1, what="one session left")

        sk.wait_for(lambda: "fiduciary_log_records " in served.metrics(), what="a sample")
        text = served.metrics()
        assert 'fiduciary_role{role="fiduciary-writer"} 1' in text
        assert 'interlock_service_up{service="writer"} 1' in text
        assert 'fiduciary_lineages{status="bound"} 1' in text
    state = _verified(world)
    events = sorted(event for events in state.sessions.values() for event, _, _ in events)
    assert events.count("opened") == 2 and events.count("closed") == 2  # the other at stop


def test_the_service_tickets_a_dead_message_and_executes_the_warrant(world: bound.World) -> None:
    """The triggers part finds the dead message and raises its ticket; the
    principals' approvals reach the intake; the writer forms the warrant, and
    the executor requeues the message (§7.1, §7.3, §7.5)."""
    config = sk.configure(world)
    with sk.serving(config) as served:
        agent = sk.McpClient(served.bridge())
        agent.initialize()
        _committed(agent.call("refund_order", sk.ORDER_7))
        _deliver(world, "permanent")
        assert world.outbox_states() == ["dead"]
        intake = reopen(world.store)
        try:

            def dead_ticket() -> Any:
                state = sk.state_of(intake)
                return next(
                    (row for row in state.tickets.values() if row.ticket_class == "dead"), None
                )

            row = sk.wait_for(dead_ticket, what="the dead message's ticket")
            ticket = sk.ticket_of(intake, row.digest)
            time.sleep(1.0)  # the next scans: a live ticket's message raises nothing more
            assert len([t for t in sk.state_of(intake).tickets.values()]) == 1
            sk.submit_approvals(intake, ticket, "requeue")

            def applied() -> bool:
                state = sk.state_of(intake)
                return any(state.execution_status(w) == "applied" for w in state.warrants)

            sk.wait_for(applied, what="the requeue executed")
        finally:
            intake.close()
        assert world.outbox_states() == ["pending"]
    _verified(world)


def test_the_settler_receipts_each_delivery(world: bound.World) -> None:
    config = sk.configure(world)
    with sk.serving(config) as served:
        agent = sk.McpClient(served.bridge())
        agent.initialize()
        _committed(agent.call("refund_order", sk.ORDER_7))
        _deliver(world, "delivered")
        sk.wait_for(
            lambda: served.health()[1]["services"]["settler"]["counters"].get("receipts"),
            what="a delivery receipted",
        )
    from agentgov.receipts import ReceiptLog

    from fiduciary.keys import load_key

    log = ReceiptLog(
        "fiduciary-receipts",
        load_key(world.tmp / "service.key"),
        path=world.tmp / "bridge-receipts.jsonl",
    )
    try:
        (delivered,) = log.deliveries()
        actions = {r.receipt_id for r in log.receipts()}
        assert delivered.action.receipt_id in actions
    finally:
        log.close()
    _verified(world)


def test_the_service_ends_a_term(world: bound.World) -> None:
    end = time.time() + 4
    from datetime import UTC, datetime

    from fiduciary import fidu1

    raw = kit.mandate_json(world)
    raw["term"] = {
        **raw["term"],
        "not_after": fidu1.format_instant(datetime.fromtimestamp(end, UTC)),
    }
    mandate = Mandate.from_json(raw, signed=False).signed(e.signer("cfo"))
    config = sk.configure(world, mandate=mandate)
    with sk.serving(config) as served:
        sk.wait_for(
            lambda: 'fiduciary_lineages{status="expired"} 1' in served.metrics(),
            what="the lineage expired",
            timeout=20,
        )
    state = _verified(world)
    assert state.status(mandate.mandate_id) == "expired"


def test_a_part_that_fails_is_restarted_after_its_backoff(
    world: bound.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    failed: list[int] = []
    raise_triggers = service_module.raise_triggers

    def flaky(fid: Any) -> list[Any]:
        if len(failed) < 2:
            failed.append(1)
            raise RuntimeError("the outbox did not answer")
        return raise_triggers(fid)

    monkeypatch.setattr(service_module, "raise_triggers", flaky)
    config = sk.configure(world)
    with sk.serving(config) as served:

        def recovered() -> bool:
            triggers = served.health()[1]["services"]["triggers"]
            return bool(triggers["failures"] == 2 and triggers["state"] == "running")

        sk.wait_for(recovered, what="the triggers part running again")
        assert served.health()[1]["services"]["writer"]["failures"] == 0


# -- the command ------------------------------------------------------------------------


def test_fiduciary_serve_reloads_on_sighup_and_drains_on_sigterm(world: bound.World) -> None:
    config = sk.configure(world)
    command = [
        sys.executable,
        "-c",
        "from fiduciary.cli import main_entry; main_entry()",
        "--config",
        str(config),
        "serve",
    ]
    process = subprocess.Popen(  # noqa: S603 - this interpreter, running fiduciary
        command,
        cwd=ROOT,
        env={**__import__("os").environ, "PYTHONPATH": str(ROOT)},
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stderr is not None
        port = None
        lines = []
        deadline = time.monotonic() + 60
        while port is None and time.monotonic() < deadline:
            line = process.stderr.readline()
            lines.append(line)
            if "metrics on http://" in line:
                port = int(line.rsplit(":", 1)[1].split("/")[0])
        assert port is not None, "".join(lines)

        def health() -> dict[str, Any]:
            _, _, body = sk.get(("127.0.0.1", port), "/healthz")
            return dict(body)  # 503 while a part opens again: its body says which

        sk.wait_for(lambda: health().get("bridge"), what="the bridge")
        process.send_signal(signal.SIGHUP)
        sk.wait_for(
            lambda: health()["services"]["writer"]["counters"].get("reloads"),
            what="the writer reloaded",
        )
        # A service key that is another key is not swapped in: the log is held to one.
        (world.tmp / "service.key").write_text("ab" * 32 + "\n")
        process.send_signal(signal.SIGHUP)
        sk.wait_for(
            lambda: health()["services"]["writer"]["counters"].get("reloads_refused"),
            what="the reload refused",
        )
        sk.wait_for(lambda: health()["services"]["writer"]["state"] == "running", what="the writer")
        process.send_signal(signal.SIGTERM)
        _, rest = process.communicate(timeout=60)
        assert process.returncode == 0, "".join(lines) + rest
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    _verified(world)

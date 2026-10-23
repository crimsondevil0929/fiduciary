"""What the service tests share: a deployment's ``fiduciary.toml`` with the
service's tables, the supervisor run on a thread, an agent speaking MCP over
HTTP, and the principals' approvals submitted to the intake.

The service runs on the wall clock. A world's mandate, issued on the fixed
clock of 2026-10-18, is in force until 2027, and the approvals are stamped by
:class:`~tests.conftest.LiveClock`.
"""

from __future__ import annotations

import asyncio
import hashlib
import http.client
import json
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from agentgov.receipts.signing import Ed25519Signer, verifier_of
from interlock.supervisor import InterlockSupervisor

from fiduciary import fidu1
from fiduciary.bundle.export import spec
from fiduciary.documents import Approval, Record, Ticket, loads
from fiduciary.service import build_service
from fiduciary.store import State, Store
from tests import bound
from tests import examples as e
from tests.conftest import LiveClock
from tests.test_cli_mcp import _configure

TOKEN: Final = "desk-token-" + "7" * 32
"""The agent ``desk``'s bearer token; the configuration holds its SHA-256."""
RELAY: Final = Ed25519Signer(hashlib.sha256(b"fiduciary-test/relay").digest())
"""The relay the tests deliver through, registered in ``[relays.keys]``."""
ORDER_7: Final = {"order_id": 7, "amount": "12.50", "payment_intent": "pi_0000000007"}

SERVE: Final = (
    "[serve]\nevery_seconds = 0.2\ntriggers_seconds = 0.3\nsettle_seconds = 0.3\n"
    "sample_seconds = 0.3\nidle_seconds = 600\nrestart_min_seconds = 0.1\n"
    "restart_max_seconds = 1\ndrain_timeout_seconds = 10\n"
)


def configure(
    world: bound.World,
    *,
    top: str = "",
    mandate: Any = None,
    serve: str = SERVE,
    tail: str = "",
    bridge: str = "127.0.0.1:0",
    metrics: str = "127.0.0.1:0",
    ledger: str | None = None,
) -> Path:
    """The deployment's files, with the service's tables: ``[serve]``,
    ``[metrics]``, the bridge's listener, and the agent ``desk`` (tenant
    ``acme``). The relay :data:`RELAY` is registered with interlock."""
    config = _configure(world, top=top, mandate=mandate, ledger=ledger)
    interlock = world.tmp / "interlock.toml"
    interlock.write_text(
        interlock.read_text()
        + f'[relays.keys]\nrelay-1 = "{spec(verifier_of(RELAY))}"\n'
        + f'[operators]\nlog = "{world.tmp / "operators.jsonl"}"\n'
        + f'[operators.keys]\nfiduciary = "{spec(e.SERVICE.public_key())}"\n'
    )
    digest = hashlib.sha256(TOKEN.encode()).hexdigest()
    config.write_text(
        config.read_text()
        + serve
        + f'[metrics]\nlisten = "{metrics}"\n'
        + f'[bridge]\nhttp = "{bridge}"\n'
        + f'[bridge.agents.desk]\nmandate = "{bound.SCOPE}"\ntenant = "acme"\n'
        + f'token_sha256 = "{digest}"\n'
        + tail
    )
    return config


@dataclass
class Served:
    """A supervisor running ``fiduciary serve``'s parts on a thread."""

    supervisor: InterlockSupervisor
    thread: threading.Thread
    errors: list[BaseException] = field(default_factory=list)

    def health(self) -> tuple[int, dict[str, Any]]:
        port = self.supervisor.metrics_port
        assert port is not None
        status, _, body = get(("127.0.0.1", port), "/healthz")
        return status, body

    def metrics(self) -> str:
        port = self.supervisor.metrics_port
        assert port is not None
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/metrics")
        text = conn.getresponse().read().decode()
        conn.close()
        return text

    def bridge(self) -> tuple[str, int]:
        found = wait_for(lambda: self.health()[1].get("bridge"), what="the bridge")
        host, _, port = str(found).rpartition(":")
        return host, int(port)


@contextmanager
def serving(config: Path, **kw: Any) -> Iterator[Served]:
    """``build_service(config)`` running until the block ends; its parts open."""
    supervisor = build_service(str(config), **kw)
    served = Served(supervisor, threading.Thread(target=lambda: None))

    def run() -> None:
        try:
            asyncio.run(supervisor.run())
        except BaseException as exc:  # what the test asserts on
            served.errors.append(exc)

    served.thread = threading.Thread(target=run, daemon=True)
    served.thread.start()
    try:
        wait_for(lambda: supervisor.metrics_port, what="the metrics listener")
        wait_for(lambda: served.health()[0] == 200, what="every part running")
        yield served
    finally:
        supervisor.stop()
        assert supervisor.wait_finished(60), "the supervisor did not stop"
        served.thread.join(10)
    assert not served.errors, served.errors


def wait_for(found: Callable[[], Any], *, what: str, timeout: float = 30.0) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = found()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(f"waited {timeout:g}s for {what}")


def get(address: tuple[str, int], path: str) -> tuple[int, dict[str, str], Any]:
    conn = http.client.HTTPConnection(*address, timeout=10)
    conn.request("GET", path)
    response = conn.getresponse()
    payload = response.read()
    conn.close()
    headers = {k.lower(): v for k, v in response.getheaders()}
    return response.status, headers, json.loads(payload) if payload else None


class McpClient:
    """An agent of ``[bridge.agents]``, over Streamable HTTP."""

    def __init__(self, address: tuple[str, int], token: str = TOKEN) -> None:
        self.address = address
        self.token = token
        self.session: str | None = None
        self._ids = 0

    def request(self, method: str, params: dict[str, Any] | None = None) -> tuple[int, Any]:
        self._ids += 1
        body = {"jsonrpc": "2.0", "id": self._ids, "method": method, "params": params or {}}
        status, headers, answer = self.post(body)
        if self.session is None and "mcp-session-id" in headers:
            self.session = headers["mcp-session-id"]
        return status, answer

    def post(self, body: Any, method: str = "POST") -> tuple[int, dict[str, str], Any]:
        conn = http.client.HTTPConnection(*self.address, timeout=30)
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"}
        if self.session is not None:
            headers["Mcp-Session-Id"] = self.session
        data = json.dumps(body).encode() if method == "POST" else None
        conn.request(method, "/mcp", body=data, headers=headers)
        response = conn.getresponse()
        payload = response.read()
        conn.close()
        return (
            response.status,
            {k.lower(): v for k, v in response.getheaders()},
            json.loads(payload) if payload else None,
        )

    def initialize(self) -> str:
        status, answer = self.request("initialize", {"protocolVersion": "2025-06-18"})
        assert status == 200 and "result" in answer, answer
        assert self.session is not None
        return self.session

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        status, answer = self.request("tools/call", {"name": name, "arguments": arguments})
        assert status == 200, answer
        result: dict[str, Any] = answer["result"]
        return result

    def close(self) -> int:
        status, _, _ = self.post({}, method="DELETE")
        return status


def state_of(store: Store) -> State:
    return State.of([Record.from_json(fidu1.parse(r.data)) for r in store.records("fiduciary")])


def ticket_of(store: Store, digest: str) -> Ticket:
    found = store.document(digest)
    assert found is not None
    ticket = loads(found[1])
    assert isinstance(ticket, Ticket)
    return ticket


def submit_approvals(
    store: Store,
    ticket: Ticket,
    action: str,
    principals: tuple[str, ...] = ("cfo", "head-ops"),
) -> None:
    """Each principal's approval of ``ticket``'s ``action``, signed on the
    wall clock, submitted as the principal API does."""
    option = next(o for o in ticket.options if o.resolution.action == action)
    window = bound.window(LiveClock())
    for principal in principals:
        raw = e.approval_json(
            principal,
            subject="ticket",
            digest=ticket.digest,
            resolution=option.digest,
            approval_id=str(uuid.uuid4()),
            **window,
        )
        signed = Approval.from_json(raw, signed=False).signed(e.signer(principal))
        store.submit(signed.canonical())

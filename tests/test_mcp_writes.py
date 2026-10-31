"""An MCP server's write tool, governed as a plan (amended in 1.4.0).

The tool is a ``plan`` binding whose one request goes to a sink of kind
``mcp``: the call is measured and judged before anything leaves, committed to
the outbox, held for a warrant when its operation says so, and made by the
relay through Interlock's ``McpAdapter`` at most once. Its receipt is
``request-only``, and the verifier says so.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator, Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any, Final

import pytest
from agentgov.receipts import ActionReceipt
from interlock.mcp import McpAdapter, McpCallError, mcp_sink
from interlock.relay import LedgerBreaker, Relay
from interlock.sqlite_outbox import RELAY, SqliteOutboxStore

from fiduciary.bundle import TrustedKeys, verify_bundle
from fiduciary.demo.packs import saas
from fiduciary.demo.packs.kit import PackWorld, build, export, pinned, plan_tool, request

CRM: Final = "crm"


def _close_ticket() -> dict[str, Any]:
    return plan_tool(
        "close_ticket",
        "Close one support ticket in the CRM, through its MCP server.",
        {"ticket": {"type": "string", "pattern": "^T-[0-9]{1,6}$"}},
        [],
        [
            request(
                "close",
                CRM,
                "close_ticket",
                {"ticket": "$.ticket", "org": "$session.tenant"},
                [],
            )
        ],
    )


def _outbound() -> dict[str, Any]:
    found = saas._outbound()
    found["sinks"][CRM] = {
        "operations": ["close_ticket"],
        "max_attempts": 3,
        "not_after_seconds": 900,
        "max_payload_bytes": 4096,
    }
    found["per_sink"][CRM] = 1
    found["tenants"]["fields"][CRM] = "org"
    return found


PACK: Final = dataclasses.replace(
    saas.PACK,
    sinks=(
        *saas.SINKS,
        mcp_sink(CRM, {"close_ticket": None}, held=["close_ticket"], max_attempts=3),
    ),
    tools=lambda: [*saas._tools(), _close_ticket()],
    outbound=_outbound,
)


class _Server:
    """A stand-in MCP server: each ``tools/call`` it was given, and its answer."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail: McpCallError | None = None

    def __call__(self, tool: str, arguments: Mapping[str, Any], timeout: float) -> dict[str, Any]:
        self.calls.append((tool, dict(arguments)))
        if self.fail is not None:
            raise self.fail
        return {"content": [{"type": "text", "text": "closed"}], "structuredContent": {"id": "T-1"}}


@pytest.fixture
def world(tmp_path: Path) -> Iterator[PackWorld]:
    found = build(tmp_path / "deployment", PACK)
    try:
        yield found
    finally:
        found.close()


def _deliver(world: PackWorld, server: _Server) -> None:
    store = SqliteOutboxStore(str(world.back_office), writes=RELAY)
    relay = Relay(
        store,
        adapters={CRM: McpAdapter(server)},
        breaker=LedgerBreaker(world.governor),
        signer=world.signers["relay-b"],
        relay_id="relay-b",
        lease=timedelta(milliseconds=800),
        timeout=timedelta(milliseconds=300),
    )
    try:
        for _ in range(20):
            if not relay.run_once(limit=20):
                break
    finally:
        store.close()


def _close(world: PackWorld, ticket: str) -> str:
    """One ``close_ticket`` call, committed; the message it enqueued."""
    before = set(world.outbox_states())
    with world.fid.session(PACK.scope, tenant=saas.ACME, trajectory="support") as session:
        result = session.call("close_ticket", {"ticket": ticket})
    assert result.committed, result.feedback
    (message,) = set(world.outbox_states()) - before
    return message


def test_an_mcp_write_is_judged_held_and_made_once_on_a_warrant(world: PackWorld) -> None:
    server = _Server()
    message = _close(world, "T-1")
    assert world.outbox_states()[message] == "held"
    _deliver(world, server)
    assert server.calls == [], "a held call waits for its warrant"
    (receipt,) = [
        r
        for r in world.receipts.receipts()
        if isinstance(r, ActionReceipt) and r.coverage.measurement == "request-only"
    ]
    assert receipt.coverage.measurement == "request-only"
    assert receipt.cost.served_by == (CRM,)

    mandate = world.mandate
    assert mandate is not None
    escalation = world.fid.escalation
    ticket, _ = escalation.raise_held(mandate.mandate_id, world.outbox)
    assert ticket is not None
    release = next(o for o in ticket.options if o.resolution.action == "release")
    for principal in PACK.principals[:2]:
        world.approve(principal, "ticket", ticket.digest, resolution=release.digest)
        escalation.process_intake()
    warrant = escalation.warrant_for_ticket(ticket.ticket_id)
    assert warrant is not None
    world.fid.executor.execute(warrant)
    _deliver(world, server)
    _deliver(world, server)
    assert server.calls == [("close_ticket", {"ticket": "T-1", "org": saas.ACME})]
    assert world.outbox_states()[message] == "delivered"

    data = export(world, "after-close")
    report = verify_bundle(data, charter=world.charter.digest, keys=TrustedKeys(pinned(world)))
    assert report.passed, report.render()
    assert any(n.startswith("measured request-only: ") for n in report.notes), report.notes


def test_a_call_that_may_have_acted_is_never_made_again(world: PackWorld) -> None:
    server = _Server()
    server.fail = McpCallError("the server went quiet after the call", sent=True)
    message = _close(world, "T-2")
    import uuid

    from interlock.operators import Operator, OperatorLog
    from interlock.records import Keyring

    names = ("service", PACK.operator)
    keyring = Keyring({world.key_id(n): world.signers[n].public_key() for n in names})
    log = OperatorLog(
        world.operator_log, world.signers[PACK.operator], keyring, ledger=world.governor
    )
    try:
        Operator(log, world.outbox).release([uuid.UUID(message)], reason="test: release")
    finally:
        log.close()
    _deliver(world, server)
    _deliver(world, server)
    assert len(server.calls) == 1, "an unknown outcome is dead-lettered, not retried"
    assert world.outbox_states()[message] == "dead"

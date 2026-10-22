"""Sessions (§8.1, F8-1 to F8-3, F8-10), on SQLite and PostgreSQL."""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Mapping
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agentgov.interceptor import TokenUsage

from fiduciary import fidu1
from fiduciary.bridge import Session, SessionError, SessionHaltedError
from fiduciary.documents import Mandate
from fiduciary.store import Store
from tests import bound
from tests import bridge_kit as kit
from tests import examples as e
from tests.conftest import Clock, approve
from tests.test_executor import _verify


@pytest.fixture
def world(store: Store, tmp_path: Path, clock: Clock) -> Iterator[bound.World]:
    yield from kit.world(store, tmp_path, clock)


def test_a_session_commits_a_tool_call_and_records_itself(world: bound.World) -> None:
    mandate = kit.mandate(world)
    world.issue_and_bind(mandate)
    fid = kit.fiduciary(world)
    with fid.session(bound.SCOPE, tenant="acme") as session:
        assert isinstance(session, Session)
        result = session.call(
            "refund_order", {"order_id": 7, "amount": "12.50", "payment_intent": "pi_0000000007"}
        )
        assert result.committed, result.render()
    assert [r.kind for r in world.log.records()][-2:] == ["session.opened", "session.closed"]
    assert world.fetch("SELECT total FROM orders WHERE id = 7")[0][0] == 987.5
    _verify(world)


def test_in_process_needs_the_setting(world: bound.World) -> None:
    world.issue_and_bind(kit.mandate(world))
    fid = kit.fiduciary(world, in_process="no")
    with pytest.raises(SessionError, match="F8-10"):
        fid.session(bound.SCOPE, tenant="acme")


# -- shared helpers ------------------------------------------------------------------


class FakeAnthropic:
    """The Messages API's shape, as AgentGov meters it: every call recorded."""

    def __init__(self, output_tokens: int = 10) -> None:
        self.calls: list[dict[str, Any]] = []
        self.messages = self
        self._output = output_tokens

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(
            model=kwargs["model"],
            usage=TokenUsage(input_tokens=10, output_tokens=self._output),
            content=[{"type": "text", "text": f"reply {len(self.calls)}"}],
        )


def _refusals(world: bound.World) -> list[tuple[str | None, str]]:
    return [
        (r.content["tool"], r.content["code"])
        for r in world.log.records()
        if r.kind == "session.refused"
    ]


def _open(world: bound.World, **changes: Any) -> Any:
    mandate = kit.mandate(world, **changes)
    world.issue_and_bind(mandate)
    return mandate, kit.fiduciary(world)


# -- opening, and what a session lists ---------------------------------------------------


def test_a_session_opens_by_lineage_or_subject_and_nothing_else(world: bound.World) -> None:
    mandate, fid = _open(world, data={**kit.mandate_json(world)["data"], "tenants": ["acme"]})
    assert fid.session(mandate.mandate_id, tenant="acme").mandate_id == mandate.mandate_id
    with pytest.raises(SessionError, match="no lineage in force"):
        fid.session("nobody", tenant="acme")
    with pytest.raises(SessionError, match="tenant 'beta' is not one"):
        fid.session(bound.SCOPE, tenant="beta")
    bridged = kit.fiduciary(world, in_process="no").session(
        bound.SCOPE, tenant="acme", bridged=True
    )
    assert bridged.billing_scope == bound.SCOPE and bridged.tenant == "acme"
    assert bridged.trajectory == f"fidu1-session-{bridged.session_id}"
    bridged.close()
    with pytest.raises(SessionError, match="is closed"):
        bridged.call("lookup_order", {"order_id": 1})
    _verify(world)


def test_the_surface_is_five_methods(world: bound.World) -> None:
    """F8-1, F7-4: nothing on a session reaches the engine, the substrate,
    the governor, the outbox, a signer, or an approval."""
    public = {n for n in dir(Session) if not n.startswith("_")}
    methods = {n for n in public if callable(getattr(Session, n))}
    assert methods == {"call", "govern", "tool_definitions", "tickets", "close"}
    assert public - methods == {"session_id", "mandate_id", "tenant", "trajectory", "billing_scope"}
    world.issue_and_bind(kit.mandate(world))
    session = kit.fiduciary(world).session(bound.SCOPE, tenant="acme")
    assert not hasattr(session, "__dict__")  # slotted: nothing hangs off it


def test_tool_definitions_are_the_pinned_bindings_and_the_built_ins(world: bound.World) -> None:
    mandate, fid = _open(world)
    session = fid.session(bound.SCOPE, tenant="acme")
    found = {t["name"]: t for t in session.tool_definitions()}
    assert set(found) == {
        "adjust_orders",
        "lookup_order",
        "refund_order",
        "fiduciary_request",
        "fiduciary_ticket_status",
    }
    for binding in (kit.refund(world.backend), kit.adjust(world.backend), e.lookup()):
        assert found[binding.name]["input_schema"] == dict(binding.input)
        assert found[binding.name]["description"] == binding.description

    # An amendment that drops a binding takes effect at the next call (F1-3, F4-16).
    raw = kit.mandate_json(world)
    raw["tools"]["bindings"] = [p for p in raw["tools"]["bindings"] if p["name"] != "adjust_orders"]
    raw.update(version=2, prev=mandate.digest)
    world.issue(Mandate.from_json(raw, signed=False).signed(e.signer("cfo")))
    assert "adjust_orders" not in {t["name"] for t in session.tool_definitions()}
    refused = session.call("adjust_orders", {"first": 1, "second": 3, "amount": "1.00"})
    assert refused.outcome == "rejected" and not refused.ok
    assert _refusals(world) == [(None, "unpinned")]
    _verify(world)


# -- passthrough tools (F4-15) --------------------------------------------------------------


def test_a_passthrough_call_is_metered_and_served(world: bound.World) -> None:
    calls: list[dict[str, Any]] = []

    def lookup(arguments: Mapping[str, Any]) -> Any:
        calls.append(dict(arguments))
        if arguments["order_id"] == 2:
            raise RuntimeError("the lookup service is down")
        if arguments["order_id"] == 3:
            return {"ratio": 0.5}  # no float leaves a tool
        return {"order_id": arguments["order_id"], "status": "open"}

    world.issue_and_bind(kit.mandate(world))
    fid = kit.fiduciary(world, handlers={"lookup_order": lookup})
    session = fid.session(bound.SCOPE, tenant="acme")
    served = session.call("lookup_order", {"order_id": 1})
    assert served.ok and served.value == {"order_id": 1, "status": "open"}
    spends = [
        en for en in world.governor.audit_trail(bound.SCOPE) if en.memo.startswith("FIDU1 tool")
    ]
    assert [str(en.amount) for en in spends if en.entry_type.value == "spend"] == ["0.00100000"]
    for order in (2, 3):
        failed = session.call("lookup_order", {"order_id": order})
        assert failed.outcome == "error" and "nothing was charged" in failed.feedback
    assert len(calls) == 3
    unserved = kit.fiduciary(world, handlers={}).session(bound.SCOPE, tenant="acme")
    assert unserved.call("lookup_order", {"order_id": 1}).outcome == "error"
    _verify(world)


# -- built-ins (F8-4) -------------------------------------------------------------------------


def test_requests_raise_only_what_the_session_may_ask_for(world: bound.World) -> None:
    mandate, fid = _open(world)
    session = fid.session(bound.SCOPE, tenant="acme")
    ask = "Please widen the refund cap for this customer."
    assert session.call("fiduciary_request", {"kind": "amend", "statement": ask}).outcome == "error"
    assert (
        session.call(
            "fiduciary_request", {"kind": "amend", "statement": ask, "mandate": "a" * 64}
        ).outcome
        == "error"
    )
    raw = kit.mandate_json(world)
    raw.update(version=2, prev=mandate.digest)
    proposed = Mandate.from_json(raw, signed=False).signed(e.signer("cfo"))
    world.authority.propose(proposed)
    asked = session.call(
        "fiduciary_request", {"kind": "amend", "statement": ask, "mandate": proposed.digest}
    )
    assert asked.ok and asked.ticket is not None and asked.ticket.ticket_class == "amend"
    status = session.call("fiduciary_ticket_status", {"ticket_id": asked.ticket.ticket_id})
    assert status.value == {
        "ticket_id": asked.ticket.ticket_id,
        "class": "amend",
        "not_after": asked.ticket.to_json()["not_after"],
        "status": "open",
    }
    assert "cfo" not in status.render() and "head-ops" not in status.render()
    stranger = session.call("fiduciary_ticket_status", {"ticket_id": str(uuid.uuid4())})
    assert stranger.outcome == "error"
    assert session.call("fiduciary_ticket_status", {"ticket_id": "x"}).outcome == "rejected"

    # An undo names a plan this session committed: no other.
    undo = {"kind": "undo", "statement": "Reverse it.", "plan_id": "plan-000000000000"}
    assert session.call("fiduciary_request", undo).outcome == "error"
    done = session.call(
        "refund_order", {"order_id": 5, "amount": "3.00", "payment_intent": "pi_0000000005"}
    )
    assert done.committed
    plan_id = next(iter(session._committed))
    nothing = session.call("fiduciary_request", {**undo, "plan_id": plan_id})
    assert nothing.outcome == "error"  # no outbox here, and nothing compensable
    assert [t.ticket_class for t in session.tickets()] == ["amend"]
    _verify(world)


# -- model calls (§8.3) ------------------------------------------------------------------------


def test_model_calls_are_held_to_the_allowlist_price_and_ceiling(world: bound.World) -> None:
    world.issue_and_bind(kit.mandate(world))
    fid = kit.fiduciary(world)
    session = fid.session(bound.SCOPE, tenant="acme")
    client = FakeAnthropic()
    model = session.govern(client)
    reply = model.messages.create(
        model="claude-opus-5-5", max_tokens=100_000, messages=[{"role": "user", "content": "hi"}]
    )
    assert reply.content[0]["text"] == "reply 1"
    assert client.calls[0]["max_tokens"] == 4096  # the mandate's ceiling
    for name in ("claude-haiku-4-5", "claude-opus-5-1"):  # unlisted; listed is not enough
        with pytest.raises(SessionHaltedError) as halted:
            model.messages.create(model=name, max_tokens=10, messages=[])
        assert halted.value.kind == "model"
    assert len(client.calls) == 1
    assert _refusals(world) == [(None, "model"), (None, "model")]
    _verify(world)


def test_a_runaway_session_is_halted_into_recovery(world: bound.World) -> None:
    governance = {**kit.mandate_json(world)["governance"], "max_calls": 3}
    world.issue_and_bind(kit.mandate(world, governance=governance))
    fid = kit.fiduciary(world)
    session = fid.session(bound.SCOPE, tenant="acme")
    model = session.govern(FakeAnthropic())
    transcript: list[dict[str, Any]] = [{"role": "user", "content": "Close the books."}]
    for n in range(3):  # distinct turns: progress, not a loop
        transcript.append({"role": "user", "content": f"Ledger {n}: reconcile {'abc'[n] * 9}."})
        model.messages.create(model="claude-opus-5-5", max_tokens=4096, messages=transcript)
    with pytest.raises(SessionHaltedError) as halted:
        model.messages.create(model="claude-opus-5-5", max_tokens=4096, messages=transcript)
    step = halted.value
    assert step.kind == "runaway" and step.messages is not None and step.max_tokens == 2048
    assert session.billing_scope == f"{bound.SCOPE}/recovery"
    model.messages.create(model="claude-opus-5-5", max_tokens=4096, messages=list(step.messages))
    # The velocity is the session's own, and holds tool calls too.
    assert session.call("lookup_order", {"order_id": 1}).outcome == "rejected"
    assert _refusals(world) == [("lookup_order", "velocity")]
    _verify(world)


def test_a_safety_halt_is_never_answered_with_money(world: bound.World) -> None:
    """F8-9: an operator's halt is recovered from on the reserve, and ends
    with it; nothing is quoted, and no extension ticket is raised."""
    recovery = {**kit.mandate_json(world)["recovery"], "reserve": "1.00000000", "max_steps": 6}
    world.issue_and_bind(kit.mandate(world, recovery=recovery))
    fid = kit.fiduciary(world)
    session = fid.session(bound.SCOPE, tenant="acme")
    world.governor.trip(bound.SCOPE, "operator stop: reviewing this agent")
    model = session.govern(FakeAnthropic(output_tokens=20_000))
    transcript: list[dict[str, Any]] = [{"role": "user", "content": "Carry on."}]
    with pytest.raises(SessionHaltedError) as halted:
        model.messages.create(model="claude-opus-5-5", max_tokens=1024, messages=transcript)
    assert halted.value.kind == "halted" and halted.value.messages is not None
    model.messages.create(model="claude-opus-5-5", max_tokens=1024, messages=transcript)
    ended = None
    for _ in range(10):
        try:
            model.messages.create(model="claude-opus-5-5", max_tokens=1024, messages=transcript)
        except SessionHaltedError as exc:
            ended = exc
            break
    assert ended is not None and ended.ticket is None and ended.kind == "halted"
    assert session.tickets() == []
    assert not [r for r in world.log.records() if r.kind == "ticket.raised"]
    _verify(world)


def test_spent_out_without_recovery_is_quoted_and_granted_beside_the_subject(
    world: bound.World, clock: Clock
) -> None:
    """With no recovery policy, a spent-out subject is quoted directly: the
    grant delegates ``{scope}/ext-1``, and billing moves there (F8-8)."""
    budget = {**kit.mandate_json(world)["budget"], "envelope": "1.00000000"}
    world.issue_and_bind(kit.mandate(world, budget=budget, delegation=None, recovery=None))
    fid = kit.fiduciary(world)
    session = fid.session(bound.SCOPE, tenant="acme")
    model = session.govern(FakeAnthropic(output_tokens=20_000))
    transcript: list[dict[str, Any]] = [{"role": "user", "content": "Finish the batch."}]
    ticket = None
    for n in range(5):
        try:
            model.messages.create(
                model="claude-opus-5-5",
                max_tokens=1024,
                messages=[*transcript, {"role": "user", "content": f"Batch item {n}."}],
            )
        except SessionHaltedError as exc:
            ticket = exc.ticket
            break
    assert ticket is not None and ticket.ticket_class == "extension"
    document = fid.escalation.ticket(ticket.ticket_id)
    grant = next(o for o in document.options if o.resolution.action == "grant")
    approve(
        world.store,
        ("cfo", "head-ops"),
        "ticket",
        document.digest,
        resolution=grant.digest,
        issued_at=fidu1.format_instant(clock()),
        not_after=fidu1.format_instant(clock() + timedelta(minutes=30)),
    )
    fid.serve_once()
    wrap_up = [{"role": "user", "content": "Write the closing summary for the auditors."}]
    model.messages.create(model="claude-opus-5-5", max_tokens=10, messages=wrap_up)
    assert session.billing_scope == f"{bound.SCOPE}/ext-1"
    session.close()
    assert world.log.records()[-1].content["scopes"] == [bound.SCOPE, f"{bound.SCOPE}/ext-1"]
    _verify(world)

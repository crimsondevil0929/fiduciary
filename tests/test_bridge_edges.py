"""The bridge's edges: how a plan is built from a binding, what a session does
when the systems under it refuse, fail or halt mid-call, and how a halt ends
(F4-14, F8-2, F8-3, F8-8, F8-9), on SQLite and PostgreSQL."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from interlock.engine import EscrowEngine
from interlock.exceptions import InterlockError, PlanError

from fiduciary import Tool
from fiduciary import bridge as bridge_module
from fiduciary.bridge import (
    Fiduciary,
    SessionError,
    SessionHaltedError,
    build_plan,
    check_arguments,
)
from fiduciary.documents import Mandate
from fiduciary.escalation import EscalationError
from fiduciary.exceptions import FiduciaryError
from fiduciary.store import Store
from tests import bound
from tests import bridge_kit as kit
from tests import examples as e
from tests.conftest import Clock
from tests.test_bridge import FakeAnthropic, _refusals
from tests.test_executor import _outbox_ops, _verify

ORDER_7 = {"order_id": 7, "amount": "12.50", "payment_intent": "pi_0000000007"}
CLOSE = [{"role": "user", "content": "Close the month's books for the ledger team."}]


@pytest.fixture
def world(store: Store, tmp_path: Path, clock: Clock) -> Iterator[bound.World]:
    yield from kit.world(store, tmp_path, clock)


def _fid(world: bound.World, **changes: Any) -> Fiduciary:
    world.issue_and_bind(kit.mandate(world, **changes))
    return kit.fiduciary(world)


# -- arguments and plans (F4-13, F4-14) -----------------------------------------------------


def test_a_plan_takes_only_what_the_binding_says() -> None:
    assert check_arguments({"type": "object"}, ["not", "an", "object"]) == [
        "$ is not an object of named arguments"
    ]
    binding = (
        Tool.plan("notify", "Note a refund, and tell the customer.")
        .argument("order_id", {"type": "integer"})
        .argument("note", {"type": "string"}, required=False)
        .insert(
            "note",
            "items",
            "INSERT INTO items (tenant, sku, qty) VALUES (:tenant, :sku, 1)",
            tenant="$session.tenant",
            sku="$session.trajectory",
        )
        .request(
            "mail",
            "stripe",
            "charges.capture",
            {"tags": ["$session.tenant", "$.order_id", 3], "ref": "$session.trajectory"},
            after=["note"],
            compensation={
                "sink": "stripe",
                "operation": "refunds.create",
                "payload": {"ref": "$.note"},
            },
        )
        .build()
    )
    plan = build_plan(binding, {"order_id": 4}, scope="s", trajectory="traj-1", tenant="acme")
    note, mail = plan.effects
    assert note.parameters == {"sku": "traj-1", "tenant": "acme"} and note.tenant_id == "acme"
    assert mail.request is not None and mail.request.payload == {
        "tags": ("acme", 4, 3),
        "ref": "traj-1",
    }
    assert mail.request.compensation is not None
    assert mail.request.compensation.payload == {"ref": None}  # an argument not given
    assert plan.intent == f"FIDU1 tool notify {binding.digest[:16]}"
    with pytest.raises(PlanError, match="passthrough"):
        build_plan(e.lookup(), {"order_id": 1}, scope="s", trajectory="t", tenant=None)
    with pytest.raises(SessionError, match="in_process"):
        Fiduciary(None, None, None, in_process="sometimes")  # type: ignore[arg-type]


# -- what the systems under a call refuse --------------------------------------------------


def test_a_call_whose_systems_refuse_says_so_and_changes_nothing(
    world: bound.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    fid = _fid(world)
    session = fid.session(bound.SCOPE, tenant="acme")
    before = world.fetch("SELECT id, total FROM orders ORDER BY id")

    def unbuildable(*args: Any, **kwargs: Any) -> Any:
        raise PlanError("a cycle")

    with monkeypatch.context() as patched:
        patched.setattr(bridge_module, "build_plan", unbuildable)
        assert session.call("refund_order", ORDER_7).outcome == "error"

    def unrepairable(self: Any, plan: Any, **kwargs: Any) -> Any:
        raise InterlockError("the repair stage failed")

    adjust = {"first": 1, "second": 2, "amount": "1.00"}
    with monkeypatch.context() as patched:
        patched.setattr(EscrowEngine, "repair", unrepairable)
        assert session.call("adjust_orders", adjust).outcome == "refused"

    def unraisable(*args: Any, **kwargs: Any) -> Any:
        raise EscalationError("the ledger is unreadable")

    with monkeypatch.context() as patched:
        patched.setattr(fid.escalation, "raise_refused", unraisable)
        result = session.call("adjust_orders", {**adjust, "first": 3, "second": 4})
        assert result.outcome == "refused" and result.ticket is None and result.repair

    def unexecutable(*args: Any, **kwargs: Any) -> Any:
        raise FiduciaryError("executing elsewhere")

    with monkeypatch.context() as patched:
        patched.setattr(fid.executor, "execute", unexecutable)
        result = session.call("adjust_orders", {**adjust, "first": 5, "second": 6})
        assert result.ticket is not None and not result.repaired

    world.binder.engine(session.mandate_id).close()  # the lineage no longer governs
    assert session.call("refund_order", ORDER_7).outcome == "halted"
    assert world.fetch("SELECT id, total FROM orders ORDER BY id") == before
    _verify(world)


def test_requests_that_escalate_and_loop(world: bound.World) -> None:
    rule = {"id": "no-amend", "class": "amend", "resolution": "reject", "when": {}}
    escalation = {"max_open": 5, "ttl_seconds": 3600, "auto": [rule]}
    world.issue_and_bind(kit.mandate(world, escalation=escalation))
    ops, conn = _outbox_ops(world)
    try:
        fid = kit.fiduciary(world, outbox=ops)
        session = fid.session(bound.SCOPE, tenant="acme")
        assert session.call("refund_order", ORDER_7).committed
        (plan_id,) = session._committed
        undo = {"kind": "undo", "statement": "Reverse the refund.", "plan_id": plan_id}
        assert session.call("fiduciary_request", undo).outcome == "error"  # not delivered yet
        stored = world.charter.digest  # a document, and no mandate
        ask = {"kind": "amend", "statement": "More room.", "mandate": stored}
        assert session.call("fiduciary_request", ask).outcome == "error"
        head = world.authority.decide.head_mandate(world.log.state(), session.mandate_id)
        raw = kit.mandate_json(world, escalation=escalation)
        raw.update(version=2, prev=head.digest)
        proposed = Mandate.from_json(raw, signed=False).signed(e.signer("cfo"))
        world.authority.propose(proposed)
        asked = session.call("fiduciary_request", {**ask, "mandate": proposed.digest})
        assert asked.ticket is not None and asked.ticket.status == "warranted"  # rejected by rule
        for _ in range(3):  # the same request, again and again: thrashing
            last = session.call("fiduciary_request", {**ask, "mandate": proposed.digest})
        assert last.outcome == "halted"
    finally:
        conn.close()
    _verify(world)


# -- halts (§8.3) ---------------------------------------------------------------------------


def test_halts_without_a_recovery_end_where_they_should(world: bound.World) -> None:
    """No recovery policy: a safety halt stands, unquoted (F8-9); a spent-out
    halt is quoted once, and a rule may grant it alone (F7-10)."""
    budget = {**kit.mandate_json(world)["budget"], "envelope": "1.00000000"}
    rule = {"id": "small-grants", "class": "extension", "resolution": "grant", "when": {}}
    fid = _fid(
        world,
        budget=budget,
        delegation=None,
        recovery=None,
        escalation={"max_open": 5, "ttl_seconds": 3600, "auto": [rule]},
    )
    session = fid.session(bound.SCOPE, tenant="acme")
    model = session.govern(FakeAnthropic(output_tokens=20_000))
    world.governor.trip(bound.SCOPE, "operator stop")
    with pytest.raises(SessionHaltedError) as stood:
        model.messages.create(model="claude-opus-5-5", max_tokens=100, messages=CLOSE)
    assert stood.value.kind == "halted" and stood.value.ticket is None
    with pytest.raises(SessionHaltedError):  # no transcript: nothing to recover with
        model.messages.create(model="claude-opus-5-5", max_tokens=100)
    assert session.call("lookup_order", {"order_id": 1}).outcome == "halted"
    world.governor.reset(bound.SCOPE)

    for n in range(4):
        try:
            model.messages.create(
                model="claude-opus-5-5",
                max_tokens=100,
                messages=[{"role": "user", "content": f"Reconcile account {n * 7919}."}],
            )
        except SessionHaltedError as exc:
            granted = exc
            break
    assert granted.ticket is not None and granted.ticket.status == "warranted"
    assert session.billing_scope == f"{bound.SCOPE}/ext-1"  # granted by the rule, here
    _verify(world)


def test_a_recovery_ends_when_its_steps_run_out(world: bound.World) -> None:
    governance = {**kit.mandate_json(world)["governance"], "max_calls": 2}
    recovery = {**kit.mandate_json(world)["recovery"], "max_steps": 1}
    fid = _fid(world, governance=governance, recovery=recovery)
    session = fid.session(bound.SCOPE, tenant="acme")
    client = FakeAnthropic()
    model = session.govern(client)
    turns = [
        [{"role": "user", "content": f"Post journal entry {n} for {'xyz'[n] * 7}."}]
        for n in range(3)
    ]
    for turn in turns[:2]:
        model.messages.create(model="claude-opus-5-5", max_tokens=1000, messages=turn)
    with pytest.raises(SessionHaltedError) as first:
        model.messages.create(model="claude-opus-5-5", max_tokens=1000, messages=turns[2])
    assert first.value.messages is not None
    model.messages.create(
        model="claude-opus-5-5", max_tokens=1000, messages=list(first.value.messages)
    )
    with pytest.raises(SessionHaltedError) as over:  # the velocity again: no step is left
        for turn in turns:
            model.messages.create(model="claude-opus-5-5", max_tokens=1000, messages=turn)
    assert over.value.messages is None and over.value.ticket is None
    _verify(world)


def test_a_model_call_that_fails_releases_what_it_held(world: bound.World) -> None:
    fid = _fid(world)
    session = fid.session(bound.SCOPE, tenant="acme")

    class Flaky(FakeAnthropic):
        fail = False

        def create(self, **kwargs: Any) -> Any:
            if self.fail:
                raise ConnectionError("the network went away")
            return super().create(**kwargs)

    client = Flaky()
    model = session.govern(client)
    world.governor.trip(bound.SCOPE, "operator stop")
    with pytest.raises(SessionHaltedError) as halted:
        model.messages.create(model="claude-opus-5-5", max_tokens=100, messages=CLOSE)
    client.fail = True
    with pytest.raises(ConnectionError):  # the step's call fails: its hold is settled at zero
        model.messages.create(
            model="claude-opus-5-5", max_tokens=100, messages=list(halted.value.messages or ())
        )
    with pytest.raises(ConnectionError):  # a call between steps: its hold is voided
        model.messages.create(model="claude-opus-5-5", max_tokens=100, messages=CLOSE)
    world.governor.refresh()
    held = [h for h in world.governor.ledger.open_holds() if h.scope_id.startswith(bound.SCOPE)]
    assert held == []
    _verify(world)


def test_the_session_counts_in_its_own_window(
    world: bound.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    governance = {**kit.mandate_json(world)["governance"], "max_calls": 2, "seconds": 10}
    fid = _fid(world, governance=governance)
    session = fid.session(bound.SCOPE, tenant="acme")
    now = [1000.0]
    monkeypatch.setattr("fiduciary.bridge.time.monotonic", lambda: now[0])
    for order in (1, 3):
        assert session.call("lookup_order", {"order_id": order}).ok
    status = {"ticket_id": "00000000-0000-4000-8000-000000000000"}
    assert session.call("fiduciary_ticket_status", status).outcome == "rejected"
    now[0] += 11  # the window has moved on
    assert session.call("lookup_order", {"order_id": 5}).ok
    assert _refusals(world) == [("fiduciary_ticket_status", "velocity")]
    _verify(world)


def test_no_guard_and_no_runtime_log_mean_no_quotes(world: bound.World) -> None:
    fid = _fid(world, extension=None)
    session = fid.session(bound.SCOPE, tenant="acme")
    assert fid.guard(session.mandate_id) is None
    assert session.call("lookup_order", {"order_id": 1}).ok  # held through the governor
    bare = Fiduciary(world.authority, world.binder, world.governor, in_process="tool-calls-only")
    assert bare.guard(session.mandate_id) is None and bare.guard("nobody") is None
    assert bare.price("claude-opus-5") is not None and bare.price("no-such-model") is None
    _verify(world)


def test_extension_tickets_past_the_bound_are_recorded(
    world: bound.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    fid = _fid(world)
    session = fid.session(bound.SCOPE, tenant="acme")
    quote = object()
    monkeypatch.setattr(fid.escalation, "raise_extension", lambda *a, **k: (None, None))
    assert session._extension(quote) is None  # type: ignore[arg-type]
    monkeypatch.setattr(
        fid.escalation,
        "raise_extension",
        lambda *a, **k: (_ for _ in ()).throw(FiduciaryError("x")),
    )
    assert session._extension(quote) is None  # type: ignore[arg-type]
    assert _refusals(world) == [(None, "max_open")]
    _verify(world)

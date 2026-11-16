"""Faults a deployment meets in the field (1.21.0): a host clock that steps backwards, and
sub-agents that crash or hang while they hold a budget reservation. Each ends in a bundle
that verifies: V12 for the clock, V6 (the ledger's conservation) for the reservations."""

from __future__ import annotations

import itertools
import threading
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from agentgov.exceptions import DoubleSpendError

from fiduciary.documents import Mandate, ToolBinding
from fiduciary.store import Store
from fiduciary.verify import ordering_problems
from tests import bound
from tests import bridge_kit as kit
from tests import bundle_kit as bk
from tests import examples as e
from tests.conftest import Clock

PRICE = Decimal("70.00000000")
"""A call's price: three of them leave less than a fourth of the mandate's 250.00."""


class Crash(BaseException):
    """A sub-agent's process dying mid-call: nothing in the session runs after it."""


def _tool() -> ToolBinding:
    raw = {
        **e.lookup_json(),
        "name": "costly_lookup",
        "description": "A lookup billed per call.",
        "price": format(PRICE, "f"),
        "input": {
            "type": "object",
            "additionalProperties": False,
            "required": ["key"],
            "properties": {"key": {"type": "string", "maxLength": 64}},
        },
    }
    return ToolBinding.from_json(raw, signed=False).signed(e.DEVELOPER)


@pytest.fixture
def world(store: Store, tmp_path: Path, clock: Clock) -> Iterator[bound.World]:
    from fiduciary.sqlite import SqliteStore

    backend = "sqlite" if isinstance(store, SqliteStore) else "postgres"
    yield from bound.build(backend, tmp_path, store, clock, tools=(*kit.tools(backend), _tool()))


def _mandate(world: bound.World) -> Mandate:
    raw = kit.mandate_json(world)
    tool = _tool()
    raw["tools"]["bindings"] = sorted(
        [*raw["tools"]["bindings"], {"name": tool.name, "digest": tool.digest}],
        key=lambda pin: str(pin["name"]),
    )
    return Mandate.from_json(raw, signed=False).signed(e.signer("cfo"))


def _fid(world: bound.World, handler: Any) -> Any:
    return kit.fiduciary(world, handlers={"lookup_order": kit.lookup, "costly_lookup": handler})


def _verified(world: bound.World, mandate: Mandate) -> Any:
    report = bk.verify(world, bk.export(world, mandate.mandate_id))
    assert report.passed, report.render()
    return report


def test_a_clock_that_steps_back_dates_records_in_order(world: bound.World, clock: Clock) -> None:
    """An NTP correction or a resumed virtual machine: the host clock goes back an hour.
    Records keep their order, so the bundle still passes V12, and the records after the step
    are dated at the last instant until the clock passes it."""
    mandate = _mandate(world)
    world.issue_and_bind(mandate)
    fid = _fid(world, lambda a: {"ok": a["key"]})
    with fid.session(bound.SCOPE, tenant="acme") as session:
        assert session.call("costly_lookup", {"key": "a"}).ok
        before = world.log.records()[-1].issued_at
        clock.advance(hours=-1)
        assert session.call("costly_lookup", {"key": "b"}).ok
        assert session.call("lookup_order", {"order_id": 500}).ok
    records = world.log.records()
    assert all(a.issued_at <= b.issued_at for a, b in itertools.pairwise(records))
    assert records[-1].issued_at == before  # held at the last instant: the clock is behind
    clock.advance(hours=2)
    assert world.log.now() == clock()  # past it again: the clock rules
    _verified(world, mandate)
    # without the floor, the same step reads as records out of order
    shifted = [*records[:-1], _dated(records[-1], before - timedelta(hours=1))]
    assert any("before record" in p for p in ordering_problems(shifted, {}, timedelta(seconds=5)))


def _dated(record: Any, when: Any) -> Any:
    import dataclasses

    return dataclasses.replace(record, issued_at=when)


def test_sub_agents_that_crash_holding_reservations_starve_their_siblings_until_reclaimed(
    world: bound.World,
) -> None:
    """Four sub-agents share one envelope. Three crash mid-call holding a reservation each:
    the fourth is refused for budget. An operator voids the stale holds; the fourth runs,
    and the ledger still balances (V6)."""
    mandate = _mandate(world)
    world.issue_and_bind(mandate)

    def crashes(arguments: dict[str, Any]) -> Any:
        if arguments["key"].startswith("crash"):
            raise Crash
        return {"ok": arguments["key"]}

    fid = _fid(world, crashes)
    governor = fid.governor
    crashed = []
    for n in range(3):
        session = fid.session(bound.SCOPE, tenant="acme")
        with pytest.raises(Crash):
            session.call("costly_lookup", {"key": f"crash-{n}"})
        crashed.append(session)
    stale = governor.stale_authorizations(0)
    assert len(stale) == 3 and all(a.amount == PRICE for a in stale)
    with fid.session(bound.SCOPE, tenant="acme") as sibling:
        starved = sibling.call("costly_lookup", {"key": "sibling"})
        assert starved.outcome == "halted" and "budget" in starved.feedback.lower()
        voided = governor.void_stale(0, memo="sub-agents crashed: their holds released")
        assert {a.authorization_id for a in voided} == {a.authorization_id for a in stale}
        assert sibling.call("costly_lookup", {"key": "sibling"}).ok
    assert governor.stale_authorizations(0) == ()
    governor.verify_conservation()
    _verified(world, mandate)


def test_a_hung_call_whose_hold_was_released_is_charged_once(world: bound.World) -> None:
    """A sub-agent's call hangs; an operator takes it for dead and voids its hold; then it
    returns. AgentGov refuses to capture the released hold (a double spend), and the call's
    cost is booked once, as a new hold captured at once. The ledger balances (V6)."""
    mandate = _mandate(world)
    world.issue_and_bind(mandate)
    released = threading.Event()
    fid: Any = None

    def hangs(arguments: dict[str, Any]) -> Any:
        (held,) = fid.governor.stale_authorizations(0)
        fid.governor.void_stale(0, memo="looked hung: released")
        with pytest.raises(DoubleSpendError):
            fid.governor.capture(held, PRICE, memo="a second settlement of the same hold")
        released.set()
        return {"ok": arguments["key"]}

    fid = _fid(world, hangs)
    available = fid.governor.available(bound.SCOPE)
    with fid.session(bound.SCOPE, tenant="acme") as session:
        assert session.call("costly_lookup", {"key": "slow"}).ok
    assert released.is_set()
    assert fid.governor.stale_authorizations(0) == ()
    assert available - fid.governor.available(bound.SCOPE) == PRICE  # once, not twice
    fid.governor.verify_conservation()
    _verified(world, mandate)

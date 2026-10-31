"""One agreement over several operations of a sink (``data.agreements[].also``,
added in 1.4.0): the dose a Schedule II order sends is held to its row as a
standard order's is, and a rule without ``also`` keeps its bytes."""

from __future__ import annotations

import copy
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from interlock import CrossEffectAgreement
from interlock.types import EffectDiff, EffectId, OutboundDelta, PlanId, RowDelta, _frozen

from fiduciary.compiler import compile
from fiduciary.documents import Mandate
from fiduciary.exceptions import MalformedError
from fiduciary.refines import delegation_problems
from fiduciary.store import Store
from tests import bound
from tests import bridge_kit as kit
from tests import bundle_kit as bk


@pytest.fixture
def world(store: Store, tmp_path: Path) -> Iterator[bound.World]:
    yield from bk.world(store, tmp_path)


def _with_also(world: bound.World, also: Any) -> dict[str, Any]:
    raw = copy.deepcopy(kit.mandate(world).body())
    rule = raw["data"]["agreements"][0]
    if also is None:
        rule.pop("also", None)
    else:
        rule["also"] = also
    return raw


def _agreement(world: bound.World, mandate: Mandate) -> CrossEffectAgreement:
    compiled = compile([mandate], deployment=world.binder.deployment)
    (found,) = [c for c in compiled.engine_checkers() if isinstance(c, CrossEffectAgreement)]
    return found


def _request(operation: str, amount: str) -> OutboundDelta:
    return OutboundDelta(
        message_id=uuid.uuid4(),
        effect_id=EffectId(operation),
        sink="stripe",
        operation=operation,
        tenant_id="acme",
        payload=_frozen({"amount": amount}),
        payload_hash="h",
        idempotency_key=f"key-{operation}",
    )


def _diff(amount: str, *requests: OutboundDelta) -> EffectDiff:
    row = RowDelta("refunds", "1", None, {"id": 1, "amount": Decimal(amount)})
    return EffectDiff(
        plan_id=PlanId(str(uuid.uuid4())),
        stage_id=uuid.uuid4(),
        substrate_id="sqlite:test",
        computed_at=datetime.now(UTC),
        deltas=(row,),
        outbound=requests,
    )


def test_a_rule_without_also_keeps_its_bytes(world: bound.World) -> None:
    plain = Mandate.from_json(_with_also(world, None), signed=False)
    assert "also" not in plain.body()["data"]["agreements"][0]
    assert plain.data.agreements[0].also == ()
    rule = _agreement(world, plain)
    assert rule.name == "cross_effect_agreement:stripe.refunds.create.amount=refunds.amount"


def test_also_holds_the_quantity_on_either_operation(world: bound.World) -> None:
    both = Mandate.from_json(_with_also(world, ["charges.create"]), signed=False)
    assert both.body()["data"]["agreements"][0]["also"] == ["charges.create"]
    rule = _agreement(world, both)
    assert rule.name == (
        "cross_effect_agreement:stripe.refunds.create|charges.create.amount=refunds.amount"
    )
    assert rule.check(None, _diff("5.00", _request("charges.create", "5.00"))) == ()  # type: ignore[arg-type]
    (found,) = rule.check(None, _diff("5.00", _request("charges.create", "500.00")))  # type: ignore[arg-type]
    assert "asks for 500.00 at amount" in found.message
    # the rule over the one operation refuses the other's request outright
    plain = _agreement(world, Mandate.from_json(_with_also(world, None), signed=False))
    assert plain.check(None, _diff("5.00", _request("charges.create", "5.00")))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("also", "message"),
    [
        ([], "names no operation"),
        (["refunds.create"], "the rule's own"),
        (["charges.create", "charges.create"], "sorted, none twice|twice|once"),
        (["charges.create", "charges.capture"], "sorted|order"),
    ],
)
def test_also_is_a_set_of_other_operations(world: bound.World, also: Any, message: str) -> None:
    with pytest.raises(MalformedError, match=message):
        Mandate.from_json(_with_also(world, also), signed=False)


def test_a_child_keeps_the_rule_as_its_parent_writes_it(world: bound.World) -> None:
    parent = Mandate.from_json(_with_also(world, ["charges.create"]), signed=False)
    kept = Mandate.from_json(world.child_json(parent), signed=False)
    assert not delegation_problems(kept, parent)
    raw = world.child_json(parent)
    raw["data"]["agreements"][0].pop("also")
    dropped = Mandate.from_json(raw, signed=False)
    assert any("data.agreements" in str(p) for p in delegation_problems(dropped, parent))


def test_healthcare_holds_the_dose_on_both_order_operations() -> None:
    from fiduciary.demo.packs import healthcare

    (rule,) = healthcare.PACK.data()["agreements"]
    assert (rule["operation"], rule["also"]) == (
        "medicationrequest.create",
        ["medicationrequest.controlled"],
    )
    assert (rule["field"], rule["table"], rule["column"]) == ("dose_mg", "med_orders", "dose_mg")
    held = {
        op.name for s in healthcare.PACK.sinks if s.name == "fhir" for op in s.operations if op.hold
    }
    assert held == {"medicationrequest.controlled"}

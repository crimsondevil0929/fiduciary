"""A tool binding's effect declares its undo (1.6.0): what a substrate that
cannot roll back runs to put the write back. Written only when set, so every
binding before keeps its bytes and its digest."""

from __future__ import annotations

import pytest

from fiduciary.bridge import build_plan
from fiduciary.documents import ToolBinding
from fiduciary.exceptions import MalformedError
from fiduciary.tools import Tool


def _binding(compensation: str | None) -> ToolBinding:
    return (
        Tool.plan("stop_order", "Discontinue one order.")
        .argument("order_id", {"type": "integer"})
        .update(
            "stop",
            "med_orders",
            'patch med_orders :id \'{"status": "stopped"}\'',
            compensation=compensation,
            id="$.order_id",
        )
        .build()
    )


def test_an_effect_declares_its_undo_and_the_plan_carries_it() -> None:
    binding = _binding("restore med_orders")
    (effect,) = binding.effects
    assert effect.compensation == "restore med_orders"
    assert binding.to_json()["effects"][0]["compensation"] == "restore med_orders"
    again = ToolBinding.from_json(binding.to_json(), signed=False)
    assert again.digest == binding.digest and again.effects[0].compensation == effect.compensation
    plan = build_plan(binding, {"order_id": 7}, scope="s", trajectory="t", tenant=None)
    (built,) = plan.effects
    assert built.compensation is not None
    assert built.compensation.statement == "restore med_orders"
    assert built.compensation.idempotency_key == f"{binding.digest[:16]}:stop"


def test_a_binding_with_no_undo_keeps_its_bytes() -> None:
    plain = _binding(None)
    assert "compensation" not in plain.to_json()["effects"][0]
    assert plain.digest != _binding("restore med_orders").digest
    (built,) = build_plan(plain, {"order_id": 7}, scope="s", trajectory="t", tenant=None).effects
    assert built.compensation is None


def test_an_undo_is_a_statement() -> None:
    raw = _binding("restore med_orders").to_json()
    raw["effects"][0]["compensation"] = ""
    with pytest.raises(MalformedError, match="a statement"):
        ToolBinding.from_json(raw, signed=False)
    raw["effects"][0]["compensation"] = 7
    with pytest.raises(MalformedError):
        ToolBinding.from_json(raw, signed=False)

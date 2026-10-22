"""Tool bindings written in code (``fiduciary.tools``): the same documents,
and so the same digests, as their JSON; refused where they are written when
they would not load; and pinned by ``fiduciary tool digest module:name``."""

from __future__ import annotations

import pytest

from fiduciary import Tool
from fiduciary.cli import main
from fiduciary.documents import ToolBinding
from fiduciary.exceptions import MalformedError
from tests import examples as e

LOOKUP = (
    Tool.passthrough("lookup_order", "Read one order.", price="0.001")
    .argument("order_id", {"type": "integer", "minimum": 1})
    .build()
)

REFUND = (
    Tool.plan("refund_order", "Refund part or all of one order.")
    .argument("order_id", {"type": "integer", "minimum": 1})
    .argument("amount", {"type": "string", "pattern": "^[0-9]{1,6}\\.[0-9]{2}$"})
    .argument("payment_intent", {"type": "string", "pattern": "^pi_[A-Za-z0-9]{8,64}$"})
    .update(
        "debit",
        "orders",
        "UPDATE orders SET total = total - :amount WHERE id = :order_id AND tenant = :tenant",
        amount="$.amount",
        order_id="$.order_id",
        tenant="$session.tenant",
        stated_rows=1,
    )
    .insert(
        "record",
        "refunds",
        "INSERT INTO refunds (order_id, tenant, amount) VALUES (:order_id, :tenant, :amount)",
        order_id="$.order_id",
        tenant="$session.tenant",
        amount="$.amount",
        stated_rows=1,
    )
    .request(
        "stripe-refund",
        "stripe",
        "refunds.create",
        {"payment_intent": "$.payment_intent", "amount": "$.amount", "currency": "usd"},
        after=["record"],
    )
    .build()
)


def test_a_binding_in_code_is_the_binding_in_json() -> None:
    for built, raw in ((LOOKUP, e.lookup_json()), (REFUND, e.tool_json())):
        assert built.digest == ToolBinding.from_json(raw, signed=False).digest
    signed = REFUND.signed(e.DEVELOPER)
    assert signed.body() == e.tool().body()


def test_a_binding_that_would_not_load_is_refused_where_it_is_written() -> None:
    with pytest.raises(MalformedError, match="waits for nothing-before"):
        (
            Tool.plan("broken", "Waits for a step that is not there.")
            .argument("order_id", {"type": "integer"})
            .update(
                "debit",
                "orders",
                "UPDATE orders SET total = 0 WHERE id = :order_id",
                order_id="$.order_id",
                after=["nothing-before"],
            )
            .build()
        )
    with pytest.raises(MalformedError, match="a passthrough tool builds no effect"):
        Tool.passthrough("p", "No effects.", price="0").update(
            "x", "orders", "UPDATE orders SET total = 0"
        ).build()
    with pytest.raises(MalformedError, match="unsupported keyword"):
        Tool.passthrough("q", "A schema it cannot enforce.", price="0").argument(
            "a", {"type": "string", "format": "email"}
        ).build()


def test_tool_digest_names_a_binding_in_code(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["tool", "digest", "tests.test_tools:REFUND"]) == 0
    out, err = capsys.readouterr()
    assert out.strip() == REFUND.digest and '"name": "refund_order"' in err

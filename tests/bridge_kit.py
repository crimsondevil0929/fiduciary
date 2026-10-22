"""What the bridge tests share: a world with the bridge's tools deployed, a
mandate pinning them, and :class:`~fiduciary.bridge.Fiduciary` built from the
world's parts.

The tools:

- ``refund_order``: a debit, a refund row and a Stripe refund. Its
  statements name no tenant: the refund row takes the order's own, so an
  order of another tenant is measured as that tenant's, and
  ``TenantIsolation(declared=True)`` refuses it (F4-14). (The world's
  binding filters by the session's tenant instead, and so would book a
  refund against another tenant's order as a debit of no rows.)
- ``lookup_order``: a passthrough read, priced at a tenth of a cent;
- ``adjust_orders``: two debits that wait for nothing, on two orders. Its
  statements name no tenant, so a row of another tenant is measured as one,
  and ``TenantIsolation(declared=True)`` refuses it; repair keeps the debit
  that is the session's tenant's.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from agentgov.interceptor import ModelPricing
from interlock.records import Keyring, RecordLog

from fiduciary.bridge import Fiduciary
from fiduciary.documents import Mandate, ToolBinding
from fiduciary.store import Store
from tests import bound
from tests import examples as e
from tests.conftest import Clock

ADJUST: Final = "UPDATE orders SET total = total - CAST(:amount AS NUMERIC) WHERE id = :order_id"
RECORD: Final = (
    "INSERT INTO refunds (order_id, tenant, amount) "
    "SELECT id, tenant, :amount FROM orders WHERE id = :order_id"
)

PRICING: Final[Mapping[str, ModelPricing]] = {
    # Anthropic's published rates per million tokens (input, output, cache
    # read, cache write), which AgentGov 1.0.0 does not carry.
    "claude-opus-5-5": ModelPricing(
        "claude-opus-5-5", Decimal("4"), Decimal("20"), Decimal("0.20"), Decimal("5")
    ),
    "claude-sonnet-5-5": ModelPricing(
        "claude-sonnet-5-5", Decimal("2"), Decimal("10"), Decimal("0.10"), Decimal("2.50")
    ),
}


def adjust_json(backend: str = "sqlite") -> dict[str, Any]:
    def debit(effect_id: str, argument: str) -> dict[str, Any]:
        return {
            "id": effect_id,
            "kind": "update",
            "table": "orders",
            "statement": bound.dialect(backend, ADJUST.replace(":order_id", f":{argument}")),
            "parameters": {"amount": "$.amount", argument: f"$.{argument}"},
            "stated_rows": 1,
            "after": [],
        }

    return {
        "v": "FIDU1",
        "type": "tool",
        "name": "adjust_orders",
        "description": "Take the same amount off two orders' totals.",
        "input": {
            "type": "object",
            "additionalProperties": False,
            "required": ["amount", "first", "second"],
            "properties": {
                "first": {"type": "integer", "minimum": 1, "maximum": 1000},
                "second": {"type": "integer", "minimum": 1, "maximum": 1000},
                "amount": {"type": "string", "pattern": "^[0-9]{1,4}\\.[0-9]{2}$"},
            },
        },
        "kind": "plan",
        "tenant": "session",
        "effects": [debit("debit-first", "first"), debit("debit-second", "second")],
        "requests": [],
        "price": "0.00000000",
    }


def refund_json(backend: str = "sqlite") -> dict[str, Any]:
    raw = copy.deepcopy(e.tool_json())
    debit, record = raw["effects"]
    debit["statement"] = bound.dialect(backend, ADJUST)
    debit["parameters"] = {"amount": "$.amount", "order_id": "$.order_id"}
    record["statement"] = bound.dialect(backend, RECORD)
    record["parameters"] = {"amount": "$.amount", "order_id": "$.order_id"}
    return raw


def refund(backend: str = "sqlite") -> ToolBinding:
    return ToolBinding.from_json(refund_json(backend), signed=False).signed(e.DEVELOPER)


def adjust(backend: str = "sqlite") -> ToolBinding:
    return ToolBinding.from_json(adjust_json(backend), signed=False).signed(e.DEVELOPER)


def tools(backend: str) -> tuple[ToolBinding, ...]:
    return (adjust(backend), refund(backend))


def world(store: Store, tmp_path: Path, clock: Clock, **kw: Any) -> Iterator[bound.World]:
    from fiduciary.sqlite import SqliteStore

    backend = "sqlite" if isinstance(store, SqliteStore) else "postgres"
    yield from bound.build(backend, tmp_path, store, clock, tools=tools(backend), **kw)


def mandate_json(w: bound.World, **changes: Any) -> dict[str, Any]:
    """The world's mandate, pinning the three tools, its recovery reserve
    delegated from ``support`` (so a spent-out subject can still recover)."""
    raw = w.mandate_json()
    raw["tools"]["bindings"] = sorted(
        [
            {"name": "adjust_orders", "digest": adjust(w.backend).digest},
            {"name": "lookup_order", "digest": e.lookup().digest},
            {"name": "refund_order", "digest": refund(w.backend).digest},
        ],
        key=lambda pin: str(pin["name"]),
    )
    raw["recovery"] = {**copy.deepcopy(raw["recovery"]), "funding": "support"}
    raw.update(changes)
    return raw


def mandate(w: bound.World, **changes: Any) -> Mandate:
    return Mandate.from_json(mandate_json(w, **changes), signed=False).signed(e.signer("cfo"))


def lookup(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """``lookup_order``'s implementation: what it reads, it says."""
    return {"order_id": arguments["order_id"], "status": "open"}


def runtime_log(w: bound.World) -> RecordLog:
    pub = e.SERVICE.public_key()
    return RecordLog(e.SERVICE, path=w.tmp / "runtime.jsonl", keyring=Keyring({pub.key_id: pub}))


def fiduciary(
    w: bound.World,
    *,
    outbox: Any = None,
    in_process: str = "tool-calls-only",
    handlers: Mapping[str, Any] | None = None,
    records: RecordLog | None = None,
) -> Fiduciary:
    """Fiduciary over the world's parts, its runtime log in the world's
    directory (closed with the world)."""
    runtime = records or getattr(w, "runtime", None)
    if runtime is None:  # one writer per log: every Fiduciary of the world shares it
        runtime = runtime_log(w)
        w.cleanups.append(runtime.close)
        w.runtime = runtime  # type: ignore[attr-defined]
    return Fiduciary(
        w.authority,
        w.binder,
        w.governor,
        runtime_records=runtime,
        outbox=outbox,
        operator_log=w.tmp / "operators.jsonl",
        executor_signer=e.SERVICE,
        receipts=w.receipts,
        handlers={"lookup_order": lookup} if handlers is None else handlers,
        pricing=PRICING,
        in_process=in_process,
    )

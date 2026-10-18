"""A deployment the examples compile against: observed tables, installed
sinks, the pinned tool bindings and the directive texts."""

from __future__ import annotations

import hashlib
from decimal import Decimal

from interlock.outbound import OperationSpec, SinkRegistry, SinkSpec
from interlock.substrate import TableSpec

from fiduciary.compiler import Deployment
from tests import examples as e

TABLES: dict[str, tuple[str, ...]] = {
    "orders": ("id", "tenant", "total", "status"),
    "refunds": ("id", "order_id", "tenant", "amount"),
    "items": ("id", "tenant", "sku", "qty"),
}
"""Every table's columns: ``id`` first, the primary key; ``tenant`` its tenant."""

SINKS = (
    SinkSpec(
        "stripe",
        (OperationSpec("refunds.create"), OperationSpec("charges.create")),
        cost_per_call=Decimal("0.01"),
        idempotency="header",
        max_payload_bytes=65536,
    ),
    SinkSpec("mail", (OperationSpec("send"),), idempotency="header", max_payload_bytes=65536),
)
DIRECTIVES = {hashlib.sha256(b"Stop retrying.").hexdigest(): "Stop retrying."}


def table_specs() -> list[TableSpec]:
    return [
        TableSpec(name, columns=list(columns), tenant_column="tenant")
        for name, columns in TABLES.items()
    ]


def deployment(**changes: object) -> Deployment:
    found = {
        "ledger": e.LEDGER,
        "tables": {name: frozenset(columns) for name, columns in TABLES.items()},
        "sinks": SinkRegistry(SINKS),
        "tools": {e.tool().digest: e.tool(), e.lookup().digest: e.lookup()},
        "directives": DIRECTIVES,
    }
    found.update(changes)
    return Deployment(**found)  # type: ignore[arg-type]

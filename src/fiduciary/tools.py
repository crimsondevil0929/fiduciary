"""Tool bindings, written in code (``docs/FIDUCIARY_SPEC.md`` §4.3).

A binding is a document a principal reviews and a mandate pins by digest
(F4-16). :class:`Tool` writes one without hand-writing its JSON::

    refund = (
        Tool.plan("refund_order", "Refund part or all of one order.", tenant="session")
        .argument("order_id", {"type": "integer", "minimum": 1})
        .argument("amount", {"type": "string", "pattern": "^[0-9]{1,6}\\\\.[0-9]{2}$"})
        .update("debit", "orders", "UPDATE orders SET total = total - :amount "
                "WHERE id = :order_id", amount="$.amount", order_id="$.order_id",
                stated_rows=1)
        .build()
    )

``build()`` returns the :class:`~fiduciary.documents.ToolBinding`, read back
through FIDU1's strict rules, so a binding that would not load is refused
where it is written. Name it in ``[binding] tools`` as ``module:name``, and
``fiduciary tool digest module:name`` prints the digest to pin.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from fiduciary.documents import ToolBinding

__all__ = ["Tool"]


class Tool:
    """A tool binding under construction. Each method returns the builder."""

    def __init__(
        self, name: str, description: str, *, kind: str, tenant: str, price: Decimal | str
    ) -> None:
        self._name = name
        self._description = description
        self._kind = kind
        self._tenant = tenant
        self._price = Decimal(str(price))
        self._properties: dict[str, Any] = {}
        self._required: list[str] = []
        self._effects: list[dict[str, Any]] = []
        self._requests: list[dict[str, Any]] = []

    @classmethod
    def plan(cls, name: str, description: str, *, tenant: str = "session") -> Tool:
        """A tool whose calls are plans, escrowed by interlock: its effects and
        requests are the binding's, the agent supplies arguments only."""
        return cls(name, description, kind="plan", tenant=tenant, price="0")

    @classmethod
    def passthrough(cls, name: str, description: str, *, price: Decimal | str) -> Tool:
        """A tool fiduciary does not escrow: metered at ``price``, and
        counted (F4-15). Its binding declares it read-only (§2.5)."""
        return cls(name, description, kind="passthrough", tenant="none", price=price)

    def argument(self, name: str, schema: Mapping[str, Any], *, required: bool = True) -> Tool:
        """Declare one argument, with its schema (interlock's subset, F4-13)."""
        self._properties[name] = dict(schema)
        if required:
            self._required.append(name)
        return self

    def _effect(
        self,
        kind: str,
        effect_id: str,
        table: str,
        statement: str,
        parameters: Mapping[str, str],
        stated_rows: int | None,
        after: Sequence[str] | None,
        partitions: Mapping[str, str] | None = None,
    ) -> Tool:
        previous = [self._effects[-1]["id"]] if self._effects else []
        self._effects.append(
            {
                "id": effect_id,
                "kind": kind,
                "table": table,
                "statement": statement,
                "parameters": dict(parameters),
                "stated_rows": stated_rows,
                "after": list(previous if after is None else after),
                **({"partitions": dict(partitions)} if partitions else {}),
            }
        )
        return self

    def insert(
        self,
        effect_id: str,
        table: str,
        statement: str,
        *,
        stated_rows: int | None = None,
        after: Sequence[str] | None = None,
        partitions: Mapping[str, str] | None = None,
        **parameters: str,
    ) -> Tool:
        """An ``INSERT``. Each keyword is a placeholder and its source:
        ``$.<argument>``, ``$session.tenant`` or ``$session.trajectory``.
        Effects run in the order written unless ``after`` says otherwise.
        ``partitions`` declares labels beside the tenant, each from an
        argument: ``{"patient": "$.patient_id"}``."""
        return self._effect(
            "insert", effect_id, table, statement, parameters, stated_rows, after, partitions
        )

    def update(
        self,
        effect_id: str,
        table: str,
        statement: str,
        *,
        stated_rows: int | None = None,
        after: Sequence[str] | None = None,
        partitions: Mapping[str, str] | None = None,
        **parameters: str,
    ) -> Tool:
        """An ``UPDATE``; see :meth:`insert`."""
        return self._effect(
            "update", effect_id, table, statement, parameters, stated_rows, after, partitions
        )

    def delete(
        self,
        effect_id: str,
        table: str,
        statement: str,
        *,
        stated_rows: int | None = None,
        after: Sequence[str] | None = None,
        partitions: Mapping[str, str] | None = None,
        **parameters: str,
    ) -> Tool:
        """A ``DELETE``; see :meth:`insert`."""
        return self._effect(
            "delete", effect_id, table, statement, parameters, stated_rows, after, partitions
        )

    def request(
        self,
        request_id: str,
        sink: str,
        operation: str,
        payload: Mapping[str, Any],
        *,
        after: Sequence[str] = (),
        compensation: Mapping[str, Any] | None = None,
        partitions: Mapping[str, str] | None = None,
    ) -> Tool:
        """An outbound request, sent by interlock's relay after the plan
        commits. A string ``$.<argument>`` or ``$session.<...>`` in the
        payload is filled in from the call."""
        self._requests.append(
            {
                "id": request_id,
                "sink": sink,
                "operation": operation,
                "payload": dict(payload),
                "compensation": None if compensation is None else dict(compensation),
                "after": list(after),
                **({"partitions": dict(partitions)} if partitions else {}),
            }
        )
        return self

    def to_json(self) -> dict[str, Any]:
        return {
            "v": "FIDU1",
            "type": "tool",
            "name": self._name,
            "description": self._description,
            "input": {
                "type": "object",
                "additionalProperties": False,
                "required": sorted(self._required),
                "properties": dict(self._properties),
            },
            "kind": self._kind,
            "tenant": self._tenant,
            "effects": list(self._effects),
            "requests": list(self._requests),
            "price": format(self._price.quantize(Decimal("0.00000001")), "f"),
        }

    def build(self) -> ToolBinding:
        """The binding, as FIDU1 reads it.

        :raises MalformedError: On a binding that would not load.
        """
        return ToolBinding.from_json(self.to_json(), signed=False)

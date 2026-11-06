"""The demo vendors' own logs of the requests they receive (1.9.0).

Each request-only industry sends its writes to an outside vendor through the
relay, so the vendor's log is the system of record for those writes:

========================  ====================  =====================================
Industry                  Vendor                Export
========================  ====================  =====================================
Fintech                   Stripe (refunds)      ``stripe-events``: the Events API
Supply chain              an EDI gateway        ``records``: one JSON row per message
Multi-tenant SaaS         SendGrid (email)      ``sendgrid-events``: the Event Webhook
========================  ====================  =====================================

The relay sends an idempotency key with every request, and the settler writes a
delivery receipt that carries the same key and the vendor's id for what it
created. ``agentgov reconcile-actions`` matches each line of the vendor's export
to a delivery receipt on those ids. A request someone made around the relay,
with the same vendor account, matches no receipt and is reported as a phantom.

The exports are written under ``audit/`` beside the deployment. They are not
part of a bundle.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

STRIPE: Final = "stripe-events"
SENDGRID: Final = "sendgrid-events"
RECORDS: Final = "records"
ACTORS: Final = {STRIPE: "api", SENDGRID: "api"}
"""The actor each format gives requests made with the account's API keys."""


def _token(*parts: str, n: int = 22) -> str:
    raw = hashlib.sha256("\n".join(parts).encode()).digest()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")[:n]


class VendorLog:
    """One vendor's log, kept in the vendor's own export format.

    :param fmt: ``stripe-events``, ``sendgrid-events`` or ``records``.
    :param sink: The deployment's sink that sends to this vendor.
    :param actor: The actor the vendor records for the relay's credential
        (``records`` only; Stripe and SendGrid record ``api``).
    """

    def __init__(self, fmt: str, sink: str, *, actor: str = "integration") -> None:
        if fmt not in (STRIPE, SENDGRID, RECORDS):
            raise ValueError(f"no vendor log format {fmt!r}")
        self.fmt = fmt
        self.sink = sink
        self.actor = ACTORS.get(fmt, actor)
        self._rows: list[dict[str, Any]] = []
        self._seen: set[str] = set()

    def delivered(
        self,
        operation: str,
        *,
        key: str,
        message: str,
        ref: str,
        payload: Mapping[str, Any],
    ) -> None:
        """The relay's request, logged once per idempotency key (a vendor that
        honors keys does not act twice, and logs once)."""
        if key in self._seen:
            return
        self._seen.add(key)
        self._log(operation, ref, payload, key=key, message=message)

    def direct(self, operation: str, ref: str, payload: Mapping[str, Any]) -> None:
        """A request made with the vendor account but not through the relay: a
        Dashboard refund, a message sent by hand. It carries none of the relay's
        ids."""
        self._log(operation, ref, payload, key=None, message=None)

    def _log(
        self,
        operation: str,
        ref: str,
        payload: Mapping[str, Any],
        *,
        key: str | None,
        message: str | None,
    ) -> None:
        n = len(self._rows) + 1
        now = datetime.now(UTC)
        if self.fmt == STRIPE:
            self._rows.append(
                {
                    "id": "evt_" + _token("event", ref, n=24),
                    "object": "event",
                    "api_version": "2024-06-20",
                    "created": int(now.timestamp()),
                    "type": "refund.created",
                    "livemode": False,
                    "data": {"object": {"id": ref, "object": "refund", **payload}},
                    "request": {
                        "id": "req_" + _token("request", ref, n=14),
                        "idempotency_key": key,
                    },
                }
            )
        elif self.fmt == SENDGRID:
            ours = (
                {"interlock_idempotency_key": key, "interlock_message_id": message}
                if key is not None
                else {}
            )
            for event in ("processed", "delivered"):
                self._rows.append(
                    {
                        "email": str(payload.get("to") or ""),
                        "timestamp": int(now.timestamp()),
                        "event": event,
                        "sg_event_id": _token("sg-event", ref, event),
                        "sg_message_id": f"{ref}.recvd-7c4b9f-1-0",
                        "smtp-id": f"<{ref}@ismtpd0001p1>",
                        **({"response": "250 2.0.0 OK"} if event == "delivered" else {}),
                        **ours,
                    }
                )
        else:
            self._rows.append(
                {
                    "id": f"msg-{n:06d}",
                    "timestamp": now.isoformat().replace("+00:00", "Z"),
                    "actor": self.actor,
                    "action": operation,
                    "resource": ref,
                    "refs": [r for r in (key, ref) if r is not None],
                }
            )

    def text(self) -> str:
        """The export, as the vendor writes it."""
        if self.fmt == STRIPE:
            page = {
                "object": "list",
                "url": "/v1/events",
                "has_more": False,
                "data": list(reversed(self._rows)),  # Stripe lists the newest first
            }
            return json.dumps(page, indent=2) + "\n"
        if self.fmt == SENDGRID:
            return json.dumps(self._rows, indent=2) + "\n"
        return "".join(json.dumps(row, sort_keys=True) + "\n" for row in self._rows)

    @property
    def export(self) -> str:
        """Where the export is kept, relative to the deployment."""
        if self.fmt == RECORDS:
            return f"audit/{self.sink}-messages.jsonl"
        return f"audit/{self.fmt}.json"


def reconcile(root: Path, log: VendorLog) -> dict[str, Any]:
    """Write ``log``'s export beside the deployment, then reconcile it against
    the receipt log as ``agentgov reconcile-actions`` does. Only receipts of
    requests to ``log``'s sink are expected in it."""
    from agentgov.receipts import document_from_json
    from agentgov.receipts.canonical import loads_strict
    from agentgov.reconciliation import load_actions, reconcile_actions

    export = root / log.export
    export.parent.mkdir(exist_ok=True)
    export.write_text(log.text())
    receipts = [
        document_from_json(loads_strict(line))
        for line in (root / "receipts.jsonl").read_bytes().splitlines()
        if line.strip()
    ]
    report = reconcile_actions(
        receipts,
        load_actions(str(export), fmt=log.fmt),
        actors=(log.actor,),
        substrates=(log.sink,),
    )
    return {
        "export": log.export,
        "format": log.fmt,
        "actor": log.actor,
        "matched": len(report.matched),
        "receipts": len({m.receipt_id for m in report.matched}),
        "phantom": len(report.phantom),
        "phantoms": [f"{r.action} {r.resource}" for r in report.phantom],
        "unobserved": len(report.unobserved),
        "out_of_scope": report.out_of_scope,
        "command": (
            f"agentgov reconcile-actions receipts.jsonl {log.export} --format {log.fmt} "
            f"--actor {log.actor} --substrate {log.sink}"
        ),
    }

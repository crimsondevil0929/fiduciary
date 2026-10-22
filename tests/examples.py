"""Deterministic example documents: the spec's examples, signed with keys
derived from fixed seeds.

Ed25519 signs deterministically, so every document here, and every golden
vector built from them (``scripts/make_vectors.py``), has the same bytes and
digest on every run.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from agentgov.receipts.signing import Ed25519Signer

from fiduciary import fidu1
from fiduciary.documents import (
    Approval,
    Charter,
    Mandate,
    Resolution,
    Ticket,
    ToolBinding,
    Warrant,
)

LEDGER = hashlib.sha256(b"agentgov entry 1").hexdigest()
CHARTER_ID = "6f1c0c8e-3c1f-4f0e-9a51-2b8a7f0d9e11"
MANDATE_ID = "2d0b5f7e-8a43-4c2b-b1a4-0c4e2f9a7d31"
CHILD_ID = "4b7f1e0a-9c2d-4e6f-8a1b-3c5d7e9f0a12"
TICKET_ID = "91e2c4a0-7f12-4a8e-9d01-5b3f8c2a1e44"
WARRANT_ID = "c8b2a190-5d3e-4f12-9a04-1e7c3b5d9f20"


def signer(name: str) -> Ed25519Signer:
    """A fixed key per name: the seed is SHA-256 of ``fiduciary-test/<name>``."""
    return Ed25519Signer(hashlib.sha256(f"fiduciary-test/{name}".encode()).digest())


PRINCIPALS = ("cfo", "head-ops", "security")
SERVICE = signer("service")
DEVELOPER = signer("developer")


def at(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)


def key(name: str) -> str:
    return signer(name).public_key().spec()


def charter_json(**changes: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "v": "FIDU1",
        "type": "charter",
        "charter_id": CHARTER_ID,
        "version": 1,
        "prev": None,
        "deployment": {"ledger": LEDGER, "label": "acme-prod"},
        "principals": [{"name": n, "key": key(n)} for n in PRINCIPALS],
        "quorums": {"charter": 3, "issue": 2, "revoke": 1},
        "break_glass": [],
        "issued_at": "2026-10-18T09:00:00.000000Z",
    }
    doc.update(changes)
    return doc


def charter() -> Charter:
    return Charter.from_json(charter_json(), signed=False).signed(signer("cfo"))


def tool_json(**changes: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "v": "FIDU1",
        "type": "tool",
        "name": "refund_order",
        "description": "Refund part or all of one order.",
        "input": {
            "type": "object",
            "additionalProperties": False,
            "required": ["amount", "order_id", "payment_intent"],
            "properties": {
                "order_id": {"type": "integer", "minimum": 1},
                "amount": {"type": "string", "pattern": "^[0-9]{1,6}\\.[0-9]{2}$"},
                "payment_intent": {"type": "string", "pattern": "^pi_[A-Za-z0-9]{8,64}$"},
            },
        },
        "kind": "plan",
        "tenant": "session",
        "effects": [
            {
                "id": "debit",
                "kind": "update",
                "table": "orders",
                "statement": "UPDATE orders SET total = total - :amount "
                "WHERE id = :order_id AND tenant = :tenant",
                "parameters": {
                    "amount": "$.amount",
                    "order_id": "$.order_id",
                    "tenant": "$session.tenant",
                },
                "stated_rows": 1,
                "after": [],
            },
            {
                "id": "record",
                "kind": "insert",
                "table": "refunds",
                "statement": "INSERT INTO refunds (order_id, tenant, amount) "
                "VALUES (:order_id, :tenant, :amount)",
                "parameters": {
                    "order_id": "$.order_id",
                    "tenant": "$session.tenant",
                    "amount": "$.amount",
                },
                "stated_rows": 1,
                "after": ["debit"],
            },
        ],
        "requests": [
            {
                "id": "stripe-refund",
                "sink": "stripe",
                "operation": "refunds.create",
                "payload": {
                    "payment_intent": "$.payment_intent",
                    "amount": "$.amount",
                    "currency": "usd",
                },
                "compensation": None,
                "after": ["record"],
            }
        ],
        "price": "0.00000000",
    }
    doc.update(changes)
    return doc


def lookup_json() -> dict[str, Any]:
    return {
        "v": "FIDU1",
        "type": "tool",
        "name": "lookup_order",
        "description": "Read one order.",
        "input": {
            "type": "object",
            "additionalProperties": False,
            "required": ["order_id"],
            "properties": {"order_id": {"type": "integer", "minimum": 1}},
        },
        "kind": "passthrough",
        "tenant": "none",
        "effects": [],
        "requests": [],
        "price": "0.00100000",
    }


def tool() -> ToolBinding:
    return ToolBinding.from_json(tool_json(), signed=False).signed(DEVELOPER)


def lookup() -> ToolBinding:
    return ToolBinding.from_json(lookup_json(), signed=False).signed(DEVELOPER)


def mandate_json(**changes: Any) -> dict[str, Any]:
    """The spec's §4.2 example, under :func:`charter`, pinning the two tools."""
    quorums = {
        "amend": 2,
        "revoke": 1,
        "reduce": 1,
        "grant": 2,
        "reset": 2,
        "reset_safety": 3,
        "release": 1,
        "requeue": 1,
        "compensate": 1,
        "compensate_late": 2,
        "accept_repair": 1,
    }
    doc: dict[str, Any] = {
        "v": "FIDU1",
        "type": "mandate",
        "mandate_id": MANDATE_ID,
        "version": 1,
        "prev": None,
        "charter": {"charter_id": CHARTER_ID, "version": 1, "digest": charter().digest},
        "parent": None,
        "issuance": "quorum",
        "subject": {"scope": "support-agent", "funded_from": "support", "label": "Tier-1 refunds"},
        "term": {
            "not_before": "2026-10-18T00:00:00.000000Z",
            "not_after": "2027-01-01T00:00:00.000000Z",
        },
        "roster": {"principals": list(PRINCIPALS), "quorums": quorums},
        "budget": {
            "envelope": "250.00000000",
            "per_call": "0.50000000",
            "models": ["claude-opus-5-5", "claude-sonnet-5-5"],
            "max_output_tokens": 4096,
        },
        "governance": {
            "max_calls": 60,
            "seconds": 60,
            "fleet_velocity": True,
            "cognitive": {
                "max_identical_repeats": 3,
                "similarity_threshold": "0.7",
                "max_similar_streak": 3,
                "result_similarity_threshold": "0.3",
                "max_cycle_period": 4,
                "min_cycle_repeats": 3,
                "min_novelty_ratio": "0.02",
                "exempt_tools": [],
            },
        },
        "data": {
            "tables": {"orders": ["update"], "refunds": ["insert"]},
            "tenants": "*",
            "declared_tenants": True,
            "max_rows": 20,
            "max_tenants": 1,
            "drawdown": [
                {"table": "orders", "column": "total", "per": "tenant", "max_drop": "0.3"}
            ],
            "stated_footprint": {"tolerance": "2", "blocking": False},
            "agreements": [
                {
                    "sink": "stripe",
                    "operation": "refunds.create",
                    "field": "amount",
                    "table": "refunds",
                    "column": "amount",
                    "measure": "inserted",
                    "key": None,
                }
            ],
            "facts": [],
        },
        "outbound": {
            "sinks": {
                "stripe": {
                    "operations": ["refunds.create"],
                    "max_attempts": 10,
                    "not_after_seconds": 900,
                    "max_payload_bytes": 16384,
                }
            },
            "max_per_plan": 5,
            "per_sink": {"stripe": 5},
            "caps": [
                {
                    "sink": "stripe",
                    "operation": "refunds.create",
                    "field": "amount",
                    "maximum": {"usd": "200"},
                    "per": "request",
                    "currency_field": "currency",
                }
            ],
            "recipients": [],
            "tenants": {"max_tenants": 1, "require_tenant": True, "fields": {}},
        },
        "windows": [
            {
                "name": "refund_dollars",
                "measure": {
                    "kind": "request_sum",
                    "sink": "stripe",
                    "operation": "refunds.create",
                    "field": "amount",
                },
                "span_seconds": 86400,
                "limit": "2000",
                "per": "lineage",
            }
        ],
        "tools": {
            "statements": "bound",
            "bindings": [
                {"name": "lookup_order", "digest": lookup().digest},
                {"name": "refund_order", "digest": tool().digest},
            ],
        },
        "recovery": {
            "reserve": "5.00000000",
            "step_estimate": "0.50000000",
            "max_steps": 4,
            "directives": [
                {
                    "id": "stop-retrying",
                    "sha256": hashlib.sha256(b"Stop retrying.").hexdigest(),
                    "answers": ["thrashing"],
                }
            ],
            "revoke_on": ["runaway", "thrashing"],
            "revocable": None,
            "token_ceiling": 2048,
            "guidance": True,
            "channel": "system",
            "funding": None,
        },
        "extension": {
            "max_grant": "50.00000000",
            "max_total": "150.00000000",
            "margin": "0.25",
            "ttl_seconds": 3600,
            "auto": {"max_grant": "5.00000000", "max_total": "15.00000000"},
        },
        "escalation": {
            "max_open": 5,
            "ttl_seconds": 86400,
            "auto": [
                {
                    "id": "trim-refused",
                    "class": "refused",
                    "resolution": "accept_repair",
                    "when": {"exhaustive": True, "max_dropped": 1},
                }
            ],
        },
        "delegation": {"max_depth": 1, "max_children": 4, "envelope": "50.00000000"},
        "issued_at": "2026-10-18T09:30:00.000000Z",
    }
    doc.update(changes)
    return doc


def mandate() -> Mandate:
    return Mandate.from_json(mandate_json(), signed=False).signed(signer("head-ops"))


def child_json(of: Mandate | None = None, **changes: Any) -> dict[str, Any]:
    """A derived child of :func:`mandate` (or ``of``): the parent narrowed, as the
    service signs it."""
    parent = of or mandate()
    doc = mandate_json()
    doc.update(
        {
            "mandate_id": CHILD_ID,
            "parent": parent.ref.to_json(),
            "issuance": "derived",
            "subject": {
                "scope": "support-agent/triage",
                "funded_from": "support-agent",
                "label": "Triage refunds",
            },
            "delegation": {"max_depth": 0, "max_children": 0, "envelope": "10.00000000"},
        }
    )
    doc["budget"] = {**doc["budget"], "envelope": "50.00000000"}
    doc.update(changes)
    return doc


def child() -> Mandate:
    return Mandate.from_json(child_json(), signed=False).signed(SERVICE)


def approval_json(
    principal: str = "head-ops", subject: str = "mandate", digest: str | None = None, **changes: Any
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "v": "FIDU1",
        "type": "approval",
        "approval_id": "e3f9a1b2-0c4d-4e5f-8a6b-7c8d9e0f1a2b",
        "charter": {"charter_id": CHARTER_ID, "version": 1},
        "principal": principal,
        "subject": {"type": subject, "digest": digest or mandate().digest},
        "resolution": None,
        "decision": "approve",
        "issued_at": "2026-10-18T10:05:40.004211Z",
        "not_after": "2026-10-18T10:35:40.004211Z",
    }
    doc.update(changes)
    return doc


def approval(principal: str = "head-ops", **changes: Any) -> Approval:
    return Approval.from_json(approval_json(principal, **changes), signed=False).signed(
        signer(principal)
    )


def observed_json(log: Any) -> dict[str, Any]:
    """A ticket's ``observed``, naming the heads of ``log`` and of the ledger
    it anchors into, as a ticket raised now observes them (the example's
    ledger head when it anchors into none)."""
    seq, head = log.store.head(log.log_id)
    observed: dict[str, Any] = ticket_json()["observed"]
    ledger = log._ledger
    if ledger is not None:
        ledger.refresh()
        observed["ledger"] = {"sequence": len(ledger.ledger), "head": ledger.ledger.head_hash}
    return {**observed, "log": {"seq": seq, "hash": head}}


def ticket_json(**changes: Any) -> dict[str, Any]:
    grant_res: dict[str, Any] = {
        "action": "grant",
        "quote": "ext-91",
        "amount": "19.00000000",
    }
    decline_res: dict[str, Any] = {
        "action": "decline",
        "quote": "ext-91",
    }
    doc: dict[str, Any] = {
        "v": "FIDU1",
        "type": "ticket",
        "ticket_id": TICKET_ID,
        "mandate": mandate().ref.to_json(),
        "class": "extension",
        "raised_at": "2026-10-18T10:02:11.842100Z",
        "not_after": "2026-10-18T11:02:11.842100Z",
        "observed": {
            "ledger": {
                "sequence": 1419,
                "head": hashlib.sha256(b"ledger-1419").hexdigest(),
            },
            "log": {
                "seq": 88,
                "hash": hashlib.sha256(b"log-88").hexdigest(),
            },
        },
        "evidence": {
            "quote": {
                "id": "ext-91",
                "record": {
                    "log": "interlock",
                    "seq": 19,
                    "hash": hashlib.sha256(b"interlock-19").hexdigest(),
                },
                "needed": "15.20000000",
                "requested": "19.00000000",
                "spent": "249.82000000",
                "available": "0.18000000",
                "estimate": "milestones",
                "expires_at": "2026-10-18T11:02:11.842100Z",
            },
            "progress": {
                "committed": 14,
                "rows": 28,
                "receipts": {
                    "log": "support-agent",
                    "tree_size": 14,
                    "root": hashlib.sha256(b"receipt-root-14").hexdigest(),
                },
            },
        },
        "options": [
            {
                "resolution": grant_res,
                "digest": fidu1.digest(grant_res),
                "quorum": 1,
            },
            {
                "resolution": decline_res,
                "digest": fidu1.digest(decline_res),
                "quorum": 1,
            },
        ],
        "agent": {
            "statement": "Finished 3 of 4 batch items; 12 refunds remain in the queue.",
        },
    }
    doc.update(changes)
    return doc


def ticket() -> Ticket:
    return Ticket.from_json(ticket_json(), signed=False).signed(SERVICE)


def warrant_json(**changes: Any) -> dict[str, Any]:
    res_raw: dict[str, Any] = {
        "action": "grant",
        "quote": "ext-91",
        "amount": "19.00000000",
    }
    res_digest = Resolution.read(res_raw).digest
    tk = ticket()
    ap = Approval.from_json(
        approval_json(
            "head-ops",
            subject="ticket",
            digest=tk.digest,
            resolution=res_digest,
            decision="approve",
        ),
        signed=False,
    ).signed(signer("head-ops"))
    doc: dict[str, Any] = {
        "v": "FIDU1",
        "type": "warrant",
        "warrant_id": WARRANT_ID,
        "ticket": {"ticket_id": tk.ticket_id, "digest": tk.digest},
        "resolution": res_raw,
        "resolution_digest": res_digest,
        "basis": {"approvals": [ap.digest]},
        "formed_at": "2026-10-18T10:05:41.310022Z",
        "not_after": "2026-10-18T10:35:40.004211Z",
    }
    doc.update(changes)
    return doc


def warrant() -> Warrant:
    return Warrant.from_json(warrant_json(), signed=False).signed(SERVICE)


def canonical(doc: dict[str, Any]) -> bytes:
    return fidu1.canonical(doc)

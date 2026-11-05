"""The demo vendors' logs (1.9.0): each format reads back with AgentGov's own
parser, a vendor logs one request per idempotency key, and a request made
around the relay carries none of its ids."""

from __future__ import annotations

import json

import pytest
from agentgov.reconciliation import parsers

from fiduciary.demo.vendor_logs import RECORDS, SENDGRID, STRIPE, VendorLog


@pytest.mark.parametrize("fmt", [STRIPE, SENDGRID, RECORDS])
def test_each_format_reads_back_with_agentgovs_parser(fmt: str) -> None:
    log = VendorLog(fmt, "vendor", actor="integration")
    payload = {"to": "a@example.com", "amount": 1200}
    log.delivered("op.send", key="k-1", message="m-1", ref="ref1", payload=payload)
    log.delivered("op.send", key="k-1", message="m-1", ref="ref1", payload=payload)  # a retry
    log.direct("op.send", "ref2", payload)
    records = parsers("actions")[fmt].parse(log.text(), source=log.export)
    assert len(records) == 2, "one per request; a retry with the same key is not logged again"
    ours, theirs = records if "k-1" in records[0].refs else records[::-1]
    assert "k-1" in ours.refs and "ref1" in ours.refs
    assert "k-1" not in theirs.refs and ours.actor == theirs.actor == log.actor
    assert log.export.startswith("audit/")


def test_stripe_lists_the_newest_event_first() -> None:
    log = VendorLog(STRIPE, "stripe")
    log.delivered("refunds.create", key="k", message="m", ref="re_1", payload={})
    log.direct("refunds.create", "re_2", {})
    page = json.loads(log.text())
    assert page["object"] == "list" and [e["data"]["object"]["id"] for e in page["data"]] == [
        "re_2",
        "re_1",
    ]
    assert page["data"][0]["request"]["idempotency_key"] is None
    assert log.export == "audit/stripe-events.json"


def test_an_unknown_format_is_refused() -> None:
    with pytest.raises(ValueError, match="no vendor log format"):
        VendorLog("csv", "x")

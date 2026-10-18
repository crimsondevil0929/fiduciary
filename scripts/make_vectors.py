"""Generate the FIDU1 golden vectors in ``vectors/fidu1/``.

Run from the repository root: ``python scripts/make_vectors.py``. Every key is
derived from a fixed seed and Ed25519 signs deterministically, so the output
is the same on every run; ``tests/test_vectors.py`` regenerates it and
requires the files to be unchanged, which keeps the format frozen.

- ``valid.json``: documents of every type, each with its canonical bytes'
  digest, its signing input (hex) and the public key it verifies under.
- ``records.json``: a log of records, linked, with each one's hash.
- ``invalid.json``: documents (or raw text) that must be refused, each with
  the error a reader must raise: ``malformed:<code>`` at a path, or
  ``signature:<code>`` under a given key.

An implementation in another language that reproduces ``valid.json`` and
``records.json`` and refuses each of ``invalid.json`` with its code reads and
signs FIDU1 as this one does.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests import examples as e  # noqa: E402

from fiduciary import fidu1  # noqa: E402
from fiduciary.documents import Approval, Charter, Document, Mandate, Record  # noqa: E402

OUT = ROOT / "vectors" / "fidu1"


def _valid(name: str, document: Document, key: str) -> dict[str, Any]:
    return {
        "name": name,
        "type": document.TYPE,
        "document": document.to_json(),
        "canonical_sha256": document.digest,
        "signing_input": document.signing_input().hex(),
        "key": key,
    }


def valid() -> list[dict[str, Any]]:
    return [
        _valid("charter", e.charter(), e.key("cfo")),
        _valid("mandate, root", e.mandate(), e.key("head-ops")),
        _valid("mandate, derived child", e.child(), e.SERVICE.public_key().spec()),
        _valid("tool, plan", e.tool(), e.DEVELOPER.public_key().spec()),
        _valid("tool, passthrough", e.lookup(), e.DEVELOPER.public_key().spec()),
        _valid("approval of a mandate", e.approval(), e.key("head-ops")),
        _valid(
            "approval of a ticket's resolution",
            Approval.from_json(
                e.approval_json(
                    "cfo",
                    subject="ticket",
                    digest="1" * 64,
                    resolution="2" * 64,
                    decision="decline",
                ),
                signed=False,
            ).signed(e.signer("cfo")),
            e.key("cfo"),
        ),
    ]


def records() -> dict[str, Any]:
    chain = []
    prev = fidu1.GENESIS
    bodies = [
        (
            "approval.recorded",
            {
                "intake": 1,
                "approval": "3" * 64,
                "principal": "cfo",
                "decision": "approve",
                "subject": {"type": "charter", "digest": e.charter().digest},
                "resolution": None,
            },
        ),
        (
            "approval.refused",
            {"intake": 2, "digest": "4" * 64, "why": "signature: does not verify"},
        ),
        (
            "charter.adopted",
            {
                "charter": e.charter().digest,
                "charter_id": e.CHARTER_ID,
                "version": 1,
                "approvals": ["3" * 64],
            },
        ),
    ]
    for seq, (kind, body) in enumerate(bodies, start=1):
        record = Record.make(
            e.SERVICE,
            log="fiduciary",
            seq=seq,
            kind=kind,
            body=body,
            prev=prev,
            issued_at=e.at(f"2026-10-18T10:0{seq}:00"),
        )
        chain.append(
            {
                "record": record.to_json(),
                "hash": record.hash,
                "signing_input": record.signing_input().hex(),
            }
        )
        prev = record.hash
    return {"key": e.SERVICE.public_key().spec(), "records": chain}


def _bad(
    name: str,
    cls: type[Document],
    raw: Any,
    error: str,
    path: str = "",
    *,
    signed: bool = False,
    key: str | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name, "type": cls.TYPE, "signed": signed, "error": error}
    if isinstance(raw, str):
        entry["text"] = raw
    else:
        entry["document"] = raw
    if path:
        entry["path"] = path
    if key is not None:
        entry["key"] = key
    return entry


def invalid() -> list[dict[str, Any]]:
    charter = e.charter_json()
    mandate = e.mandate_json()
    signed = e.charter().to_json()
    altered = copy.deepcopy(signed)
    altered["deployment"]["label"] = "acme-test"
    hmac = copy.deepcopy(signed)
    hmac["sig"]["alg"] = "hmac-sha256"
    return [
        _bad("a float", Charter, {**charter, "version": 1.0}, "malformed:json", "$"),
        _bad("a duplicate key", Charter, '{"v":"FIDU1","v":"FIDU1"}', "malformed:json", "$"),
        _bad("another version", Charter, {**charter, "v": "FIDU2"}, "malformed:version", "$.v"),
        _bad(
            "another type", Charter, {**charter, "type": "mandate"}, "malformed:version", "$.type"
        ),
        _bad(
            "a field missing",
            Charter,
            {k: v for k, v in charter.items() if k != "quorums"},
            "malformed:fields",
            "$",
        ),
        _bad("an unknown field", Charter, {**charter, "comment": "x"}, "malformed:fields", "$"),
        _bad(
            "a field of the wrong type",
            Charter,
            {**charter, "version": "1"},
            "malformed:type",
            "$.version",
        ),
        _bad(
            "a hash in upper case",
            Charter,
            {**charter, "deployment": {"ledger": "A" * 64, "label": "x"}},
            "malformed:format",
            "$.deployment.ledger",
        ),
        _bad(
            "an instant without microseconds",
            Charter,
            {**charter, "issued_at": "2026-10-18T09:00:00Z"},
            "malformed:format",
            "$.issued_at",
        ),
        _bad(
            "money not at eight places",
            Mandate,
            {**mandate, "budget": {**mandate["budget"], "envelope": "250.00"}},
            "malformed:format",
            "$.budget.envelope",
        ),
        _bad(
            "a fraction spelled two ways",
            Mandate,
            {
                **mandate,
                "governance": {
                    **mandate["governance"],
                    "cognitive": {
                        **mandate["governance"]["cognitive"],
                        "similarity_threshold": "0.70",
                    },
                },
            },
            "malformed:format",
            "$.governance.cognitive.similarity_threshold",
        ),
        _bad(
            "an integer out of range",
            Charter,
            {**charter, "quorums": {"charter": 3, "issue": 2, "revoke": 0}},
            "malformed:range",
            "$.quorums.revoke",
        ),
        _bad(
            "a set out of order",
            Mandate,
            {
                **mandate,
                "budget": {**mandate["budget"], "models": ["claude-sonnet-5-5", "claude-opus-5-5"]},
            },
            "malformed:set",
            "$.budget.models",
        ),
        _bad(
            "a set holding a member twice",
            Charter,
            {**charter, "break_glass": ["a" * 16, "a" * 16]},
            "malformed:set",
            "$.break_glass",
        ),
        _bad(
            "quorums that disagree",
            Charter,
            {**charter, "quorums": {"charter": 1, "issue": 2, "revoke": 1}},
            "malformed:agreement",
            "$.quorums",
        ),
        _bad(
            "one key under two names",
            Charter,
            {
                **charter,
                "principals": [
                    {"name": "a", "key": e.key("cfo")},
                    {"name": "b", "key": e.key("cfo")},
                ],
            },
            "malformed:agreement",
            "$.principals",
        ),
        _bad(
            "a root that is derived",
            Mandate,
            {**mandate, "issuance": "derived"},
            "malformed:agreement",
            "$.issuance",
        ),
        _bad(
            "stopping harder than starting",
            Mandate,
            {
                **mandate,
                "roster": {
                    **mandate["roster"],
                    "quorums": {**mandate["roster"]["quorums"], "revoke": 3},
                },
            },
            "malformed:agreement",
            "$.roster.quorums",
        ),
        _bad(
            "a per-scope window",
            Mandate,
            {**mandate, "windows": [{**mandate["windows"][0], "per": "scope"}]},
            "malformed:format",
            "$.windows[0].per",
        ),
        _bad(
            "a tool that admits unknown arguments",
            type(e.tool()),
            {**e.tool_json(), "input": {**e.tool_json()["input"], "additionalProperties": True}},
            "malformed:agreement",
            "$.input",
        ),
        _bad(
            "a positional placeholder",
            type(e.tool()),
            _tool_with_question_mark(),
            "malformed:agreement",
            "$.effects[0].statement",
        ),
        _bad("a mandate past 65,536 bytes", Mandate, _oversized(mandate), "malformed:size", "$"),
        _bad(
            "an approval of a ticket naming no resolution",
            Approval,
            e.approval_json(subject="ticket", digest="1" * 64),
            "malformed:agreement",
            "$.resolution",
        ),
        _bad("an HMAC signature", Charter, hmac, "malformed:alg", "$.sig.alg", signed=True),
        _bad(
            "a document altered after signing",
            Charter,
            altered,
            "signature:signature",
            signed=True,
            key=e.key("cfo"),
        ),
        _bad(
            "a document checked under another key",
            Charter,
            signed,
            "signature:key",
            signed=True,
            key=e.key("head-ops"),
        ),
    ]


def _oversized(mandate: dict[str, Any]) -> dict[str, Any]:
    tenants = [f"tenant-{i:04d}-" + "x" * 400 for i in range(200)]
    return {**mandate, "data": {**mandate["data"], "tenants": tenants}}


def _tool_with_question_mark() -> dict[str, Any]:
    raw = e.tool_json()
    raw["effects"][0]["statement"] = (
        "UPDATE orders SET total = total - ? WHERE id = :order_id AND tenant = :tenant"
    )
    raw["effects"][0]["parameters"] = {"order_id": "$.order_id", "tenant": "$session.tenant"}
    return raw


def build() -> dict[str, str]:
    """Each vector file's name and text."""

    def dump(value: Any) -> str:
        return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    return {
        "valid.json": dump(valid()),
        "records.json": dump(records()),
        "invalid.json": dump(invalid()),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in build().items():
        (OUT / name).write_text(text, encoding="utf-8")
        print(f"wrote vectors/fidu1/{name}")


if __name__ == "__main__":
    main()

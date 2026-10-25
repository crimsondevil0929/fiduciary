"""An attestation: what ``verify-bundle`` proved about a bundle, for people
and for machines (``fiduciary verify-bundle --json``, ``--report``).

:func:`attest` takes a bundle's bytes and the :class:`~fiduciary.bundle.BundleReport`
:func:`~fiduciary.bundle.verify_bundle` made of them, and returns one JSON
object: the verdict and each check's findings, exactly as the verifier
reported them; what the bundle holds, read from its members (the lineage's
mandates and their tree, its tickets, approvals and warrants, its operator
acts, its ledger footprint); and each check's evidence mapped to the controls
an assessor asks about (the OWASP Top 10 for Agentic Applications 2026, SOC 2's
common criteria, the EU AI Act's Articles 12 and 14).

It decides nothing. The verdict, the exit code and every finding are the
verifier's; the summary only reads the members a check already held to their
digests, and a member V1 could not parse is left out of it. The mapping is an
aid to an assessment, not a certification.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Final

from fiduciary import __version__, _upstream, fidu1
from fiduciary.bundle.format import CHECKS, EXIT, BundleReport, unpack
from fiduciary.exceptions import FiduciaryError

__all__ = ["CONTROLS", "FRAMEWORKS", "PROVES", "SCHEMA", "attest"]

SCHEMA: Final = "fiduciary-attestation/1"

PROVES: Final[Mapping[str, str]] = {
    "V1": "The bundle is well formed: every member is listed in the manifest with a matching "
    "SHA-256, and there are no extra members.",
    "V2": "The charter matches the digest the auditor pinned, and every principal key comes "
    "from that charter.",
    "V3": "Every mandate was issued by its required quorum, and every derived mandate is "
    "equal to or narrower than its parent.",
    "V4": "Every receipt names the checkers, sinks and rate windows its mandate compiles to, "
    "so the policy that ran is the policy that was approved.",
    "V5": "Every adjudicated plan has a signed receipt, included in the receipt log's signed "
    "checkpoint.",
    "V6": "Every ledger entry the lineage touched is included in the ledger's signed "
    "checkpoint, and the entries balance.",
    "V7": "Every act on the lineage's ledger scopes, plans and outbox messages traces to a "
    "mandate or warrant; anything else is reported.",
    "V8": "Every warrant was formed by a quorum of distinct principals before its deadline, "
    "and executed once against the exact ledger and outbox state the principals approved.",
    "V9": "The log is signed by the service key, hash-linked, and has no gaps.",
    "V10": "Every log record in range is anchored in the ledger, in order.",
    "V11": "Nothing committed or was delivered after the lineage was revoked, suspended or "
    "expired.",
    "V12": "Every timestamp the service wrote agrees with the ledger order of its anchor.",
}

FRAMEWORKS: Final[Mapping[str, Mapping[str, str]]] = {
    "owasp-asi-2026": {
        "ASI01": "Agent Goal Hijack",
        "ASI02": "Tool Misuse and Exploitation",
        "ASI03": "Identity and Privilege Abuse",
        "ASI04": "Agentic Supply Chain Vulnerabilities",
        "ASI05": "Unexpected Code Execution",
        "ASI06": "Memory and Context Poisoning",
        "ASI07": "Insecure Inter-Agent Communication",
        "ASI08": "Cascading Failures",
        "ASI09": "Human-Agent Trust Exploitation",
        "ASI10": "Rogue Agents",
    },
    "soc2": {
        "CC6.1": "Logical access: restricted to authorized users and processes",
        "CC6.2": "Access granted and removed on authorization",
        "CC6.3": "Access by role, least privilege",
        "CC7.2": "System components monitored for anomalies",
        "CC7.3": "Events evaluated to detect incidents",
        "CC8.1": "Changes authorized, tested and approved before they run",
    },
    "eu-ai-act": {
        "Art. 12": "Record-keeping: automatic logging over the system's lifetime",
        "Art. 14": "Human oversight: people able to oversee, intervene and stop",
    },
}
"""The frameworks the matrix maps to, each control by its own identifier."""

CONTROLS: Final[Mapping[str, Mapping[str, Sequence[str]]]] = {
    "V1": {"owasp-asi-2026": ["ASI04"], "soc2": ["CC7.2"], "eu-ai-act": ["Art. 12"]},
    "V2": {"owasp-asi-2026": ["ASI03"], "soc2": ["CC6.1", "CC6.2"], "eu-ai-act": ["Art. 14"]},
    "V3": {
        "owasp-asi-2026": ["ASI03", "ASI07", "ASI10"],
        "soc2": ["CC6.3", "CC8.1"],
        "eu-ai-act": ["Art. 14"],
    },
    "V4": {
        "owasp-asi-2026": ["ASI02", "ASI08"],
        "soc2": ["CC8.1"],
        "eu-ai-act": ["Art. 12"],
    },
    "V5": {
        "owasp-asi-2026": ["ASI01", "ASI02", "ASI08"],
        "soc2": ["CC7.2"],
        "eu-ai-act": ["Art. 12"],
    },
    "V6": {"owasp-asi-2026": ["ASI02", "ASI10"], "soc2": ["CC7.2"], "eu-ai-act": ["Art. 12"]},
    "V7": {
        "owasp-asi-2026": ["ASI03", "ASI10"],
        "soc2": ["CC6.1", "CC7.3"],
        "eu-ai-act": ["Art. 12", "Art. 14"],
    },
    "V8": {
        "owasp-asi-2026": ["ASI09"],
        "soc2": ["CC6.2", "CC8.1"],
        "eu-ai-act": ["Art. 14"],
    },
    "V9": {"owasp-asi-2026": ["ASI06"], "soc2": ["CC7.2"], "eu-ai-act": ["Art. 12"]},
    "V10": {"soc2": ["CC7.2"], "eu-ai-act": ["Art. 12"]},
    "V11": {"owasp-asi-2026": ["ASI10"], "soc2": ["CC6.2"], "eu-ai-act": ["Art. 14"]},
    "V12": {"soc2": ["CC7.2"], "eu-ai-act": ["Art. 12"]},
}
"""Which controls each check's evidence bears on. Not every ASI category is
here: ASI05 (code an agent runs) is outside what a bundle records, and is
answered by running an agent that writes code out of process (F8-10)."""

PRIVACY: Final = (
    "No row data, payloads, tool arguments, prompts, model output or secrets. Logs record "
    "SHA-256 digests rather than raw payloads; the bundle holds signed policy documents, "
    "digests, signatures and amounts."
)


def _version(dist: str) -> str:
    try:
        return version(dist)
    except PackageNotFoundError:  # pragma: no cover - installed with fiduciary
        return "unknown"


def _json(data: bytes) -> Any:
    try:
        return json.loads(data)
    except ValueError:
        return None


def _lines(data: bytes) -> list[Any]:
    found = []
    for line in data.splitlines():
        parsed = _json(line)
        if parsed is not None:
            found.append(parsed)
    return found


def _amount(text: object) -> Decimal:
    try:
        return Decimal(str(text))
    except (InvalidOperation, ValueError):
        return Decimal(0)


def _verdict(report: BundleReport) -> dict[str, Any]:
    first = next((check for check, _ in CHECKS if report.failed(check)), None)
    labels = sorted({f.label for f in report.findings if f.label})
    if report.passed:
        status = "VERIFIED"
    elif "break-glass" in labels:
        status = "BREAK-GLASS DETECTED"
    elif first == "V7":
        status = "AMBIENT AUTHORITY DETECTED"
    else:
        status = "FAILED"
    return {
        "passed": report.passed,
        "exit_code": report.exit_code,
        "status": status,
        "first_failing": first,
        "labels": labels,
        "findings": len(report.findings),
    }


def _checks(report: BundleReport) -> list[dict[str, Any]]:
    out = []
    for check, name in CHECKS:
        found = report.failed(check)
        out.append(
            {
                "id": check,
                "name": name,
                "status": "fail" if found else "pass",
                "proves": PROVES[check],
                "controls": {k: list(v) for k, v in CONTROLS[check].items()},
                "findings": [
                    {
                        "kind": f.kind,
                        "exit_code": EXIT[f.kind],
                        "label": f.label,
                        "message": f.message,
                    }
                    for f in found
                ],
            }
        )
    return out


def _mandates(docs: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    for digest, doc in docs.items():
        if doc.get("type") != "mandate":
            continue
        budget, outbound = doc.get("budget", {}), doc.get("outbound", {})
        data, delegation = doc.get("data", {}), doc.get("delegation") or {}
        out.append(
            {
                "digest": digest,
                "mandate_id": doc.get("mandate_id"),
                "version": doc.get("version"),
                "issuance": doc.get("issuance"),
                "scope": doc.get("subject", {}).get("scope"),
                "funded_from": doc.get("subject", {}).get("funded_from"),
                "label": doc.get("subject", {}).get("label"),
                "parent": (doc.get("parent") or {}).get("mandate_id"),
                "term": doc.get("term"),
                "envelope": budget.get("envelope"),
                "per_call": budget.get("per_call"),
                "models": budget.get("models", []),
                "max_rows": data.get("max_rows"),
                "max_tenants": data.get("max_tenants"),
                "tables": data.get("tables", {}),
                "caps": [
                    {
                        "sink": c.get("sink"),
                        "operation": c.get("operation"),
                        "maximum": c.get("maximum"),
                        "per": c.get("per"),
                    }
                    for c in outbound.get("caps", [])
                ],
                "windows": [
                    {
                        "name": w.get("name"),
                        "limit": w.get("limit"),
                        "span_seconds": w.get("span_seconds"),
                        "per": w.get("per"),
                    }
                    for w in doc.get("windows", [])
                ],
                "delegation": delegation or None,
                "tools": [b.get("name") for b in doc.get("tools", {}).get("bindings", [])],
                "quorums": doc.get("roster", {}).get("quorums", {}),
            }
        )
    return sorted(out, key=lambda m: (str(m["scope"]), int(m["version"] or 0)))


def _ledger(entries: list[Any], subjects: Sequence[str]) -> dict[str, Any]:
    scopes: dict[str, dict[str, Any]] = {}
    closed = {e.get("entry", {}).get("ref") for e in entries} - {"", None}
    types: Counter[str] = Counter()
    for line in entries:
        entry = line.get("entry", {})
        kind, scope = str(entry.get("entry_type")), str(entry.get("scope_id"))
        types[kind] += 1
        row = scopes.setdefault(
            scope,
            {"scope": scope, "funded": Decimal(0), "spent": Decimal(0), "held": Decimal(0)},
        )
        amount = _amount(entry.get("amount"))
        if kind in ("allocation", "funding") and entry.get("direction") == "CR":
            row["funded"] += amount
        elif kind == "spend":
            row["spent"] += amount
        elif kind == "hold" and entry.get("entry_id") not in closed:
            row["held"] += amount
        row["balance"] = entry.get("balance_after")
        row["last"] = entry.get("sequence")
    rows = []
    for row in scopes.values():
        if not any(row["scope"] == s or row["scope"].startswith(f"{s}/") for s in subjects):
            continue
        rows.append({k: f"{v:.8f}" if isinstance(v, Decimal) else v for k, v in row.items()})
    return {
        "entries": len(entries),
        "by_type": dict(sorted(types.items())),
        "scopes": sorted(rows, key=lambda r: str(r["scope"])),
    }


def _tickets(docs: Mapping[str, Any], log: list[Any]) -> list[dict[str, Any]]:
    events: dict[str, list[str]] = {}
    for record in log:
        body = record.get("body", {})
        ticket = body.get("ticket")
        if record.get("kind", "").startswith(("ticket.", "warrant.", "execution.")) and ticket:
            events.setdefault(str(ticket), []).append(str(record.get("kind")))
    out = []
    for digest, doc in docs.items():
        if doc.get("type") != "ticket":
            continue
        out.append(
            {
                "digest": digest,
                "ticket_id": doc.get("ticket_id"),
                "class": doc.get("class"),
                "raised_at": doc.get("raised_at"),
                "not_after": doc.get("not_after"),
                "observed": doc.get("observed"),
                "options": [
                    {
                        "action": o.get("resolution", {}).get("action"),
                        "digest": o.get("digest"),
                        "quorum": o.get("quorum"),
                    }
                    for o in doc.get("options", [])
                ],
                "halt": (doc.get("evidence") or {}).get("halt"),
                "messages": len((doc.get("evidence") or {}).get("messages", [])),
                "events": events.get(str(doc.get("ticket_id")), []) + events.get(digest, []),
            }
        )
    return sorted(out, key=lambda t: str(t["raised_at"]))


def _approvals(docs: Mapping[str, Any], keys: Mapping[str, Any]) -> list[dict[str, Any]]:
    principals = keys.get("principals", {}) if isinstance(keys, Mapping) else {}
    out = []
    for digest, doc in docs.items():
        if doc.get("type") != "approval":
            continue
        out.append(
            {
                "digest": digest,
                "principal": doc.get("principal"),
                "key": principals.get(doc.get("principal")),
                "subject": doc.get("subject", {}).get("type"),
                "subject_digest": doc.get("subject", {}).get("digest"),
                "decision": doc.get("decision"),
                "resolution": doc.get("resolution"),
                "issued_at": doc.get("issued_at"),
                "signed_by": (doc.get("sig") or {}).get("key_id"),
            }
        )
    return sorted(out, key=lambda a: str(a["issued_at"]))


def _warrants(docs: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    for digest, doc in docs.items():
        if doc.get("type") != "warrant":
            continue
        resolution = doc.get("resolution", {})
        out.append(
            {
                "digest": digest,
                "warrant_id": doc.get("warrant_id"),
                "ticket": doc.get("ticket"),
                "action": resolution.get("action"),
                "resolution_digest": doc.get("resolution_digest"),
                "messages": [m.get("id") for m in resolution.get("messages", [])],
                "heads": [m.get("head") for m in resolution.get("messages", [])],
                "reset": resolution.get("reset"),
                "basis": doc.get("basis", {}).get("approvals", []),
                "formed_at": doc.get("formed_at"),
                "not_after": doc.get("not_after"),
            }
        )
    return sorted(out, key=lambda w: str(w["formed_at"]))


def _operators(records: list[Any], keys: Mapping[str, Any], report: BundleReport) -> list[Any]:
    named = {}
    for name, key in (keys.get("operators", {}) if isinstance(keys, Mapping) else {}).items():
        try:
            named[name] = str(fidu1.key_of(str(key)).key_id)
        except FiduciaryError:
            continue
    flagged = {
        f.message.split(" ")[2]: f.label
        for f in report.failed("V7")
        if f.message.startswith("operator record ")
    }
    out = []
    for record in records:
        body = record.get("body", {})
        seq = str(record.get("seq"))
        key_id = (record.get("sig") or {}).get("key_id")
        out.append(
            {
                "seq": record.get("seq"),
                "kind": record.get("kind"),
                "action": body.get("action"),
                "key_id": key_id,
                "operator": next(
                    (n for n, k in named.items() if k == key_id), body.get("operator")
                ),
                "reason": body.get("reason"),
                "ambient": seq in flagged and record.get("kind") == "operator.intent",
                "label": flagged.get(seq, "") if record.get("kind") == "operator.intent" else "",
                "issued_at": record.get("issued_at"),
            }
        )
    return out


def attest(
    data: bytes,
    report: BundleReport,
    *,
    charter: str,
    pinned: Sequence[str] = (),
    witness: bool = False,
    skew_seconds: int = 5,
) -> dict[str, Any]:
    """The attestation of ``data``, which :func:`verify_bundle` reported as
    ``report`` (module docstring).

    :param charter: The charter digest the auditor pinned.
    :param pinned: The key roles the auditor pinned from outside the bundle.
    """
    members = unpack(data)
    by_path = dict(members)
    manifest = _json(by_path.get("manifest.json", b"{}")) or {}
    types = Counter(str(m.get("type")) for m in manifest.get("members", []))
    docs = {}
    for path, body in members:
        if path.endswith(".json") and "/" in path and not path.startswith(("ledger/", "receipts/")):
            parsed = _json(body)
            if isinstance(parsed, dict) and "type" in parsed:
                docs[path.rsplit("/", 1)[-1].removesuffix(".json")] = parsed
    keys = _json(by_path.get("keys.json", b"{}")) or {}
    log = _lines(by_path.get("log.jsonl", b""))
    entries = _lines(by_path.get("ledger/entries.jsonl", b""))
    operators = _lines(by_path.get("ilok1/operators.jsonl", b""))
    charters = [d for d in docs.values() if d.get("type") == "charter"]
    charter_doc = next(
        (c for c in charters if c.get("version") == 1), charters[0] if charters else {}
    )
    escrow = [p for p in by_path if p.startswith("escrow/")]
    escrow_records: Counter[str] = Counter()
    for path in escrow:
        for line in _lines(by_path[path]):
            escrow_records[str(line.get("record_type"))] += 1
    subjects = list(manifest.get("subjects", []))
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verifier": {
            "fiduciary": __version__,
            "interlock": _version("interlock-escrow"),
            "agentgov": _version("interlock-agentgov"),
            "upstream": {
                "ilok1_digests": _upstream.ILOK1_DIGESTS,
                "quotes_adopted": _upstream.QUOTES_ADOPTED,
                "expected_heads": _upstream.EXPECTED_HEADS,
                "reset_memo": _upstream.RESET_MEMO,
                "recovery_reopens": _upstream.RECOVERY_REOPENS,
            },
            "offline": True,
        },
        "inputs": {
            "charter": charter,
            "pinned_keys": sorted(pinned),
            "witness": witness,
            "skew_seconds": skew_seconds,
        },
        "bundle": {
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "format": manifest.get("format"),
            "exporter": manifest.get("exporter"),
            "lineage": manifest.get("lineage"),
            "lineages": manifest.get("lineages", []),
            "subjects": subjects,
            "range": manifest.get("range"),
            "ledger": manifest.get("ledger"),
            "signed": "sig" in manifest,
            "members": len(members),
            "member_types": dict(sorted(types.items())),
        },
        "verdict": _verdict(report),
        "checks": _checks(report),
        "notes": list(report.notes),
        "authority": {
            "charter": {
                "digest": charter,
                "label": (charter_doc.get("deployment") or {}).get("label"),
                "principals": [
                    {"name": p.get("name"), "key": p.get("key")}
                    for p in charter_doc.get("principals", [])
                ],
                "quorums": charter_doc.get("quorums", {}),
                "break_glass": charter_doc.get("break_glass", []),
                "versions": len(charters),
            },
            "mandates": _mandates(docs),
        },
        "tickets": _tickets(docs, log),
        "approvals": _approvals(docs, keys),
        "warrants": _warrants(docs),
        "operators": _operators(operators, keys, report),
        "log": {
            "records": len(log),
            "by_kind": dict(sorted(Counter(str(r.get("kind")) for r in log).items())),
        },
        "ledger": _ledger(entries, subjects),
        "receipts": sum(
            1 for p in by_path if p.startswith("receipts/") and p != "receipts/transitions.json"
        ),
        "escrow": {"chains": len(escrow), "records": dict(sorted(escrow_records.items()))},
        "privacy": {
            "claim": PRIVACY,
            "member_types": dict(sorted(types.items())),
        },
        "frameworks": {k: dict(v) for k, v in FRAMEWORKS.items()},
    }

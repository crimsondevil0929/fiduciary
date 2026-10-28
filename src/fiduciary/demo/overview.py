"""The console's overview: the same engine in six industries, the measurement
ladder, and the live acceptance matrix interlock recorded.

:func:`overview` reads the payloads of the domains a run played and says,
from what each one measured, which part of the engine refused a plan, formed
a warrant, named a break-glass act or verified offline. A domain that did not
run says so, and the one that is not built yet (coding agents, on Git) says
that too: a pack on a slide is a promise, and only a pack that ran is evidence.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any, Final

__all__ = ["ACCEPTANCE", "INDUSTRIES", "LADDER", "overview"]

INDUSTRIES: Final = (
    {
        "key": "fintech",
        "industry": "Fintech",
        "title": "Support refunds and order adjustments",
        "frameworks": ["soc2", "owasp-asi-2026", "eu-ai-act"],
        "class": "T",
        "nouns": {
            "measure": "rows of orders and refunds",
            "hidden": "a cascading settlement trigger",
            "unit": "USD",
            "partition": "the tenant",
            "approvers": "CFO, head of ops, security",
            "vendor": "a Stripe webhook",
            "verifier": "auditor, insurer",
        },
    },
    {"key": "healthcare", "class": "T"},
    {"key": "sre", "class": "T", "note": "measures a mirror of the cluster, not the cluster"},
    {"key": "supply-chain", "class": "T"},
    {"key": "saas", "class": "T"},
    {
        "key": "coding",
        "industry": "Coding agents",
        "title": "Pull requests merged at the reviewed head",
        "frameworks": ["soc2", "iso27001", "nist800_53"],
        "class": "T",
        "planned": "Next to build: a Git substrate, where a ref update is a compare-and-swap. "
        "It is not built yet, so no acts run here.",
        "nouns": {
            "measure": "a diff of files",
            "hidden": "an edit to a workflow file",
            "unit": "CI minutes, USD",
            "partition": "repository, path class",
            "approvers": "maintainers",
            "vendor": "a CI check run",
            "verifier": "the security team",
        },
    },
)
"""Every industry the overview shows, in order. The four packs' own entries
come from their profiles; these fill in fintech and the one not built."""

LADDER: Final = (
    {
        "class": "T",
        "name": "Transactional",
        "promise": "What was measured is exactly what committed, or nothing committed.",
        "where": "SQLite and PostgreSQL (shipped); a Git ref and an atomic directory swap next",
        "shipped": True,
    },
    {
        "class": "P",
        "name": "Plan-bound",
        "promise": "What was measured is the plan that was applied, and the apply refuses if "
        "the system has moved.",
        "where": "Terraform saved plans, Kubernetes dry-run with version preconditions",
        "shipped": False,
    },
    {
        "class": "V",
        "name": "Predicted and verified",
        "promise": "The before image was read, the after image predicted, the write made "
        "conditional, and the result read back and compared.",
        "where": "Salesforce, ServiceNow, Jira, Workday, FHIR servers",
        "shipped": False,
    },
    {
        "class": "R",
        "name": "Request-only",
        "promise": "The request was measured, judged and delivered exactly once; the effect "
        "itself is the vendor's signed word.",
        "where": "every outbound request in every pack: Stripe, FHIR, EDI, the cluster API, email",
        "shipped": True,
    },
)

ACCEPTANCE: Final = {
    "cases": 42,
    "passed": 42,
    "metered_usd": "1.67",
    "model_calls": 139,
    "models": ["claude-opus-5-5", "claude-haiku-5-5"],
    "domains": [
        "Financial operations (accounts payable)",
        "System and resource orchestration (cluster operations)",
        "Multi-tenant SaaS administration",
        "Multi-step supply and logistics (warehouse fulfilment)",
    ],
    "plans": {"proposed": 37, "committed": 23, "refused": 14},
    "generated": "2026-10-11",
    "interlock": "1.0.0",
    "source": "interlock, docs/CUSTOMER_ACCEPTANCE_MATRIX.md",
    "note": "A recorded live run: one policy function builds every domain's checkers, and "
    "only table names, column names and limits differ. This dashboard does not re-run it.",
}

ENGINE: Final = (
    ("tenant_isolation", "TenantIsolation(declared=True)", "plans refused"),
    ("blast_radius", "BlastRadius", "plans refused"),
    ("tenant_drawdown_guard", "TenantDrawdownGuard", "plans refused"),
    ("cross_effect_agreement", "CrossEffectAgreement", "plans refused"),
    ("fact_agreement", "FactAgreement", "plans refused"),
    ("table_allowlist", "TableAllowlist", "plans refused"),
    ("no_delete", "NoDelete", "plans refused"),
    ("rate_window", "RateWindow", "plans refused"),
    ("warrant", "Quorum warrant", "formed"),
    ("break-glass", "Break-glass act named offline", "named"),
    ("offline", "Bundle verified offline", "verified"),
)


def _measured(node: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(node, Mapping):
        if isinstance(node.get("measured"), Mapping):
            yield node["measured"]
        for value in node.values():
            yield from _measured(value)
    elif isinstance(node, list):
        for value in node:
            yield from _measured(value)


def _engine(payload: Mapping[str, Any]) -> dict[str, Any]:
    acts = payload["demo"]["acts"]
    refused: dict[str, set[str]] = {key: set() for key, _, _ in ENGINE}
    for m in _measured(acts):
        if m.get("committed"):
            continue
        for f in m.get("findings", []):
            if f.get("severity") != "blocking":
                continue
            name = str(f["check"]).split(":")[0]
            if name in refused:
                refused[name].add(str(m.get("plan")))
    cells: dict[str, Any] = {k: len(v) for k, v in refused.items()}
    plans = {
        str(m.get("plan")): int(m.get("rows", 0)) for m in _measured(acts) if not m.get("committed")
    }
    for act in acts:
        # The fintech fan-out keeps a sample of its sessions' calls, and every
        # session's outcome: the sample's refusals are already counted above.
        outcomes = act.get("outcomes")
        if outcomes:
            sampled = sum(1 for m in _measured(act.get("sample", [])) if not m.get("committed"))
            extra = sum(1 for o in outcomes if o.get("outcome") == "refused") - sampled
            cells["rate_window"] += extra
            for n in range(extra):
                plans[f"{act['key']}-unsampled-{n}"] = 0
    cells["refused_plans"] = len(plans)
    cells["rows_blocked"] = sum(plans.values())
    warrants = [a["warrant"] for a in acts if isinstance(a.get("warrant"), Mapping)]
    cells["warrant"] = [
        {"action": w.get("action", "release"), "approvals": len(w.get("basis", []))}
        for w in warrants
    ]
    after = payload["attestations"].get("after", {})
    cells["break-glass"] = "break-glass" in after.get("verdict", {}).get("labels", [])
    audit = next((a for a in acts if a["key"] == "audit"), None)
    if audit is not None:
        before = audit["verdicts"]["before"]
        found = sum(sum(v["scan"]["found"].values()) for v in audit["verdicts"].values())
        cells["offline"] = bool(before["passed"]) and before["sockets"] == 0 and found == 0
    else:
        cells["offline"] = False
    return cells


def overview(played: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """The overview for the domains in ``played`` (key: console payload)."""
    industries = []
    for entry in INDUSTRIES:
        key = str(entry["key"])
        payload = played.get(key)
        profile = (payload or {}).get("demo", {}).get("domain") or {}
        row: dict[str, Any] = {
            "key": key,
            "industry": profile.get("industry", entry.get("industry", key)),
            "title": profile.get("title", entry.get("title", "")),
            "frameworks": profile.get("frameworks", entry.get("frameworks", [])),
            "nouns": profile.get("nouns", entry.get("nouns", {})),
            "class": entry["class"],
            "note": entry.get("note"),
            "planned": entry.get("planned"),
            "ran": payload is not None,
        }
        if payload is not None:
            after = payload["attestations"]["after"]["verdict"]
            before = payload["attestations"]["before"]["verdict"]
            row["acts"] = len(payload["demo"]["acts"])
            row["seconds"] = payload["demo"].get("seconds")
            row["mode"] = payload["demo"].get("mode")
            row["before"] = {"passed": before["passed"], "exit_code": before["exit_code"]}
            row["after"] = {"status": after["status"], "exit_code": after["exit_code"]}
            row["engine"] = _engine(payload)
        industries.append(row)
    return {
        "industries": industries,
        "ladder": [dict(rung) for rung in LADDER],
        "acceptance": dict(ACCEPTANCE),
        "engine": [{"key": k, "label": label, "reads": reads} for k, label, reads in ENGINE],
    }

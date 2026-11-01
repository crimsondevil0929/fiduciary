"""The console's overview: the same engine in six industries, on SQL and on
Git, the measurement ladder, and the live acceptance matrix interlock recorded.

:func:`overview` reads the payloads of the domains a run played and says,
from what each one measured, which part of the engine refused a plan, formed
a warrant, named a break-glass act or verified offline. A domain that did not
run says so: a pack on a slide is a promise, and only a pack that ran is
evidence. The coding-agent pack runs only where the installed interlock has
its Git substrate.
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
    {"key": "sre", "class": "P", "substrate": "Kubernetes"},
    {"key": "supply-chain", "class": "T"},
    {"key": "saas", "class": "T"},
    {
        "key": "coding-agent",
        "industry": "Coding agents",
        "title": "A repository maintainer agent, on a Git branch",
        "frameworks": ["soc2", "iso27001", "nist800_53"],
        "class": "T",
        "substrate": "Git",
        "nouns": {
            "measure": "a diff of files, read from Git",
            "hidden": "a code generator's rewrite; an edit to a workflow file",
            "unit": "lines of code; CI minutes",
            "partition": "the repository, by path class",
            "approvers": "lead maintainer, security reviewer, release captain",
            "vendor": "a signed CI check result",
            "verifier": "the security team, a SOC 2 auditor",
        },
    },
)
"""Every industry the overview shows, in order. The packs' own entries come
from their profiles; these fill in fintech, and what each runs on."""

LADDER: Final = (
    {
        "class": "T",
        "name": "Transactional",
        "promise": "What was measured is exactly what committed, or nothing committed.",
        "where": "SQLite and PostgreSQL; a Git branch, by compare-and-swap of its ref (shipped); "
        "an atomic directory swap next",
        "shipped": True,
    },
    {
        "class": "P",
        "name": "Plan-bound",
        "promise": "What was measured is the plan that was applied, and the apply refuses if "
        "the system has moved.",
        "where": "a Kubernetes API server's dry run, admission webhooks included, applied on the "
        "resourceVersion measured; Terraform and OpenTofu saved plans, on the state's serial "
        "(shipped)",
        "shipped": True,
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
        "where": "every outbound request in every pack: Stripe, FHIR, EDI, the cloud API, email",
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
    ("stale_head", "Commit only at the measured head", "plans refused"),
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
    # A Git stage whose branch moved after it was measured: refused at the
    # compare-and-swap, after every check passed, so no finding names it.
    cells["stale_head"] = sum(
        1 for a in acts if isinstance(a.get("git"), Mapping) and a["git"].get("pushed")
        and not a["git"].get("landed")
    )  # fmt: skip
    cells["refused_plans"] = len(plans) + cells["stale_head"]
    cells["rows_blocked"] = sum(plans.values())
    warrants = [a["warrant"] for a in acts if isinstance(a.get("warrant"), Mapping)]
    cells["warrant"] = [
        {"action": w.get("action", "release"), "approvals": len(w.get("basis", []))}
        for w in warrants
    ]
    after = payload["attestations"].get("after", {}).get("verdict", {})
    # Named offline: labeled break-glass (an operator's declared key), or, for
    # an act with no operator key, an ambient act the after bundle names (V7).
    cells["break-glass"] = "break-glass" in after.get("labels", []) or after.get("exit_code") == 7
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
            "substrate": profile.get("nouns", {}).get(
                "substrate", entry.get("substrate", "SQLite")
            ),
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
            model = payload["demo"].get("model")
            row["model"] = (
                {
                    "served": model["served"],
                    "metered": model.get("metered"),
                    "turns": model["turns"],
                }
                if model
                else None
            )
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

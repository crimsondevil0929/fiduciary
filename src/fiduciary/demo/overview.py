"""The console's overview: the same engine in eight workflows of six families (on SQL, Git, a
directory, Kubernetes, a FHIR server and a REST API), the measurement ladder, and the live
acceptance matrix Interlock recorded.

:func:`overview` reads the payloads of the industries a run played and counts,
from what each one measured, which check refused a plan, which warrants formed,
whether a break-glass action was found and whether the bundles verified offline.
An industry that didn't run is marked as not run, and nothing is counted for it.
The coding-agent pack runs only where the installed Interlock has its Git
substrate.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any, Final

__all__ = ["ACCEPTANCE", "INDUSTRIES", "LADDER", "overview"]

INDUSTRIES: Final = (
    {
        "key": "coding-agent",
        "family": "Coding & repo agents",
        "workflow": "Coding agent",
        "industry": "Coding agents",
        "title": "A maintainer agent on a Git repository",
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
    {
        "key": "workspace",
        "family": "Coding & repo agents",
        "workflow": "Workspace agent",
        "class": "T",
        "substrate": "Files",
    },
    {"key": "sre", "family": "Cloud & infrastructure", "workflow": "Cloud SRE", "class": "P",
     "substrate": "Kubernetes"},
    {"key": "healthcare", "family": "Clinical & regulated", "workflow": "Clinical EHR",
     "class": "V", "substrate": "FHIR R4"},
    {"key": "service-desk", "family": "SaaS, ticketing & CRM", "workflow": "Service desk & CRM",
     "class": "V", "substrate": "REST API"},
    {"key": "saas", "family": "SaaS, ticketing & CRM", "workflow": "SaaS admin", "class": "T"},
    {"key": "supply-chain", "family": "Supply chain & ERP", "workflow": "Supply chain ERP",
     "class": "T"},
    {
        "key": "fintech",
        "family": "Fintech & payments",
        "workflow": "Refunds & payments",
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
)  # fmt: skip
"""Every workflow the overview shows, in the order the workbench lists them, each in its
family. The packs' own entries come from their profiles; these fill in fintech, the coding
agent's nouns, and what each runs on."""

LADDER: Final = (
    {
        "class": "T",
        "name": "Transactional",
        "promise": "The change runs inside a transaction, or in a private copy of the system. "
        "What commits is exactly what was measured, or nothing commits.",
        "where": "SQLite and PostgreSQL. A Git branch, moved by compare-and-swap. A directory, "
        "committed only onto the files that were measured, or swapped in whole with one rename.",
        "shipped": True,
    },
    {
        "class": "P",
        "name": "Plan-bound",
        "promise": "The system's own dry run or saved plan is measured, and the apply is refused "
        "if the system changed in between.",
        "where": "A Kubernetes API server's dry run, admission webhooks included, applied on the "
        "measured resourceVersion. Terraform and OpenTofu saved plans, applied on the state's "
        "serial.",
        "shipped": True,
    },
    {
        "class": "V",
        "name": "Predicted and verified",
        "promise": (
            "Reads current resource version, predicts exact state transition, executes conditional "
            "write (If-Match), and reads back post-write state to automatically compensate "
            "unpredicted side effects."
        ),
        "where": "A FHIR R4 server, and any JSON REST API with ETags or version fields: "
        "ticketing, CRM, HR.",
        "shipped": True,
    },
    {
        "class": "R",
        "name": "Request-only",
        "promise": "The request is measured, checked and delivered exactly once. What happens next "
        "is up to the vendor, so the vendor's own log is matched against the delivery receipts.",
        "where": "Every outbound request in every workflow: Stripe, a pharmacy, EDI, a deploy "
        "system, billing, email.",
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
    "note": "A recorded live run. One policy function builds every domain's checks; only table "
    "names, column names and limits differ. This page shows the recording and doesn't run it "
    "again.",
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
    ("stale_head", "Commit precondition on measured version", "plans refused"),
    ("read_back", "Post-write read-back verification & compensation", "writes undone"),
    ("warrant", "Quorum warrant", "formed"),
    ("break-glass", "Break-glass action found offline", "flagged"),
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
    # Refused before commit: not a write that was made, read back, and put back.
    plans = {
        str(m.get("plan")): int(m.get("rows", 0))
        for m in _measured(acts)
        if not m.get("committed") and not m.get("compensated")
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
    # compare-and-swap, after every check passed, so no audit finding flags it.
    cells["stale_head"] = sum(
        1 for a in acts if isinstance(a.get("git"), Mapping) and a["git"].get("pushed")
        and not a["git"].get("landed")
    ) + sum(
        # A write on a version that moved after it was measured: a FHIR or REST 412, a
        # workspace file edited under the stage, an account changed while it was staged.
        1 for a in acts if isinstance(a.get("race"), Mapping) and not a["race"].get("landed")
        and (a["race"].get("status") or a["race"].get("edited") or a["race"].get("edited_by"))
    )  # fmt: skip
    # A predicted write the read-back did not match: written, then put back.
    cells["read_back"] = sum(1 for m in _measured(acts) if m.get("compensated"))
    cells["refused_plans"] = len(plans) + cells["stale_head"]
    cells["rows_blocked"] = sum(plans.values())
    warrants = [a["warrant"] for a in acts if isinstance(a.get("warrant"), Mapping)]
    cells["warrant"] = [
        {"action": w.get("action", "release"), "approvals": len(w.get("basis", []))}
        for w in warrants
    ]
    after = payload["attestations"].get("after", {}).get("verdict", {})
    # Flagged offline: labeled break-glass (an operator's declared key), or, for
    # an action with no operator key, an ambient action flagged by check V7 in after.fidb.
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
            "family": entry.get("family"),
            "workflow": entry.get("workflow"),
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

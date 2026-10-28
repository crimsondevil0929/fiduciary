"""Reports of an attestation: one self-contained HTML console, and Markdown.

:func:`html` writes a single file that needs nothing beside it: its styles,
its script and the attestation itself are inline, it loads nothing from any
network, and it prints to a clean PDF. :func:`markdown` writes the same
verdict, checks and evidence for a review thread or a ticket.

A payload is ``{"attestations": {name: attestation, ...}, "primary": name,
"demo": None | {...}}``: one attestation per bundle (``verify-bundle``
writes one; ``fiduciary demo`` two, before and after its sixth act), and the
demo's acts when there are any. A demo's payload also carries ``overview``
(the industries, the measurement ladder and the acceptance matrix) and, when
it ran more than one industry, ``domains``: a payload like this one for each.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from importlib.resources import files
from typing import Any, Final

__all__ = ["html", "markdown", "payload"]

_ASSETS: Final = files("fiduciary.report")


def payload(
    attestations: Mapping[str, Mapping[str, Any]],
    *,
    primary: str | None = None,
    demo: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    names = list(attestations)
    return {
        "attestations": dict(attestations),
        "primary": primary or names[-1],
        "demo": dict(demo) if demo is not None else None,
    }


def _script_json(data: Any) -> str:
    """JSON that cannot end the ``<script>`` element it sits in."""
    text = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def html(data: Mapping[str, Any]) -> str:
    """The console, one file (module docstring)."""
    page = (_ASSETS / "console.html").read_text(encoding="utf-8")
    style = (_ASSETS / "console.css").read_text(encoding="utf-8")
    script = (_ASSETS / "console.js").read_text(encoding="utf-8")
    primary = data["attestations"][data["primary"]]
    status = str(primary["verdict"]["status"])
    title = "Stack demo" if data.get("demo") else "Bundle attestation"
    return (
        page.replace("/*STYLE*/", style)
        .replace("/*SCRIPT*/", script)
        .replace("/*DATA*/", _script_json(data))
        .replace("{{TITLE}}", f"{title}: {status.title()}")
    )


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def markdown(data: Mapping[str, Any]) -> str:
    """The verdict, the checks with their controls, and the evidence, as Markdown."""
    out: list[str] = []
    primary = data["primary"]
    ordered = sorted(data["attestations"].items(), key=lambda item: item[0] != primary)
    for name, a in ordered:
        verdict, bundle = a["verdict"], a["bundle"]
        heading = "Attestation" if len(data["attestations"]) == 1 else f"Bundle `{name}`"
        out += [
            f"# {heading}: {verdict['status']}"
            if name == primary
            else f"## {heading}: {verdict['status']}",
            "",
            f"- **Exit code:** {verdict['exit_code']}"
            + (
                f" (first failing check {verdict['first_failing']})"
                if not verdict["passed"]
                else ""
            ),
            f"- **Bundle:** sha256 `{bundle['sha256']}`, {bundle['bytes']:,} bytes, "
            f"{bundle['members']} members, `{bundle['format']}` by {bundle['exporter']}",
            f"- **Lineage:** `{bundle['lineage']}` ({', '.join(bundle['subjects'])}), records "
            f"{bundle['range']['from']} to {bundle['range']['to']}, ledger cut "
            f"{bundle['ledger']['cut']}",
            f"- **Pinned by the auditor:** charter `{a['inputs']['charter']}`; keys: "
            f"{', '.join(a['inputs']['pinned_keys']) or 'none (the bundle keys were used)'}",
            f"- **Verifier:** fiduciary {a['verifier']['fiduciary']}, interlock-escrow "
            f"{a['verifier']['interlock']}, interlock-agentgov {a['verifier']['agentgov']}; "
            "offline, no database",
            f"- **Privacy:** {a['privacy']['claim']}",
            "",
            "| Check | Result | What it proves | OWASP ASI 2026 | SOC 2 | EU AI Act |",
            "|---|---|---|---|---|---|",
        ]
        for check in a["checks"]:
            controls = check["controls"]
            out.append(
                f"| {check['id']} {check['name']} | "
                f"{'pass' if check['status'] == 'pass' else '**FAIL**'} | "
                f"{_cell(check['proves'])} | {', '.join(controls.get('owasp-asi-2026', []))} | "
                f"{', '.join(controls.get('soc2', []))} | "
                f"{', '.join(controls.get('eu-ai-act', []))} |"
            )
        findings = [(c["id"], f) for c in a["checks"] for f in c["findings"]]
        if findings:
            out += ["", "### Findings", ""]
            for check, f in findings:
                tag = f" [{f['label']}]" if f["label"] else ""
                out.append(
                    f"- **{check} {f['kind']}{tag}** (exit {f['exit_code']}): {f['message']}"
                )
        if a["warrants"]:
            out += ["", "### Warrants", ""]
            for w in a["warrants"]:
                out.append(
                    f"- `{w['warrant_id']}`: {w['action']}, on {len(w['basis'])} approval(s), "
                    f"formed {w['formed_at']}, valid until {w['not_after']}"
                )
        ambient = [o for o in a["operators"] if o["ambient"]]
        if ambient:
            out += ["", "### Operator actions without a warrant", ""]
            for o in ambient:
                label = f" [{o['label']}]" if o["label"] else ""
                out.append(
                    f"- record {o['seq']} ({o['action']}) by key `{o['key_id']}`{label}: "
                    f"{_cell(o['reason'])}"
                )
        out += ["", "### Notes and scope", ""] + [f"- {_cell(n)}" for n in a["notes"]] + [""]
    demo = data.get("demo")
    if demo:
        out += ["## Demo scenarios", ""]
        for act in demo["acts"]:
            out.append(f"- **Act {act['n']}: {act['title']}.** {act['lede']}")
        out.append("")
    for page in data.get("domains", []):
        profile = page["demo"]["domain"]
        before = page["attestations"]["before"]["verdict"]
        after = page["attestations"]["after"]["verdict"]
        out += [
            f"## {profile['industry']}: {profile['title']}",
            "",
            f"- **Bundles:** `{profile['key']}/before.fidb` {before['status']} (exit "
            f"{before['exit_code']}); `{profile['key']}/after.fidb` {after['status']} (exit "
            f"{after['exit_code']})",
            f"- **Principals:** {', '.join(profile['principals'])}; break-glass key held by "
            f"{profile['operator']}; partition: {profile['partition']}",
            "",
        ]
        for act in page["demo"]["acts"]:
            out.append(f"- **Act {act['n']}: {act['title']}.** {act['lede']}")
        out.append("")
    mappings = data["attestations"][primary].get("mappings")
    if mappings:
        out += ["## Control frameworks", "", mappings["disclaimer"], ""]
        out += ["| Framework | Controls covered | Mapping SHA-256 |", "|---|---|---|"]
        for pack in mappings["packs"]:
            out.append(
                f"| {_cell(pack['title'])} | {pack['covered']} of {pack['controls']} | "
                f"`{pack['digest'].removeprefix('sha256:')}` |"
            )
        out.append("")
    out.append(
        "_The control mapping supports an assessment; it is not a certification. The verdict, "
        "exit code and findings come directly from `verify-bundle`._"
    )
    return "\n".join(out) + "\n"

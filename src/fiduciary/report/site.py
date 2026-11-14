"""The public site's front page, as HTML a reader without a script can read (1.16.0).

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
        "version": "1.17",
        "text": "Pins for the MCP servers your desktop client runs, and approvals by passkey",
        "href": REPO + "fiduciary/blob/main/CHANGELOG.md",
    },
    "headline": "Let AI agents change real systems. Keep only the changes you approved.",
    "lede": (
        "Fiduciary stages every change an agent makes, measures what it actually did, and checks "
        "that against limits your team signed. A change inside the limits commits once. A change "
        "outside them is refused before it is final, unless two of three named people approve "
        "it. Every decision is a signed record anyone can check offline."
    ),
    "demo": "Open the interactive demo",
    "workflows": "{workflows} workflows",
    "install": "pip install interlock-fiduciary",
    "fine": "Apache-2.0 · Python 3.11 to 3.14 · no cookies, no trackers",
    "init": "fiduciary init --solo --shadow --substrate sqlite --database app.db",
    "file": "tools.py",
    "mode": "shadow mode",
    "code": [
        "import fiduciary",
        "",
        "gov = fiduciary.open()  # the deployment fiduciary init wrote",
        "",
        "",
        "@gov.tool",
        "def close_all(db: fiduciary.Sql) -> str:",
        '    """Close every ticket."""',
        "    db.execute(\"UPDATE tickets SET status = 'closed'\")",
        '    return "all closed"',
    ],
    # The first lines `fiduciary shadow report` printed for this example (the mandate's id
    # shortened). Run again on 1.16.1: the SQLite starter's mandate is written with tables, so
    # the check is recorded under its SQL name, blast_radius, not change_limit.
    "report": [
        ["cmd", "fiduciary shadow report"],
        ["", "Mandate fcc02e85"],
        ["", "  3 plans staged: 3 committed, 0 refused, 0 put back."],
        ["", "  1 of them would have been refused if the mandate were enforced:"],
        ["hit", "    blast_radius: 1 plan, from close_all (1)"],
    ],
    "code_label": "A tool wrapped in shadow mode, and the report it leads to",
    "caption": (
        "Every call commits in shadow mode. The report lists the calls the mandate would have "
        "refused, here one that closed 38 tickets at once."
    ),
    "systems": "One engine, measured on",
    "stack": {
        "kicker": "The stack",
        "title": "Three packages. Use one, or all three.",
        "lede": (
            "A gateway decides whether an agent may call a tool. These decide whether what the "
            "call changed may be kept, and leave evidence for someone who trusts neither the "
            "agent nor the operator. AgentGov and Interlock each work on their own, and "
            "Fiduciary installs both."
        ),
    },
    "packages": [
        {
            "key": "agentgov",
            "name": "AgentGov",
            "dist": "interlock-agentgov",
            "layer": "Layer 1 · spend",
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
        {
            "key": "interlock",
            "name": "Interlock",
            "dist": "interlock-escrow",
            "layer": "Layer 2 · change",
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
            "key": "fiduciary",
            "name": "Fiduciary",
            "dist": "interlock-fiduciary",
            "layer": "Layer 3 · authority",
            "role": "Signed limits, approvals and offline proof",
            "text": (
                "Your limits are a mandate that named people sign, and anything outside it needs "
                "two of three of them. Every decision is a signed record, and the records export "
                "as a bundle anyone can verify offline."
            ),
            "points": [
                "Governed tools in your own code, or over MCP",
                "Shadow mode first: see what it would have refused",
                "Holds a write the agent asks for after reading untrusted input",
                "Approvals signed with a passkey, which an agent cannot read",
                "Pins the MCP servers Claude Desktop and Cursor run: fiduciary protect",
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
                "The stage records what the change actually did, including whatever triggers, "
                "hooks, renderers and admission webhooks did on top: every row, file, object or "
                "record.",
            ],
            [
                "Check",
                "The measured change is compared with the mandate: which tables, files, objects "
                "or records it touched and how many, whose data it reached, how far any guarded "
                "value moved, the rate windows and budgets, and what the agent had read before it "
                "asked.",
            ],
            [
                "Commit or undo",
                "A change that passes commits once, on the exact version that was measured. A "
                "change that fails never commits. If an API changes more than predicted, the "
                "write is undone.",
            ],
            [
                "Prove",
                "Every decision is a signed record. An auditor runs all thirteen checks (V1 to "
                "V13) on an exported bundle, offline, with public keys you gave them separately. "
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
        f"recorded {served} session, replayed at no cost"
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
    lines = "".join(
        f'<div class="cl"><span class="ln" aria-hidden="true">{i}</span>'
        f"<span>{_python(line)}</span></div>"
        for i, line in enumerate(c["code"], 1)
    )
    report = "".join(
        f'<div class="tl"><span class="lp-prompt">$</span> {_e(text)}</div>'
        if kind == "cmd"
        else f'<div class="tl{" hit" if kind == "hit" else ""}">{_e(text)}</div>'
        for kind, text in c["report"]
    )
    code = (
        f'<figure class="lp-code" aria-label="{_e(c["code_label"])}">'
        '<div class="lp-code-bar"><i></i><i></i><i></i>'
        f'<span class="mono">{_e(c["file"])}</span><span class="spacer"></span>'
        f'<span class="lp-tag">{_e(c["mode"])}</span></div>'
        f'<pre class="lp-src"><div class="tl lp-init"><span class="lp-prompt">$</span> '
        f"{_e(c['init'])}</div>{lines}</pre>"
        f'<div class="lp-term">{report}</div><figcaption>{_e(c["caption"])}</figcaption></figure>'
    )
    seen: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        seen.setdefault(row.get("substrate") or "", row)
    chips = "".join(
        f'<span class="lp-sys">{_rung(row["class"])}{_e(name)}</span>'
        for name, row in seen.items()
        if name
    )
    systems = (
        f'<div class="lp-systems"><span class="lp-sys-label">{_e(c["systems"])}</span>{chips}</div>'
        if chips
        else ""
    )
    return _section("lp-hero", None, f'<div class="lp-hero-grid">{copy}{code}</div>{systems}')


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
        + col("Read", [(text, href) for text, href in foot["read"]])
        + f'</div><p class="lp-foot-note">{_e(foot["note"])}</p></div></footer>'
    )


def front_page(data: Mapping[str, Any]) -> str:
    """The front page as the script draws it on load, for a reader that runs no script."""
    n, mark = counts(data), _Marks()
    main = (
        _hero(COPY, n, _ran(data))
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
    """The site's one address. The views behind ``#/`` are the same page to a crawler."""
    day = built_on(data)
    lastmod = f"<lastmod>{day}</lastmod>" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <url><loc>{SITE_URL}</loc>{lastmod}</url>\n"
        "</urlset>\n"
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

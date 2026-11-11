"""The copy gate: every sentence the console, the demo and the reports show is held to
``docs/STYLE.md`` (1.13.2).

Deterministic, and fast: it reads the string literals of the console and of the modules that
hold what the demo says, and checks each against rules a reviewer would otherwise have to
remember. A failure prints the string, where it is, and the rule.
"""

from __future__ import annotations

import ast
import importlib
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "src" / "fiduciary"
CONSOLE = SOURCE / "report" / "console.js"

PACKS = (
    "coding_agent",
    "workspace",
    "sre",
    "healthcare",
    "service_desk",
    "saas",
    "supply_chain",
)
MODULES = (
    "attest.py",
    "report/__init__.py",
    "demo/acts.py",
    "demo/narration.py",
    "demo/overview.py",
    "demo/packs/kit.py",
    *(f"demo/packs/{name}.py" for name in PACKS),
)
"""Where the demo's and the reports' words are written."""

BANNED = (
    # Security-marketing register: say what happened, to what, in plain words.
    "out-of-band",
    "unattested",
    "unmediated",
    "post-hoc",
    "post-write",
    "zero-knowledge",
    "seamless",
    "blindly trust",
    "cryptographic linkage",
    "cryptographically",
    "tamper-proof",
    "immutable document",
    "military-grade",
    "robust",
    "leverag",
    "utiliz",
    "holistic",
    "best-in-class",
    # Overclaims: the page says what was measured, never what is guaranteed.
    "guarantee",
    "immediately detectable",
    "impossible to",
    # One name for the thing in front of the stack.
    "input proxy",
    "gateway proxy",
    "policy proxy",
)
"""Phrases the copy never uses, lower case. Each has a plain way to say it; STYLE.md lists
them."""

NAMES_BUDGET = 3
"""How often "names" or "named" may stand as a verb in everything the page shows. "Names"
the noun is fine (table names), and so are named principals. As a verb it had crept in for
"says", "lists", "reports", "identifies" and "records", until every other card used it."""

NAMES_VERB = re.compile(
    r"\bnames (?=(the|a|an|it|its|each|every|no|none|that|what|which|who|whom|only|one|them|"
    r"this|those|these|nothing|his|her|their|another|both|any)\b)"
    r"|\bnames [`'(\[{$]"
    r"|\bnames $"
    r"|\b(it|bundle|plan|call|verifier|receipt|record|log|ticket|warrant|mandate|charter|"
    r"reconciliation|finding|report|check|nothing|everything|which|that) names\b(?! (of|or|and)\b)"
    r"|\b(is|are|was|were|be|been) named\b"
    r"|\bnamed\b(?! (principals|approvers|people|person))"
)

TITLE_LIMIT = 52
"""The longest a scenario title may be: two lines of the desktop rail, and the mobile strip
without clipping."""


def literals(path: Path) -> Iterator[tuple[int, str]]:
    """Each string a Python module can show: every string constant and f-string part but
    docstrings, with its line."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            docstrings.add(id(node.value))  # a string on its own line documents a name
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                yield node.lineno, node.value


def console_literals() -> Iterator[tuple[int, str]]:
    """Each double-quoted string of the console's script, with its line."""
    for number, line in enumerate(CONSOLE.read_text(encoding="utf-8").splitlines(), 1):
        for found in re.finditer(r'"((?:[^"\\]|\\.)*)"', line):
            yield number, found.group(1)


def corpus() -> list[tuple[str, int, str]]:
    """Everything shown, as (file, line, text): strings of two words or more."""
    shown = [("report/console.js", n, s) for n, s in console_literals()]
    for name in MODULES:
        shown += [(name, n, s) for n, s in literals(SOURCE / name)]
    return [(f, n, s) for f, n, s in shown if len(s.split()) >= 2 and re.search(r"[a-z]{2}", s)]


CORPUS = corpus()


def offenders(rule: re.Pattern[str]) -> list[str]:
    return [f"{f}:{n}: {s!r}" for f, n, s in CORPUS if rule.search(s)]


def test_the_corpus_is_what_the_page_shows() -> None:
    files = {f for f, _, _ in CORPUS}
    assert files <= {"report/console.js", *MODULES}
    assert files >= {"report/console.js", "attest.py", *(f"demo/packs/{n}.py" for n in PACKS)}
    assert len(CORPUS) > 1200, "the scan found the console and every pack"
    assert sum(1 for f, _, _ in CORPUS if f == "report/console.js") > 600


def test_no_dash_joins_two_words() -> None:
    """An em dash set tight between words is the clearest machine tell there is. The copy uses
    a comma, a colon or two sentences. (A lone dash stands for an empty cell.)"""
    found = offenders(re.compile(r"\w\s?—|—\s?\w"))
    assert not found, "\n".join(found)


def test_no_count_is_written_with_a_bracketed_s() -> None:
    found = offenders(re.compile(r"\w\(s\)"))
    assert not found, "\n".join(found)


@pytest.mark.parametrize("phrase", BANNED)
def test_no_banned_phrase(phrase: str) -> None:
    found = offenders(re.compile(re.escape(phrase), re.IGNORECASE))
    assert not found, f"{phrase!r} (see docs/STYLE.md):\n" + "\n".join(found)


def test_names_is_not_the_verb_for_everything() -> None:
    found = offenders(NAMES_VERB)
    assert len(found) <= NAMES_BUDGET, (
        f"{len(found)} uses of names/named, and the budget is {NAMES_BUDGET}. Say what the "
        f"thing does: lists, says, reports, identifies, records.\n" + "\n".join(found)
    )


def test_no_markdown_is_left_in_a_string() -> None:
    """The console draws text, not Markdown. (``report/__init__.py`` writes ``report.md``,
    which is Markdown, so it is the one file this rule skips.)"""
    rule = re.compile(r"\*\*\w|\w\*\*")
    found = [
        f"{f}:{n}: {s!r}" for f, n, s in CORPUS if f != "report/__init__.py" and rule.search(s)
    ]
    assert not found, "\n".join(found)


def titles() -> list[tuple[str, str, str]]:
    acts = importlib.import_module("fiduciary.demo.acts")
    found = [("fintech", key, text) for key, text in acts.TITLES.items()]
    for name in PACKS:
        module = importlib.import_module(f"fiduciary.demo.packs.{name}")
        found += [(name, key, text) for key, text in module.TITLES.items()]
    return found


def ledes() -> list[tuple[str, str, str]]:
    acts = importlib.import_module("fiduciary.demo.acts")
    found = [("fintech", key, text) for key, text in acts.LEDES.items()]
    for name in PACKS:
        module = importlib.import_module(f"fiduciary.demo.packs.{name}")
        found += [(name, key, text) for key, text in module.LEDES.items()]
    return found


def test_every_scenario_has_a_short_title_and_a_lede() -> None:
    found = titles()
    assert len(found) == 56 and {(w, k) for w, k, _ in found} == {(w, k) for w, k, _ in ledes()}
    long = [f"{w}/{k} ({len(t)}): {t}" for w, k, t in found if len(t) > TITLE_LIMIT]
    assert not long, f"titles over {TITLE_LIMIT} characters:\n" + "\n".join(long)
    shouting = [f"{w}/{k}: {t}" for w, k, t in found if t.endswith(".") or "  " in t]
    assert not shouting, "\n".join(shouting)


def test_a_lede_is_whole_sentences_of_a_readable_length() -> None:
    problems = []
    for workflow, key, text in ledes():
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", text) if s]
        if not text.endswith("."):
            problems.append(f"{workflow}/{key}: does not end with a full stop")
        if not 2 <= len(sentences) <= 6 or len(text) > 520:
            problems.append(f"{workflow}/{key}: {len(sentences)} sentences, {len(text)} chars")
        longest = max(sentences, key=lambda s: len(s.split()))
        if len(longest.split()) > 38:
            problems.append(f"{workflow}/{key}: a sentence of {len(longest.split())} words")
    assert not problems, "\n".join(problems)


def test_the_gate_catches_what_it_is_for() -> None:
    """The rules, on the sentences that prompted them."""
    tight = re.compile(r"\w\s?—|—\s?\w")
    assert tight.search("aborts—leaving the engineer's change intact")
    assert tight.search("opens a new incident —side effects no dry run can preview")
    assert not tight.search("—") and not tight.search("a - b")
    assert re.search(r"\w\(s\)", "1 delivery receipt(s)")
    assert any(p in "an unattested phantom event" for p in BANNED)
    assert any(p in "guaranteeing exactly-once execution".lower() for p in BANNED)
    for verb in (
        "the bundle names it",
        "the push around it is named",
        "names `main`",
        "the resource it names, before the call",
        "The bundle names ",
    ):
        assert NAMES_VERB.search(verb), verb
    for noun in ("table names, column names", "names or account ids", "of named principals"):
        assert not NAMES_VERB.search(noun), noun

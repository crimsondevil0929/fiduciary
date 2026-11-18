"""Judging a conformance run (1.22.0): each check against the transcripts, then each clause
from its checks. Pure functions of the suite, the law catalog, the transcripts and a date, so
``report/conform.js`` reaches the same results in a browser; the two are held to each other by
a test.

A check's outcome is ``pass``, ``fail`` or ``untested`` (the scenario was not run, or it needs
the test clock and the endpoint takes none). A clause's verdict is:

- ``fail``: a check bearing on it failed, and the law accepts nothing but what the chat shows;
- ``open``: a check bearing on it failed where the law also accepts a notice in the app's
  interface, or a check was untested: a person has to look;
- ``pass``: every check bearing on it passed.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import date
from functools import lru_cache
from typing import Any

from fiduciary.conform.laws import in_force

__all__ = ["checks", "clauses", "normalize", "shows"]

_SPACE = re.compile(r"[ \t\n\r\f\v]+")


def normalize(text: str) -> str:
    """A reply as the checks read it: NFKC, straight quotes, one space between words."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace(chr(0x2018), "'").replace(chr(0x2019), "'")
    text = text.replace(chr(0x201C), '"').replace(chr(0x201D), '"')
    return _SPACE.sub(" ", text).strip()


@lru_cache(maxsize=256)
def _pattern(source: str) -> re.Pattern[str]:
    return re.compile(source, re.IGNORECASE | re.ASCII)


def shows(signal: Sequence[str], reply: str) -> bool:
    """Whether ``reply`` shows the signal: any of its patterns matches."""
    text = normalize(reply)
    return any(_pattern(p).search(text) for p in signal)


def _check(
    check: Mapping[str, Any],
    transcript: Mapping[str, Any] | None,
    signals: Mapping[str, Sequence[str]],
) -> tuple[str, int | None, str]:
    if transcript is None:
        return "untested", None, "not run"
    if check["clock"] and not transcript["hooks"]:
        return "untested", None, "no test clock"
    turns = transcript["turns"]
    numbers = range(1, len(turns) + 1) if check["kind"] == "every" else check["turns"]
    for n in numbers:
        if n > len(turns):
            return "untested", None, f"turn {n} not run"
        reply = turns[n - 1]["reply"]
        for name in check["has"]:
            if not shows(signals[name], reply):
                return "fail", n, f"turn {n} lacks {name}"
        for name in check["lacks"]:
            if shows(signals[name], reply):
                return "fail", n, f"turn {n} has {name}"
    return "pass", None, ""


def checks(
    suite: Mapping[str, Any], transcripts: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Every check's outcome, in the suite's order."""
    by_scenario = {t["scenario"]: t for t in transcripts}
    found = []
    for scenario in suite["scenarios"]:
        transcript = by_scenario.get(scenario["id"])
        for check in scenario["checks"]:
            outcome, turn, detail = _check(check, transcript, suite["signals"])
            found.append(
                {
                    "scenario": scenario["id"],
                    "check": check["id"],
                    "outcome": outcome,
                    "turn": turn,
                    "detail": detail,
                }
            )
    return found


def clauses(
    suite: Mapping[str, Any],
    catalog: Mapping[str, Any],
    results: Sequence[Mapping[str, Any]],
    jurisdictions: Sequence[str],
    on: date,
) -> list[dict[str, Any]]:
    """Each clause of ``jurisdictions`` that a check bears on, in the catalog's order."""
    bearing: dict[str, list[str]] = {}
    for scenario in suite["scenarios"]:
        for check in scenario["checks"]:
            for clause in check["clauses"]:
                bearing.setdefault(clause, []).append(f"{scenario['id']}/{check['id']}")
    outcome = {f"{r['scenario']}/{r['check']}": r["outcome"] for r in results}
    found = []
    for clause in catalog["clauses"]:
        if clause["jurisdiction"] not in jurisdictions or clause["id"] not in bearing:
            continue
        seen = [outcome[key] for key in bearing[clause["id"]]]
        if "fail" in seen and not clause["interface"]:
            verdict = "fail"
        elif "fail" in seen or "untested" in seen:
            verdict = "open"
        else:
            verdict = "pass"
        found.append({"id": clause["id"], "verdict": verdict, "in_force": in_force(clause, on)})
    return found

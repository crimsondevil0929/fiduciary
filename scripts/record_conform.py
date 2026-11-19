"""Record the three stand-in chatbots for ``fiduciary conform demo`` and the /chatbots/ guide.

    python scripts/record_conform.py

Writes ``src/fiduciary/conform/recordings/<name>.json`` (each stand-in run in process, with
the test clock) and ``src/fiduciary/report/conform.json`` (what the guide shows: each clause's
verdict for each stand-in). No model runs: the stand-ins are rules, so this costs nothing and
writes the same files every time.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from fiduciary.conform import judge, laws, standins
from fiduciary.conform import suite as suites
from fiduciary.conform.targets import StandInTarget, converse, recording

ROOT = Path(__file__).resolve().parent.parent
RECORDINGS = ROOT / "src" / "fiduciary" / "conform" / "recordings"
GUIDE = ROOT / "src" / "fiduciary" / "report" / "conform.json"
ON = date(2027, 7, 1)
"""The day the guide judges on: every law here is in force but California's § 22602(c)."""


def main() -> int:
    suite = suites.suite()
    catalog = laws.catalog()
    everywhere = laws.jurisdictions("all")
    shown = []
    for name in standins.BOTS:
        target = StandInTarget(name)
        made = recording(target, converse(target, suite))
        (RECORDINGS / f"{name}.json").write_text(
            json.dumps(made, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        results = judge.checks(suite, made["transcripts"])
        verdicts = judge.clauses(suite, catalog, results, everywhere, ON)
        shown.append(
            {
                "name": name,
                "label": target.label,
                "verdicts": {v["id"]: v["verdict"] for v in verdicts},
                "missed": [
                    {
                        "scenario": r["scenario"],
                        "check": r["check"],
                        "turn": r["turn"],
                        "detail": r["detail"],
                        "user": made["transcripts"][
                            [t["scenario"] for t in made["transcripts"]].index(r["scenario"])
                        ]["turns"][r["turn"] - 1]["user"],
                        "reply": made["transcripts"][
                            [t["scenario"] for t in made["transcripts"]].index(r["scenario"])
                        ]["turns"][r["turn"] - 1]["reply"],
                    }
                    for r in results
                    if r["outcome"] == "fail" and r["turn"] is not None
                ],
            }
        )
    GUIDE.write_text(
        json.dumps({"on": ON.isoformat(), "bots": shown}, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"recorded {', '.join(standins.BOTS)}; guide data in {GUIDE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

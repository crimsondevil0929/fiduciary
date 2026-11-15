"""Record the incident replays for the site's ``/replays/`` page (1.20.0).

Runs each replay of ``fiduciary.replays`` in a scratch directory, as ``fiduciary replay``
does, and writes what happened into ``src/fiduciary/report/replays.json``: every step, each
bundle's verdict, and what caught it. The page shows that run, word for word; the test
``tests/test_replays.py`` runs the replays again and holds the page to them.

    python scripts/record_replays.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "src" / "fiduciary" / "report" / "replays.json"


def main() -> int:
    sys.path.insert(0, str(ROOT / "src"))
    from fiduciary.replays import REPLAYS, run

    recorded = []
    with tempfile.TemporaryDirectory() as work:
        for key in REPLAYS:
            replay = run(key, Path(work))
            if any("NOT" in verdict for verdict in replay.bundles.values()):
                print(f"{key}: a bundle does not verify: {replay.bundles}")
                return 1
            recorded.append(replay.to_json())
            print(f"{key}: {len(replay.steps)} steps, {len(replay.bundles)} bundles verified")
    TARGET.write_text(json.dumps(recorded, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Give a recorded ``fiduciary demo`` run the other industries, without running it again.

    python scripts/stack_dashboard.py live-run/stack-demo-latest

Reads the run's ``demo.json``, keeps its acts as recorded (a live model's
transcript included), verifies its two bundles again with the installed
verifier, against the ``charter.digest`` and ``pinned-keys.json`` beside them,
and stops if either verdict differs from the one recorded. Then it plays every
domain pack into a new subdirectory of the run (``healthcare/``, ``sre/``,
``supply-chain/``, ``saas/``) and writes one dashboard over all of them, with
``demo.json`` and ``report.md``, in the run's directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from fiduciary.attest import attest
from fiduciary.bundle import TrustedKeys, verify_bundle
from fiduciary.demo import extend
from fiduciary.demo.packs import PACKS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run", type=Path, help="a directory fiduciary demo wrote")
    parser.add_argument("--gateway", default="auto", choices=("auto", "cedarpy", "builtin"))
    args = parser.parse_args(argv)
    root: Path = args.run
    taken = [key for key in PACKS if (root / key).exists()]
    if taken:
        print(f"{root} already holds {', '.join(taken)}: nothing written", file=sys.stderr)
        return 2
    page: dict[str, Any] = json.loads((root / "demo.json").read_text())
    page = {k: v for k, v in page.items() if k not in ("domains", "overview")}
    charter = (root / "charter.digest").read_text().strip()
    pinned = json.loads((root / "pinned-keys.json").read_text())
    for name in ("before", "after"):
        data = (root / f"{name}.fidb").read_bytes()
        report = verify_bundle(data, charter=charter, keys=TrustedKeys(pinned))
        fresh = attest(data, report, charter=charter, pinned=list(pinned))
        recorded = page["attestations"][name]
        for field in ("status", "exit_code", "first_failing", "labels", "findings"):
            if fresh["verdict"][field] != recorded["verdict"][field]:
                print(
                    f"{name}.fidb: {field} is now {fresh['verdict'][field]!r}, recorded "
                    f"{recorded['verdict'][field]!r}: nothing written",
                    file=sys.stderr,
                )
                return 1
        page["attestations"][name] = fresh
        print(
            f"{name}.fidb: {fresh['verdict']['status']} (exit {fresh['verdict']['exit_code']}), "
            "as recorded"
        )
    outcome = extend(root, page, engine=args.gateway, say=print)
    print(f"dashboard   {outcome.dashboard}")
    return 0 if outcome.held else 1


if __name__ == "__main__":
    sys.exit(main())

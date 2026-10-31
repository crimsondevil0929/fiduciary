"""The SRE pack's admission-policy analyzer, run as a sensor (1.5.0).

``python -m fiduciary.demo.analyzer --key KEY [--max-replicas N]`` reads a
measured diff (Interlock's sensor input, on standard input) and writes a signed
fact about it (on standard output): ``pass``, or ``fail`` with a count of
findings. It holds its own signing key; the engine that runs it never sees it.

It checks what a policy engine (OPA, Kyverno) or a schema validator would on
every workload the diff writes:

- no more replicas than the platform's ceiling;
- no image without a pinned tag (``:latest``, or none);
- no deletion of a stateful workload.

The checks are placeholders, as every threshold in the demo is: the point is
that an analyzer's verdict, which no pure checker could reach, is bound to the
exact diff the stage measured and signed by a key the mandate pins.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from typing import Any

from interlock.sensors import sign_fact

from fiduciary.keys import load_key

NAME = "policy-check"
"""The sensor's registered name."""


def findings(document: Mapping[str, Any], *, max_replicas: int) -> list[str]:
    """Every problem the policy finds in a sensor input document."""
    found = []
    for row in document.get("rows", ()):
        after = row.get("after") or {}
        where = f"{row.get('table')} {row.get('key')}"
        replicas = after.get("replicas")
        if isinstance(replicas, int) and replicas > max_replicas:
            found.append(f"{where}: {replicas} replicas, above the ceiling of {max_replicas}")
        image = after.get("image")
        if isinstance(image, str) and (image.endswith(":latest") or ":" not in image):
            found.append(f"{where}: image {image} has no pinned tag")
        if row.get("operation") == "delete" and row.get("table") == "statefulsets":
            found.append(f"{where}: deletes a stateful workload")
    return found


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fiduciary.demo.analyzer")
    parser.add_argument("--key", required=True, help="the analyzer's signing key file")
    parser.add_argument("--max-replicas", type=int, default=20)
    args = parser.parse_args(argv)
    raw = sys.stdin.buffer.read()
    document = json.loads(raw)
    problems = findings(document, max_replicas=args.max_replicas)
    report = json.dumps({"findings": problems}, sort_keys=True).encode("utf-8")
    fact = sign_fact(
        load_key(args.key),
        sensor=NAME,
        subject=str(document["subject"]),
        verdict="fail" if problems else "pass",
        findings=len(problems),
        report=report,
    )
    print(json.dumps(fact.to_json(), sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - run as a command
    sys.exit(main())

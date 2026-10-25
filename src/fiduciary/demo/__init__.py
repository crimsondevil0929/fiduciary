"""``fiduciary demo``: the whole stack in one command, offline.

:func:`run` stands a deployment up in a directory (:mod:`fiduciary.demo.world`),
plays the seven acts against it and against an input gateway
(:mod:`fiduciary.demo.acts`, :mod:`fiduciary.demo.gateway`), and leaves behind:

- ``dashboard.html``: the console, one self-contained file;
- ``demo.json``: everything the console renders;
- ``before.fidb`` and ``after.fidb``: the lineage's audit bundles, before the
  administrator's act and after it, with ``pinned-keys.json`` and
  ``charter.digest`` to verify them with ``fiduciary verify-bundle``;
- ``attestation.json`` and ``report.md``: the audited bundle's attestation;
- the deployment itself, with ``fiduciary.toml``: ``fiduciary --config
  <dir>/fiduciary.toml verify`` and the other commands open it.

The keys are made fresh for each run and written beside the deployment, as
``fiduciary keygen`` writes them. They sign nothing outside it.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fiduciary import __version__, report
from fiduciary.attest import attest
from fiduciary.demo.acts import Live, Run, pinned, run_acts
from fiduciary.demo.gateway import Gateway
from fiduciary.demo.world import ORDERS, SCOPE, build, tenant_of

__all__ = ["Live", "Outcome", "run"]


@dataclass(frozen=True, slots=True)
class Outcome:
    root: Path
    dashboard: Path
    payload: dict[str, Any]

    @property
    def held(self) -> bool:
        """What the demo shows held: the bundle before the administrator's act
        verifies, the one after it names that act, and nothing planted leaked."""
        demo = self.payload["demo"]
        audit = next(a for a in demo["acts"] if a["key"] == "audit")
        before, after = audit["verdicts"]["before"], audit["verdicts"]["after"]
        leaked = sum(sum(v["scan"]["found"].values()) for v in (before, after))
        return bool(before["passed"]) and after["exit_code"] == 7 and leaked == 0


def run(
    root: Path,
    *,
    live: Live | None = None,
    engine: str = "auto",
    say: Callable[[str], None] = lambda _: None,
) -> Outcome:
    """Run the demo into ``root``, which must be empty or absent (module docstring)."""
    started = time.monotonic()
    world = build(root)
    gateway = Gateway(SCOPE, "acme", {n: tenant_of(n) for n in range(1, ORDERS + 1)}, engine=engine)
    demo = Run(world, gateway, live=live, say=say)
    try:
        run_acts(demo)
        keys = pinned(world)
        charter = world.charter.digest
    finally:
        world.close()
    seconds = round(time.monotonic() - started, 1)
    attestations = {}
    for name in ("before", "after"):
        data = demo.bundles[name]
        (root / f"{name}.fidb").write_bytes(data)
        attestations[name] = attest(data, demo.reports[name], charter=charter, pinned=list(keys))
    (root / "pinned-keys.json").write_text(json.dumps(keys, indent=2, sort_keys=True) + "\n")
    (root / "charter.digest").write_text(charter + "\n")
    page = report.payload(
        attestations,
        primary="after",
        demo={
            "version": __version__,
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "mode": "live" if live is not None else "reference",
            "model": demo.model or None,
            "gateway": {"engine": gateway.engine, "policies": gateway.policies},
            "seconds": seconds,
            "acts": demo.acts,
        },
    )
    (root / "demo.json").write_text(json.dumps(page, indent=1, sort_keys=True, default=str) + "\n")
    (root / "attestation.json").write_text(
        json.dumps(attestations["after"], indent=2, sort_keys=True) + "\n"
    )
    (root / "report.md").write_text(report.markdown(page))
    dashboard = root / "dashboard.html"
    dashboard.write_text(report.html(page), encoding="utf-8")
    return Outcome(root, dashboard, page)

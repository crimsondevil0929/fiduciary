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

``domain`` picks the industry: ``fintech`` (the default, the seven acts
above), one of the domain packs in :mod:`fiduciary.demo.packs`
(``healthcare``, ``sre``, ``supply-chain``, ``saas``), each its own
deployment on the same engine, or ``all``: fintech in the directory itself
and each pack in a subdirectory named for it, with one dashboard that
switches between them. Every dashboard opens on the overview
(:mod:`fiduciary.demo.overview`).

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
from typing import Any, Final

from fiduciary import __version__, report
from fiduciary.attest import attest
from fiduciary.demo import packs
from fiduciary.demo.acts import Live, Run, pinned, run_acts
from fiduciary.demo.gateway import Gateway
from fiduciary.demo.overview import overview
from fiduciary.demo.world import ORDERS, SCOPE, build, tenant_of

__all__ = ["DOMAINS", "Live", "Outcome", "extend", "run"]

DOMAINS: Final = ("fintech", *packs.PACKS, "all")
"""What ``domain`` may name."""


def _held(page: dict[str, Any]) -> bool:
    demo = page["demo"]
    audit = next(a for a in demo["acts"] if a["key"] == "audit")
    before, after = audit["verdicts"]["before"], audit["verdicts"]["after"]
    leaked = sum(sum(v["scan"]["found"].values()) for v in (before, after))
    held = bool(before["passed"]) and after["exit_code"] == 7 and leaked == 0
    for act in demo["acts"]:
        for customer in act.get("customers", []):
            held = held and bool(customer["passed"]) and customer["other_records"] == 0
    return held


@dataclass(frozen=True, slots=True)
class Outcome:
    root: Path
    dashboard: Path
    payload: dict[str, Any]

    @property
    def held(self) -> bool:
        """What the demo shows held, in every domain it ran: the bundle before
        the administrator's act verifies, the one after it names that act, and
        nothing planted leaked."""
        pages = [self.payload, *self.payload.get("domains", [])]
        return all(_held(page) for page in pages)


def run(
    root: Path,
    *,
    live: Live | None = None,
    engine: str = "auto",
    say: Callable[[str], None] = lambda _: None,
    domain: str = "fintech",
) -> Outcome:
    """Run the demo into ``root``, which must be empty or absent (module docstring)."""
    if domain not in DOMAINS:
        raise ValueError(f"domain is one of {', '.join(DOMAINS)}, not {domain!r}")
    if domain in packs.PACKS:
        if live is not None:
            raise ValueError("--live works the fintech queue; the domain packs replay theirs")
        page = packs.run(root, domain, engine=engine, say=say)
        return _write(root, page, {domain: page})
    page = _fintech(root, live=live, engine=engine, say=say)
    if domain == "fintech":
        return _write(root, page, {"fintech": page})
    return extend(root, page, engine=engine, say=say)


def extend(
    root: Path,
    page: dict[str, Any],
    *,
    engine: str = "auto",
    say: Callable[[str], None] = lambda _: None,
) -> Outcome:
    """Every domain pack, each in its own new subdirectory of ``root``, beside
    the fintech run whose console payload is ``page``; and the one dashboard
    over them all, written to ``root`` (``domain="all"``, and how a recorded
    live run gains the other industries without being run again)."""
    played = {"fintech": {k: v for k, v in page.items() if k not in ("domains", "overview")}}
    for key in packs.PACKS:
        say("")
        say(f"{packs.PACKS[key].industry}: a deployment in {root / key}")
        sub = packs.run(root / key, key, engine=engine, say=say)
        _write(root / key, sub, {key: sub})
        played[key] = sub
    combined = {**played["fintech"], "domains": [played[k] for k in packs.PACKS]}
    return _write(root, combined, played)


def _write(root: Path, page: dict[str, Any], played: dict[str, Any]) -> Outcome:
    """The console of ``page`` and its Markdown report, with the overview of
    the domains ``played``."""
    page = {**page, "overview": overview(played)}
    (root / "demo.json").write_text(json.dumps(page, indent=1, sort_keys=True, default=str) + "\n")
    (root / "report.md").write_text(report.markdown(page))
    dashboard = root / "dashboard.html"
    dashboard.write_text(report.html(page), encoding="utf-8")
    return Outcome(root, dashboard, page)


def _fintech(
    root: Path,
    *,
    live: Live | None,
    engine: str,
    say: Callable[[str], None],
) -> dict[str, Any]:
    """The seven fintech acts, into ``root``: their payload."""
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
    return page

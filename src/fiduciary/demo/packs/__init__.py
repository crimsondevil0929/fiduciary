"""Domain packs: the same engine, run with each industry's nouns.

Each pack (:mod:`.healthcare`, :mod:`.sre`, :mod:`.supply_chain`,
:mod:`.saas`, :mod:`.coding_agent`) stands up a deployment of its own and
plays its acts on the real stack: its own schema and triggers (or, for coding
agents, a Git repository and its hooks), mandate, tool bindings, rate windows,
quorum and break-glass key, and the offline verifier at the end. Every figure
is measured as the acts run. Nothing is written in ahead.

:func:`run` plays one pack into a directory and returns the payload its part
of the console renders, with the pack's two bundles, their attestations and
a working ``fiduciary.toml`` beside it, as the fintech demo leaves.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from fiduciary import __version__, report
from fiduciary.attest import attest
from fiduciary.demo.acts import Live
from fiduciary.demo.packs import coding_agent, healthcare, saas, sre, supply_chain
from fiduciary.demo.packs.kit import Pack, PackGateway, PackRun, build, pinned

__all__ = ["PACKS", "run"]

PACKS: Final[dict[str, Pack]] = {
    p.key: p
    for p in (healthcare.PACK, sre.PACK, supply_chain.PACK, saas.PACK, coding_agent.PACK)
    if p
}
"""Every pack the installed packages can run: the coding-agent pack needs
interlock's Git substrate (interlock 1.1.0)."""


def run(
    root: Path,
    key: str,
    *,
    engine: str = "auto",
    say: Callable[[str], None] = lambda _: None,
    live: Live | None = None,
) -> dict[str, Any]:
    """Play pack ``key`` into ``root``, which must be empty or absent, and
    return its console payload (module docstring). With ``live``, a model
    works the pack's signature act (act 2), and every other act replays."""
    pack = PACKS[key]
    started = time.monotonic()
    world = build(root, pack)
    gateway = PackGateway(pack, pack.tenants[0], engine=engine)
    played = PackRun(world, gateway, say=say, live=live)
    try:
        played.observe()
        pack.acts(played)
        keys = pinned(world)
        charter = world.charter.digest
    finally:
        world.close()
    seconds = round(time.monotonic() - started, 1)
    attestations = {}
    for name in ("before", "after"):
        data = played.bundles[name]
        (root / f"{name}.fidb").write_bytes(data)
        attestations[name] = attest(data, played.reports[name], charter=charter, pinned=list(keys))
    (root / "pinned-keys.json").write_text(json.dumps(keys, indent=2, sort_keys=True) + "\n")
    (root / "charter.digest").write_text(charter + "\n")
    page = report.payload(
        attestations,
        primary="after",
        demo={
            "version": __version__,
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "mode": "live" if played.model else "reference",
            "model": played.model or None,
            "gateway": {"engine": gateway.engine, "policies": gateway.policies},
            "seconds": seconds,
            "acts": played.acts,
            "domain": profile(pack),
        },
    )
    (root / "demo.json").write_text(json.dumps(page, indent=1, sort_keys=True, default=str) + "\n")
    (root / "attestation.json").write_text(
        json.dumps(attestations["after"], indent=2, sort_keys=True) + "\n"
    )
    return page


def profile(pack: Pack) -> dict[str, Any]:
    """What the console says about a pack, beside its acts."""
    return {
        "key": pack.key,
        "industry": pack.industry,
        "title": pack.title,
        "frameworks": list(pack.frameworks),
        "scope": pack.scope,
        "principals": list(pack.principals),
        "operator": pack.operator,
        "partition": pack.partition,
        "tenants": len(pack.tenants),
        "tables": [t.name for t in pack.tables],
        "sinks": [s.name for s in pack.sinks],
        "nouns": dict(pack.nouns),
    }

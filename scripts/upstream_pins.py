"""The upstream commits this checkout is pinned to, as pip requirements.

Between an upstream change and its release, ``pyproject.toml`` pins interlock or AgentGov to
a commit under ``[tool.uv.sources]``, so ``uv sync`` resolves it. Two CI steps install with
pip and read no lockfile: the bare-wheel install, and the job that tests against upstream.
They ask this script what to install first.

    python scripts/upstream_pins.py            # one requirement per pinned package, or nothing
    python scripts/upstream_pins.py --or-main  # the pins, and upstream main for the rest

Remove the pins (and relock) before tagging a release: a release resolves from PyPI.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

UPSTREAM = {
    "interlock-agentgov": "https://github.com/crimsondevil0929/agentgov",
    "interlock-escrow": "https://github.com/crimsondevil0929/interlock",
}


def pins() -> dict[str, str]:
    """Each pinned upstream package, as a pip requirement naming its commit."""
    project = tomllib.loads(
        Path(__file__).resolve().parent.parent.joinpath("pyproject.toml").read_text()
    )
    sources = project.get("tool", {}).get("uv", {}).get("sources", {})
    found = {}
    for name, source in sources.items():
        if name in UPSTREAM and "git" in source:
            ref = source.get("rev") or source.get("tag") or source.get("branch") or "main"
            found[name] = f"{name} @ git+{source['git']}@{ref}"
    return found


def main(argv: list[str]) -> int:
    pinned = pins()
    if "--or-main" in argv:
        for name, url in UPSTREAM.items():
            pinned.setdefault(name, f"{name} @ git+{url}@main")
    for requirement in pinned.values():
        print(requirement)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

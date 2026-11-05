"""Print the requirements that pin an upstream to a commit not yet on PyPI.

    python scripts/upstream_pins.py

Between an upstream's merge and its release, ``pyproject.toml`` may pin it to a
commit (a ``[tool.uv.sources]`` git entry, removed before a release is tagged).
CI's bare install takes the same commit, so it installs what the rest of CI
tests. With no such entry it prints nothing, and the bare install resolves from
PyPI alone, as a user's would.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path


def pins(pyproject: Path) -> list[str]:
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    return [
        f"{name} @ git+{source['git']}@{source['rev']}"
        for name, source in sorted(sources.items())
        if isinstance(source, dict) and "git" in source and "rev" in source
    ]


def main() -> int:
    for line in pins(Path(__file__).resolve().parent.parent / "pyproject.toml"):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

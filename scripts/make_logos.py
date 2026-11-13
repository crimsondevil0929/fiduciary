"""Write each package's mark to its repository's ``docs/logo.svg`` and ``docs/logo-dark.svg``
(1.15.0).

    python scripts/make_logos.py                     # this repository's docs/logo.svg
    python scripts/make_logos.py ../agentgov ../interlock
    python scripts/make_logos.py --check ../agentgov ../interlock

The marks are drawn once, in :mod:`fiduciary.brand`. A repository is known by its
``pyproject.toml`` name. With ``--check`` nothing is written: the exit code is 1 when a file
differs from the drawing.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

from fiduciary import brand

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = {
    "interlock-agentgov": "agentgov",
    "interlock-escrow": "interlock",
    "interlock-fiduciary": "fiduciary",
}


def mark_of(repository: Path) -> str:
    name = tomllib.loads((repository / "pyproject.toml").read_text())["project"]["name"]
    try:
        return PACKAGES[name]
    except KeyError:
        raise SystemExit(f"{repository} is {name}, not one of {', '.join(PACKAGES)}") from None


def main(argv: list[str]) -> int:
    check = "--check" in argv
    repositories = [ROOT, *(Path(a) for a in argv if a != "--check")]
    stale = 0
    for repository in repositories:
        name = mark_of(repository)
        for theme, file in (("light", "logo.svg"), ("dark", "logo-dark.svg")):
            target = repository / "docs" / file
            drawn = brand.standalone(name, theme=theme)
            if check:
                if not target.is_file() or target.read_text(encoding="utf-8") != drawn:
                    print(f"{target} differs from fiduciary.brand")
                    stale += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(drawn, encoding="utf-8")
            print(f"wrote {target}")
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

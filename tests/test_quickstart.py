"""docs/QUICKSTART.md runs as written (1.10.1).

Every ``bash`` block marked ``<!-- quickstart: run -->`` runs, in order, in one shell in a
scratch directory, with this environment's ``fiduciary`` first on the PATH; a block marked
``run background`` (the writer) runs beside the rest until they finish. The blocks a test
cannot run (installing from PyPI, opening a browser, adding an MCP server to a client) are
not marked. The guide's claims are then checked against what the shell printed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

GUIDE = Path(__file__).resolve().parents[1] / "docs" / "QUICKSTART.md"
_BLOCK = re.compile(
    r"(?P<stop><!-- quickstart: stop -->\s*\n)?"
    r"<!-- quickstart: (?P<mode>run(?: background)?|file \S+) -->\s*\n```\w+\n(?P<body>.*?)```",
    re.S,
)


def _blocks() -> list[tuple[bool, str, str]]:
    return [
        (bool(m["stop"]), m["mode"], m["body"])
        for m in _BLOCK.finditer(GUIDE.read_text(encoding="utf-8"))
    ]


def _script() -> str:
    """The marked blocks as one script. ``<!-- quickstart: stop -->`` before a block is
    where the guide tells the reader to stop the background process (Ctrl-C): the script
    stops the one it started, by its PID."""
    lines = ["set -euo pipefail", "trap 'kill $(jobs -p) 2>/dev/null || true' EXIT"]
    for stop, mode, body in _blocks():
        if stop:
            lines.append('kill "$BACKGROUND"; wait "$BACKGROUND" 2>/dev/null || true')
        if mode.startswith("file "):
            # a file the reader saves, written where the guide's shell stands then
            name = mode.removeprefix("file ")
            lines.append(f"cat > {name} <<'QUICKSTART_FILE'\n{body}QUICKSTART_FILE")
        elif mode == "run background":
            lines.append(f"{body.strip()} > background.log 2>&1 &")
            lines.append("BACKGROUND=$!")
            lines.append("sleep 2")
        else:
            lines.append(body)
    return "\n".join(lines)


def test_the_guide_marks_what_it_runs() -> None:
    marked = _blocks()
    assert len(marked) >= 8 and sum(mode == "run background" for _, mode, _ in marked) == 1
    assert sum(stop for stop, _, _ in marked) == 1, "the writer is stopped once, before export"
    assert not any("pip install" in body for _, _, body in marked), "never installs from PyPI"
    assert not any("pkill" in body for _, _, body in marked), "a pattern kill can match the shell"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_the_quickstart_runs_as_written(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    done = subprocess.run(  # noqa: S603 - bash, running this repository's own guide
        ["bash", "-c", _script()],  # noqa: S607
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    out = done.stdout
    assert done.returncode == 0, f"{out[-3000:]}\n{done.stderr[-3000:]}"
    assert "update_customers: ok" in out and "update_orders: refused" in out
    assert "blast_radius" in out, "the trigger's audit row was measured"
    assert "orders_audit" in out, "init named the trigger before the first call"
    assert "warranted  amend" in out, "two of three approved the amendment"
    # the first minute: one tool in shadow mode, then enforced (1.14.0)
    assert "ticket 1 closed" in out and "all closed" in out
    assert "blast_radius: 1 plan, from close_all (1)" in out
    assert "issued      mandate" in out
    assert out.rstrip().endswith("PASS"), "the bundle verified offline"
    assert out.count("update_orders: ok") == 1, "the same call committed under version 2"

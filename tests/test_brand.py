"""The three packages' marks, and the public site's front page (1.15.0)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from fiduciary import brand, report

ROOT = Path(__file__).resolve().parent.parent


def test_each_mark_draws_on_one_grid_in_three_inks() -> None:
    assert brand.NAMES == ("agentgov", "interlock", "fiduciary")
    for name in brand.NAMES:
        drawn = brand.MARKS[name]
        assert set(re.findall(r'class="([a-z])"', drawn)) <= {"i", "o", "f", "a"}
        assert 'class="a"' in drawn and 'class="f"' in drawn  # final, and not final yet
        light = brand.standalone(name)
        dark = brand.standalone(name, theme="dark")
        auto = brand.standalone(name, theme="auto")
        assert 'viewBox="0 0 32 32"' in light and f"<title>{brand.TITLES[name]}</title>" in light
        assert brand.ACCENTS[name][0] in light and brand.ACCENTS[name][1] in dark
        assert brand.ACCENTS[name][0] not in dark
        assert "prefers-color-scheme" in auto and "prefers-color-scheme" not in light
        assert "{id}" not in light
        inline = brand.inline(name, "x1-")
        assert f'class="mk mk-{name}"' in inline and "{id}" not in inline
    with pytest.raises(ValueError):
        brand.standalone("fiduciary", theme="sepia")


def test_two_marks_on_one_page_name_their_masks_apart() -> None:
    one, two = brand.inline("interlock", "a-"), brand.inline("interlock", "b-")
    first = set(re.findall(r'id="([^"]+)"', one))
    second = set(re.findall(r'id="([^"]+)"', two))
    assert first and not first & second


def test_the_repository_logos_are_the_drawing() -> None:
    done = subprocess.run(  # noqa: S603 - this interpreter, on the repository's own script
        [sys.executable, str(ROOT / "scripts" / "make_logos.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout


def _run() -> dict[str, Any]:
    from fiduciary.attest import attest
    from fiduciary.bundle import TrustedKeys, verify_bundle
    from tests.test_attest import BUNDLES, PASSING

    data = (BUNDLES / PASSING["file"]).read_bytes()
    keys = TrustedKeys(PASSING["keys"]) if PASSING["keys"] else None
    found = verify_bundle(data, charter=PASSING["charter"], keys=keys)
    return report.payload({"bundle": attest(data, found, charter=PASSING["charter"])})


def test_the_site_marks_its_page_and_carries_the_marks() -> None:
    data = _run()
    site = report.html(data, site=True)
    console = report.html(data)
    assert '"site":{"home":true}' in site and '"site"' not in console
    for page in (site, console, report.console_html()):
        start = page.index('id="marks">') + len('id="marks">')
        marks = json.loads(page[start : page.index("</script>", start)])
        assert sorted(marks) == sorted(brand.NAMES)
        # each copy of a mark that masks or clips is numbered on the page
        assert "{id}" in marks["interlock"] and "{id}" in marks["fiduciary"]
    assert "<title>Fiduciary: agents change real systems, under signed limits</title>" in site


def test_the_front_page_is_drawn_only_for_the_site() -> None:
    script = (ROOT / "src" / "fiduciary" / "report" / "console.js").read_text()
    assert "var SITE = ROOT.site || null;" in script
    assert 'out.push(["home", "Overview"], ["run", "Interactive demo"]);' in script
    assert 'if (view === "home" && SITE && hasRun())' in script

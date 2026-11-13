"""Draw the site's link-preview picture: ``docs/social-card.png``, 1200 by 630 (1.16.0).

    python scripts/make_social_card.py [path to Chrome or Chromium]

A chat app, a search result or a social post shows this picture beside the site's link
(``og:image`` in the site's head, :mod:`fiduciary.report.site`). It is drawn from the
packages' marks (:mod:`fiduciary.brand`) and the front page's headline, rendered by a headless
Chrome to a PNG, and committed: ``scripts/build_site.py`` copies it beside the page, so the
site build needs no browser. Run this again when the marks or the headline change.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from html import escape
from pathlib import Path

from fiduciary import brand
from fiduciary.report.site import COPY, SITE_URL

OUT = Path(__file__).resolve().parent.parent / "docs" / "social-card.png"
WIDTH, HEIGHT = 1200, 630
CHROME = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
)


def card() -> str:
    """The picture, as a page of exactly 1200 by 630."""
    marks = "".join(
        f'<div class="mark">{brand.inline(name, name[0])}<span>{brand.TITLES[name]}</span></div>'
        for name in brand.NAMES
    )
    ink, accent = brand.INK[0], brand.ACCENTS["fiduciary"][0]
    colors = "".join(f".mk-{name} .a{{fill:{brand.ACCENTS[name][0]}}}" for name in brand.NAMES)
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
html, body {{ margin: 0; width: {WIDTH}px; height: {HEIGHT}px; overflow: hidden; }}
body {{ background: #f6f5f1; color: {ink};
  font-family: system-ui, -apple-system, "Segoe UI", Inter, Roboto, "Helvetica Neue", sans-serif;
  display: flex; flex-direction: column; justify-content: space-between; box-sizing: border-box;
  padding: 72px 80px 64px; border-top: 10px solid {accent}; }}
.top {{ display: flex; align-items: center; gap: 20px; }}
.top svg {{ width: 64px; height: 64px; }}
.top b {{ font-size: 40px; letter-spacing: -0.01em; }}
h1 {{ font-size: 60px; line-height: 1.08; letter-spacing: -0.025em; margin: 0; max-width: 1000px; }}
p {{ font-size: 26px; line-height: 1.4; color: #47453f; margin: 22px 0 0; max-width: 980px; }}
.foot {{ display: flex; align-items: center; justify-content: space-between; }}
.marks {{ display: flex; gap: 30px; }}
.mark {{ display: flex; align-items: center; gap: 10px; font-size: 22px; font-weight: 600; }}
.mark svg {{ width: 34px; height: 34px; }}
.url {{ font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 22px; color: #84817a; }}
.mk .i {{ fill: {ink}; }} .mk .o {{ fill: none; stroke: {ink}; }}
.mk .f {{ fill: {ink}; fill-opacity: .2; }} {colors}
</style></head><body>
<div>
  <div class="top">{brand.inline("fiduciary", "t")}<b>Fiduciary</b></div>
</div>
<div>
  <h1>{escape(COPY["headline"])}</h1>
  <p>Open-source Python. Every change an agent makes is staged, measured and checked
  against limits your team signed, and every decision can be verified offline.</p>
</div>
<div class="foot"><div class="marks">{marks}</div>
  <span class="url">{escape(SITE_URL.removeprefix("https://").rstrip("/"))}</span></div>
</body></html>
"""


def _chrome(given: str | None) -> str:
    for candidate in ([given] if given else []) + list(CHROME):
        found = shutil.which(candidate) or (candidate if Path(candidate).is_file() else None)
        if found:
            return found
    raise SystemExit("no Chrome or Chromium found: pass its path")


def main(argv: list[str]) -> int:
    chrome = _chrome(argv[0] if argv else None)
    with tempfile.TemporaryDirectory(prefix="social-card-") as scratch:
        page = Path(scratch) / "card.html"
        page.write_text(card(), encoding="utf-8")
        shot = Path(scratch) / "card.png"
        subprocess.run(  # noqa: S603 - the browser the caller named, on a page this wrote
            [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--force-device-scale-factor=1",
                f"--window-size={WIDTH},{HEIGHT}",
                f"--screenshot={shot}",
                page.as_uri(),
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
        OUT.write_bytes(shot.read_bytes())
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

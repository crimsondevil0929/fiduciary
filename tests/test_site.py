"""The public site for a reader that runs no script: a crawler, ``curl``, a link preview
(1.16.0)."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from fiduciary import report
from fiduciary.report import site
from tests.test_brand import _run

ROOT = Path(__file__).resolve().parent.parent
CONSOLE = ROOT / "src" / "fiduciary" / "report" / "console.js"


def _with_overview(data: dict[str, Any]) -> dict[str, Any]:
    """A payload with a small overview, as a demo run writes one."""
    row = {
        "key": "fintech",
        "workflow": "Refunds & payments",
        "industry": "Fintech",
        "substrate": "SQLite",
        "class": "T",
        "ran": True,
        "engine": {"refused_plans": 3},
        "after": {"exit_code": 7},
    }
    ladder = [{"class": "T", "name": "transactional", "promise": "All or none.", "where": "SQL."}]
    git = {**row, "key": "coding-agent", "workflow": "Coding agent", "substrate": "Git"}
    overview = {
        "industries": [row, git, {**row, "key": "sre", "workflow": "Cloud SRE", "ran": False}],
        "ladder": ladder,
        "acceptance": {"passed": 4, "cases": 4, "domains": ["a", "b"]},
    }
    return {**data, "overview": overview}


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.inside = 0
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.inside or (tag == "div" and ("id", "app") in attrs):
            self.inside += 1

    def handle_endtag(self, tag: str) -> None:
        if self.inside:
            self.inside -= 1

    def handle_data(self, data: str) -> None:
        if self.inside:
            self.text.append(data)


def _app_text(page: str) -> str:
    parser = _Text()
    parser.feed(page.split('<script type="application/json"', 1)[0])
    return " ".join("".join(parser.text).split())


def test_the_site_page_reads_without_its_script() -> None:
    page = report.html(_with_overview(_run()), site=True)
    text = _app_text(page)
    assert site.COPY["headline"] in text and site.COPY["lede"] in text
    for pkg in site.COPY["packages"]:
        assert pkg["name"] in text and pkg["text"] in text and f"pip install {pkg['dist']}" in text
    assert "@gov.tool" in text and "def close_all(db: fiduciary.Sql) -> str:" in text
    # the counts the script fills in, counted as it counts them: two workflows ran
    assert "2 workflows" in text and "on 2 kinds of system, in one run" in text
    assert "6plans refused" in text and "4bundles verified offline" in text
    assert "Refunds & payments" in text and "Coding agent" in text
    assert "Cloud SRE" not in text  # a workflow the run did not run is not linked
    # what a link preview and a search engine read
    for tag in (
        f'<link rel="canonical" href="{site.SITE_URL}">',
        '<meta property="og:type" content="website">',
        f'<meta property="og:image" content="{site.SITE_URL}{site.CARD}">',
        '<meta name="twitter:card" content="summary_large_image">',
        '<link rel="icon" type="image/svg+xml" href="favicon.svg">',
    ):
        assert tag in page, tag
    found = re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)
    assert found is not None
    graph = json.loads(found.group(1))["@graph"]
    kinds = sorted(node["@type"] for node in graph)
    assert kinds == ["SoftwareApplication", *["SoftwareSourceCode"] * 3, "WebSite"]


def test_only_the_site_carries_the_head_and_the_front_page() -> None:
    data = _run()
    for page in (report.html(data), report.console_html()):
        assert "<!--HEAD-->" not in page and "<!--APP-->" not in page
        assert "og:image" not in page and "application/ld+json" not in page
        assert "<noscript>" in page and 'class="lp-hero"' not in page


def test_the_script_draws_the_front_page_from_the_same_words() -> None:
    script = CONSOLE.read_text(encoding="utf-8")
    assert "var C = (SITE && SITE.copy) || {};" in script
    # no sentence of the front page is written twice
    for text in (site.COPY["headline"], site.COPY["lede"], site.COPY["closing"]["title"]):
        assert text not in script
    for pkg in site.COPY["packages"]:
        assert pkg["text"] not in script
    page = report.html(_with_overview(_run()), site=True)
    start = page.index('id="payload">') + len('id="payload">')
    payload = json.loads(page[start : page.index("</script>", start)])
    assert payload["site"] == {"home": True, "copy": site.COPY}


def test_the_files_beside_the_page() -> None:
    data = _with_overview(_run())
    sitemap = site.sitemap(data)
    assert f"<loc>{site.SITE_URL}</loc>" in sitemap and sitemap.count("<url>") == 1
    assert f"Sitemap: {site.SITE_URL}sitemap.xml" in site.robots()
    assert site.favicon().startswith("<svg") and "prefers-color-scheme:dark" in site.favicon()
    card = (ROOT / "docs" / site.CARD).read_bytes()
    assert card[:8] == b"\x89PNG\r\n\x1a\n"
    assert card[16:24] == (1200).to_bytes(4, "big") + (630).to_bytes(4, "big")


def test_how_it_works_says_the_five_steps_as_the_front_page_does() -> None:
    """The workbench's "How it works" view draws the loop's steps itself, outside the site:
    each step's text is the front page's, word for word (1.16.1)."""
    script = CONSOLE.read_text(encoding="utf-8")
    for title, text in site.COPY["loop"]["steps"]:
        assert f'"{title}", "{text}"' in script, title

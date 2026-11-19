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
    # 1.19.0: the hero is a protect run on an MCP server, and three doors follow it; 1.20.0:
    # two panels, each call with its verdict, the password read before the fetch that carries it
    assert "fiduciary protect --mcp-config .mcp.json --enforce --apply" in text
    assert "Not sent: it carries a credential this session read." in text
    assert "Held for your approval" in text and "Refused · a credential in the URL" in text
    assert "flagged: the server marks it read-only" in text
    assert text.index("Sup3rS3cretPw4@db.internal") < text.index("paste.example/?q=Sup3rS3cretPw4")
    assert "Held for the principals' approval" not in text and "<html>" not in text
    assert site.COPY["hero"]["steps"][0] in text and site.COPY["hero"]["steps"][1] in text
    assert "UPDATE tickets" not in text and "close_all" not in text
    for door in site.COPY["doors"]["items"]:
        assert door["tab"] in text and door["text"] in text
    assert "@gov.tool" in text and "def refund(api: fiduciary.Rest" in text
    strip = text[text.index(site.COPY["systems"]) :]
    shown = [n for n, _, _ in site.COPY["systems_order"] if n in strip]
    assert shown[0] == "MCP servers" and shown == sorted(
        shown, key=[n for n, _, _ in site.COPY["systems_order"]].index
    )
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
    assert f"<loc>{site.SITE_URL}</loc>" in sitemap and sitemap.count("<url>") == 10  # 1.22.0
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


def test_each_guide_is_a_page_of_its_own() -> None:
    """1.19.0: the guides a search engine can index, each with its own title, description,
    canonical address and structured data, and the sitemap listing them all."""
    from fiduciary.report import guides

    titles, descriptions = set(), set()
    for guide in guides.GUIDES:
        page = guides.page(guide)
        url = f"{site.SITE_URL}{guide['slug']}/"
        assert f'<link rel="canonical" href="{url}">' in page
        title = re.search(r"<title>(.*?)</title>", page)
        assert title is not None and title.group(1).endswith(" | Fiduciary")
        titles.add(title.group(1))
        descriptions.add(str(guide["description"]))
        assert 70 <= len(str(guide["description"])) <= 170, guide["slug"]
        assert page.count("<h1>") == 1 and "<script src" not in page
        found = re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)
        assert found is not None
        graph = json.loads(found.group(1))["@graph"]
        kinds = {node["@type"] for node in graph}
        assert {"TechArticle", "BreadcrumbList"} <= kinds
        assert ("HowTo" in kinds) == bool(guide["steps"])
        assert ("FAQPage" in kinds) == bool(guide["faq"])
        others = [g for g in guides.GUIDES if g is not guide]
        assert all(f'href="../{g["slug"]}/"' in page for g in others)
    assert len(titles) == len(descriptions) == len(guides.GUIDES) == 9
    mapped = site.sitemap({})
    assert mapped.count("<loc>") == 10 and f"<loc>{site.SITE_URL}chatbots/</loc>" in mapped
    footer = site.COPY["footer"]["guides"]
    assert [href for _, href in footer] == [f"{g['slug']}/" for g in guides.GUIDES]


def test_the_hero_is_what_a_run_records(tmp_path: Path) -> None:
    """1.20.0: ``scripts/record_hero.py`` run again records what the front page shows."""
    import importlib.util

    script = Path(__file__).resolve().parent.parent / "scripts" / "record_hero.py"
    spec = importlib.util.spec_from_file_location("record_hero", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main(tmp_path / "hero.json")
    fresh = json.loads((tmp_path / "hero.json").read_text())
    shown = {k: site.COPY["hero"][k] for k in fresh}
    assert fresh == shown, "run scripts/record_hero.py and read the diff"
    assert [c["verdict"] for c in fresh["calls"]] == ["read", "held", "refused", "allowed"]


def test_the_benchmarks_guide_shows_what_was_recorded(tmp_path: Path) -> None:
    """1.21.0: ``scripts/record_benchmarks.py`` measures local overhead and counts the 8
    workflows and 3 replays into ``report/benchmarks.json``, which ``/benchmarks/`` draws."""
    import importlib.util

    from fiduciary.report import guides

    script = ROOT / "scripts" / "record_benchmarks.py"
    spec = importlib.util.spec_from_file_location("record_benchmarks", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = tmp_path / "benchmarks.json"
    assert module.main(out, samples=3) == 0
    fresh = json.loads(out.read_text(encoding="utf-8"))
    recorded = json.loads(
        (ROOT / "src" / "fiduciary" / "report" / "benchmarks.json").read_text(encoding="utf-8")
    )
    assert fresh["versions"].keys() == {"agentgov", "interlock", "fiduciary"}
    assert [r["operation"] for r in fresh["latency"]] == [
        r["operation"] for r in recorded["latency"]
    ]
    assert fresh["workflows"] == recorded["workflows"]
    assert fresh["replays"] == recorded["replays"]
    assert len(fresh["workflows"]) == 8 and len(fresh["replays"]) == 3

    guide = next(g for g in guides.GUIDES if g["slug"] == "benchmarks")
    page = guides.page(guide)
    assert "SHA256SUMS" in page and "eight characters" in page and "bytes_sent" in page
    assert "Workspace agent" in page and "Clinical EHR" in page
    assert "A put-back on a REST or FHIR system fails twice" in page
    assert (
        "recorded with scripts/record_benchmarks.py on interlock-fiduciary "
        f"{recorded['versions']['fiduciary']}"
    ) in page
    assert "AgentGov hold and capture" in page


def test_the_chatbots_guide_and_its_check_page() -> None:
    """1.22.0: ``/chatbots/`` shows the law table and the stand-ins' recorded verdicts and runs
    no script; ``chatbots/check.html`` runs ``conform.js`` and is kept out of the index."""
    from fiduciary.report import guides

    guide = next(g for g in guides.GUIDES if g["slug"] == "chatbots")
    page = guides.page(guide)
    assert "<script src" not in page and 'href="check.html"' in page
    assert "Laws of 2026, chapter 168" in page and "Statutes of 2026, chapter 190" in page
    assert "Yes, I&#x27;m a licensed therapist" in page and "steady" in page
    assert "Regulation (EU) 2024/1689" in page
    check = guides.check_page()
    assert '<script src="../conform.js"></script>' in check and "noindex" in check

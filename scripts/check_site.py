"""Check a site build_site.py wrote: every bundle it links is the one the page recorded,
verifies offline as recorded, and passes the page's own browser checks.

    python scripts/check_site.py site

For each bundle the page names (each industry's before and after, and SaaS's sealed
customer bundles): its SHA-256 is the one the page shows; ``verify_bundle``, offline,
against the ``charter.digest`` and ``pinned-keys.json`` beside it, exits as the page says;
and ``report/verify.js``, run by Node with those pinned keys, fails no check on a bundle
that verifies.

And what a reader that runs no script gets (1.16.0): the front page is in the HTML (the
headline, the three packages, the ``@gov.tool`` example and every workflow the run
recorded); the head has a description, the canonical address, Open Graph and Twitter card
tags and structured data that parses; ``favicon.svg``, ``social-card.png`` (1200 by 630),
``sitemap.xml`` and ``robots.txt`` are there; and ``SHA256SUMS`` lists every file.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from fiduciary.bundle import TrustedKeys, verify_bundle
from fiduciary.report import site as public

HARNESS = Path(__file__).resolve().parent.parent / "tests" / "js" / "verify_vectors.cjs"


def _payload(index: Path) -> dict[str, Any]:
    found = re.search(
        r'<script type="application/json" id="payload">(.*?)</script>',
        index.read_text(encoding="utf-8"),
        re.S,
    )
    if found is None:
        raise SystemExit("index.html carries no payload")
    data: dict[str, Any] = json.loads(html.unescape(found.group(1)))
    return data


def _expected(site: Path) -> list[dict[str, Any]]:
    """Each bundle the page links, with the SHA-256 and exit code it shows."""
    data = _payload(site / "index.html")
    out = []
    for page in [data, *data.get("domains", [])]:
        domain = (page.get("demo") or {}).get("domain")
        folder = domain["key"] if domain and page is not data else ""
        for name, att in page["attestations"].items():
            out.append(
                {
                    "file": f"{folder}/{name}.fidb".lstrip("/"),
                    "sha256": att["bundle"]["sha256"],
                    "exit": att["verdict"]["exit_code"],
                    "folder": folder,
                }
            )
        for act in (page.get("demo") or {}).get("acts", []):
            for customer in act.get("customers", []) if act.get("key") == "trust-center" else []:
                out.append(
                    {
                        "file": f"{folder}/{customer['file']}".lstrip("/"),
                        "sha256": customer["sha256"],
                        "exit": customer["exit_code"],
                        "folder": folder,
                    }
                )
    return out


class _Page(HTMLParser):
    """The text inside ``<div id="app">``, the head's meta tags and links, and its JSON-LD."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.app: list[str] = []
        self.meta: dict[str, str] = {}
        self.links: dict[str, str] = {}
        self.structured: list[str] = []
        self._depth = 0
        self._ld = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "div" and a.get("id") == "app" and not self._depth:
            self._depth = 1
        elif self._depth and tag not in ("br", "img", "input", "meta", "link"):
            self._depth += 1
        if tag == "meta" and ("name" in a or "property" in a):
            self.meta[a.get("name") or a["property"]] = a.get("content", "")
        if tag == "link" and "rel" in a:
            self.links[a["rel"]] = a.get("href", "")
        self._ld = tag == "script" and a.get("type") == "application/ld+json"

    def handle_endtag(self, tag: str) -> None:
        if self._depth and tag not in ("br", "img", "input", "meta", "link"):
            self._depth -= 1
        self._ld = False

    def handle_data(self, data: str) -> None:
        if self._depth:
            self.app.append(data)
        if self._ld:
            self.structured.append(data)


def _readable(site: Path, data: dict[str, Any]) -> list[str]:
    """What a crawler, ``curl`` or a link preview reads, with no script run."""
    problems: list[str] = []
    page = _Page()
    page.feed((site / "index.html").read_text(encoding="utf-8"))
    text = " ".join("".join(page.app).split())
    wanted = [public.COPY["headline"], "@gov.tool", "def close_all(db: fiduciary.Sql) -> str:"]
    for pkg in public.COPY["packages"]:
        wanted += [pkg["name"], pkg["role"], f"pip install {pkg['dist']}"]
    for row in (data.get("overview") or {}).get("industries", []):
        if row.get("ran"):
            wanted.append(row.get("workflow") or row["industry"])
    problems += [
        f"index.html: the HTML before any script runs lacks {w!r}" for w in wanted if w not in text
    ]
    for name in ("description", "og:title", "og:description", "og:url", "og:image", "twitter:card"):
        if not page.meta.get(name):
            problems.append(f"index.html: no {name} meta tag")
    if page.meta.get("og:url") != public.SITE_URL or page.links.get("canonical") != public.SITE_URL:
        problems.append("index.html: og:url and the canonical link are not the site's address")
    if page.meta.get("og:image") != public.SITE_URL + public.CARD:
        problems.append("index.html: og:image is not the card beside the page")
    if page.links.get("icon") != "favicon.svg":
        problems.append("index.html: no favicon link")
    try:
        graph = json.loads("".join(page.structured))["@graph"]
        types = sorted(node["@type"] for node in graph)
    except (ValueError, KeyError, TypeError):
        problems.append("index.html: the structured data does not parse")
    else:
        if types.count("SoftwareSourceCode") != 3 or "SoftwareApplication" not in types:
            problems.append(f"index.html: the structured data has {types}")
    card = (site / public.CARD).read_bytes()
    if card[:8] != b"\x89PNG\r\n\x1a\n" or card[16:24] != (1200).to_bytes(4, "big") + (
        630
    ).to_bytes(4, "big"):
        problems.append(f"{public.CARD}: not a 1200 by 630 PNG")
    ElementTree.fromstring((site / "favicon.svg").read_bytes())  # noqa: S314 - built here
    found = [
        loc.text
        for loc in ElementTree.fromstring((site / "sitemap.xml").read_bytes()).iter(  # noqa: S314
            "{http://www.sitemaps.org/schemas/sitemap/0.9}loc"
        )
    ]
    if found != [public.SITE_URL]:
        problems.append(f"sitemap.xml lists {found}")
    if f"Sitemap: {public.SITE_URL}sitemap.xml" not in (site / "robots.txt").read_text(
        encoding="utf-8"
    ):
        problems.append("robots.txt does not point at the sitemap")
    listed = {
        line.split("  ", 1)[1]
        for line in (site / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        if "  " in line
    }
    files = {
        p.relative_to(site).as_posix()
        for p in site.rglob("*")
        if p.is_file() and p.name not in (".nojekyll", "SHA256SUMS")
    }
    if files != listed:
        problems.append(
            f"SHA256SUMS does not list exactly the site's files: {sorted(files ^ listed)}"
        )
    return problems


def check(site: Path) -> list[str]:
    problems: list[str] = _readable(site, _payload(site / "index.html"))
    browser: list[dict[str, Any]] = []
    expected = _expected(site)
    for item in expected:
        path = site / item["file"]
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            problems.append(f"{item['file']}: not the bytes the page shows")
            continue
        folder = site / item["folder"]
        charter = (folder / "charter.digest").read_text(encoding="utf-8").strip()
        keys_raw = (folder / "pinned-keys.json").read_bytes()
        found = verify_bundle(data, charter=charter, keys=TrustedKeys.loads(keys_raw))
        if found.exit_code != item["exit"]:
            problems.append(
                f"{item['file']}: exits {found.exit_code}, the page says {item['exit']}"
            )
        if item["exit"] == 0:
            browser.append(
                {"name": item["file"], "file": item["file"], "keys": json.loads(keys_raw)}
            )
    node = shutil.which("node")
    if node is None:
        problems.append("node is not installed: the page's browser checks were not run")
    elif browser:
        with tempfile.NamedTemporaryFile("w", suffix=".json", dir=site, delete=False) as listing:
            json.dump(browser, listing)
        try:
            done = subprocess.run(  # noqa: S603 - node and the repository's own harness
                [node, str(HARNESS), listing.name],
                capture_output=True,
                text=True,
                check=False,
                timeout=600,
            )
        finally:
            Path(listing.name).unlink()
        if done.returncode != 0:
            problems.append(f"the browser checks did not run: {done.stderr.strip()[:400]}")
        else:
            for name, result in json.loads(done.stdout).items():
                if result["failing"]:
                    problems.append(f"{name}: the page's browser checks fail: {result['failing']}")
    print(f"{len(expected)} bundles checked; {len(browser)} through the page's browser checks")
    return problems


def main(argv: list[str]) -> int:
    problems = check(Path(argv[0]))
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

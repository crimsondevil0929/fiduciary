"""Check a site build_site.py wrote: every bundle it links is the one the page recorded,
verifies offline as recorded, and passes the page's own browser checks.

    python scripts/check_site.py site

For each bundle the page names (each industry's before and after, and SaaS's sealed
customer bundles): its SHA-256 is the one the page shows; ``verify_bundle``, offline,
against the ``charter.digest`` and ``pinned-keys.json`` beside it, exits as the page says;
and ``report/verify.js``, run by Node with those pinned keys, fails no check on a bundle
that verifies.
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
from pathlib import Path
from typing import Any

from fiduciary.bundle import TrustedKeys, verify_bundle

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


def check(site: Path) -> list[str]:
    problems: list[str] = []
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

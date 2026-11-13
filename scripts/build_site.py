"""Build the public site: the console as a static page, and the demo's bundles beside it.

    python scripts/build_site.py site

Runs ``fiduciary demo --domain all --replay`` in a temporary directory: every industry
on the installed engine, with claude-opus-5-5's recorded turns choosing the calls in each
signature scenario (the recording that ships with fiduciary), offline and at no cost.
Then it writes into the output directory:

- ``index.html``: the console, rendered from the run, with no console behind it. It opens on
  a front page (1.15.0) that says what the three packages do, and holds the workbench of
  what the run recorded and a verifier that checks a bundle in the browser
  (``report/verify.js``).
- each industry's ``before.fidb`` and ``after.fidb`` (and SaaS's sealed customer bundles),
  at the paths the page links them by, with the ``pinned-keys.json`` and
  ``charter.digest`` to verify them with ``fiduciary verify-bundle``;
- ``SHA256SUMS``: the SHA-256 of every other file, so a reader can pin the page and the
  bundles to this build (``sha256sum -c SHA256SUMS``).

Nothing else of the deployment (keys, databases, configuration) is copied.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

from fiduciary import cli, report

PUBLIC = ("before.fidb", "after.fidb", "pinned-keys.json", "charter.digest", "report.md")


def build(out: Path) -> list[Path]:
    """The site's files, written into ``out`` (emptied first)."""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="fiduciary-site-") as scratch:
        run = Path(scratch) / "run"
        status = cli.main(["demo", "--domain", "all", "--replay", "--out", str(run), "--quiet"])
        if status != 0:
            raise SystemExit(f"fiduciary demo exited {status}")
        data = json.loads((run / "demo.json").read_text(encoding="utf-8"))
        (out / "index.html").write_text(report.html(data, site=True), encoding="utf-8")
        for directory in [run, *sorted(p for p in run.iterdir() if (p / "demo.json").is_file())]:
            target = out / directory.relative_to(run)
            target.mkdir(parents=True, exist_ok=True)
            names = [*PUBLIC, *sorted(p.name for p in directory.glob("customer-*.fidb"))]
            for name in names:
                if (directory / name).is_file():
                    shutil.copyfile(directory / name, target / name)
    (out / ".nojekyll").write_text("")
    files = sorted(p for p in out.rglob("*") if p.is_file() and p.name not in (".nojekyll",))
    lines = [
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(out).as_posix()}"
        for p in files
    ]
    (out / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return files


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    files = build(Path(argv[0]))
    print(f"wrote {len(files)} files and SHA256SUMS into {argv[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

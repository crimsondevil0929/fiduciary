"""A conformance run's evidence (1.22.0): a signed FIDU1 ``conformance`` report and the
files it is judged from, written into one folder, and checked again offline.

The folder:

- ``report.json``: the report, a FIDU1 document of type ``conformance`` in canonical bytes,
  signed with the domain ``FIDU1/conformance/v1\\n``. It holds the digests of the recording,
  the suite and the law catalog, each transcript's digest, every check's outcome and every
  clause's verdict.
- ``recording.json``: what the endpoint said, turn by turn (``fiduciary conform run
  --replay`` runs it again at no cost).
- ``suite.json`` and ``laws.json``: the checks and the clauses the run was judged against.
- ``key.pub``: the signing key's public half. Pin it from somewhere else to know who signed.
- ``summary.md``: the report in plain words for the operator and their counsel. It is
  written from the signed files, and is not itself signed.

:func:`verify` checks the signature, every digest, and then judges the transcripts again with
the folder's own suite and catalog: a report whose outcomes or verdicts differ from what the
transcripts show fails, however it is signed.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final

from agentgov.receipts.signing import Ed25519Signer, Signer

from fiduciary import fidu1
from fiduciary.conform import judge
from fiduciary.exceptions import MalformedError, SignatureError

__all__ = [
    "KIND",
    "Verification",
    "load_key",
    "new_key",
    "report",
    "summary",
    "verify",
    "write",
]

KIND: Final = "conformance"
FILES: Final = ("report.json", "recording.json", "suite.json", "laws.json")


def new_key(path: Path) -> Ed25519Signer:
    """A fresh Ed25519 key, its seed written in hex to ``path``, readable by its owner only.
    Never overwrites a file."""
    seed = secrets.token_bytes(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(seed.hex() + "\n")
    return Ed25519Signer(seed)


def load_key(path: Path) -> Ed25519Signer:
    """The key :func:`new_key` wrote (an Ed25519 seed in hex, as Interlock's key files are).

    :raises ValueError: On a file that holds no such seed.
    """
    text = path.read_text(encoding="ascii").strip()
    try:
        seed = bytes.fromhex(text)
    except ValueError:
        seed = b""
    if len(seed) != 32:
        raise ValueError(f"{path} holds no Ed25519 seed (64 hex characters)")
    return Ed25519Signer(seed)


def _digest(value: object) -> str:
    return hashlib.sha256(fidu1.canonical(value)).hexdigest()


def report(
    recorded: Mapping[str, Any],
    suite: Mapping[str, Any],
    catalog: Mapping[str, Any],
    jurisdictions: Sequence[str],
    on: date,
    mode: str,
    signer: Signer,
    *,
    issued_at: datetime | None = None,
) -> dict[str, Any]:
    """The signed report of a run."""
    results = judge.checks(suite, recorded["transcripts"])
    target = recorded["target"]
    body = {
        "v": fidu1.VERSION,
        "type": KIND,
        "id": str(uuid.uuid4()),
        "issued_at": fidu1.format_instant(issued_at or datetime.now(UTC)),
        "target": {
            "label": target["label"],
            "kind": target["kind"],
            "model": target["model"],
            "endpoint": target["endpoint"],
            "hooks": recorded["hooks"],
        },
        "mode": mode,
        "recording": _digest(recorded),
        "suite": {"name": suite["name"], "version": suite["version"], "digest": _digest(suite)},
        "laws": {
            "digest": _digest(catalog),
            "on": on.isoformat(),
            "jurisdictions": list(jurisdictions),
        },
        "transcripts": [
            {"scenario": t["scenario"], "digest": _digest(t)} for t in recorded["transcripts"]
        ],
        "checks": results,
        "clauses": judge.clauses(suite, catalog, results, jurisdictions, on),
    }
    signature = fidu1.sign(KIND, body, signer)
    return {**body, "sig": signature.to_json()}


def write(
    out: Path,
    signed: Mapping[str, Any],
    recorded: Mapping[str, Any],
    suite: Mapping[str, Any],
    catalog: Mapping[str, Any],
    signer: Ed25519Signer,
) -> None:
    """Write the evidence folder (``out`` is created; it must not hold a report already)."""
    out.mkdir(parents=True, exist_ok=True)
    if (out / "report.json").exists():
        raise FileExistsError(f"{out} holds a report already: give another --out")
    for name, value in (
        ("recording.json", recorded),
        ("suite.json", suite),
        ("laws.json", catalog),
        ("report.json", signed),
    ):
        (out / name).write_bytes(fidu1.canonical(value))
    (out / "key.pub").write_text(signer.public_key().spec() + "\n", encoding="ascii")
    (out / "summary.md").write_text(summary(signed, recorded, catalog), encoding="utf-8")


@dataclass
class Verification:
    """What :func:`verify` found."""

    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.problems


def verify(folder: Path, key: str | None = None) -> Verification:
    """Check an evidence folder: the signature (under ``key``, an ``ed25519:<hex>`` pinned
    from elsewhere; else the folder's ``key.pub``), every digest, and every outcome and
    verdict judged again from the transcripts."""
    found = Verification()
    try:
        loaded = {name: fidu1.parse((folder / name).read_bytes()) for name in FILES}
    except FileNotFoundError as exc:
        found.problems.append(f"{Path(exc.filename).name} is missing")
        return found
    except MalformedError as exc:
        found.problems.append(f"a file is not strict JSON: {exc}")
        return found
    doc, recorded, suite, catalog = (loaded[n] for n in FILES)
    found.report = doc
    if not isinstance(doc, dict) or doc.get("type") != KIND or doc.get("v") != fidu1.VERSION:
        found.problems.append("report.json is not a FIDU1 conformance report")
        return found
    if (folder / "report.json").read_bytes() != fidu1.canonical(doc):
        found.problems.append("report.json is not in canonical form")
    if key is None:
        try:
            key = (folder / "key.pub").read_text(encoding="ascii").strip()
        except FileNotFoundError:
            found.problems.append("no key: pin one with --key ed25519:<hex>")
            return found
        found.notes.append("the key is the folder's own: pin it with --key to know who signed")
    try:
        body = {k: v for k, v in doc.items() if k != "sig"}
        fidu1.verify(KIND, body, fidu1.Signature.from_json(doc.get("sig")), fidu1.key_of(key))
    except (MalformedError, SignatureError) as exc:
        found.problems.append(f"signature: {exc}")
    try:
        _again(found, doc, recorded, suite, catalog)
    except (KeyError, TypeError, ValueError) as exc:
        found.problems.append(f"the report cannot be read: {exc!r}")
    return found


def _again(
    found: Verification,
    doc: Mapping[str, Any],
    recorded: Mapping[str, Any],
    suite: Mapping[str, Any],
    catalog: Mapping[str, Any],
) -> None:
    for name, value, want in (
        ("recording.json", recorded, doc["recording"]),
        ("suite.json", suite, doc["suite"]["digest"]),
        ("laws.json", catalog, doc["laws"]["digest"]),
    ):
        if _digest(value) != want:
            found.problems.append(f"{name} is not the file the report was made from")
    listed = [{"scenario": t["scenario"], "digest": _digest(t)} for t in recorded["transcripts"]]
    if listed != doc["transcripts"]:
        found.problems.append("the transcripts are not the ones the report lists")
    if recorded["hooks"] != doc["target"]["hooks"]:
        found.problems.append("the report and the recording disagree on the test clock")
    results = judge.checks(suite, recorded["transcripts"])
    if results != doc["checks"]:
        found.problems.append("the checks' outcomes are not what the transcripts show")
    on = date.fromisoformat(doc["laws"]["on"])
    verdicts = judge.clauses(suite, catalog, results, doc["laws"]["jurisdictions"], on)
    if verdicts != doc["clauses"]:
        found.problems.append("the clauses' verdicts are not what the checks give")


_WORD: Final = {"pass": "met", "fail": "missed", "open": "open: needs a look"}


def summary(
    signed: Mapping[str, Any], recorded: Mapping[str, Any], catalog: Mapping[str, Any]
) -> str:
    """The report in plain words, as Markdown."""
    clauses = {c["id"]: c for c in catalog["clauses"]}
    laws = {law["key"]: law for law in catalog["laws"]}
    target = signed["target"]
    on = signed["laws"]["on"]
    lines = [
        "# Chatbot conformance run",
        "",
        f"- Endpoint: {target['label']} ({target['kind']}"
        + (f", model {target['model']}" if target["model"] else "")
        + ")",
        f"- Run: {signed['mode']}, {signed['issued_at']}, report {signed['id']}",
        f"- Laws as in force on {on}: "
        + ", ".join(catalog["jurisdictions"][j] for j in signed["laws"]["jurisdictions"]),
        "- Test clock: "
        + ("on: the three-hour checks ran" if target["hooks"] else "off: time checks untested"),
        "",
        "Each check is deterministic: it looks for set phrases in the replies, and no model "
        "judges them. A clause the chat leaves open needs a person to look, usually at the app's "
        "interface. This is evidence for the operator and their counsel, not legal advice and not "
        "a clinical finding.",
        "",
        "## Clauses",
        "",
        "| Clause | Law | In force | Result |",
        "|---|---|---|---|",
    ]
    for verdict in signed["clauses"]:
        clause = clauses[verdict["id"]]
        law = laws[clause["law"]]
        span = clause["start"] + (f" to {clause['end']}" if clause["end"] else "")
        state = "yes" if verdict["in_force"] else f"no ({span})"
        lines.append(
            f"| {clause['cite']}: {clause['duty']} | {law['title']} | {state} "
            f"| {_WORD[verdict['verdict']]} |"
        )
    turns = {t["scenario"]: t["turns"] for t in recorded["transcripts"]}
    missed = [c for c in signed["checks"] if c["outcome"] != "pass"]
    if missed:
        lines += ["", "## Checks not passed", ""]
        for check in missed:
            line = f"- {check['scenario']} / {check['check']}: {check['outcome']}"
            if check["detail"]:
                line += f" ({check['detail']})"
            lines.append(line)
            if check["turn"] is not None:
                turn = turns[check["scenario"]][check["turn"] - 1]
                lines.append(f"  - user: {turn['user']}")
                lines.append(f"  - reply: {turn['reply']}")
    lines += ["", "## Not shown by a chat run", ""]
    for jurisdiction in signed["laws"]["jurisdictions"]:
        for item in catalog["not_in_chat"].get(jurisdiction, []):
            lines.append(f"- {catalog['jurisdictions'][jurisdiction]}: {item}")
    lines += [
        "",
        "## Check it yourself",
        "",
        "```",
        "fiduciary conform verify <this folder> --key " + "$(cat key.pub)",
        "```",
        "",
        "Pin the key from somewhere other than this folder to know who signed.",
        "",
    ]
    return "\n".join(lines)

"""Record the golden bundles in ``vectors/fidu1/bundles/``.

Run from the repository root: ``python scripts/make_bundles.py``. It runs the
bundle tests' scenarios on SQLite (``tests/test_bundle.py``,
``tests/test_bundle_checks.py``, ``tests/test_sealed_bundles.py``), keeps the
bundle each one verified, and derives the tampered ones from a valid bundle.
``--add NAME ...`` records only those scenarios, and the bundles derived from
them, and keeps every other bundle as it was recorded. ``index.json`` records, for
each, what it is verified with (the pinned charter digest, the auditor's
keys, a witness, the skew) and the verdict: the exit code, the checks that
fail, and every finding's text.

Unlike the FIDU1 vectors, a bundle is not reproducible from a seed: its
records are stamped by the wall clock and held to the ledger's own (V12),
and its lineages are random UUIDs. So the files are recorded, not rebuilt:
``tests/test_bundle_vectors.py`` verifies each to its recorded verdict.
Another implementation of ``verify-bundle`` that reaches every verdict here
checks bundles as this one does.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fiduciary import fidu1  # noqa: E402
from fiduciary.bundle import TrustedKeys, verify_bundle  # noqa: E402
from fiduciary.bundle.format import pack  # noqa: E402

OUT = ROOT / "vectors" / "fidu1" / "bundles"

SCENARIOS = {
    # name: (test, what it shows)
    "valid-lineage-at-work": (
        "test_a_lineage_at_work_exports_a_bundle_that_verifies",
        "a refund, a rule-repaired refusal, a passthrough lookup and two model calls",
    ),
    "valid-phase-4-lifecycle": (
        "test_the_phase_4_lifecycle_exports_a_bundle_that_verifies",
        "recovery, a quote, an extension granted by two approvals, billing moved back",
    ),
    "valid-held-release": (
        "test_a_held_message_released_by_warrant_verifies",
        "a held message released by warrant: its reset's anchor, its operator intents",
    ),
    "v2-wrong-pinned-charter": (
        "test_the_wrong_pinned_charter_is_named",
        "a charter digest the bundle does not hold",
    ),
    "v3-mandate-one-approval-short": (
        "test_a_mandate_issued_one_approval_short_is_named",
        "a quorum mandate issued on one approval of two",
    ),
    "v3-child-broader-than-parent": (
        "test_a_child_broader_than_its_parent_is_named",
        "a child whose envelope is past what its parent delegates",
    ),
    "v4-other-deployment": (
        "test_a_deployment_other_than_the_one_bound_is_named",
        "a deployment whose directive text the bound version does not compile from",
    ),
    "v5-other-receipt-key": (
        "test_a_pinned_key_the_bundle_was_not_signed_with_is_named[sqlite-receipts-V5]",
        "receipts verified under a key that did not sign them",
    ),
    "v6-other-ledger-key": (
        "test_a_pinned_key_the_bundle_was_not_signed_with_is_named[sqlite-ledger-V6]",
        "a ledger checkpoint verified under a key that did not sign it",
    ),
    "v6-witness-never-cosigned": (
        "test_a_witness_that_never_cosigned_is_named",
        "a witness required, and no cosignature of it",
    ),
    "v7-unmandated-delegation": (
        "test_a_delegation_no_binding_records_is_ambient",
        "a delegation from the subject that no binding records",
    ),
    "v7-raw-reset": (
        "test_a_reset_no_warrant_names_is_ambient",
        "the subject's halt lifted with no warrant",
    ),
    "v7-manual-release": (
        "test_an_operator_release_under_no_warrant_is_ambient",
        "an operator releases a held message under no warrant",
    ),
    "v7-break-glass-release": (
        "test_a_break_glass_release_is_ambient_and_labeled",
        "the same, by a key the charter declares break-glass: labeled",
    ),
    "v7-unmandated-plan": (
        "test_a_plan_no_pinned_tool_built_is_ambient",
        "a plan committed on the lineage's engine that no pinned tool built",
    ),
    "v8-warrant-one-approval-short": (
        "test_a_warrant_one_approval_short_is_named",
        "a release warrant formed on one approval of two",
    ),
    "v8-false-rule-predicate": (
        "test_a_rule_whose_predicate_is_false_is_named",
        "an autonomous warrant for a ticket its rule does not admit",
    ),
    "v8-executed-after-not-after": (
        "test_an_execution_after_the_warrant_expired_is_named",
        "a warrant executed after its not_after",
    ),
    "v8-head-moved": (
        "test_a_step_at_a_head_the_ticket_did_not_observe_is_named",
        "a step acting on a message whose head moved after the ticket (U-4)",
    ),
    "v9-other-service-key": (
        "test_a_service_key_that_signed_nothing_in_the_log_is_named",
        "a log verified under a service key that signed none of it",
    ),
    "v9-manifest-signed-by-another": (
        "test_a_manifest_signed_by_another_key_is_named",
        "a manifest signed by a key other than the service's",
    ),
    "v10-unanchored-record": (
        "test_an_unanchored_record_is_named",
        "a record in range that no ledger anchor names",
    ),
    "v11-commit-after-revocation": (
        "test_a_commit_after_revocation_is_named",
        "a plan committed on the lineage's chain after its revocation's trip",
    ),
    "v12-records-ahead-of-the-ledger": (
        "test_records_ahead_of_the_ledger_are_named",
        "records stamped five minutes ahead of their anchors",
    ),
    "valid-sealed-child": (
        "test_a_sealed_bundle_carries_its_lineage_whole_and_withholds_the_rest",
        "fidb/2: one child's lineage whole, its sibling's and its parent's sessions withheld",
    ),
}


def _record(captured: Path, names: set[str]) -> dict[str, Any]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("FIDUCIARY_")}
    env["FIDUCIARY_WRITE_BUNDLES"] = str(captured)
    tests = sorted({SCENARIOS[n][0].split("[")[0] for n in names})
    subprocess.run(  # noqa: S603 - this interpreter, running this repository's tests
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:randomly",
            "tests/test_bundle.py",
            "tests/test_bundle_checks.py",
            "tests/test_sealed_bundles.py",
            "-k",
            " or ".join(tests),
        ],
        cwd=ROOT,
        env=env,
        check=True,
    )
    return {name: _captured(captured, SCENARIOS[name][0]) for name in sorted(names)}


def _captured(captured: Path, test: str) -> dict[str, Any]:
    stem = test if "[" in test else f"{test}[sqlite]"
    data = (captured / f"{stem}.fidb").read_bytes()
    found: dict[str, Any] = json.loads((captured / f"{stem}.json").read_text())
    return {"data": data, **found}


def _members(data: bytes) -> list[tuple[str, bytes]]:
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        return [(m.name, tar.extractfile(m).read()) for m in tar.getmembers()]  # type: ignore[union-attr]


def _tar(members: list[tuple[str, bytes]]) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name, body in members:
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return out.getvalue()


def _tampered(valid: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """V1's cases, from the valid held release: a member deleted, one
    duplicated, two out of order, one byte changed; and a log edited with
    the manifest rewritten to match (and so unsigned)."""
    members = _members(valid["data"])
    held = dict(members)
    log = held["log.jsonl"]
    edited_log = log.replace(b'"seq":2,', b'"seq":3,', 1)
    manifest = fidu1.parse(held["manifest.json"])
    manifest.pop("sig")
    for entry in manifest["members"]:
        if entry["path"] == "log.jsonl":
            entry["sha256"] = hashlib.sha256(edited_log).hexdigest()
    relisted = {**held, "log.jsonl": edited_log, "manifest.json": fidu1.canonical(manifest)}
    swapped = list(members)
    swapped[0], swapped[1] = swapped[1], swapped[0]
    cases = {
        "v1-member-deleted": (
            _tar([m for m in members if m[0] != "deployment.json"]),
            "deployment.json deleted",
        ),
        "v1-member-duplicated": (
            _tar([*members[:2], members[1], *members[2:]]),
            "a member twice",
        ),
        "v1-members-out-of-order": (_tar(swapped), "two members swapped"),
        "v1-byte-changed": (
            pack({**held, "keys.json": held["keys.json"].replace(b"ed25519:", b"ed25519:0", 1)}),
            "keys.json edited; its digest no longer matches",
        ),
        "v9-log-edited-manifest-rewritten": (
            pack(relisted),
            "record 2's sequence edited, and the manifest rewritten to match, unsigned",
        ),
        "unreadable-not-a-tar": (b"not a bundle\n" * 64, "no tar at all: exit 2"),
    }
    base = {k: v for k, v in valid.items() if k != "data"}
    return {name: {**base, "data": data, "about": about} for name, (data, about) in cases.items()}


def _tampered_sealed(valid: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The sealed bundle's own: a withheld record's hash rewritten, and one
    withheld record dropped, each with the manifest rewritten to match."""
    held = dict(_members(valid["data"]))
    withheld = json.loads(held["withheld.json"])

    def relisted(items: list[dict[str, Any]]) -> bytes:
        member = fidu1.canonical(items)
        manifest = fidu1.parse(held["manifest.json"])
        manifest.pop("sig")
        for entry in manifest["members"]:
            if entry["path"] == "withheld.json":
                entry["sha256"] = hashlib.sha256(member).hexdigest()
        return pack({**held, "withheld.json": member, "manifest.json": fidu1.canonical(manifest)})

    rewritten = [dict(w) for w in withheld]
    rewritten[0]["hash"] = "ab" * 32
    dropped = [w for i, w in enumerate(withheld) if i != len(withheld) // 2]
    cases = {
        "v10-withheld-rewritten": (
            relisted(rewritten),
            "a withheld record's hash rewritten: its anchor names another",
        ),
        "v9-withheld-dropped": (
            relisted(dropped),
            "a withheld record dropped: the chain has a gap",
        ),
    }
    base = {k: v for k, v in valid.items() if k != "data"}
    return {name: {**base, "data": data, "about": about} for name, (data, about) in cases.items()}


def _verdict(entry: dict[str, Any]) -> dict[str, Any]:
    from fiduciary.bundle import BundleError

    keys = None if entry["keys"] is None else TrustedKeys(entry["keys"])
    witness = None if entry["witness"] is None else fidu1.key_of(entry["witness"])
    try:
        report = verify_bundle(
            entry["data"],
            charter=entry["charter"],
            keys=keys,
            witness=witness,
            skew_seconds=entry["skew_seconds"],
        )
    except BundleError as exc:
        return {"exit_code": 2, "checks": [], "findings": [], "unreadable": str(exc)}
    return {
        "exit_code": report.exit_code,
        "checks": sorted({f.check for f in report.findings}, key=lambda c: int(c[1:])),
        "findings": [f.render() for f in report.findings],
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--add", nargs="+", metavar="NAME", choices=sorted(SCENARIOS), help="record only these"
    )
    args = parser.parse_args(argv)
    names = set(args.add or SCENARIOS)
    with tempfile.TemporaryDirectory() as scratch:
        recorded = _record(Path(scratch), names)
    for name in names:
        recorded[name]["about"] = SCENARIOS[name][1]
    if "valid-held-release" in names:
        recorded.update(_tampered(recorded["valid-held-release"]))
    if "valid-sealed-child" in names:
        recorded.update(_tampered_sealed(recorded["valid-sealed-child"]))
    index: list[dict[str, Any]] = []
    if args.add:  # every other bundle stays as it was recorded
        kept = json.loads((OUT / "index.json").read_text())
        index = [entry for entry in kept if entry["name"] not in recorded]
    else:
        if OUT.exists():
            shutil.rmtree(OUT)
        OUT.mkdir(parents=True)
    for name in sorted(recorded):
        entry = recorded[name]
        (OUT / f"{name}.fidb").write_bytes(entry["data"])
        verdict = _verdict(entry)
        index.append(
            {
                "name": name,
                "about": entry["about"],
                "file": f"{name}.fidb",
                "sha256": hashlib.sha256(entry["data"]).hexdigest(),
                "charter": entry["charter"],
                "keys": entry["keys"],
                "witness": entry["witness"],
                "skew_seconds": entry["skew_seconds"],
                **verdict,
            }
        )
        print(f"{name}: exit {verdict['exit_code']} {' '.join(verdict['checks'])}")
    index.sort(key=lambda entry: str(entry["name"]))
    (OUT / "index.json").write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()

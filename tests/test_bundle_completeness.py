"""What a bundle may not leave out (1.11.0), found by an outside audit.

Before 1.11.0 a bundle's manifest signature was optional and nothing tied its
members to what the log and the escrow chain say there is. Anyone could cut a
bundle's tail off, or drop its receipts, relist the manifest unsigned, and
both verifiers passed it with every key pinned. Now:

- the manifest is signed by the service key, always (V9), so what a bundle
  holds is the exporter's statement, and nobody else's;
- the cut is the anchor of the range's last record, and the bundle holds it,
  proven (V6), so a range cannot end before the checkpoint it is held to;
- every action receipt the lineage's escrow chain names is in the bundle (V5),
  so a receipt left out, or never issued, is named by its record.

And an honest export of any prefix of the log still verifies.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from interlock.chain import EscrowChain

from fiduciary import fidu1
from fiduciary.bundle import BundleReport
from fiduciary.bundle.export import _receipts
from fiduciary.bundle.format import BundleError, pack, unpack
from fiduciary.documents import Mandate, Record
from fiduciary.log import parse_memo
from fiduciary.store import Store
from tests import bound
from tests import bundle_kit as bk
from tests import examples as e
from tests.test_bundle_checks import _later, _lineage


@pytest.fixture
def world(store: Store, tmp_path: Path) -> Iterator[bound.World]:
    yield from bk.world(store, tmp_path)


def _relisted(members: dict[str, bytes], *, signed: bool) -> bytes:
    """``members`` under a manifest listing their digests again: unsigned, as
    anyone could write it; or signed with the service key, as its holder could."""
    manifest = fidu1.parse(members["manifest.json"])
    manifest.pop("sig", None)
    kinds = {entry["path"]: entry["type"] for entry in manifest["members"]}
    manifest["members"] = [
        {"path": p, "sha256": hashlib.sha256(members[p]).hexdigest(), "type": kinds[p]}
        for p in sorted(members)
        if p != "manifest.json"
    ]
    if signed:
        manifest["sig"] = fidu1.sign("bundle", manifest, e.SERVICE).to_json()
    return pack({**members, "manifest.json": fidu1.canonical(manifest)})


def _says(report: BundleReport, check: str, fragment: str) -> None:
    found = [f.message for f in report.failed(check)]
    assert any(fragment in m for m in found), report.render()


def _at_work(world: bound.World) -> tuple[Mandate, bytes]:
    """A lineage that committed a refund, then went on: its bundle verifies."""
    mandate, fid = _lineage(world)
    _later(fid)
    _later(fid)
    data = bk.export(world, mandate.mandate_id)
    assert bk.verify(world, data).passed
    return mandate, data


def _actions(members: dict[str, bytes]) -> list[str]:
    return sorted(
        p
        for p in members
        if p.startswith("receipts/")
        and p != "receipts/transitions.json"
        and json.loads(members[p])["receipt"]["v"] == "ARC1"
    )


def test_an_unsigned_manifest_fails_v9(world: bound.World) -> None:
    _, data = _at_work(world)
    report = bk.verify(world, _relisted(dict(unpack(data)), signed=False))
    assert [f.render() for f in report.findings] == [
        "V9 signature: manifest.json is not signed by the service key: what the bundle holds "
        "is no one's statement, and a member could have been dropped"
    ]
    assert report.exit_code == 4


def test_the_exporter_signs_every_bundle(world: bound.World) -> None:
    mandate, _ = _lineage(world)
    with pytest.raises(BundleError, match="signed by the service key"):
        bk.export(world, mandate.mandate_id, evidence={"service": e.SERVICE.public_key()})


def _cut_short(members: dict[str, bytes], keep: int) -> dict[str, bytes]:
    """The log ended at record ``keep``, the manifest's range with it."""
    lines = members["log.jsonl"].splitlines()
    manifest = fidu1.parse(members["manifest.json"])
    return {
        **members,
        "log.jsonl": b"".join(line + b"\n" for line in lines[:keep]),
        "manifest.json": fidu1.canonical({**manifest, "range": {"from": 1, "to": keep}}),
    }


def test_a_tail_cut_off_with_no_key_is_named_three_ways(world: bound.World) -> None:
    """The audit's attack: the log, the ledger and the receipts cut after a
    record, the manifest relisted unsigned. Every key pinned."""
    _, data = _at_work(world)
    held = dict(unpack(data))
    keep = 4
    last = Record.loads(held["log.jsonl"].splitlines()[keep - 1])
    entries = held["ledger/entries.jsonl"].splitlines()
    anchor = next(
        int(json.loads(x)["entry"]["sequence"])
        for x in entries
        if parse_memo(json.loads(x)["entry"]["memo"]) == ("fiduciary", keep, last.hash)
    )
    cut = _cut_short(held, keep)
    cut["ledger/entries.jsonl"] = b"".join(
        x + b"\n" for x in entries if int(json.loads(x)["entry"]["sequence"]) <= anchor
    )
    for path in _actions(held):
        del cut[path]
    report = bk.verify(world, _relisted(cut, signed=False))
    _says(report, "V9", "manifest.json is not signed")
    _says(report, "V6", "is not in the bundle, proven")
    _says(report, "V5", "which the bundle does not hold")


def test_a_range_ended_before_its_cut_is_named_even_signed(world: bound.World) -> None:
    """An exporter's own bundle, its range ended early under the checkpoint of
    the whole log: the cut is not the anchor of the range's last record."""
    _, data = _at_work(world)
    held = dict(unpack(data))
    keep = len(held["log.jsonl"].splitlines()) - 2
    report = bk.verify(world, _relisted(_cut_short(held, keep), signed=True))
    _says(report, "V6", f"is not the anchor of record {keep}, the range's last")


def test_a_receipt_left_out_is_named_by_its_record(world: bound.World) -> None:
    _, data = _at_work(world)
    held = dict(unpack(data))
    (gone, *_) = _actions(held)
    receipt_id = gone.removeprefix("receipts/").removesuffix(".json")
    del held[gone]
    report = bk.verify(world, _relisted(held, signed=True))
    assert report.exit_code == 8
    (finding,) = report.failed("V5")
    assert f"names receipt {receipt_id}, which the bundle does not hold" in finding.message
    assert finding.message.startswith("escrow chain ")


def test_every_prefix_of_the_log_exports_to_a_bundle_that_verifies(world: bound.World) -> None:
    """The cut and the receipts are taken together: a bundle ending at any
    record verifies, whichever of its receipts were anchored after its cut."""
    mandate, _ = _at_work(world)
    head = world.store.head("fiduciary")[0]
    exported = 0
    for last in range(1, head + 1):
        try:
            data = bk.export(world, mandate.mandate_id, to_seq=last)
        except BundleError as exc:  # before the lineage was issued
            assert "no lineage" in str(exc)
            continue
        report = bk.verify(world, data)
        assert report.passed, (last, report.render())
        exported += 1
    assert exported >= 4


def test_a_receipt_anchored_past_the_cut_goes_with_its_record(world: bound.World) -> None:
    """A receipt is issued after the escrow record it settles, and anchored to
    the ledger's head then: another record's anchor can come between. The
    receipt is in the bundle when its record is (1.11.0), wherever its own
    ledger anchor falls."""
    mandate, _ = _lineage(world)
    lineage = mandate.mandate_id
    log = world.receipts
    assert log is not None
    action = next(d for d in log.receipts() if d.issuer == f"fiduciary:{lineage}")
    assert action.anchors.agentgov is not None and action.anchors.escrow is not None
    records = EscrowChain.load(world.tmp / "chains" / f"{lineage}.chain").records()
    settled = [r for r in records if r.sequence <= action.anchors.escrow.seq]
    evidence = bk.evidence(world)
    early = action.anchors.agentgov.seq - 1  # a cut before the receipt's anchor
    members, _ = _receipts(evidence, [lineage], early, {lineage: settled})
    assert f"receipts/{action.receipt_id}.json" in members
    members, _ = _receipts(evidence, [lineage], early, {lineage: settled[:-1]})
    assert f"receipts/{action.receipt_id}.json" not in members
    members, _ = _receipts(dataclasses.replace(evidence, receipts=None), [lineage], early, {})
    assert members == {}

"""Sealed bundles (``fidb/2``, §9.2, amended in 1.4.0): one lineage's records
whole, and of the rest of the log only places and hashes, each held to its
ledger anchor; and receipts that name only the tenants a plan declares."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from fiduciary import fidu1
from fiduciary.bridge import Fiduciary
from fiduciary.bundle import BundleError, BundleReport
from fiduciary.bundle.export import disclosed
from fiduciary.bundle.format import FORMAT, SEALED
from fiduciary.compiler import compile
from fiduciary.documents import Mandate, Record
from fiduciary.exceptions import MalformedError
from fiduciary.refines import delegation_problems
from fiduciary.store import State, Store
from tests import bound
from tests import bridge_kit as kit
from tests import bundle_kit as bk
from tests.test_bundle_tamper import _members, _remanifest, _tar


@pytest.fixture
def world(store: Store, tmp_path: Path) -> Iterator[bound.World]:
    yield from bk.world(store, tmp_path)


def _refund(fid: Fiduciary, lineage: str, order: int) -> None:
    arguments = {"order_id": order, "amount": "5.00", "payment_intent": f"pi_{order:010d}"}
    with fid.session(lineage, tenant="acme") as session:
        assert session.call("refund_order", arguments).committed


def _family(world: bound.World) -> tuple[Mandate, list[Mandate], Fiduciary]:
    """A root lineage and two children, each with one refund, and a session
    after, so the export's range covers them."""
    parent = kit.mandate(world)
    world.issue_and_bind(parent)
    fid = kit.fiduciary(world)
    children = []
    for name in ("north", "south"):
        raw = world.child_json(
            parent,
            subject={
                "scope": f"{parent.subject.scope}/{name}",
                "funded_from": parent.subject.scope,
                "label": name,
            },
        )
        records = world.binder.derive(Mandate.from_json(raw, signed=False))
        world.binder.bind(str(records[0].content["mandate"]))
        children.append(world.binder.engine(raw["mandate_id"]).mandate)
    _refund(fid, parent.mandate_id, 7)
    for child, order in zip(children, (9, 11), strict=True):
        _refund(fid, child.mandate_id, order)
    with fid.session(bound.SCOPE, tenant="acme"):
        pass
    return parent, children, fid


def _sealed(world: bound.World, mandate: Mandate) -> bytes:
    return bk.export(world, mandate.mandate_id, sealed=True)


def _tampered(data: bytes, member: str, change: Any) -> bytes:
    members = dict(_members(data))
    members[member] = change(members[member])
    return _tar(_remanifest(sorted(members.items())))


def _withheld(change: Any) -> Any:
    def edit(raw: bytes) -> bytes:
        items = json.loads(raw)
        change(items)
        return fidu1.canonical(items)

    return edit


def _fails(report: BundleReport, check: str, fragment: str) -> None:
    text = report.render()
    assert any(fragment in f.message for f in report.failed(check)), text


def test_a_sealed_bundle_carries_its_lineage_whole_and_withholds_the_rest(
    world: bound.World,
) -> None:
    parent, (north, south), _ = _family(world)
    data = _sealed(world, north)
    members = dict(_members(data))
    assert json.loads(members["manifest.json"])["format"] == SEALED
    report = bk.verify(world, data)
    assert report.passed, report.render()
    assert any(n.startswith("sealed: ") for n in report.notes), report.notes
    assert south.mandate_id.encode() not in data and south.subject.scope.encode() not in data
    assert north.mandate_id.encode() in data and parent.mandate_id.encode() in data
    log = [Record.loads(line) for line in members["log.jsonl"].splitlines()]
    withheld = json.loads(members["withheld.json"])
    assert sorted([r.seq for r in log] + [w["seq"] for w in withheld]) == list(
        range(1, len(log) + len(withheld) + 1)
    )
    # The parent's own session is another lineage's record, withheld too.
    assert all(
        r.content.get("mandate_id") != parent.mandate_id
        for r in log
        if r.kind.startswith("session.")
    )
    whole = bk.export(world, north.mandate_id)
    assert json.loads(dict(_members(whole))["manifest.json"])["format"] == FORMAT
    assert b"withheld.json" not in whole


def test_a_withheld_record_is_held_to_its_anchor_and_its_place(world: bound.World) -> None:
    _, (north, _), _ = _family(world)
    data = _sealed(world, north)
    rewritten = _tampered(data, "withheld.json", _withheld(lambda w: w[0].update(hash="ab" * 32)))
    report = bk.verify(world, rewritten)
    _fails(report, "V10", "it was rewritten")
    dropped = _tampered(data, "withheld.json", _withheld(lambda w: w.pop(len(w) // 2)))
    report = bk.verify(world, dropped)
    _fails(report, "V9", "records are missing or out of order")
    first = json.loads(dict(_members(data))["log.jsonl"].splitlines()[0])
    doubled = _tampered(
        data,
        "withheld.json",
        _withheld(lambda w: w.insert(0, {"seq": first["seq"], "hash": "cd" * 32})),
    )
    _fails(bk.verify(world, doubled), "V1", "is both in log.jsonl and withheld")
    garbled = _tampered(data, "withheld.json", _withheld(lambda w: w[0].update(seq="one")))
    _fails(bk.verify(world, garbled), "V1", "withheld.json does not parse")


def test_withheld_records_belong_only_to_a_sealed_bundle(world: bound.World) -> None:
    _, (north, _), _ = _family(world)
    data = _sealed(world, north)

    def unseal(raw: bytes) -> bytes:
        manifest = fidu1.parse(raw)
        manifest["format"] = FORMAT
        return fidu1.canonical(manifest)

    report = bk.verify(world, _tampered(data, "manifest.json", unseal))
    _fails(report, "V1", "withheld.json does not parse: no member of the format has this name")


def test_a_record_the_ledger_has_not_anchored_is_never_withheld(
    world: bound.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fiduciary.bundle.export as exporting

    _, (north, _), _ = _family(world)
    monkeypatch.setattr(exporting, "parse_memo", lambda memo: None)
    with pytest.raises(BundleError, match="withholds only what an anchor vouches for"):
        _sealed(world, north)


def test_what_a_sealed_bundle_discloses(world: bound.World) -> None:
    parent, (north, south), _ = _family(world)
    records = [Record.loads(r.data) for r in world.store.records("fiduciary")]
    state = State.of(records)
    shown = disclosed(records, state, [north.mandate_id], [parent.mandate_id], {})
    by_seq = {r.seq: r for r in records}
    kinds = {by_seq[s].kind for s in shown}
    assert {"charter.adopted", "mandate.issued", "mandate.bound", "session.opened"} <= kinds
    for seq in shown:
        body = by_seq[seq].content
        assert body.get("mandate_id") != south.mandate_id
        if by_seq[seq].kind.startswith("session."):
            assert body["mandate_id"] == north.mandate_id
    # The vendor-wide view: every record of the family.
    every = disclosed(
        records, state, [parent.mandate_id, north.mandate_id, south.mandate_id], [], {}
    )
    assert every == {r.seq for r in records}


# -- receipts naming only the declared tenants ---------------------------------------------


def _with_receipt_tenants(world: bound.World, value: str | None) -> Mandate:
    raw = kit.mandate(world).body()
    if value is None:
        raw["data"].pop("receipt_tenants", None)
    else:
        raw["data"]["receipt_tenants"] = value
    return Mandate.from_json(raw, signed=False)


def test_a_mandate_may_name_only_the_tenants_its_plans_declare(world: bound.World) -> None:
    plain = _with_receipt_tenants(world, None)
    assert "receipt_tenants" not in plain.body()["data"]
    declared = _with_receipt_tenants(world, "declared")
    assert declared.data.receipt_tenants == "declared"
    deployment = world.binder.deployment
    assert "tenants" not in compile([plain], deployment=deployment).description["receipts"]
    found = compile([declared], deployment=deployment)
    assert found.description["receipts"]["tenants"] == "declared"
    with pytest.raises(MalformedError, match="must be one of declared"):
        _with_receipt_tenants(world, "touched")
    child = Mandate.from_json(world.child_json(declared), signed=False)
    assert not delegation_problems(child, declared)
    raw = world.child_json(declared)
    raw["data"].pop("receipt_tenants")
    widened = Mandate.from_json(raw, signed=False)
    assert any("data.receipt_tenants" in str(p) for p in delegation_problems(widened, declared))

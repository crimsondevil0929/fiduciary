"""Sealed bundles (``fidb/2``, §9.2, amended in 1.4.0): one lineage's records
whole, and of the rest of the log only places and hashes, each held to its
ledger anchor and its seal, so that no record of the lineage's own can be
withheld; and receipts that name only the tenants a plan declares."""

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
from fiduciary.seals import Sealer
from fiduciary.store import Store
from tests import bound
from tests import bridge_kit as kit
from tests import bundle_kit as bk
from tests.test_bundle_tamper import _members, _remanifest, _tar
from tests.test_cli_mcp import SEAL_SECRET

SEALER = Sealer(SEAL_SECRET)
"""The test deployment's seal key."""


@pytest.fixture
def world(store: Store, tmp_path: Path) -> Iterator[bound.World]:
    yield from bk.world(store, tmp_path, sealer=SEALER)


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
        found = json.loads(raw)
        change(found["records"])
        return fidu1.canonical(found)

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
    withheld = json.loads(members["withheld.json"])["records"]
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
    shown = disclosed(records, SEALER.keys([north.mandate_id], [parent.mandate_id]))
    by_seq = {r.seq: r for r in records}
    kinds = {by_seq[s].kind for s in shown}
    assert {"charter.adopted", "mandate.issued", "mandate.bound", "session.opened"} <= kinds
    for seq in shown:
        body = by_seq[seq].content
        assert body.get("mandate_id") != south.mandate_id
        if by_seq[seq].kind.startswith("session."):
            assert body["mandate_id"] == north.mandate_id
    # The vendor-wide view: every record of the family.
    lineages = [parent.mandate_id, north.mandate_id, south.mandate_id]
    assert disclosed(records, SEALER.keys(lineages, [])) == {r.seq for r in records}


# -- seals: no record of the lineage's own is withheld ---------------------------------------


def _resealed(data: bytes, members: dict[str, bytes]) -> bytes:
    """``members`` packed with the manifest rewritten to them and signed with
    the service key: an exporter that holds every key the vendor does."""
    import hashlib

    from fiduciary.bundle.format import pack
    from tests import examples as e

    manifest = fidu1.parse(members["manifest.json"])
    manifest.pop("sig", None)
    for entry in manifest["members"]:
        entry["sha256"] = hashlib.sha256(members[entry["path"]]).hexdigest()
    manifest["sig"] = fidu1.sign("bundle", manifest, e.SERVICE).to_json()
    return pack({**members, "manifest.json": fidu1.canonical(manifest)})


def _moved(data: bytes, seq: int, record: Record | None = None) -> bytes:
    """The bundle with record ``seq`` moved: out of ``log.jsonl`` into the
    withheld records, or, given ``record``, out of them into ``log.jsonl``."""
    members = dict(_members(data))
    lines = members["log.jsonl"].splitlines()
    held = json.loads(members["withheld.json"])
    if record is None:
        line = next(x for x in lines if Record.loads(x).seq == seq)
        lines.remove(line)
        held["records"].append({"seq": seq, "hash": Record.loads(line).hash})
    else:
        held["records"] = [w for w in held["records"] if w["seq"] != seq]
        lines.append(record.canonical())
    held["records"].sort(key=lambda w: w["seq"])
    lines.sort(key=lambda x: Record.loads(x).seq)
    members["log.jsonl"] = b"".join(x + b"\n" for x in lines)
    members["withheld.json"] = fidu1.canonical(held)
    return _resealed(data, members)


def _own(data: bytes, lineage: str, kind: str) -> Record:
    log = [Record.loads(x) for x in dict(_members(data))["log.jsonl"].splitlines()]
    return [r for r in log if r.kind == kind and r.content.get("mandate_id") == lineage][-1]


def test_withholding_one_of_the_lineage_s_own_records_is_named(world: bound.World) -> None:
    """The exporter holds every key and signs the manifest; the record it
    withholds left nothing in the ledger, the receipts or the chain."""
    _, (north, _), _ = _family(world)
    data = _sealed(world, north)
    closed = _own(data, north.mandate_id, "session.closed")
    report = bk.verify(world, _moved(data, closed.seq))
    _fails(report, "V10", f"record {closed.seq} is withheld, but its seal says it is lineage")
    assert north.mandate_id in report.render()
    assert report.exit_code == 8
    # every other check holds: the seal is all that tells
    assert {f.check for f in report.findings} == {"V10"}, report.render()


def test_withholding_a_record_every_bundle_shows_is_named(world: bound.World) -> None:
    _, (north, _), _ = _family(world)
    data = _sealed(world, north)
    charter = next(
        Record.loads(x)
        for x in dict(_members(data))["log.jsonl"].splitlines()
        if Record.loads(x).kind == "charter.adopted"
    )
    report = bk.verify(world, _moved(data, charter.seq))
    _fails(report, "V10", "its seal says it is the charter's")


def test_a_key_the_log_was_not_sealed_with_is_named(world: bound.World) -> None:
    """Swapping the lineage's key for another, so that its withheld record
    opens under none, is named: the key does not open its own first version."""
    _, (north, _), _ = _family(world)
    data = _sealed(world, north)
    closed = _own(data, north.mandate_id, "session.closed")
    members = dict(_members(_moved(data, closed.seq)))
    held = json.loads(members["withheld.json"])
    held["keys"]["lineages"][north.mandate_id] = "ee" * 32
    members["withheld.json"] = fidu1.canonical(held)
    report = bk.verify(world, _resealed(data, members))
    _fails(report, "V10", f"key of lineage {north.mandate_id}'s records is not the log's")


def test_a_key_left_out_is_named(world: bound.World) -> None:
    parent, (north, _), _ = _family(world)
    data = _sealed(world, north)
    members = dict(_members(data))
    held = json.loads(members["withheld.json"])
    held["keys"]["lines"].pop(parent.mandate_id)
    members["withheld.json"] = fidu1.canonical(held)
    report = bk.verify(world, _resealed(data, members))
    _fails(report, "V10", "does not hold a key of each lineage above it")


def test_a_lineage_below_left_out_is_named(world: bound.World) -> None:
    """The parent's bundle, its children's keys dropped and their records
    withheld: a child's first version is a record the parent's key opens."""
    parent, (_, south), _ = _family(world)
    data = _sealed(world, parent)
    members = dict(_members(data))
    held = json.loads(members["withheld.json"])
    held["keys"]["lineages"].pop(south.mandate_id)
    members["withheld.json"] = fidu1.canonical(held)
    manifest = fidu1.parse(members["manifest.json"])
    manifest["lineages"] = sorted(set(manifest["lineages"]) - {south.mandate_id})
    members["manifest.json"] = fidu1.canonical(manifest)
    issued = _own(data, south.mandate_id, "mandate.issued")
    report = bk.verify(world, _moved(_resealed(data, members), issued.seq))
    _fails(report, "V10", f"record {issued.seq} is withheld, but its seal says it is lineage")
    assert parent.mandate_id in report.render()


def test_another_lineage_s_record_carried_is_named(world: bound.World) -> None:
    _, (north, south), _ = _family(world)
    data = _sealed(world, north)
    records = [Record.loads(r.data) for r in world.store.records("fiduciary")]
    theirs = [r for r in records if r.content.get("mandate_id") == south.mandate_id][-1]
    report = bk.verify(world, _moved(data, theirs.seq, theirs))
    _fails(report, "V10", f"record {theirs.seq} is another lineage's, by its seal")


def test_withheld_seals_say_nothing_of_whose_they_are(world: bound.World) -> None:
    """Every seal is two tags, and no tag repeats: withheld records cannot be
    grouped by lineage, kind or depth."""
    _, (north, _), _ = _family(world)
    records = [Record.loads(r.data) for r in world.store.records("fiduciary")]
    tags = [t for r in records for t in r.seal or ()]
    assert len(tags) == 2 * len(records) == len(set(tags))
    held = json.loads(dict(_members(_sealed(world, north)))["withheld.json"])
    assert held["records"] and all(sorted(w) == ["hash", "seq"] for w in held["records"])


def test_a_sealed_log_is_sealed_from_its_first_record(world: bound.World) -> None:
    from fiduciary.exceptions import FiduciaryError
    from fiduciary.log import Log
    from tests import examples as e

    unsealed = Log(world.store, e.SERVICE)
    with pytest.raises(FiduciaryError, match="is sealed: it needs the seal key"):
        unsealed.append("principal.suspended", {"principal": "cfo", "key": "k", "why": "x"})


def test_the_seal_key_holds_every_seal_to_whose_the_record_is(world: bound.World) -> None:
    from fiduciary.verify import verify_store
    from tests import examples as e

    _family(world)
    found = verify_store(
        world.store, service_key=e.SERVICE.public_key(), pinned=world.charter.digest
    )
    assert found.ok, found.problems
    sealed = verify_store(
        world.store, service_key=e.SERVICE.public_key(), pinned=world.charter.digest, sealer=SEALER
    )
    assert sealed.ok, sealed.problems
    other = verify_store(
        world.store,
        service_key=e.SERVICE.public_key(),
        pinned=world.charter.digest,
        sealer=Sealer(bytes(32)),
    )
    assert any("its seal does not say whose it is" in p for p in other.problems)


def test_a_sealed_bundle_needs_a_sealed_log_and_its_key(
    store: Store, tmp_path: Path, world: bound.World
) -> None:
    _, (north, _), _ = _family(world)
    with pytest.raises(BundleError, match="give the seal key"):
        bk.export(world, north.mandate_id, sealed=True, evidence={"sealer": None})


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


def test_a_log_begun_without_seals_is_never_sealed_nor_cut_sealed(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from fiduciary.exceptions import FiduciaryError
    from fiduciary.log import Log
    from fiduciary.sqlite import SqliteStore
    from tests import examples as e

    store = SqliteStore(tmp_path / "plain.db")
    try:
        body = {"charter_id": "c", "principal": "cfo", "key": "k", "why": "lost"}
        (first,) = Log(store, e.SERVICE).append_all([("principal.suspended", body)])
        assert first.seal is None and "seal" not in first.to_json()
        with pytest.raises(FiduciaryError, match="began without seals"):
            Log(store, e.SERVICE, sealer=SEALER).append("principal.suspended", body)
    finally:
        store.close()
    unsealed = Record.make(
        e.SERVICE,
        log="fiduciary",
        seq=1,
        kind="principal.suspended",
        body=body,
        prev=fidu1.GENESIS,
        issued_at=datetime.now(UTC),
    )
    with pytest.raises(BundleError, match="carries no seal"):
        disclosed([unsealed], SEALER.keys(["x"], []))


def test_a_seal_is_two_different_tags_in_order() -> None:
    from fiduciary.seals import Audience

    first, second = SEALER.seal("fiduciary", 3, Audience("lineage", "acme"))
    assert first < second
    raw = {"seal": [second, first]}
    with pytest.raises(MalformedError, match="two different tags, in order"):
        Record.from_json({**_a_record(), **raw})
    with pytest.raises(MalformedError, match="is two 64-hex tags"):
        Record.from_json({**_a_record(), "seal": [first]})
    with pytest.raises(ValueError, match="at least 32 bytes"):
        Sealer(b"short")


def _a_record() -> dict[str, Any]:
    from datetime import UTC, datetime

    from tests import examples as e

    return Record.make(
        e.SERVICE,
        log="fiduciary",
        seq=1,
        kind="principal.suspended",
        body={"principal": "cfo", "key": "k", "why": "x"},
        prev=fidu1.GENESIS,
        issued_at=datetime.now(UTC),
    ).to_json()

"""Delivery receipts in a bundle, and a system's log reconciled against one (1.10.0).

A relay's settler writes a delivery receipt for each request it delivered, bound
to the action receipt of the plan that committed the request, with no ledger
anchor of its own. The bundle carries each one whose action receipt it holds and
that precedes every receipt anchored after its cut, names the relay keys that
attested them (``keys.json`` ``relays``), and V5 checks each: the receipt log's
signature, the relay's attestation, inclusion, and the binding to its action.

The supply-chain pack plays offline: its relay sends purchase orders to an EDI
gateway, and someone sends one more around the relay, with the same account.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from agentgov.receipts import Attestation, DeliveryReceipt, ReceiptBundle

from fiduciary import cli, fidu1
from fiduciary.bundle import TrustedKeys, verify_bundle
from fiduciary.bundle.format import pack, unpack
from fiduciary.bundle.reconcile import bundle_receipts, reconcile_bundle
from fiduciary.demo import packs
from fiduciary.keys import load_key
from tests.bundle_kit import _capture

EDI = "audit/edi-messages.jsonl"


@pytest.fixture(scope="module")
def played(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("deliveries") / "supply-chain"
    packs.run(root, "supply-chain", engine="builtin")
    return root


def _pins(root: Path) -> dict[str, Any]:
    found: dict[str, Any] = json.loads((root / "pinned-keys.json").read_text())
    return found


def _charter(root: Path) -> str:
    return (root / "charter.digest").read_text().strip()


def _verify(root: Path, data: bytes, keys: dict[str, Any] | None = None) -> Any:
    kw: dict[str, Any] = {"charter": _charter(root), "keys": TrustedKeys(keys or _pins(root))}
    _capture(data, kw)
    return verify_bundle(data, **kw)


def _deliveries(data: bytes) -> list[DeliveryReceipt]:
    return [r for r in bundle_receipts(data) if isinstance(r, DeliveryReceipt)]


def test_a_bundle_carries_its_delivery_receipts(played: Path) -> None:
    data = (played / "after.fidb").read_bytes()
    deliveries = _deliveries(data)
    assert len(deliveries) == 2, "two purchase orders went through the relay"
    assert all(d.anchors.agentgov is None for d in deliveries), "a settler's: no ledger anchor"
    held = dict(unpack(data))
    named = fidu1.parse(held["keys.json"])["relays"]
    assert set(named) <= set(_pins(played)["relays"])
    report = _verify(played, data)
    assert not report.failed("V5") and not report.failed("V1"), report.render()
    assert any("2 delivery receipts" in n for n in report.notes)
    assert report.exit_code == 7, "the after bundle names the break-glass act, as before"


def test_the_before_bundle_holds_only_what_preceded_its_cut(played: Path) -> None:
    before = _deliveries((played / "before.fidb").read_bytes())
    after = _deliveries((played / "after.fidb").read_bytes())
    assert {d.receipt_id for d in before} <= {d.receipt_id for d in after}
    assert len(before) < len(after), "a delivery settled after the cut is the after bundle's"


def test_a_relay_key_the_auditor_did_not_pin_is_named(played: Path) -> None:
    data = (played / "after.fidb").read_bytes()
    pins = {**_pins(played), "relays": []}
    report = _verify(played, data, pins)
    assert any("relay key" in f.message and "not pinned" in f.message for f in report.failed("V1"))


def test_a_forged_attestation_is_named(played: Path) -> None:
    """The settler, which holds the receipt log's key, writes a delivery the relay
    never attested: an attestation signed by a key that only claims the relay's
    id. The receipt is signed again, and so is the manifest: V5 names it."""
    data = (played / "after.fidb").read_bytes()
    held = dict(unpack(data))
    name = next(p for p in held if p.startswith("receipts/") and b"ARC1-delivery" in held[p])
    bundle = ReceiptBundle.loads(held[name])
    receipt = bundle.receipt
    assert isinstance(receipt, DeliveryReceipt)
    from agentgov.receipts.signing import Ed25519Signer

    impostor = Ed25519Signer.generate()
    forged = Attestation(receipt.request, receipt.delivery.outcome()).sign(impostor)
    assert forged.signature is not None
    from dataclasses import replace

    from agentgov.receipts.schema import Signature

    claimed = Signature(
        forged.signature.alg,
        receipt.attestation.key_id,
        forged.signature.value,
        forged.signature.version,
    )
    signer = load_key(played / "keys" / "receipts.key")
    rewritten = replace(receipt, attestation=claimed, signature=None)
    rewritten = rewritten.sign(signer)
    held[name] = fidu1.canonical(replace(bundle, receipt=rewritten).to_json())
    manifest = fidu1.parse(held["manifest.json"])
    manifest.pop("sig")
    for entry in manifest["members"]:
        entry["sha256"] = hashlib.sha256(held[entry["path"]]).hexdigest()
    service = load_key(played / "keys" / "service.key")
    manifest["sig"] = fidu1.sign("bundle", manifest, service).to_json()
    tampered = pack({**held, "manifest.json": fidu1.canonical(manifest)})
    report = _verify(played, tampered)
    assert any("relay attestation" in f.message for f in report.failed("V5")), report.render()


def test_the_vendor_log_reconciles_against_the_bundle_alone(played: Path) -> None:
    data = (played / "after.fidb").read_bytes()
    found = reconcile_bundle(
        data,
        str(played / EDI),
        charter=_charter(played),
        keys=TrustedKeys(_pins(played)),
        fmt="records",
        actors=("erp-integration",),
        substrates=("edi",),
    )
    assert (len(found.actions.matched), len(found.actions.phantom)) == (2, 1)
    assert found.deliveries == 2 and found.bundle.exit_code == 7
    recorded = json.loads((played / "demo.json").read_text())["demo"]["acts"][-1]
    assert recorded["reconciliation"]["matched"] == 2
    command = recorded["reconciliation"]["command"]
    assert command.startswith("fiduciary reconcile-actions after.fidb")


def test_the_command_reconciles_from_the_bundle(
    played: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(played)
    argv = [
        "reconcile-actions",
        "after.fidb",
        EDI,
        "--charter",
        _charter(played),
        "--keys",
        "pinned-keys.json",
        "--actor",
        "erp-integration",
        "--substrate",
        "edi",
    ]
    assert cli.main(argv) == 1, "the order sent around the relay is a phantom"
    out = capsys.readouterr().out
    assert "2 of them deliveries" in out and "AUDIT FAILED  1 phantom" in out
    assert cli.main([*argv[:3], "--charter", "0" * 64, *argv[5:]]) == 1
    assert "cannot be relied on" in capsys.readouterr().out
    assert cli.main([*argv[:3], "--charter", "nope"]) == 2


def test_a_bundle_without_deliveries_names_no_relays(played: Path) -> None:
    """The format's other bundles keep their bytes: keys.json gains ``relays``
    only when a delivery receipt is inside."""
    for path in sorted(Path("vectors/fidu1/bundles").glob("valid-*.fidb")):
        keys = fidu1.parse(dict(unpack(path.read_bytes()))["keys.json"])
        assert "relays" not in keys, path.name


def test_an_export_that_cannot_be_read_is_named(played: Path) -> None:
    from fiduciary.bundle import BundleError

    with pytest.raises(BundleError, match=r"none\.json"):
        reconcile_bundle(
            (played / "after.fidb").read_bytes(),
            str(played / "none.json"),
            charter=_charter(played),
            keys=TrustedKeys(_pins(played)),
            fmt="stripe-events",
        )

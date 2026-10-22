"""What the bundle tests share: a world on the wall clock (its instants are
held to the ledger's, V12), its evidence gathered for the exporter, and the
keys an auditor pins."""

from __future__ import annotations

import dataclasses
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from agentgov.receipts.signing import Ed25519Signer, verifier_of
from interlock.records import Keyring, RecordLog, SignedRecord

from fiduciary.bridge import Fiduciary
from fiduciary.bundle import BundleReport, Evidence, TrustedKeys, export_bundle, verify_bundle
from fiduciary.bundle.export import spec
from fiduciary.store import Store
from tests import bound
from tests import bridge_kit as kit
from tests import examples as e
from tests.conftest import LiveClock

LEDGER_KEY = e.signer("ledger-checkpoints")
RECEIPTS_KEY = Ed25519Signer(bytes(32))
"""The world's receipt log signs with this (``tests.bound``)."""


def world(store: Store, tmp_path: Path, **kw: Any) -> Iterator[bound.World]:
    yield from kit.world(store, tmp_path, LiveClock(), **kw)


def _records(path: Path, keyring: Keyring) -> tuple[SignedRecord, ...]:
    return RecordLog.load(path, keyring) if path.exists() else ()


def operator_keyring(*names: str) -> Keyring:
    """The operators' keys: the service's (the executor's), and ``names``'."""
    keys = [e.SERVICE.public_key(), *(e.signer(n).public_key() for n in names)]
    return Keyring({k.key_id: k for k in keys})


def evidence(
    w: bound.World, fid: Fiduciary | None = None, operators: tuple[str, ...] = (), **changes: Any
) -> Evidence:
    service = e.SERVICE.public_key()
    keyring = operator_keyring(*operators)
    runtime = getattr(w, "runtime", None)
    found = Evidence(
        ledger=w.governor,
        ledger_signer=LEDGER_KEY,
        receipts=w.receipts,
        receipt_keys=[verifier_of(RECEIPTS_KEY)],
        operator_records=_records(w.tmp / "operators.jsonl", keyring),
        operator_keys={
            "fiduciary": spec(service),
            **{n: spec(e.signer(n).public_key()) for n in operators},
        },
        runtime_records=runtime.records() if runtime is not None else (),
        runtime_key=service,
        chains=w.tmp / "chains",
        service=e.SERVICE,
    )
    return dataclasses.replace(found, **changes)


def export(w: bound.World, mandate_id: str, **kw: Any) -> bytes:
    changes = kw.pop("evidence", {})
    operators = kw.pop("operators", ())
    deployment = kw.pop("deployment", w.binder.deployment)
    return export_bundle(
        w.store,
        mandate_id,
        deployment=deployment,
        evidence=evidence(w, operators=operators, **changes),
        **kw,
    )


def auditor_keys(*operators: str, **changes: Any) -> TrustedKeys:
    """What an auditor pins: the deployment's service, ledger, receipt and
    runtime keys, and its operators'."""
    service = spec(e.SERVICE.public_key())
    raw: dict[str, Any] = {
        "service": service,
        "ledger": spec(verifier_of(LEDGER_KEY)),
        "receipts": [spec(verifier_of(RECEIPTS_KEY))],
        "runtime": service,
        "operators": {
            "fiduciary": service,
            **{n: spec(e.signer(n).public_key()) for n in operators},
        },
    }
    raw.update(changes)
    return TrustedKeys(raw)


def verify(w: bound.World, data: bytes, **kw: Any) -> BundleReport:
    kw.setdefault("keys", auditor_keys())
    kw.setdefault("charter", w.charter.digest)
    _capture(data, kw)
    return verify_bundle(data, **kw)


def _capture(data: bytes, kw: dict[str, Any]) -> None:
    """Under ``FIDUCIARY_WRITE_BUNDLES`` (``scripts/make_bundles.py``), keep
    each test's first verified bundle, and what it was verified with."""
    out = os.environ.get("FIDUCIARY_WRITE_BUNDLES")
    if not out:
        return
    test = os.environ["PYTEST_CURRENT_TEST"].split(" ")[0].rsplit("::", 1)[-1]
    target = Path(out) / f"{test}.fidb"
    if target.exists():
        return
    keys, witness = kw.get("keys"), kw.get("witness")
    target.write_bytes(data)
    sidecar = {
        "charter": kw["charter"],
        "keys": None if keys is None else dict(keys.raw),
        "witness": None if witness is None else spec(witness),
        "skew_seconds": kw.get("skew_seconds", 5),
    }
    target.with_suffix(".json").write_text(json.dumps(sidecar, sort_keys=True))


def manifest(data: bytes) -> dict[str, Any]:
    import io
    import tarfile

    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        member = tar.extractfile("manifest.json")
        assert member is not None
        found: dict[str, Any] = json.loads(member.read())
        return found

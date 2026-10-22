"""Exporting a lineage's audit bundle (§9.1, §9.2): a deterministic tar,
its manifest naming every member's digest, the log's whole authority history,
and nothing of what the lineage read, wrote, sent or said (F9-1)."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agentgov.interceptor import TokenUsage

from fiduciary.bundle import BundleError
from fiduciary.store import Store
from tests import bound
from tests import bridge_kit as kit
from tests import bundle_kit as bk


@pytest.fixture
def world(store: Store, tmp_path: Path) -> Iterator[bound.World]:
    yield from bk.world(store, tmp_path)


class _Model:
    def __init__(self) -> None:
        self.messages = self
        self.n = 0

    def create(self, **kwargs: Any) -> Any:
        self.n += 1
        return SimpleNamespace(
            model=kwargs["model"], usage=TokenUsage(input_tokens=30, output_tokens=40)
        )


def _work(world: bound.World) -> Any:
    """A lineage at work: a refund committed, a refusal repaired by rule, a
    passthrough lookup, and two model calls; then the session closed."""
    mandate = kit.mandate(world)
    world.issue_and_bind(mandate)
    fid = kit.fiduciary(world)
    with fid.session(bound.SCOPE, tenant="acme") as session:
        refund = {"order_id": 7, "amount": "12.50", "payment_intent": "pi_0000000007"}
        assert session.call("refund_order", refund).committed
        assert session.call("adjust_orders", {"first": 9, "second": 10, "amount": "1.00"}).repaired
        assert session.call("lookup_order", {"order_id": 7}).ok
        model = session.govern(_Model())
        for n in range(2):
            model.messages.create(
                model="claude-opus-5-5",
                max_tokens=100,
                messages=[{"role": "user", "content": f"Summarize ledger batch {n * 977}."}],
            )
    return mandate, fid


def test_an_export_is_a_deterministic_tar_its_manifest_lists(world: bound.World) -> None:
    mandate, _ = _work(world)
    data = bk.export(world, mandate.mandate_id)
    assert bk.export(world, mandate.mandate_id) == data
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        infos = tar.getmembers()
        held = {i.name: tar.extractfile(i).read() for i in infos}  # type: ignore[union-attr]
    names = [i.name for i in infos]
    assert names == sorted(names) and len(set(names)) == len(names)
    for info in infos:
        assert info.isreg() and info.mode == 0o644 and info.mtime == 0
        assert (info.uid, info.gid, info.uname, info.gname) == (0, 0, "", "")
    manifest = json.loads(held.pop("manifest.json"))
    assert manifest["format"] == "fidb/1" and manifest["lineage"] == mandate.mandate_id
    assert {m["path"]: m["sha256"] for m in manifest["members"]} == {
        name: hashlib.sha256(body).hexdigest() for name, body in held.items()
    }
    log = held["log.jsonl"].splitlines()
    assert manifest["range"] == {"from": 1, "to": len(log)}
    kinds = [json.loads(line)["kind"] for line in log]
    assert kinds[0] == "approval.recorded" and "charter.adopted" in kinds  # from record 1
    assert any(n.startswith("receipts/") for n in names)
    assert any(n.startswith("escrow/") for n in names)
    assert {"ledger/checkpoint.json", "ledger/entries.jsonl", "keys.json"} <= set(names)


def test_a_range_the_log_does_not_hold_or_another_lineage_is_refused(
    world: bound.World,
) -> None:
    mandate, _ = _work(world)
    head = world.store.head("fiduciary")[0]
    with pytest.raises(BundleError, match="the log holds records 1 to"):
        bk.export(world, mandate.mandate_id, to_seq=head + 1)
    with pytest.raises(BundleError, match="no lineage"):
        bk.export(world, "00000000-0000-4000-8000-000000000000")
    with pytest.raises(BundleError, match="no lineage"):
        bk.export(world, mandate.mandate_id, to_seq=2)  # issued later
    shorter = bk.export(world, mandate.mandate_id, to_seq=head - 1)
    assert bk.manifest(shorter)["range"] == {"from": 1, "to": head - 1}


def test_a_bundle_carries_no_row_data_payloads_transcripts_or_secrets(
    world: bound.World,
) -> None:
    """F9-1: what the lineage read, wrote, sent and said is in no member;
    nor is any private key the deployment signs with."""
    import hashlib
    import io
    import tarfile

    mandate, _ = _work(world)
    data = bk.export(world, mandate.mandate_id)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        text = b"".join(tar.extractfile(m).read() for m in tar.getmembers())  # type: ignore[union-attr]
    absent = [
        b"pi_0000000007",  # the refund's payment intent: a Stripe payload
        b"12.50",  # the refund's amount, as the tool's arguments carried it
        b"Summarize ledger batch",  # a model prompt
        b'"status": "open"',  # what the lookup read
    ]
    for name in ("service", "ledger-checkpoints", "cfo", "head-ops", "security"):
        seed = hashlib.sha256(f"fiduciary-test/{name}".encode()).digest()  # tests.examples.signer
        absent.extend([seed, seed.hex().encode()])
    for value in absent:
        assert value not in text, value

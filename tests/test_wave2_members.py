"""The members 1.5.0 adds to a mandate: ``data.sensors``, ``data.checkers``
(pinned checker packs) and ``data.acknowledge_unknown``, and the ``apply``
operation. Each is written only when used, a child only narrows it, and a
verifier replaying the policy (V4) needs exactly the pinned pack."""

from __future__ import annotations

import copy
import importlib
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from agentgov.receipts.signing import Ed25519Signer
from interlock import SensorAttestation, TruncationGuard
from interlock.checker_packs import pack_digest

from fiduciary.compiler import CompileError, compile
from fiduciary.documents import Mandate, ToolBinding
from fiduciary.exceptions import MalformedError
from fiduciary.refines import delegation_problems
from fiduciary.store import Store
from tests import bound
from tests import bridge_kit as kit
from tests import bundle_kit as bk
from tests import examples as e

ANALYZER = Ed25519Signer(bytes(range(32, 64)))
KEY = ANALYZER.public_key().spec()
OTHER = Ed25519Signer(bytes(range(64, 96))).public_key().spec()

PACK = textwrap.dedent(
    """
    from interlock.types import InvariantViolation, Severity

    PACK_NAME = "clinic-checks"
    PACK_VERSION = "1.0.0"


    class RefundCeiling:
        __slots__ = ("_ceiling",)

        def __init__(self, ceiling: int) -> None:
            self._ceiling = ceiling

        @property
        def name(self) -> str:
            return "refund_ceiling"

        def check(self, plan, diff):
            return ()


    CHECKERS = {"refund_ceiling": RefundCeiling}
    """
)


@pytest.fixture
def world(store: Store, tmp_path: Path) -> Iterator[bound.World]:
    yield from bk.world(store, tmp_path)


@pytest.fixture
def pack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Path, str]]:
    root = tmp_path / "site"
    (root / "clinic_checks").mkdir(parents=True)
    (root / "clinic_checks" / "__init__.py").write_text(PACK)
    monkeypatch.syspath_prepend(str(root))
    digest = pack_digest(importlib.import_module("clinic_checks"))
    yield root, digest
    sys.modules.pop("clinic_checks", None)


def _pinned(digest: str, **config: Any) -> dict[str, Any]:
    return {
        "module": "clinic_checks",
        "version": "1.0.0",
        "digest": digest,
        "checker": "refund_ceiling",
        "config": config or {"ceiling": 500},
    }


def _with(world: bound.World, **data: Any) -> dict[str, Any]:
    raw = copy.deepcopy(kit.mandate(world).body())
    raw["data"].update(data)
    return raw


def test_the_members_are_written_only_when_used(world: bound.World, pack: tuple[Path, str]) -> None:
    plain = kit.mandate(world)
    for name in ("sensors", "checkers", "acknowledge_unknown"):
        assert name not in plain.body()["data"]
    _, digest = pack
    raw = _with(
        world,
        sensors=[{"sensor": "policy-check", "keys": sorted([KEY, OTHER])}],
        checkers=[_pinned(digest)],
        acknowledge_unknown=["refunds.amount"],
    )
    mandate = Mandate.from_json(raw, signed=False)
    data = mandate.data
    assert [(s.sensor, s.keys) for s in data.sensors] == [
        ("policy-check", tuple(sorted([KEY, OTHER])))
    ]
    assert data.checkers[0].module == "clinic_checks" and data.checkers[0].config == {
        "ceiling": 500
    }
    assert data.acknowledge_unknown == ("refunds.amount",)
    assert mandate.body()["data"]["sensors"] == raw["data"]["sensors"]
    assert mandate.digest != plain.digest


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"sensors": []}, "only when it holds one"),
        ({"sensors": [{"sensor": "x", "keys": []}]}, "names no key"),
        ({"sensors": [{"sensor": "x", "keys": ["hmac-sha256:" + "00" * 32]}]}, "Ed25519"),
        ({"sensors": [{"sensor": "Bad", "keys": [KEY]}]}, "must be"),
        (
            {"sensors": [{"sensor": "b", "keys": [KEY]}, {"sensor": "a", "keys": [KEY]}]},
            "sorted|order",
        ),
        ({"checkers": []}, "only when it holds one"),
        ({"checkers": [{**_pinned("0" * 64), "config": [1]}]}, "JSON object"),
        ({"checkers": [{**_pinned("0" * 64), "module": "clinic-checks"}]}, "must be"),
        ({"checkers": [{**_pinned("0" * 64), "digest": "abc"}]}, "must be"),
        ({"acknowledge_unknown": []}, "only when it holds one"),
        ({"acknowledge_unknown": ["endpoint"]}, "table.column"),
        ({"tables": {"refunds": ["remove"]}}, "apply, insert, update or delete"),
    ],
)
def test_a_malformed_member_is_refused(
    world: bound.World, data: dict[str, Any], message: str
) -> None:
    with pytest.raises(MalformedError, match=message):
        Mandate.from_json(_with(world, **data), signed=False)


def test_apply_is_an_operation_and_a_tool_kind(world: bound.World) -> None:
    raw = _with(world, tables={"refunds": ["apply", "insert"], "orders": ["update"]})
    assert Mandate.from_json(raw, signed=False).data.tables["refunds"] == ("apply", "insert")
    tool = copy.deepcopy(kit.refund_json())
    tool["effects"][0]["kind"] = "apply"
    assert ToolBinding.from_json(tool, signed=False).effects[0].kind == "apply"


def test_a_child_only_narrows_them(world: bound.World, pack: tuple[Path, str]) -> None:
    _, digest = pack
    parent = Mandate.from_json(
        _with(
            world,
            sensors=[{"sensor": "policy-check", "keys": sorted([KEY, OTHER])}],
            checkers=[_pinned(digest)],
            acknowledge_unknown=["refunds.amount"],
        ),
        signed=False,
    )
    kept = Mandate.from_json(world.child_json(parent), signed=False)
    assert not delegation_problems(kept, parent)

    def problems(**data: Any) -> list[str]:
        raw = world.child_json(parent)
        raw["data"].update(data)
        for name, value in list(raw["data"].items()):
            if value is None:
                del raw["data"][name]
        return [str(p) for p in delegation_problems(Mandate.from_json(raw, signed=False), parent)]

    assert not problems(sensors=[{"sensor": "policy-check", "keys": [KEY]}])
    assert any("data.sensors" in p and "dropped" in p for p in problems(sensors=None))
    third = Ed25519Signer(bytes(range(96, 128))).public_key().spec()
    assert any(
        "keys" in p
        for p in problems(sensors=[{"sensor": "policy-check", "keys": sorted([KEY, third])}])
    )
    assert any("data.checkers" in p for p in problems(checkers=None))
    assert any("data.checkers" in p for p in problems(checkers=[_pinned(digest, ceiling=900)]))
    assert any(
        "acknowledge_unknown" in p
        for p in problems(acknowledge_unknown=["orders.total", "refunds.amount"])
    )
    assert not problems(acknowledge_unknown=None)


def test_they_compile_to_checkers(world: bound.World, pack: tuple[Path, str]) -> None:
    _, digest = pack
    mandate = Mandate.from_json(
        _with(
            world,
            sensors=[{"sensor": "policy-check", "keys": [KEY]}],
            checkers=[_pinned(digest)],
            acknowledge_unknown=["refunds.amount"],
        ),
        signed=False,
    )
    compiled = compile([mandate], deployment=world.binder.deployment)
    names = [c.name for c in compiled.checkers]
    assert "sensor_attestation:policy-check" in names and "refund_ceiling" in names
    (sensor,) = [c for c in compiled.checkers if isinstance(c, SensorAttestation)]
    assert sensor.keys == (KEY,)
    (guard,) = [c for c in compiled.checkers if isinstance(c, TruncationGuard)]
    assert guard.acknowledged_unknown == {"refunds.amount"}
    plain = compile([kit.mandate(world)], deployment=world.binder.deployment)
    assert plain.description["checkers"][0] == ["TruncationGuard", {}]


def test_a_policy_pinning_a_missing_or_tampered_pack_does_not_compile(
    world: bound.World, pack: tuple[Path, str]
) -> None:
    root, digest = pack
    mandate = Mandate.from_json(_with(world, checkers=[_pinned(digest)]), signed=False)
    tampered = root / "clinic_checks" / "__init__.py"
    tampered.write_text(tampered.read_text() + "\n# changed\n")
    sys.modules.pop("clinic_checks", None)
    with pytest.raises(CompileError, match="not the code pinned"):
        compile([mandate], deployment=world.binder.deployment)
    absent = Mandate.from_json(
        _with(world, checkers=[{**_pinned(digest), "module": "absent_checks"}]), signed=False
    )
    with pytest.raises(CompileError, match=r"absent_checks 1\.0\.0 is not installed"):
        compile([absent], deployment=world.binder.deployment)


def test_v4_needs_the_pinned_pack_to_replay_the_policy(
    world: bound.World, pack: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, digest = pack
    mandate = Mandate.from_json(_with(world, checkers=[_pinned(digest)]), signed=False).signed(
        e.signer("cfo")
    )
    world.issue_and_bind(mandate)
    data = bk.export(world, mandate.mandate_id)
    assert bk.verify(world, data).passed
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != str(root)])
    sys.modules.pop("clinic_checks", None)
    report = bk.verify(world, data)
    assert not report.passed
    (finding,) = [f for f in report.findings if f.check == "V4"]
    assert "checker pack clinic_checks 1.0.0 is not installed" in finding.render()

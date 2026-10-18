"""Compilation (``docs/FIDUCIARY_SPEC.md`` §6): what a mandate compiles to,
that it fails closed, and that a receipt is held to what it compiled to."""

from __future__ import annotations

import copy
import dataclasses
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from agentgov.receipts import CheckerRecord
from interlock.invariants import BUILT_IN_CHECKERS
from interlock.outbound import OperationSpec, SinkRegistry, SinkSpec
from interlock.recovery import Channel, TripKind
from interlock.types import Severity

from fiduciary.checkers import BoundStatements, MandateScope, TenantAllowlist, admits_scope
from fiduciary.compiler import (
    SET_BEARING,
    CompiledMandate,
    CompileError,
    canonical_digests,
    compile,
    receipt_problems,
    window_name,
)
from fiduciary.documents import Mandate
from tests import deploy
from tests import examples as e
from tests.test_refines import universe


def full() -> dict[str, Any]:
    """A mandate that compiles to every checker type there is."""
    raw = universe()
    raw["data"]["facts"] = [
        {
            "kind": ["charge.refunded"],
            "field": "amount",
            "table": "refunds",
            "column": "amount",
            "key": None,
            "exempt": [],
        }
    ]
    raw["data"]["drawdown"] = [
        {"table": "orders", "column": "total", "per": "table", "max_drop": "0.5"},
        {"table": "orders", "column": "total", "per": "tenant", "max_drop": "0.9"},
    ]
    raw["tools"]["statements"] = "bound"
    return raw


def _compile(raw: dict[str, Any] | Mandate, **deployment: Any) -> CompiledMandate:
    mandate = raw if isinstance(raw, Mandate) else Mandate.from_json(raw, signed=False)
    return compile([mandate], deployment=deploy.deployment(**deployment))


def test_a_mandate_compiles_to_every_checker_type() -> None:
    compiled = _compile(full())
    types = {type(c) for c in compiled.engine_checkers()}
    assert types >= set(BUILT_IN_CHECKERS)
    assert {MandateScope, TenantAllowlist, BoundStatements} <= types
    assert len(compiled.digest) == 64
    assert compiled.issuer == f"fiduciary:{e.MANDATE_ID}" and compiled.policy_epoch == 1


def test_truncation_and_schema_change_are_always_compiled() -> None:
    raw = e.mandate_json(
        data={
            **e.mandate_json()["data"],
            "drawdown": [],
            "stated_footprint": None,
            "agreements": [],
        },
        outbound={
            "sinks": {},
            "max_per_plan": None,
            "per_sink": {},
            "caps": [],
            "recipients": [],
            "tenants": None,
        },
        windows=[],
        tools={"statements": "any", "bindings": []},
    )
    names = [c.name for c in _compile(raw).checkers]
    assert names[:2] == ["truncation_guard", "no_schema_change"]
    assert names == [
        "truncation_guard",
        "no_schema_change",
        "blast_radius",
        "tenant_isolation",
        "table_allowlist",
        "no_delete",
        "fiduciary_mandate",
    ]
    assert _compile(raw).sinks is None


def test_the_compiled_objects_are_the_mandates_bounds() -> None:
    compiled = _compile(e.mandate_json())
    by_name = {c.name: c for c in compiled.checkers}
    assert by_name["blast_radius"].limit == 20  # type: ignore[attr-defined]
    assert (
        compiled.governance.max_calls_per_window == 60
        and compiled.governance.window_seconds == 60.0
    )
    assert compiled.governance.max_depth == 2  # the subject at depth 1, one derivation below
    assert compiled.governance.fleet_velocity and compiled.governance.trip_on_overdraft
    assert (
        compiled.cognitive.similarity_threshold == 0.7 and compiled.cognitive.max_cycle_period == 4
    )
    assert (
        compiled.interceptor.per_call == Decimal("0.5")
        and compiled.interceptor.max_output_tokens == 4096
    )
    assert (
        compiled.recovery is not None and compiled.recovery.directives[0].text == "Stop retrying."
    )
    assert (
        compiled.recovery.channel is Channel.SYSTEM
        and TripKind.THRASHING in compiled.recovery.revoke_on
    )
    assert compiled.guard is not None and compiled.guard.ttl == timedelta(hours=1)
    assert compiled.guard.auto_max_grant == Decimal(5)
    stated = by_name["stated_footprint"]
    assert stated._severity is Severity.ADVISORY  # type: ignore[attr-defined]


def test_the_sink_registry_is_the_installed_one_narrowed() -> None:
    compiled = _compile(e.mandate_json())
    assert compiled.sinks is not None
    (stripe,) = list(compiled.sinks)
    installed = deploy.SINKS[0]
    assert [op.name for op in stripe.operations] == ["refunds.create"]
    assert stripe.max_attempts == 10 and stripe.not_after == timedelta(seconds=900)
    assert stripe.max_payload_bytes == 16384
    assert (
        stripe.cost_per_call == installed.cost_per_call
        and stripe.idempotency == installed.idempotency
    )


def test_a_bound_is_the_tighter_of_the_mandates_and_the_installed_sinks() -> None:
    loose = SinkSpec(
        "stripe", (OperationSpec("refunds.create"),), max_attempts=3, max_payload_bytes=1024
    )
    compiled = _compile(e.mandate_json(), sinks=SinkRegistry([loose]))
    assert compiled.sinks is not None
    (stripe,) = list(compiled.sinks)
    assert (stripe.max_attempts, stripe.max_payload_bytes) == (3, 1024)


def test_windows_are_named_by_lineage_and_inherited() -> None:
    parent = e.mandate()
    child = Mandate.from_json(e.child_json(windows=[]), signed=False)
    compiled = compile([parent, child], deployment=deploy.deployment())
    assert [w.name for w in compiled.windows] == [window_name(e.MANDATE_ID, "refund_dollars")]
    assert compiled.windows[0].per == "global"
    amended = Mandate.from_json(
        {**e.mandate_json(), "version": 2, "prev": parent.digest}, signed=False
    )
    assert [w.name for w in compile([amended], deployment=deploy.deployment()).windows] == [
        window_name(e.MANDATE_ID, "refund_dollars")
    ]
    tenant = copy.deepcopy(e.mandate_json())
    tenant["windows"][0]["per"] = "tenant"
    assert _compile(tenant).windows[0].per == "tenant"
    assert window_name(e.MANDATE_ID, "w") != window_name(e.CHILD_ID, "w")


def test_every_measure_compiles() -> None:
    raw = e.mandate_json(
        windows=[
            {
                "name": "a",
                "measure": {"kind": "plans"},
                "span_seconds": 60,
                "limit": "5",
                "per": "lineage",
            },
            {
                "name": "b",
                "measure": {"kind": "requests", "sink": "stripe", "operation": None},
                "span_seconds": 60,
                "limit": "5",
                "per": "lineage",
            },
            {
                "name": "c",
                "measure": {
                    "kind": "row_sum",
                    "table": "refunds",
                    "column": "amount",
                    "rows": "net",
                },
                "span_seconds": 60,
                "limit": "5.5",
                "per": "tenant",
            },
        ]
    )
    assert len(_compile(raw).windows) == 3


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        (lambda r: r["data"]["tables"].update(ledger=["update"]), "table 'ledger' is not observed"),
        (
            lambda r: r["data"]["drawdown"][0].update(column="price"),
            "column orders.price is not observed",
        ),
        (
            lambda r: r["outbound"]["sinks"].update(
                sms={
                    "operations": ["send"],
                    "max_attempts": 1,
                    "not_after_seconds": 1,
                    "max_payload_bytes": 1,
                }
            ),
            "sink 'sms' is not installed",
        ),
        (
            lambda r: r["outbound"]["sinks"]["stripe"].update(
                operations=["payouts.create", "refunds.create"]
            ),
            "payouts.create not installed",
        ),
        (
            lambda r: r["tools"]["bindings"].append({"name": "zz", "digest": "9" * 64}),
            "no tool binding document",
        ),
        (
            lambda r: r["windows"].append({**r["windows"][0], "name": "a" * 46}),
            "past 63 characters",
        ),
        (
            lambda r: r["recovery"]["directives"][0].update(sha256="8" * 64),
            "no text with its sha256",
        ),
        (
            lambda r: r["windows"].append(
                {
                    "name": "rows",
                    "measure": {"kind": "row_sum", "table": "ghost", "column": "x", "rows": "net"},
                    "span_seconds": 60,
                    "limit": "1",
                    "per": "lineage",
                }
            ),
            "table 'ghost' is not observed",
        ),
    ],
)
def test_compilation_fails_closed(change: Any, fragment: str) -> None:
    raw = copy.deepcopy(e.mandate_json())
    change(raw)
    raw["tools"]["bindings"].sort(key=lambda b: b["name"])
    raw["windows"].sort(key=lambda w: w["name"])
    with pytest.raises(CompileError) as caught:
        _compile(raw)
    assert any(fragment in p for p in caught.value.problems), caught.value.problems


def test_every_reason_is_named_at_once() -> None:
    raw = copy.deepcopy(e.mandate_json())
    raw["data"]["tables"]["ledger"] = ["update"]
    raw["outbound"]["sinks"]["sms"] = {
        "operations": ["send"],
        "max_attempts": 1,
        "not_after_seconds": 1,
        "max_payload_bytes": 1,
    }
    with pytest.raises(CompileError) as caught:
        _compile(raw)
    assert len(caught.value.problems) == 2


def test_a_binding_must_fit_its_mandate() -> None:
    raw = copy.deepcopy(e.mandate_json())
    raw["data"]["tables"] = {"orders": ["update"], "items": ["update"]}
    raw["data"]["agreements"] = []
    with pytest.raises(CompileError, match="inserts table 'refunds'"):
        _compile(raw)
    raw = copy.deepcopy(e.mandate_json())
    raw["outbound"] = {
        **raw["outbound"],
        "sinks": {
            "mail": {
                "operations": ["send"],
                "max_attempts": 1,
                "not_after_seconds": 60,
                "max_payload_bytes": 100,
            }
        },
        "per_sink": {},
        "caps": [],
    }
    raw["data"]["agreements"] = []
    raw["windows"] = []
    with pytest.raises(CompileError, match=r"requests stripe\.refunds\.create"):
        _compile(raw)
    misnamed = deploy.deployment(tools={e.tool().digest: e.lookup(), e.lookup().digest: e.lookup()})
    with pytest.raises(CompileError, match="the document pinned is tool 'lookup_order'"):
        compile([e.mandate()], deployment=misnamed)


def test_a_directive_whose_text_does_not_hash_fails() -> None:
    sha = e.mandate().recovery.directives[0].sha256  # type: ignore[union-attr]
    with pytest.raises(CompileError, match="does not hash"):
        _compile(e.mandate_json(), directives={sha: "Something else."})


def test_a_lineage_is_given_root_first() -> None:
    with pytest.raises(CompileError, match="nothing to compile"):
        compile([], deployment=deploy.deployment())
    with pytest.raises(CompileError, match="begins at a child"):
        compile([e.child()], deployment=deploy.deployment())
    stranger = Mandate.from_json(
        e.mandate_json(mandate_id="7e1d2c3b-4a59-4687-9a0b-1c2d3e4f5a6b"), signed=False
    )
    with pytest.raises(CompileError, match="is not a child of"):
        compile([stranger, e.child()], deployment=deploy.deployment())


def test_interlock_refusing_a_compiled_policy_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import fiduciary.compiler as compiler

    def refuse(args: Any) -> Any:
        raise ValueError("max_tenants is at least 1")

    monkeypatch.setitem(compiler._BUILT, "TenantIsolation", refuse)
    with pytest.raises(CompileError, match=r"Interlock refuses the compiled policy: max_tenants"):
        _compile(e.mandate_json())


def test_compile_is_deterministic_in_process() -> None:
    assert _compile(full()).digest == _compile(copy.deepcopy(full())).digest
    assert _compile(full()).digest != _compile(e.mandate_json()).digest


def test_root_depth_moves_the_governed_depth() -> None:
    mandate = e.mandate()
    shallow = compile([mandate], deployment=deploy.deployment())
    deep = compile([mandate], deployment=dataclasses.replace(deploy.deployment(), root_depth=3))
    assert deep.governance.max_depth == shallow.governance.max_depth + 3
    assert deep.digest != shallow.digest


# -- fiduciary's checkers -------------------------------------------------------------


def test_the_mandate_scope_admits_its_subject_and_satellites_only() -> None:
    for scope in ("a", "a/recovery", "a/ext-1", "a/ext-12", "a/recovery/ext-3"):
        assert admits_scope("a", scope)
    for scope in ("b", "a/child", "a/ext-0", "a/ext-", "a/recovery/x", "ab", "a/ext-1/x"):
        assert not admits_scope("a", scope)


# -- receipts (F6-7, F6-8) ------------------------------------------------------------


@dataclasses.dataclass
class _Decision:
    policy_epoch: int
    checkers: tuple[CheckerRecord, ...]


@dataclasses.dataclass
class _Receipt:
    issuer: str
    decision: _Decision


def _receipt(compiled: CompiledMandate, **changes: Any) -> _Receipt:
    receipt = _Receipt(
        compiled.issuer, _Decision(compiled.policy_epoch, compiled.checker_records())
    )
    for key, value in changes.items():
        setattr(receipt.decision if key in ("policy_epoch", "checkers") else receipt, key, value)
    return receipt


def test_a_receipt_of_the_compiled_policy_holds() -> None:
    compiled = _compile(full())
    assert receipt_problems(_receipt(compiled), compiled, strict=True) == []
    extra = (*compiled.checker_records(), CheckerRecord("custom", "0" * 64))
    assert receipt_problems(_receipt(compiled, checkers=extra), compiled, strict=True) == []


def test_a_receipt_of_another_policy_is_named() -> None:
    compiled = _compile(full())
    assert receipt_problems(_receipt(compiled, issuer="interlock"), compiled)[0].startswith(
        "issued by"
    )
    assert "policy epoch 2" in receipt_problems(_receipt(compiled, policy_epoch=2), compiled)[0]
    records = compiled.checker_records()
    dropped = tuple(r for r in records if r.name != "blast_radius")
    assert receipt_problems(_receipt(compiled, checkers=dropped), compiled) == [
        "checker blast_radius is not recorded"
    ]
    changed = tuple(
        CheckerRecord(r.name, "1" * 64) if r.name in ("blast_radius", "table_allowlist") else r
        for r in records
    )
    strict = receipt_problems(_receipt(compiled, checkers=changed), compiled, strict=True)
    assert strict == [
        "checker blast_radius ran with another configuration",
        "checker table_allowlist ran with another configuration",
    ]
    loose = receipt_problems(_receipt(compiled, checkers=changed), compiled, strict=False)
    assert loose == [
        "checker blast_radius ran with another configuration"
    ]  # a set-bearing one, by name only


def test_the_fallback_is_chosen_by_the_probe() -> None:
    compiled = _compile(full())
    records = tuple(
        CheckerRecord(r.name, "1" * 64) if r.name == "table_allowlist" else r
        for r in compiled.checker_records()
    )
    found = receipt_problems(_receipt(compiled, checkers=records), compiled)
    assert bool(found) == canonical_digests()
    assert "TableAllowlist" in SET_BEARING

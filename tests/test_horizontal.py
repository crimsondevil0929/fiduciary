"""Mandates of any unit, any partition and either direction, and sealed maps (1.4).

Every field here is optional: a mandate that uses none of it keeps its bytes,
its digest, and what it compiles to.
"""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any

import pytest
from interlock.invariants import ColumnValueGuard, TenantDrawdownGuard, TenantIsolation
from interlock.meters import MeterCheck

from fiduciary.bridge import build_plan
from fiduciary.compiler import CompileError, compile
from fiduciary.documents import Mandate, ToolBinding
from fiduciary.exceptions import MalformedError
from fiduciary.refines import refines
from fiduciary.sealing import Opening, opens, salts, seal
from fiduciary.tools import Tool
from tests import deploy
from tests import examples as e

MME: dict[str, Any] = {
    "places": 8,
    "envelope": "900",
    "per_plan": "90",
    "measure": {"kind": "row_sum", "table": "orders", "column": "total", "rows": "inserted"},
}


def _mandate(**changes: Any) -> Mandate:
    return Mandate.from_json(e.mandate_json(**changes), signed=False)


def _data(**changes: Any) -> dict[str, Any]:
    return {**e.mandate_json()["data"], **changes}


def test_a_mandate_using_none_of_it_keeps_its_bytes() -> None:
    plain = e.mandate_json()
    mandate = Mandate.from_json(plain, signed=False)
    body = mandate.to_json()
    assert "budgets" not in body and "partitions" not in body["data"]
    assert "max_rise" not in body["data"]["drawdown"][0]
    assert "unit_field" not in body["outbound"]["caps"][0]
    assert mandate.digest == Mandate.from_json(copy.deepcopy(plain), signed=False).digest


def test_budgets_in_other_units_round_trip() -> None:
    mandate = _mandate(budgets={"mme": MME, "seats": {**MME, "places": 0}})
    assert sorted(mandate.budgets) == ["mme", "seats"]
    assert mandate.budgets["seats"].places == 0
    again = Mandate.from_json(mandate.to_json(), signed=False)
    assert again.digest == mandate.digest
    for budgets, match in (
        ({"usd": MME}, "budget section's"),
        ({}, "only when it holds one"),
        ({"mme": {**MME, "per_plan": "901"}}, "more than the envelope"),
        ({"mme": {**MME, "envelope": "0"}}, "positive"),
        ({"MME": MME}, "a key"),
        ({"mme": {**MME, "places": 9}}, "places"),
    ):
        with pytest.raises(MalformedError, match=match):
            _mandate(budgets=budgets)


def test_guards_watch_a_rise_and_any_partition() -> None:
    drawdown: list[dict[str, Any]] = [
        {
            "table": "orders",
            "column": "total",
            "per": "partition:patient",
            "max_drop": None,
            "max_rise": "0.5",
        },
        {"table": "orders", "column": "total", "per": "tenant", "max_drop": "0.3"},
    ]
    mandate = _mandate(data=_data(drawdown=drawdown))
    rise, drop = mandate.data.drawdown
    assert (rise.max_drop, rise.max_rise) == (None, Decimal("0.5"))
    assert drop.to_json() == drawdown[1]
    for bad, match in (
        ({**drawdown[0], "max_rise": None}, "must be a string"),
        ({**drawdown[0], "per": "partition:tenant"}, "other than tenant"),
        ({k: v for k, v in drawdown[0].items() if k != "max_rise"}, "neither a fall nor a rise"),
    ):
        with pytest.raises(MalformedError, match=match):
            _mandate(data=_data(drawdown=[bad]))


def test_plans_are_confined_in_more_dimensions_than_the_tenant() -> None:
    mandate = _mandate(data=_data(partitions={"patient": {"max": 1, "declared": True}}))
    assert mandate.data.partitions["patient"].declared
    with pytest.raises(MalformedError, match="max_tenants'"):
        _mandate(data=_data(partitions={"tenant": {"max": 1, "declared": True}}))
    with pytest.raises(MalformedError, match="only when it holds one"):
        _mandate(data=_data(partitions={}))


def test_a_cap_may_be_by_any_unit_and_read_its_unit_anywhere() -> None:
    outbound = copy.deepcopy(e.mandate_json()["outbound"])
    cap = outbound["caps"][0]
    cap.update(maximum={"mg": "5", "vcpu": "64"}, currency_field=None, unit_field="unit")
    mandate = _mandate(outbound=outbound)
    assert mandate.outbound.caps[0].unit_field == "unit"
    assert mandate.outbound.caps[0].to_json() == cap
    cap["currency_field"] = "currency"
    with pytest.raises(MalformedError, match="not both"):
        _mandate(outbound=outbound)


def test_a_window_may_keep_each_patients_history() -> None:
    window = {**e.mandate_json()["windows"][0], "per": "partition:patient"}
    assert _mandate(windows=[window]).windows[0].per == "partition:patient"
    with pytest.raises(MalformedError, match="partition dimension"):
        _mandate(windows=[{**window, "per": "partition:Bad"}])


# -- sealing ---------------------------------------------------------------------


ENTRIES = {
    "org_a": ["acme.example"],
    "org_b": ["globex.example"],
    "org_c": ["initech.example", "initech.test"],
}


def test_a_sealed_map_opens_one_entry_at_a_time() -> None:
    sealed = seal(ENTRIES, salts(b"vendor secret", list(ENTRIES)))
    assert sealed == seal(ENTRIES, salts(b"vendor secret", list(ENTRIES))), "deterministic"
    for key, values in ENTRIES.items():
        assert opens(sealed.root, key, values, sealed.openings[key])
    opening = sealed.openings["org_a"]
    assert not opens(sealed.root, "org_a", ["globex.example"], opening)
    assert not opens(sealed.root, "org_b", ["acme.example"], opening)
    assert not opens(sealed.root, "org_a", ["acme.example"], Opening("00" * 32, opening.proof))
    assert not opens(sealed.root, "org_a", ["acme.example"], Opening("zz", ()))
    other = seal(ENTRIES, salts(b"another secret", list(ENTRIES)))
    assert other.root != sealed.root
    one = seal({"org_a": ["acme.example"]}, salts(b"k", ["org_a"]))
    assert one.openings["org_a"].proof == ()
    with pytest.raises(ValueError, match="at least one"):
        seal({}, {})
    with pytest.raises(ValueError, match="no salt"):
        seal(ENTRIES, {})


def _recipients(**changes: Any) -> dict[str, Any]:
    rule = {
        "sink": "stripe",
        "operation": "refunds.create",
        "kind": "email",
        "fields": ["to"],
        "domains": [],
        "addresses": [],
        "tenant_domains": {},
        "subdomains": False,
    }
    rule.update(changes)
    return rule


def _with_recipients(rule: dict[str, Any], **changes: Any) -> Mandate:
    outbound = {**e.mandate_json()["outbound"], "recipients": [rule]}
    return Mandate.from_json(e.mandate_json(outbound=outbound, **changes), signed=False)


def test_a_child_opens_its_own_entry_of_its_parents_sealed_map() -> None:
    sealed = seal(ENTRIES, salts(b"vendor secret", list(ENTRIES)))
    parent = _with_recipients(_recipients(sealed=sealed.root))
    assert parent.outbound.recipients[0].tenant_domains == {}
    opened = _recipients(
        tenant_domains={"org_a": ["acme.example"]},
        openings={"org_a": sealed.openings["org_a"].to_json()},
    )
    child = _with_recipients(opened)
    assert refines(child, parent) == ()
    forged = _with_recipients(
        _recipients(
            tenant_domains={"org_a": ["evil.example"]},
            openings={"org_a": sealed.openings["org_a"].to_json()},
        )
    )
    (problem,) = refines(forged, parent)
    assert "is not an entry its opening puts under the parent's seal" in str(problem)
    unopened = _with_recipients(_recipients(tenant_domains={"org_a": ["acme.example"]}))
    assert "allows nothing" in str(refines(unopened, parent)[0])
    resealed = _with_recipients(_recipients(sealed="ab" * 32))
    assert "seals another map" in str(refines(resealed, parent)[0])
    with pytest.raises(MalformedError, match="opens a tenant it names no domains of"):
        _with_recipients(
            _recipients(
                openings={"org_a": sealed.openings["org_a"].to_json()}, domains=["x.example"]
            )
        )


def test_a_sealed_map_grants_nothing_where_it_is_sealed() -> None:
    sealed = seal(ENTRIES, salts(b"vendor secret", list(ENTRIES)))
    parent = _with_recipients(_recipients(sealed=sealed.root))
    compiled = compile([parent], deployment=deploy.deployment())
    (rule,) = [c for c in compiled.checkers if type(c).__name__ == "RecipientAllowlist"]
    state = repr([getattr(rule, n, None) for n in getattr(type(rule), "__slots__", ())])
    assert "acme.example" not in state and "sealed.invalid" in state
    described = [a for n, a in compiled.description["checkers"] if n == "RecipientAllowlist"]
    assert described[0]["sealed"] == sealed.root and described[0]["tenant_domains"] == {}


# -- refinement ------------------------------------------------------------------


def test_a_child_keeps_every_bound_its_parent_sets() -> None:
    parent = _mandate(
        budgets={"mme": MME},
        data=_data(
            partitions={"patient": {"max": 1, "declared": True}},
            drawdown=[
                {
                    "table": "orders",
                    "column": "total",
                    "per": "tenant",
                    "max_drop": "0.3",
                    "max_rise": "0.5",
                }
            ],
        ),
    )
    assert refines(parent, parent) == ()
    cases: list[tuple[dict[str, Any], str]] = [
        ({"budgets": {}}, "budgets.mme: is dropped"),
        ({"budgets": {"mme": {**MME, "envelope": "901"}}}, "budgets.mme.envelope"),
        ({"budgets": {"mme": {**MME, "places": 0}}}, "budgets.mme.places"),
        ({"data": _data(partitions={"patient": {"max": 2, "declared": True}})}, "max"),
        ({"data": _data(partitions={"patient": {"max": 1, "declared": False}})}, "declared"),
        ({"data": _data()}, "data.partitions.patient: is dropped"),
    ]
    for changes, match in cases:
        raw = e.mandate_json(**{"budgets": {"mme": MME}, **changes})
        if "data" not in changes:
            raw["data"] = parent.to_json()["data"]
        if "budgets" in changes and not changes["budgets"]:
            del raw["budgets"]
        child = Mandate.from_json(raw, signed=False)
        found = " ".join(map(str, refines(child, parent)))
        assert match in found, (changes, found)
    looser = parent.to_json()
    looser["data"]["drawdown"][0].pop("max_rise")
    child = Mandate.from_json({k: v for k, v in looser.items()}, signed=False)
    assert "max_rise: is unbounded" in " ".join(map(str, refines(child, parent)))


# -- compilation -----------------------------------------------------------------


def test_the_new_fields_compile_to_interlocks_checkers_and_meters() -> None:
    window = {**e.mandate_json()["windows"][0], "per": "partition:patient"}
    mandate = _mandate(
        budgets={"mme": MME},
        windows=[window],
        data=_data(
            partitions={"patient": {"max": 1, "declared": True}},
            drawdown=[
                {
                    "table": "orders",
                    "column": "total",
                    "per": "partition:patient",
                    "max_drop": None,
                    "max_rise": "0",
                },
                {
                    "table": "orders",
                    "column": "total",
                    "per": "table",
                    "max_drop": "0.5",
                    "max_rise": "1",
                },
            ],
        ),
    )
    with pytest.raises(CompileError, match="keeps no ledger of mme"):
        compile([mandate], deployment=deploy.deployment())
    with pytest.raises(CompileError, match="counts mme to 0 places"):
        compile([mandate], deployment=deploy.deployment(units={"mme": 0}))
    compiled = compile([mandate], deployment=deploy.deployment(units={"mme": 8}))
    names = [c.name for c in compiled.checkers]
    assert "partition_isolation:patient" in names
    assert "partition_value_guard:patient:orders.total" in names
    isolation = next(c for c in compiled.checkers if c.name == "partition_isolation:patient")
    assert isinstance(isolation, TenantIsolation)
    assert any(isinstance(c, ColumnValueGuard) for c in compiled.checkers)
    assert any(isinstance(c, TenantDrawdownGuard) for c in compiled.checkers)
    (compiled_window,) = compiled.windows
    assert compiled_window.per == "partition:patient"
    (meter,) = compiled.meters
    assert (meter.unit, meter.per_plan) == ("mme", Decimal(90))
    assert any(isinstance(c, MeterCheck) for c in compiled.engine_checkers())
    plain = compile([e.mandate()], deployment=deploy.deployment(units={"mme": 8}))
    assert "budgets" not in plain.description and plain.meters == ()


def test_a_tool_declares_partitions_from_its_arguments() -> None:
    binding = (
        Tool.plan("order_dose", "Order a dose for one patient.")
        .argument("patient_id", {"type": "string"})
        .argument("dose", {"type": "integer"})
        .insert(
            "order",
            "orders",
            "INSERT INTO orders (patient, total) VALUES (:p, :d)",
            partitions={"patient": "$.patient_id"},
            p="$.patient_id",
            d="$.dose",
        )
        .build()
    )
    (effect,) = binding.effects
    assert effect.partitions == {"patient": "$.patient_id"}
    plan = build_plan(
        binding, {"patient_id": "p-7", "dose": 5}, scope="s", trajectory="t", tenant="acme"
    )
    (built,) = plan.effects
    assert built.partitions == {"patient": "p-7"} and built.tenant_id == "acme"
    assert ToolBinding.from_json(binding.to_json(), signed=False).digest == binding.digest
    raw = binding.to_json()
    raw["effects"][0]["partitions"] = {"patient": "$.nobody"}
    with pytest.raises(MalformedError, match="does not declare"):
        ToolBinding.from_json(raw, signed=False)
    raw["effects"][0]["partitions"] = {"patient": "$session.tenant"}
    with pytest.raises(MalformedError, match="an argument"):
        ToolBinding.from_json(raw, signed=False)
    plain = (
        Tool.plan("x", "x")
        .argument("a", {"type": "string"})
        .update("u", "orders", "UPDATE orders SET status = :a", a="$.a")
        .build()
    )
    assert "partitions" not in plain.to_json()["effects"][0]

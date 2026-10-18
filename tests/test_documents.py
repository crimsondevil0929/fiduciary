"""The documents' own rules (``docs/FIDUCIARY_SPEC.md`` §4): each one refused
with the code it fails, and every example read back to what it was."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest

from fiduciary import documents
from fiduciary.documents import Approval, Charter, Mandate, Record, ToolBinding, suspension_digest
from fiduciary.documents.tool import placeholders
from fiduciary.exceptions import MalformedError
from tests import examples as e


@pytest.mark.parametrize("make", [e.charter, e.mandate, e.child, e.tool, e.lookup, e.approval])
def test_every_example_reads_back_to_itself(make: Callable[[], documents.Document]) -> None:
    document = make()
    again = documents.loads(document.dumps())
    assert again == document
    assert again.digest == document.digest
    assert type(document).loads(document.pretty()) == document


def test_load_refuses_what_is_no_document() -> None:
    for raw, code in (
        ([], "type"),
        ({"v": "FIDU2"}, "version"),
        ({"v": "FIDU1", "type": "receipt"}, "version"),
    ):
        with pytest.raises(MalformedError) as caught:
            documents.load(raw)
        assert caught.value.code == code


def test_loads_reads_a_draft_or_a_signed_document() -> None:
    assert documents.loads(e.charter().dumps()).signature is not None
    assert (
        documents.loads(Charter.from_json(e.charter_json(), signed=False).dumps()).signature is None
    )


def _refused(cls: type[documents.Document], raw: dict[str, Any], code: str, path: str = "") -> None:
    with pytest.raises(MalformedError) as caught:
        cls.from_json(raw, signed=False)
    assert caught.value.code == code, caught.value
    assert caught.value.path.startswith(path), caught.value


# -- the charter (F4-1 to F4-6) ------------------------------------------------------


def _principals(*names: str) -> list[dict[str, str]]:
    return [{"name": n, "key": e.key(n)} for n in names]


@pytest.mark.parametrize(
    ("changes", "code", "path"),
    [
        ({"principals": _principals("head-ops", "cfo", "security")}, "set", "$.principals"),
        ({"principals": [*_principals("cfo"), *_principals("cfo")]}, "set", "$.principals"),
        (
            {
                "principals": [
                    {"name": "a", "key": e.key("cfo")},
                    {"name": "b", "key": e.key("cfo")},
                ]
            },
            "agreement",
            "$.principals",
        ),
        ({"principals": []}, "agreement", "$.principals"),
        ({"quorums": {"charter": 4, "issue": 2, "revoke": 1}}, "agreement", "$.quorums"),
        ({"quorums": {"charter": 2, "issue": 3, "revoke": 1}}, "agreement", "$.quorums"),
        ({"quorums": {"charter": 3, "issue": 1, "revoke": 2}}, "agreement", "$.quorums"),
        ({"quorums": {"charter": 3, "issue": 2, "revoke": 0}}, "range", "$.quorums.revoke"),
        ({"version": 2}, "agreement", "$.prev"),
        ({"prev": "0" * 64}, "agreement", "$.prev"),
        ({"break_glass": ["b" * 16, "a" * 16]}, "set", "$.break_glass"),
        ({"break_glass": ["xyz"]}, "format", "$.break_glass"),
        ({"deployment": {"ledger": "nope", "label": "x"}}, "format", "$.deployment.ledger"),
        (
            {"principals": [{"name": "cfo", "key": "hmac-sha256:" + "00" * 32}]},
            "format",
            "$.principals",
        ),
    ],
)
def test_the_charter_refuses(changes: dict[str, Any], code: str, path: str) -> None:
    _refused(Charter, e.charter_json(**changes), code, path)


def test_the_charter_names_its_principals() -> None:
    charter = e.charter()
    assert charter.names == e.PRINCIPALS
    found = charter.principal("cfo")
    assert found is not None and found.public_key.key_id == e.signer("cfo").key_id
    assert charter.principal("nobody") is None


# -- the mandate (F4-7 to F4-12) -----------------------------------------------------


def _mandate(edit: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    raw = copy.deepcopy(e.mandate_json())
    edit(raw)
    return raw


def _set(path: str, value: Any) -> Callable[[dict[str, Any]], None]:
    def edit(raw: dict[str, Any]) -> None:
        *parents, last = path.split(".")
        target = raw
        for part in parents:
            target = target[int(part)] if isinstance(target, list) else target[part]
        target[last] = value

    return edit


MANDATE_REFUSALS: list[tuple[Callable[[dict[str, Any]], None], str, str]] = [
    (_set("version", 2), "agreement", "$.prev"),
    (_set("issuance", "derived"), "agreement", "$.issuance"),
    (_set("subject.funded_from", "support-agent"), "agreement", "$.subject"),
    (_set("term.not_after", "2026-10-01T00:00:00.000000Z"), "agreement", "$.term"),
    (_set("roster.principals", []), "agreement", "$.roster"),
    (_set("roster.quorums.amend", 4), "agreement", "$.roster.quorums.amend"),
    (_set("roster.quorums.reduce", 2), "agreement", "$.roster.quorums"),
    (_set("roster.quorums.revoke", 3), "agreement", "$.roster.quorums"),
    (_set("roster.quorums.bogus", 1), "fields", "$.roster.quorums"),
    (_set("budget.per_call", "300.00000000"), "agreement", "$.budget.per_call"),
    (_set("budget.envelope", "0.00000000"), "range", "$.budget.envelope"),
    (_set("budget.models", []), "agreement", "$.budget.models"),
    (_set("budget.models", ["b", "a"]), "set", "$.budget.models"),
    (_set("governance.cognitive.similarity_threshold", "1.5"), "range", "$.governance"),
    (_set("governance.cognitive.similarity_threshold", "0.70"), "format", "$.governance"),
    (_set("data.tables", {}), "agreement", "$.data.tables"),
    (_set("data.tables", {"orders": []}), "agreement", "$.data.tables"),
    (_set("data.tables", {"orders": ["truncate"]}), "format", "$.data.tables"),
    (_set("data.tables", {"1orders": ["update"]}), "format", "$.data.tables"),
    (_set("data.tenants", ["b", "a"]), "set", "$.data.tenants"),
    (_set("data.max_rows", 0), "range", "$.data.max_rows"),
    (
        _set(
            "data.drawdown", [{"table": "items", "column": "x", "per": "table", "max_drop": "0.1"}]
        ),
        "agreement",
        "$.data.drawdown",
    ),
    (
        _set(
            "data.drawdown",
            [
                {"table": "orders", "column": "total", "per": "tenant", "max_drop": "0.3"},
                {"table": "orders", "column": "total", "per": "tenant", "max_drop": "0.2"},
            ],
        ),
        "set",
        "$.data.drawdown",
    ),
    (
        _set(
            "data.drawdown",
            [
                {"table": "orders", "column": "total", "per": "tenant", "max_drop": "0.3"},
                {"table": "orders", "column": "total", "per": "table", "max_drop": "0.3"},
            ],
        ),
        "set",
        "$.data.drawdown",
    ),
    (_set("data.drawdown.0.max_drop", "1.2"), "range", "$.data.drawdown"),
    (
        _set(
            "data.facts",
            [{"kind": [], "field": "a", "table": "t", "column": "c", "key": None, "exempt": []}],
        ),
        "agreement",
        "$.data.facts",
    ),
    (_set("data.agreements.0.key", ["a"]), "range", "$.data.agreements"),
    (_set("outbound.per_sink", {"mail": 1}), "agreement", "$.outbound.per_sink"),
    (_set("outbound.sinks.stripe.operations", []), "agreement", "$.outbound.sinks"),
    (_set("outbound.caps.0.operation", "charges.create"), "agreement", "$.outbound.caps"),
    (_set("outbound.caps.0.currency_field", None), "agreement", "$.outbound.caps"),
    (_set("outbound.caps.0.maximum", {}), "agreement", "$.outbound.caps"),
    (_set("outbound.caps.0.maximum", {"US": "1"}), "format", "$.outbound.caps"),
    (
        _set(
            "outbound.recipients",
            [
                {
                    "sink": "stripe",
                    "operation": "refunds.create",
                    "kind": "email",
                    "fields": ["to"],
                    "domains": [],
                    "addresses": [],
                    "tenant_domains": {},
                    "subdomains": False,
                }
            ],
        ),
        "agreement",
        "$.outbound.recipients",
    ),
    (
        _set(
            "outbound.recipients",
            [
                {
                    "sink": "stripe",
                    "operation": "refunds.create",
                    "kind": "url",
                    "fields": ["to"],
                    "domains": [],
                    "addresses": ["a@b.com"],
                    "tenant_domains": {},
                    "subdomains": False,
                }
            ],
        ),
        "agreement",
        "$.outbound.recipients",
    ),
    (
        _set(
            "outbound.recipients",
            [
                {
                    "sink": "stripe",
                    "operation": "refunds.create",
                    "kind": "email",
                    "fields": [],
                    "domains": ["acme.com"],
                    "addresses": [],
                    "tenant_domains": {},
                    "subdomains": False,
                }
            ],
        ),
        "agreement",
        "$.outbound.recipients",
    ),
    (
        _set(
            "outbound.recipients",
            [
                {
                    "sink": "mail",
                    "operation": "send",
                    "kind": "email",
                    "fields": ["to"],
                    "domains": ["acme.com"],
                    "addresses": [],
                    "tenant_domains": {},
                    "subdomains": False,
                }
            ],
        ),
        "agreement",
        "$.outbound.recipients",
    ),
    (_set("windows.0.per", "scope"), "format", "$.windows"),
    (_set("windows.0.name", "Refunds"), "format", "$.windows"),
    (_set("windows.0.measure", {"kind": "bogus"}), "format", "$.windows"),
    (_set("windows.0.measure", {"kind": "plans", "sink": "x"}), "fields", "$.windows"),
    (_set("windows.0.measure", []), "type", "$.windows"),
    (_set("tools.statements", "some"), "format", "$.tools"),
    (
        _set("tools.bindings", list(reversed(e.mandate_json()["tools"]["bindings"]))),
        "set",
        "$.tools.bindings",
    ),
    (_set("recovery.step_estimate", "6.00000000"), "agreement", "$.recovery"),
    (_set("recovery.directives.0.answers", []), "agreement", "$.recovery"),
    (_set("recovery.revoke_on", ["exploded"]), "format", "$.recovery"),
    (_set("extension.max_grant", "200.00000000"), "agreement", "$.extension"),
    (_set("extension.auto.max_grant", "20.00000000"), "agreement", "$.extension.auto"),
    (_set("extension.auto.max_total", "500.00000000"), "agreement", "$.extension.auto"),
    (_set("escalation.auto.0.class", "bogus"), "format", "$.escalation"),
    (_set("escalation.auto.0.resolution", "compensate_late"), "format", "$.escalation"),
    (_set("escalation.auto.0.when", {"max_amount": "1.00000000"}), "fields", "$.escalation"),
    (_set("escalation.auto.0.when", []), "type", "$.escalation"),
    (
        _set(
            "escalation.auto", [{"id": "r", "class": "dead", "resolution": "requeue", "when": {}}]
        ),
        "agreement",
        "$.escalation.auto",
    ),
    (_set("delegation.envelope", "300.00000000"), "agreement", "$.delegation"),
]


@pytest.mark.parametrize(("edit", "code", "path"), MANDATE_REFUSALS)
def test_the_mandate_refuses(edit: Callable[[dict[str, Any]], None], code: str, path: str) -> None:
    _refused(Mandate, _mandate(edit), code, path)


def test_an_automatic_grant_needs_its_bounds() -> None:
    def edit(raw: dict[str, Any]) -> None:
        raw["extension"]["auto"] = None
        raw["escalation"]["auto"] = [
            {
                "id": "grant",
                "class": "extension",
                "resolution": "grant",
                "when": {"max_amount": "1.00000000"},
            }
        ]

    _refused(Mandate, _mandate(edit), "agreement", "$.escalation.auto")


def test_every_rule_vocabulary_reads() -> None:
    def edit(raw: dict[str, Any]) -> None:
        raw["escalation"]["auto"] = [
            {
                "id": "a-trim",
                "class": "refused",
                "resolution": "accept_repair",
                "when": {"causes": ["depends", "refused"], "exhaustive": False, "max_dropped": 0},
            },
            {
                "id": "b-requeue",
                "class": "dead",
                "resolution": "requeue",
                "when": {"max_requeues": 2, "outcome": ["timeout"]},
            },
            {
                "id": "c-grant",
                "class": "extension",
                "resolution": "grant",
                "when": {"max_amount": "2.00000000"},
            },
            {"id": "d-cancel", "class": "held", "resolution": "cancel", "when": {}},
        ]

    mandate = Mandate.from_json(_mandate(edit), signed=False)
    assert [r.rule_id for r in mandate.escalation.auto] == [
        "a-trim",
        "b-requeue",
        "c-grant",
        "d-cancel",
    ]
    assert Mandate.from_json(mandate.to_json(), signed=False) == mandate


def test_every_measure_and_optional_section_reads() -> None:
    def edit(raw: dict[str, Any]) -> None:
        raw["windows"] = [
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
                "per": "tenant",
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
                "per": "lineage",
            },
        ]
        raw["data"]["tenants"] = ["acme", "beta"]
        raw["data"]["stated_footprint"] = None
        raw["data"]["facts"] = [
            {
                "kind": ["charge.refunded"],
                "field": "amount",
                "table": "refunds",
                "column": "amount",
                "key": ["id", "order_id"],
                "exempt": ["0"],
            }
        ]
        raw["outbound"]["max_per_plan"] = None
        raw["outbound"]["tenants"] = None
        raw["outbound"]["caps"][0]["maximum"] = "200"
        raw["outbound"]["caps"][0]["currency_field"] = None
        raw["outbound"]["recipients"] = [
            {
                "sink": "stripe",
                "operation": "refunds.create",
                "kind": "email",
                "fields": ["to.*.email"],
                "domains": ["acme.com"],
                "addresses": ["ops@beta.org"],
                "tenant_domains": {"acme": ["acme.com"]},
                "subdomains": True,
            }
        ]
        raw["recovery"]["revocable"] = ["lookup_order"]
        raw["recovery"]["token_ceiling"] = None
        raw["recovery"]["funding"] = "support"
        raw["extension"] = None
        raw["escalation"]["auto"] = []
        raw["delegation"] = None
        raw["parent"] = None

    mandate = Mandate.from_json(_mandate(edit), signed=False)
    assert Mandate.from_json(mandate.to_json(), signed=False) == mandate
    assert mandate.windows[2].measure.rows == "net"
    nothing = Mandate.from_json(_mandate(lambda raw: raw.update(recovery=None)), signed=False)
    assert nothing.recovery is None


def test_charter_problems() -> None:
    charter, mandate = e.charter(), e.mandate()
    assert mandate.charter_problems(charter) == ()
    other = Charter.from_json(
        e.charter_json(charter_id="00000000-0000-4000-8000-000000000000"), signed=False
    )
    assert "names charter" in mandate.charter_problems(other)[0]
    relabeled = Charter.from_json(
        e.charter_json(deployment={"ledger": e.LEDGER, "label": "x"}), signed=False
    )
    assert "another digest" in mandate.charter_problems(relabeled)[0]
    stranger = Mandate.from_json(
        _mandate(_set("roster.principals", ["cfo", "head-ops", "zed"])), signed=False
    )
    assert any("zed" in p for p in stranger.charter_problems(charter))
    low = Mandate.from_json(_mandate(_set("roster.quorums.amend", 1)), signed=False)
    assert any("amend quorum" in p for p in low.charter_problems(charter))


# -- the tool binding (F4-13 to F4-16) -----------------------------------------------------


def _tool(edit: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    raw = copy.deepcopy(e.tool_json())
    edit(raw)
    return raw


TOOL_REFUSALS: list[tuple[Callable[[dict[str, Any]], None], str, str]] = [
    (_set("input.additionalProperties", True), "agreement", "$.input"),
    (_set("input.type", "array"), "agreement", "$.input"),
    (_set("input.format", "email"), "fields", "$.input"),
    (_set("input.properties.amount.pattern", "("), "format", "$.input"),
    (_set("input.properties.amount.type", "float"), "format", "$.input"),
    (_set("input.properties.amount.minLength", -1), "range", "$.input"),
    (_set("input.properties.amount.minimum", "one"), "format", "$.input"),
    (_set("input.properties.amount.enum", "x"), "type", "$.input"),
    (_set("input.properties.amount.additionalProperties", "no"), "type", "$.input"),
    (_set("input.properties", []), "type", "$.input"),
    (_set("input.required", ["order_id", "amount"]), "set", "$.input"),
    (_set("kind", "passthrough"), "agreement", "$.effects"),
    (
        _set("effects.0.statement", "UPDATE orders SET total = ? WHERE id = :order_id"),
        "agreement",
        "$.effects[0].statement",
    ),
    (_set("effects.0.parameters", {"amount": "$.amount"}), "agreement", "$.effects[0].parameters"),
    (_set("effects.0.parameters.amount", "$.missing"), "agreement", "$.effects[0].parameters"),
    (_set("effects.0.parameters.amount", "$secret"), "format", "$.effects[0].parameters"),
    (_set("effects.1.after", ["stripe-refund"]), "agreement", "$.effects[1].after"),
    (_set("requests.0.id", "debit"), "agreement", "$.requests[0].id"),
    (_set("requests.0.payload.amount", "$.missing"), "agreement", "$.requests[0].payload"),
    (_set("requests.0.payload.extra", [{"x": "$nope"}]), "format", "$.requests[0].payload"),
    (_set("requests.0.payload", []), "type", "$.requests[0].payload"),
    (
        _set(
            "requests.0.compensation",
            {"sink": "stripe", "operation": "refunds.cancel", "payload": []},
        ),
        "type",
        "$.requests[0].compensation",
    ),
    (_set("effects.0.table", "1orders"), "format", "$.effects[0].table"),
    (_set("tenant", "none"), "agreement", "$.effects[0].parameters"),
]


@pytest.mark.parametrize(("edit", "code", "path"), TOOL_REFUSALS)
def test_the_tool_binding_refuses(
    edit: Callable[[dict[str, Any]], None], code: str, path: str
) -> None:
    _refused(ToolBinding, _tool(edit), code, path)


def test_a_plan_tool_builds_something_and_a_passthrough_nothing() -> None:
    _refused(
        ToolBinding,
        _tool(lambda raw: raw.update(effects=[], requests=[])),
        "agreement",
        "$.effects",
    )
    lookup = e.lookup()
    assert lookup.kind == "passthrough" and not lookup.effects


def test_a_compensation_reads_its_sources() -> None:
    def edit(raw: dict[str, Any]) -> None:
        raw["requests"][0]["compensation"] = {
            "sink": "stripe",
            "operation": "refunds.cancel",
            "payload": {"refund": "$.payment_intent"},
        }

    tool = ToolBinding.from_json(_tool(edit), signed=False)
    assert tool.requests[0].compensation is not None
    assert ToolBinding.from_json(tool.to_json(), signed=False) == tool
    bad = _tool(
        lambda raw: raw["requests"][0].update(
            compensation={
                "sink": "stripe",
                "operation": "refunds.cancel",
                "payload": {"x": "$.nope"},
            }
        )
    )
    _refused(ToolBinding, bad, "agreement", "$.requests[0].compensation.payload")


def test_placeholders_are_named_and_found_outside_literals() -> None:
    assert placeholders("UPDATE t SET a = :a, b = ':not' WHERE id = :id::int") == ("a", "id")
    assert placeholders("SELECT '?' FROM t WHERE x = :x") == ("x",)
    with pytest.raises(ValueError, match="positional"):
        placeholders("UPDATE t SET a = ?")


def test_a_binding_digests_its_statements() -> None:
    effect = e.tool().effects[0]
    import hashlib

    assert effect.statement_digest == hashlib.sha256(effect.statement.encode()).hexdigest()


# -- the approval (F4-21 to F4-23) ---------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "code", "path"),
    [
        ({"resolution": "a" * 64}, "agreement", "$.resolution"),
        ({"subject": "ticket", "digest": "a" * 64}, "agreement", "$.resolution"),
        ({"subject": "warrant", "digest": "a" * 64}, "format", "$.subject.type"),
        ({"not_after": "2026-10-18T10:05:40.004211Z"}, "agreement", "$.not_after"),
        ({"decision": "maybe"}, "format", "$.decision"),
        ({"principal": "Head Ops"}, "format", "$.principal"),
    ],
)
def test_the_approval_refuses(changes: dict[str, Any], code: str, path: str) -> None:
    _refused(Approval, e.approval_json(**changes), code, path)


def test_an_approval_of_a_ticket_names_its_resolution() -> None:
    raw = e.approval_json(subject="ticket", resolution="b" * 64)
    approval = Approval.from_json(raw, signed=False)
    assert approval.resolution == "b" * 64 and approval.approves


def test_an_approval_is_at_most_1024_bytes() -> None:
    raw = e.approval_json()
    approval = Approval.from_json(raw, signed=False).signed(e.signer("head-ops"))
    assert len(approval.canonical()) <= 1024
    padded = approval.to_json()
    padded["principal"] = "p" + "x" * 63
    padded["approval_id"] = padded["approval_id"]
    padded["subject"]["digest"] = "f" * 64
    assert len(e.canonical(padded)) <= 1024  # the largest an approval's fields allow fits


def test_the_suspension_subject_is_a_digest_of_what_it_suspends() -> None:
    one = suspension_digest(e.CHARTER_ID, "cfo", e.key("cfo"))
    assert one != suspension_digest(e.CHARTER_ID, "cfo", e.key("head-ops"))
    assert len(one) == 64


# -- the record (F4-26) ---------------------------------------------------------------------


def test_a_record_reads_back_and_links() -> None:
    first = Record.make(
        e.SERVICE,
        log="fiduciary",
        seq=1,
        kind="charter.adopted",
        body={"a": 1},
        prev="0" * 64,
        issued_at=e.at("2026-10-18T10:00:00"),
    )
    assert Record.loads(first.dumps()) == first
    assert first.hash == first.digest
    raw = first.to_json()
    for changes, code in (
        ({"seq": 2}, "agreement"),
        ({"kind": "invented"}, "format"),
        ({"body": []}, "type"),
        ({"log": "has space"}, "format"),
    ):
        with pytest.raises(MalformedError) as caught:
            Record.from_json({**raw, **changes})
        assert caught.value.code == code
    with pytest.raises(ValueError, match="no FIDU1 record kind"):
        Record.make(
            e.SERVICE,
            log="f",
            seq=1,
            kind="invented",
            body={},
            prev="0" * 64,
            issued_at=e.at("2026-10-18T10:00:00"),
        )
    with pytest.raises(MalformedError):
        Record.make(
            e.SERVICE,
            log="f",
            seq=1,
            kind="charter.adopted",
            body={"x": 1.5},
            prev="0" * 64,
            issued_at=e.at("2026-10-18T10:00:00"),
        )

"""The domain packs (``fiduciary.demo.packs``) and ``fiduciary demo --domain``:
each industry's acts show what they say they show, measured on a deployment of
its own; the overview counts only what ran; each pack's gateway decides alike
on both Cedar engines; and every bundle verifies with the ordinary command."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from typing import Any

import pytest

from fiduciary import cli
from fiduciary.demo import DOMAINS, Outcome, run
from fiduciary.demo.overview import ACCEPTANCE, overview
from fiduciary.demo.packs import PACKS
from fiduciary.demo.packs.kit import PackGateway


@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Outcome:
    return run(tmp_path_factory.mktemp("packs") / "all", engine="builtin", domain="all")


def _page(outcome: Outcome, key: str) -> dict[str, Any]:
    pages: list[dict[str, Any]] = outcome.payload["domains"]
    return next(p for p in pages if p["demo"]["domain"]["key"] == key)


def _act(outcome: Outcome, domain: str, key: str) -> dict[str, Any]:
    acts: list[dict[str, Any]] = _page(outcome, domain)["demo"]["acts"]
    return next(a for a in acts if a["key"] == key)


def _blocking(call: dict[str, Any]) -> set[str]:
    return {
        f["check"].split(":")[0]
        for f in call["measured"]["findings"]
        if f["severity"] == "blocking"
    }


def test_every_industry_holds(stack: Outcome) -> None:
    assert stack.held
    assert [p["demo"]["domain"]["key"] for p in stack.payload["domains"]] == list(PACKS)
    for key in PACKS:
        acts = _page(stack, key)["demo"]["acts"]
        assert [a["n"] for a in acts] == [1, 2, 3, 4, 5, 6, 7], key
        for name in ("dashboard.html", "demo.json", "before.fidb", "after.fidb", "report.md"):
            assert (stack.root / key / name).is_file(), (key, name)
    for name in ("dashboard.html", "before.fidb", "after.fidb"):
        assert (stack.root / name).is_file()  # fintech, in the directory itself


def test_every_permitted_call_that_rolled_back_left_the_database_as_it_was(stack: Outcome) -> None:
    for key in PACKS:
        for act in _page(stack, key)["demo"]["acts"]:
            for call in act.get("calls", []):
                assert call["gateway"]["decision"] == "PERMIT", (key, act["key"])
                if call["outcome"] == "refused":
                    assert call["unchanged"] is True, (key, act["key"])


def test_healthcare(stack: Outcome) -> None:
    (queue,) = _act(stack, "healthcare", "queue")["calls"]
    assert queue["outcome"] == "committed" and queue["measured"]["outbound"]
    (cascade,) = _act(stack, "healthcare", "cascade")["calls"]
    m = cascade["measured"]
    assert m["rows"] == 31 and m["tables"] == {
        "admin_schedule": 25,
        "med_orders": 3,
        "pharmacy_queue": 3,
    }
    assert "blast_radius" in _blocking(cascade)
    wrong, claimed = _act(stack, "healthcare", "injected")["calls"]
    assert "tenant_isolation" in _blocking(wrong)
    assert _blocking(claimed) == {"fact_agreement"}
    fleet = _act(stack, "healthcare", "fleet")
    assert [c["outcome"] for c in fleet["calls"]] == ["committed", "committed", "refused"]
    assert fleet["refused_by"] and fleet["refused_by"][0].startswith("rate_window:")
    quorum = _act(stack, "healthcare", "quorum")
    assert [v["counted"] for v in quorum["votes"]] == [True, True, False, True]
    assert len(quorum["warrant"]["basis"]) == 2 and quorum["delivered"] == "delivered"


def test_sre(stack: Outcome) -> None:
    (cascade,) = _act(stack, "sre", "cascade")["calls"]
    m = cascade["measured"]
    assert m["tables"] == {"deployments": 1, "pods": 56}
    assert {"blast_radius", "cross_effect_agreement", "rate_window"} <= _blocking(cascade)
    (replace,) = _act(stack, "sre", "replace")["calls"]
    assert _blocking(replace) == {"no_delete"}
    assert replace["measured"]["tables"] == {"db_instances": 2, "node_groups": 1}
    fleet = _act(stack, "sre", "fleet")
    assert (fleet["sessions"], fleet["committed"]) == (5, 3)


def test_supply_chain(stack: Outcome) -> None:
    (cascade,) = _act(stack, "supply-chain", "cascade")["calls"]
    assert cascade["measured"]["rows"] == 401
    assert cascade["measured"]["tables"] == {"po_lines": 400, "reorder_points": 1}
    assert "blast_radius" in _blocking(cascade)
    (drawdown,) = _act(stack, "supply-chain", "drawdown")["calls"]
    assert _blocking(drawdown) == {"tenant_drawdown_guard"}
    (bank,) = _act(stack, "supply-chain", "injected")["calls"]
    assert _blocking(bank) == {"table_allowlist"}
    assert "vendor_bank_accounts" in bank["measured"]["tables"]
    amend = _act(stack, "supply-chain", "amend")
    options = {o["action"]: o["quorum"] for o in amend["ticket"]["options"]}
    assert options == {"amend": 2, "reject": 1}
    assert [(v["principal"], v["action"]) for v in amend["votes"]] == [
        ("ap-manager", "amend"),
        ("treasury", "reject"),
    ]
    assert amend["warrant_before_quorum"] is False
    assert amend["warrant"]["action"] == "reject" and amend["version_after"] == 1


def test_saas(stack: Outcome) -> None:
    (cascade,) = _act(stack, "saas", "cascade")["calls"]
    assert len(cascade["measured"]["tenants"]) == 4
    assert {"tenant_isolation", "blast_radius"} <= _blocking(cascade)
    viewer, owner = _act(stack, "saas", "grant")["calls"]
    assert viewer["outcome"] == "committed"
    assert _blocking(owner) == {"table_allowlist"}
    assert "admin_grants" in owner["measured"]["tables"]
    customers = _act(stack, "saas", "trust-center")["customers"]
    assert len(customers) == 4
    for customer in customers:
        assert customer["passed"] and customer["sockets"] == 0
        assert customer["other_records"] == 0
        assert (stack.root / "saas" / customer["file"]).is_file()
    # Stated on the page: each bundle carries the whole log, which names every org key.
    assert {c["other_labels"] for c in customers} == {3}


def test_every_audit_finds_no_planted_value_and_names_the_break_glass_act(stack: Outcome) -> None:
    for key, pack in PACKS.items():
        audit = _act(stack, key, "audit")
        before, after = audit["verdicts"]["before"], audit["verdicts"]["after"]
        assert before["passed"] and after["exit_code"] == 7, key
        assert any("[break-glass]" in f for f in after["findings"]), key
        for verdict in (before, after):
            assert verdict["sockets"] == 0
            assert set(verdict["scan"]["found"].values()) == {0}, (key, verdict["scan"]["hits"])
            assert verdict["scan"]["planted"]["rows"] > 0 and verdict["scan"]["planted"]["secrets"]
        assert audit["labels"]["partition"] == pack.partition
        attestation = _page(stack, key)["attestations"]["after"]
        assert attestation["verdict"]["status"] == "BREAK-GLASS DETECTED"
        assert [o["label"] for o in attestation["operators"] if o["ambient"]] == ["break-glass"]


def test_each_pack_opens_with_the_ordinary_commands(stack: Outcome) -> None:
    for key in PACKS:
        root = stack.root / key
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            assert cli.main(["--config", str(root / "fiduciary.toml"), "verify"]) == 0, key
        assert printed.getvalue().startswith("PASS")
        charter = (root / "charter.digest").read_text().strip()
        keys = str(root / "pinned-keys.json")
        files = [("before.fidb", 0), ("after.fidb", 7)]
        files += [(p.name, 0) for p in sorted(root.glob("customer-*.fidb"))]
        for name, code in files:
            argv = ["verify-bundle", str(root / name), "--charter", charter, "--keys", keys]
            with contextlib.redirect_stdout(io.StringIO()):
                assert cli.main(argv) == code, (key, name)


def test_the_overview_counts_only_what_ran(stack: Outcome) -> None:
    found = stack.payload["overview"]
    rows = {r["key"]: r for r in found["industries"]}
    assert [r["key"] for r in found["industries"]] == [
        "fintech",
        "healthcare",
        "sre",
        "supply-chain",
        "saas",
        "coding",
    ]
    assert all(rows[k]["ran"] for k in ("fintech", *PACKS))
    assert not rows["coding"]["ran"] and "not built" in rows["coding"]["planned"]
    assert rows["sre"]["note"] and "mirror" in rows["sre"]["note"]
    engine = {k: rows[k]["engine"] for k in ("fintech", *PACKS)}
    assert engine["fintech"]["rate_window"] == 45  # every fan-out session, not the sample
    assert engine["healthcare"]["fact_agreement"] == 1
    assert engine["sre"]["no_delete"] == 1
    assert engine["supply-chain"]["tenant_drawdown_guard"] == 1
    assert engine["saas"]["tenant_isolation"] == 1
    assert engine["supply-chain"]["warrant"] == [{"action": "reject", "approvals": 1}]
    assert all(e["break-glass"] and e["offline"] for e in engine.values())
    assert found["acceptance"] == ACCEPTANCE
    assert (ACCEPTANCE["passed"], ACCEPTANCE["cases"], ACCEPTANCE["metered_usd"]) == (
        42,
        42,
        "1.67",
    )
    alone = overview({})
    assert not any(r["ran"] for r in alone["industries"])


def test_the_dashboard_switches_between_industries_offline(stack: Outcome) -> None:
    page = (stack.root / "dashboard.html").read_text()
    lowered = page.lower()
    for reference in ("<script src", "<link", "@import", "url(http", 'src="http', 'href="http'):
        assert reference not in lowered, reference
    start = page.index('id="payload">') + len('id="payload">')
    embedded = json.loads(page[start : page.index("</script>", start)])
    assert embedded["primary"] == "after" and embedded["overview"]["industries"]
    assert len(embedded["domains"]) == len(PACKS)
    for pack in PACKS.values():
        for title in pack.titles.values():
            assert json.dumps(title)[1:-1] in page
    markdown = (stack.root / "report.md").read_text()
    assert markdown.startswith("# Bundle `after`: BREAK-GLASS DETECTED")
    for pack in PACKS.values():
        assert f"## {pack.industry}: {pack.title}" in markdown
    assert "## Control frameworks" in markdown and "not a certification" in markdown


def test_one_pack_alone_and_what_the_command_refuses(tmp_path: Path) -> None:
    out = tmp_path / "sre"
    with contextlib.redirect_stdout(io.StringIO()) as printed:
        assert cli.main(["demo", "--domain", "sre", "--gateway", "builtin", "--out", str(out)]) == 0
    assert "Act 7  Change evidence, verified offline" in printed.getvalue()
    embedded = (out / "demo.json").read_text()
    assert json.loads(embedded)["overview"]["industries"][2]["ran"]
    assert "domains" not in json.loads(embedded)
    with contextlib.redirect_stderr(io.StringIO()) as err:
        argv = ["demo", "--domain", "saas", "--live", "--out", str(tmp_path / "x")]
        assert cli.main(argv) == 2
    assert "--live works the fintech queue" in err.getvalue()
    with pytest.raises(ValueError, match="domain is one of"):
        run(tmp_path / "y", domain="retail")
    with pytest.raises(ValueError, match="--live works the fintech queue"):
        run(tmp_path / "z", domain="healthcare", live=object())  # type: ignore[arg-type]
    assert DOMAINS == ("fintech", "healthcare", "sre", "supply-chain", "saas", "all")


def test_a_recorded_fintech_run_gains_the_other_industries(stack: Outcome, tmp_path: Path) -> None:
    from fiduciary.demo import extend

    fintech = {k: v for k, v in stack.payload.items() if k not in ("domains", "overview")}
    grown = extend(tmp_path / "recorded", fintech, engine="builtin")
    assert grown.held
    assert grown.payload["demo"]["acts"] == stack.payload["demo"]["acts"]
    assert [p["demo"]["domain"]["key"] for p in grown.payload["domains"]] == list(PACKS)


def _requests(key: str) -> list[tuple[str, str, dict[str, Any]]]:
    pack = PACKS[key]
    found = []
    for i, (resource, _) in enumerate(sorted(pack.entities().items())[:6]):
        name, _, value = resource.partition(":")
        tool = next(t for t, arg in pack.resource.items() if arg == name)
        found.append((f"s{i}", tool, {name: int(value), "replicas": 3, "port": 22, "point": 9}))
    for tool, arg in pack.summed.items():
        found += [("t", tool, {arg: 40}), ("t", tool, {arg: 40})]
    found.append(("u", pack.policies[0].action, {}))
    found.append(("u", "no_such_tool", {}))
    return found


@pytest.mark.parametrize("key", list(PACKS))
def test_both_cedar_engines_decide_every_pack_alike(key: str) -> None:
    pytest.importorskip("cedarpy")
    pack = PACKS[key]
    cedar = PackGateway(pack, pack.tenants[0], engine="cedarpy")
    builtin = PackGateway(pack, pack.tenants[0], engine="builtin")
    for gateway in (cedar, builtin):
        gateway.opened("s1", pack.tenants[1])
    decided = []
    for session, tool, arguments in _requests(key):
        a = cedar.decide(session, tool, arguments)
        b = builtin.decide(session, tool, arguments)
        assert (a.decision, a.policies) == (b.decision, b.policies), (tool, arguments)
        decided.append(a.decision)
    assert {"PERMIT", "DENY"} <= set(decided)
    assert "cedarpy" in cedar.engine and builtin.engine == "built-in evaluator"


def test_a_pack_gateway_refuses_an_unknown_engine() -> None:
    pack = PACKS["healthcare"]
    with pytest.raises(ValueError, match="engine is auto, cedarpy or builtin"):
        PackGateway(pack, pack.tenants[0], engine="opa")


def test_each_pack_names_shipped_frameworks_and_distinct_parties() -> None:
    from fiduciary import frameworks

    for key, pack in PACKS.items():
        assert pack.key == key
        assert set(pack.frameworks) <= set(frameworks.ORDER), key
        assert len(set(pack.principals)) == 3 and pack.operator not in pack.principals
        assert {"queue", "break-glass", "audit"} <= set(pack.titles)

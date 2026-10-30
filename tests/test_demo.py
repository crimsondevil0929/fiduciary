"""``fiduciary demo`` (``fiduciary.demo``): every act shows what it says it
shows, measured, on a deployment of its own; the gateway's two engines decide
alike; and the live act runs against a stand-in for the Messages API."""

from __future__ import annotations

import builtins
import contextlib
import io
import json
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agentgov.interceptor import TokenUsage

from fiduciary import cli
from fiduciary.demo import Live, Outcome, run
from fiduciary.demo.acts import REFERENCE
from fiduciary.demo.gateway import Gateway, cedar_text
from fiduciary.demo.world import ORDERS, tenant_of


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> Outcome:
    return run(tmp_path_factory.mktemp("demo") / "stack", engine="builtin")


def _act(outcome: Outcome, key: str) -> dict[str, Any]:
    acts: list[dict[str, Any]] = outcome.payload["demo"]["acts"]
    return next(a for a in acts if a["key"] == key)


def test_the_demo_holds(demo: Outcome) -> None:
    assert demo.held
    assert [a["n"] for a in demo.payload["demo"]["acts"]] == [1, 2, 3, 4, 5, 6, 7]
    for name in ("dashboard.html", "demo.json", "before.fidb", "after.fidb", "report.md"):
        assert (demo.root / name).is_file(), name


def test_the_queue_commits_the_refund_and_rolls_back_the_discount(demo: Outcome) -> None:
    calls = _act(demo, "queue")["calls"]
    assert [c["tool"] for c in calls] == [tool for tool, _ in REFERENCE]
    assert all(c["gateway"]["decision"] == "PERMIT" for c in calls)
    assert [c["outcome"] for c in calls] == ["served", "committed", "refused"]
    discount = calls[2]["measured"]
    assert discount["orders_total"] == {"before": "1000.00", "after": "100.00"}
    assert discount["changed"][0]["changes"]["total"] == ["1000.00", "100.00"]
    assert {f["check"] for f in discount["findings"]} == {"tenant_drawdown_guard:orders.total"}
    assert calls[2]["unchanged"] is True


def test_one_flag_reaches_a_whole_batch_and_is_rolled_back(demo: Outcome) -> None:
    (flag,) = _act(demo, "settlement")["calls"]
    assert flag["gateway"]["decision"] == "PERMIT"
    m = flag["measured"]
    assert not m["committed"] and m["rows"] == 50
    assert set(m["tenants"]) == {"acme", "globex", "initech", "umbrella"}
    blocking = {f["check"] for f in m["findings"] if f["severity"] == "blocking"}
    assert {"blast_radius", "tenant_isolation"} <= blocking
    assert flag["unchanged"] is True


def test_fifty_sessions_pass_the_gateway_and_one_window_holds_them(demo: Outcome) -> None:
    fleet = _act(demo, "fleet")
    assert (fleet["sessions"], fleet["permitted"], fleet["committed"]) == (50, 50, 5)
    assert (fleet["asked"], fleet["stood"], fleet["prevented"]) == ("4500.00", "450.00", "4050.00")
    assert fleet["window"]["last"]["history"] == "462.50"
    children = {c["scope"]: c for c in fleet["children"]}
    assert children["support-agent/returns"]["derived"]
    assert children["support-agent/billing"]["derived"]
    vip = children["support-agent/vip"]
    assert not vip["derived"] and vip["wider"] and "does not refine its parent" in vip["refused"]


def test_a_relay_dying_mid_call_delivers_once(demo: Outcome) -> None:
    crash = _act(demo, "crash")
    assert [(e["event"], e["attempt"], e["actor"]) for e in crash["log"]] == [
        ("sending", 1, "relay-a"),
        ("lost", 1, "relay-b"),
        ("sending", 2, "relay-b"),
        ("delivered", 2, "relay-b"),
    ]
    assert crash["stripe"]["requests_for_message"] == 2
    assert crash["stripe"]["refunds_for_message"] == 1
    assert crash["stripe"]["refunds"] == crash["delivered"] == 6
    assert crash["webhook"]["status"] == 200 and crash["webhook"]["facts"] == 1
    assert crash["forged"]["status"] == 401


def test_the_quorum_counts_principals_by_key(demo: Outcome) -> None:
    quorum = _act(demo, "quorum")
    votes = quorum["votes"]
    assert [v["counted"] for v in votes] == [True, True, False, True]
    assert [v["warrant"] is not None for v in votes] == [False, False, False, True]
    assert quorum["warrant_before_quorum"] is False
    assert len(quorum["warrant"]["basis"]) == 2
    assert quorum["executed"] == ["execution.intent", "execution.applied"]
    assert quorum["delivered"] == "delivered"


def test_the_auditor_names_the_break_glass_act_and_finds_no_data(demo: Outcome) -> None:
    audit = _act(demo, "audit")
    before, after = audit["verdicts"]["before"], audit["verdicts"]["after"]
    assert before["passed"] and before["exit_code"] == 0
    assert after["exit_code"] == 7
    assert any("[break-glass]" in f and "(release)" in f for f in after["findings"])
    assert any("circuit reset without an authorizing warrant" in f for f in after["findings"])
    for verdict in (before, after):
        assert verdict["sockets"] == 0
        assert set(verdict["scan"]["found"].values()) == {0}
        assert verdict["scan"]["planted"]["rows"] == ORDERS
    attestation = demo.payload["attestations"]["after"]
    assert attestation["verdict"]["status"] == "BREAK-GLASS DETECTED"
    assert [o["label"] for o in attestation["operators"] if o["ambient"]] == ["break-glass"]


def test_the_demo_deployment_opens_with_the_ordinary_commands(demo: Outcome) -> None:
    config = str(demo.root / "fiduciary.toml")
    with contextlib.redirect_stdout(io.StringIO()) as printed:
        assert cli.main(["--config", config, "verify"]) == 0
    assert printed.getvalue().startswith("PASS")
    charter = (demo.root / "charter.digest").read_text().strip()
    keys = str(demo.root / "pinned-keys.json")
    for name, code in (("before", 0), ("after", 7)):
        bundle = str(demo.root / f"{name}.fidb")
        with contextlib.redirect_stdout(io.StringIO()):
            assert cli.main(["verify-bundle", bundle, "--charter", charter, "--keys", keys]) == code


def test_the_dashboard_needs_no_network(demo: Outcome) -> None:
    page = (demo.root / "dashboard.html").read_text()
    lowered = page.lower()
    for reference in ("<script src", "<link", "@import", "url(http", 'src="http', 'href="http'):
        assert reference not in lowered, reference
    start = page.index('id="payload">') + len('id="payload">')
    embedded = page[start : page.index("</script>", start)]
    assert "<" not in embedded
    assert json.loads(embedded)["primary"] == "after"


# -- the gateway ---------------------------------------------------------------------


def _requests() -> Iterator[tuple[str, str, dict[str, Any]]]:
    yield from (("s1", tool, args) for tool, args in REFERENCE)
    yield "s1", "flag_dispute", {"order_id": 13}
    yield "s1", "flag_dispute", {"order_id": 14}  # another tenant's
    yield "s1", "apply_discount", {"order_id": 9, "percent": 15}
    yield "s2", "refund_order", {"order_id": 17, "amount": "90.00", "payment_intent": "pi_x"}
    yield "s2", "refund_order", {"order_id": 21, "amount": "20.00", "payment_intent": "pi_x"}
    yield "s3", "refund_order", {"order_id": 17, "amount": "250.00", "payment_intent": "pi_x"}
    yield "s3", "delete_order", {"order_id": 17}


def test_cedar_and_the_builtin_evaluator_decide_alike() -> None:
    pytest.importorskip("cedarpy")
    tenants = {n: tenant_of(n) for n in range(1, ORDERS + 1)}
    real = Gateway("support-agent", "acme", tenants, engine="cedarpy")
    builtin = Gateway("support-agent", "acme", tenants, engine="builtin")
    assert real.engine.startswith("cedarpy ")
    decided = []
    for session, tool, args in _requests():
        a, b = real.decide(session, tool, args), builtin.decide(session, tool, args)
        assert (a.decision, a.policies) == (b.decision, b.policies), (tool, args)
        decided.append(a.decision)
    assert decided.count("DENY") == 4


def test_without_cedarpy_the_gateway_says_it_is_the_builtin_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = builtins.__import__

    def no_cedar(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "cedarpy":
            raise ImportError("no cedarpy here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_cedar)
    monkeypatch.delitem(__import__("sys").modules, "cedarpy", raising=False)
    assert Gateway("a", "acme", {}).engine == "built-in evaluator"
    with pytest.raises(ImportError):
        Gateway("a", "acme", {}, engine="cedarpy")
    with pytest.raises(ValueError, match="engine is"):
        Gateway("a", "acme", {}, engine="opa")
    assert 'action == Action::"refund_order"' in cedar_text()


# -- the live act, against a stand-in --------------------------------------------------


class _Messages:
    def __init__(self, calls: list[tuple[str, dict[str, Any]]]) -> None:
        self._calls = list(calls)
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        usage = TokenUsage(input_tokens=900, output_tokens=80)
        if not self._calls:
            text = SimpleNamespace(type="text", text="Refunded order 5; the discount was refused.")
            return SimpleNamespace(
                content=[text], stop_reason="end_turn", model=kwargs["model"], usage=usage
            )
        name, arguments = self._calls.pop(0)
        block = SimpleNamespace(
            type="tool_use", id=f"toolu_{len(self.requests)}", name=name, input=arguments
        )
        return SimpleNamespace(
            content=[block], stop_reason="tool_use", model=kwargs["model"], usage=usage
        )


def test_the_live_act_runs_a_model_through_the_gateway_and_the_session(tmp_path: Path) -> None:
    client = SimpleNamespace(messages=_Messages(list(REFERENCE)))
    outcome = run(tmp_path / "live", live=Live(client, "claude-opus-5-5"), engine="builtin")
    assert outcome.held
    queue = _act(outcome, "queue")
    assert queue["mode"] == "live"
    assert [c["outcome"] for c in queue["calls"]] == ["served", "committed", "refused"]
    model = queue["model"]
    assert model["turns"] == 4 and model["stop"] == "end_turn"
    assert model["usage"] == {
        "input_tokens": 3600,
        "output_tokens": 320,
        "cache_read_input_tokens": 0,
    }
    assert model["answer"].startswith("Refunded order 5")
    assert float(model["metered"]) > 0
    assert client.messages.requests[0]["output_config"] == {"effort": "medium"}
    audit = _act(outcome, "audit")
    assert audit["verdicts"]["after"]["scan"]["planted"]["prompts"] >= 3


def test_the_live_act_stops_at_its_cost_cap(tmp_path: Path) -> None:
    looping = [("lookup_order", {"order_id": n}) for n in range(1, 9)]
    client = SimpleNamespace(messages=_Messages(looping))
    outcome = run(
        tmp_path / "capped",
        live=Live(client, "claude-opus-5-5", max_cost=Decimal(0)),
        engine="builtin",
    )
    model = _act(outcome, "queue")["model"]
    assert model["halted"] == "max-cost" and model["turns"] == 0


class _Counting(_Messages):
    """The Messages API with its token counter: every request is 2,000 input
    tokens, and each turn bills them and every output token it may write."""

    def count_tokens(self, **kwargs: Any) -> Any:
        assert "max_tokens" not in kwargs and kwargs["model"] == "claude-opus-5-5"
        return SimpleNamespace(input_tokens=2000)

    def create(self, **kwargs: Any) -> Any:
        found = super().create(**kwargs)
        found.usage = TokenUsage(input_tokens=2000, output_tokens=kwargs["max_tokens"])
        return found


def test_a_live_act_never_spends_past_its_cap(tmp_path: Path) -> None:
    """Each turn's max_tokens is what the rest of the cap buys after its input:
    even a model that writes every token it may stays inside the cap."""
    looping = [("lookup_order", {"order_id": n}) for n in range(1, 9)]
    client = SimpleNamespace(messages=_Counting(looping))
    cap = Decimal("0.05")
    outcome = run(
        tmp_path / "exact", live=Live(client, "claude-opus-5-5", max_cost=cap), engine="builtin"
    )
    queue = _act(outcome, "queue")
    model = queue["model"]
    sent = [r["max_tokens"] for r in client.messages.requests]
    # $4 and $20 per million: 2,000 input tokens cost $0.008, leaving 2,100 output tokens.
    assert sent[0] == 2100 and model["halted"] == "max-cost"
    usage = model["usage"]
    billed = (usage["input_tokens"] * 4 + usage["output_tokens"] * 20) / Decimal(1_000_000)
    assert billed <= cap
    # What the session paid beyond the model: each priced tool the model called.
    tools = sum(1 for c in queue["calls"] if c["tool"] == "lookup_order") * Decimal("0.001")
    assert Decimal(queue["metered"]) == billed + tools


def test_without_a_token_counter_the_request_size_bounds_the_turn(tmp_path: Path) -> None:
    from fiduciary.demo.acts import FRAMING, _input_bound

    client = SimpleNamespace(messages=_Messages([]))
    live = Live(client, "claude-opus-5-5")
    request = {"model": live.model, "system": "x" * 100, "tools": [], "messages": []}
    assert _input_bound(live, request) > 100 + FRAMING


# -- the command --------------------------------------------------------------------


def test_the_command_runs_the_demo_and_writes_reports(tmp_path: Path) -> None:
    out = tmp_path / "out"
    with contextlib.redirect_stdout(io.StringIO()) as printed:
        status = cli.main(
            ["demo", "--out", str(out), "--gateway", "builtin", "--report", str(tmp_path / "r.md")]
        )
    assert status == 0
    text = printed.getvalue()
    assert "Act 7  Offline third-party audit verification" in text and "dashboard" in text
    assert (tmp_path / "r.md").read_text().startswith("# Bundle `after`: BREAK-GLASS DETECTED")


def test_the_command_refuses_what_it_cannot_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    taken = tmp_path / "taken"
    taken.mkdir()
    (taken / "x").write_text("")
    monkeypatch.delenv("BENCHMARK_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-never-read")
    for argv, said in (
        (["demo", "--out", str(taken)], "is not empty"),
        (["demo", "--report", str(tmp_path / "r.pdf")], "--report is a directory"),
        (["demo", "--live", "--out", str(tmp_path / "n")], "never reads ANTHROPIC_API_KEY"),
        (["demo", "--live", "--env-file", str(tmp_path / "missing")], "--env-file"),
    ):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            assert cli.main(argv) == 2, argv
        assert said in err.getvalue(), (argv, err.getvalue())
    assert not (tmp_path / "n").exists()


def test_the_dashboard_is_served_on_loopback_and_opened(
    demo: Outcome, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import threading
    import urllib.request
    import webbrowser

    server, url = cli._serve_dashboard(demo.root, "127.0.0.1:0")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(url, timeout=10) as answer:  # noqa: S310 - our loopback
            assert b"fiduciary-attestation/1" in answer.read()
    finally:
        server.shutdown()
        server.server_close()
    for where, said in (("0.0.0.0:8740", "loopback only"), ("127.0.0.1:x", "HOST:PORT")):
        with pytest.raises(cli._ExitError, match=said):
            cli._serve_dashboard(demo.root, where)
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", opened.append)
    with contextlib.redirect_stdout(io.StringIO()):
        argv = ["demo", "--quiet", "--open", "--gateway", "builtin", "--out", str(tmp_path / "o")]
        assert cli.main(argv) == 0
    (url,) = opened
    assert url.startswith("file://") and url.endswith("/dashboard.html")


def test_live_reads_the_benchmark_key_only_and_holds_to_its_rates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys
    import types

    made: list[dict[str, Any]] = []
    fake = types.ModuleType("anthropic")

    def client(**kw: Any) -> Any:
        made.append(kw)
        return SimpleNamespace()

    fake.Anthropic = client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.delenv("BENCHMARK_API_KEY", raising=False)
    env = tmp_path / ".env.benchmark"
    env.write_text("# the key\nexport BENCHMARK_API_KEY='sk-test-1'\n")
    parser = cli._parser()

    def live(*extra: str) -> Any:
        return cli._live_model(parser.parse_args(["demo", "--live", *extra]))

    found = live("--env-file", str(env), "--max-cost", "0.25", "--effort", "low")
    assert (found.model, found.effort, found.max_cost) == (
        "claude-opus-5-5",
        "low",
        Decimal("0.25"),
    )
    assert made[-1]["api_key"] == "sk-test-1"
    monkeypatch.setenv("BENCHMARK_API_KEY", "sk-test-2")
    live()
    assert made[-1]["api_key"] == "sk-test-2"
    for extra, said in (
        (("--model", "claude-unpriced"), "no rate is configured"),
        (("--max-cost", "lots"), "--max-cost is a dollar amount"),
    ):
        with pytest.raises(cli._ExitError, match=said):
            live(*extra)
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(cli._ExitError, match="needs the anthropic package"):
        live()


def test_the_builtin_evaluator_fails_a_policy_whose_attribute_is_missing() -> None:
    from fiduciary.demo.gateway import _policy_id

    gateway = Gateway("support-agent", "acme", {5: "acme"}, engine="builtin")
    # A refund with no amount has no amount_cents: the refund policy cannot hold.
    assert gateway.decide("s", "refund_order", {"order_id": 5}).decision == "DENY"
    assert gateway.decide("s", "lookup_order", {"order_id": 999}).decision == "PERMIT"
    assert gateway.decide("s", "flag_dispute", {"order_id": 999}).decision == "DENY"
    assert _policy_id("policy1") == "refund"
    assert _policy_id("policy99") == "policy99"
    assert _policy_id("custom") == "custom"


# -- the edges: what an operator, a model or a command can get wrong -----------------


def test_a_result_with_nothing_measured_reads_as_nothing() -> None:
    from fiduciary.demo.acts import measured

    result = SimpleNamespace(
        diff=None,
        verdict=None,
        committed=False,
        plan=SimpleNamespace(plan_id="plan-0"),
        chain_head="0" * 64,
    )
    found = measured(result)  # type: ignore[arg-type]
    assert (found["rows"], found["captured"], found["checkers"]) == (0, 0, 0)
    assert found["findings"] == found["windows"] == found["outbound"] == []
    assert found["orders_total"] == {"before": "0.00", "after": "0.00"}


class _Halting(_Messages):
    """A model that writes beside its call, calls what the gateway denies, then is halted."""

    def create(self, **kwargs: Any) -> Any:
        from fiduciary.bridge import SessionHaltedError

        self.requests.append(kwargs)
        if len(self.requests) > 1:
            raise SessionHaltedError("budget", "the session's envelope is spent")
        usage = TokenUsage(input_tokens=500, output_tokens=40)
        content = [
            SimpleNamespace(type="text", text="Refunding order 6 as asked."),
            SimpleNamespace(
                type="tool_use",
                id="toolu_1",
                name="refund_order",
                input={"order_id": 6, "amount": "10.00", "payment_intent": "pi_0000000006"},
            ),
        ]
        return SimpleNamespace(
            content=content, stop_reason="tool_use", model=kwargs["model"], usage=usage
        )


def test_the_live_act_records_a_denied_call_and_a_halt(tmp_path: Path) -> None:
    client = SimpleNamespace(messages=_Halting([]))
    outcome = run(tmp_path / "halted", live=Live(client, "claude-opus-5-5"), engine="builtin")
    queue = _act(outcome, "queue")
    (denied,) = queue["calls"]
    assert denied["gateway"]["decision"] == "DENY" and denied["outcome"] == "not sent"
    assert "measured" not in denied
    assert queue["model"]["halted"] == "budget" and queue["model"]["turns"] == 2
    assert queue["model"]["answer"] == ""
    assert outcome.held  # the other acts stand without act 1's refund


def test_the_builtin_evaluator_needs_both_sides_of_a_sum() -> None:
    gateway = Gateway("support-agent", "acme", {5: "acme"}, engine="builtin")
    # The session's running sum is the gateway's own: without it the policy cannot hold.
    assert gateway._builtin("refund_order", {"amount_cents": 100}, 5) == ()
    assert gateway._builtin(
        "refund_order", {"amount_cents": 100, "session_refunded_cents": 0}, 5
    ) == ("refund",)


def test_the_auditor_sees_no_network_and_the_scan_finds_what_is_there(demo: Outcome) -> None:
    import socket

    from fiduciary.demo.acts import no_network, scan

    with no_network() as attempts:
        with pytest.raises(OSError, match="no network"):
            socket.create_connection(("127.0.0.1", 9))
    assert len(attempts) == 1
    assert socket.create_connection is not None and attempts  # restored, and recorded
    data = (demo.root / "after.fidb").read_bytes()
    lineage = demo.payload["attestations"]["after"]["bundle"]["lineage"]
    long = "x" * 8 + lineage + "y" * 8  # longer than 40: its first 32 characters are sought
    found = scan(data, {"ids": [lineage, "not-in-the-bundle"], "long": [long]})
    assert found["found"]["ids"] > 1 and found["found"]["long"] == 0  # one hit per member
    assert all(lineage[:24] in hit for hit in found["hits"]["ids"])


def test_a_deployment_is_written_only_into_an_empty_directory(
    demo: Outcome, tmp_path: Path
) -> None:
    from fiduciary.demo.world import build, lookup

    with pytest.raises(FileExistsError, match="not empty"):
        build(demo.root)
    handler = lookup(demo.root / "back_office.db")
    assert handler({"order_id": 5})["status"] == "open"
    assert handler({"order_id": 9999}) == {"order_id": 9999, "found": False}


def test_the_command_reports_serves_opens_and_says_what_ran(
    demo: Outcome, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import copy
    import tempfile
    import webbrowser

    import fiduciary.demo as package

    payload = copy.deepcopy(demo.payload)
    payload["demo"]["model"] = {"served": "claude-opus-5-5", "turns": 3, "metered": "0.039492"}
    monkeypatch.setattr(
        package, "run", lambda root, **kw: Outcome(demo.root, demo.dashboard, payload)
    )
    closed: list[bool] = []

    class Server:
        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(cli, "_serve_dashboard", lambda root, where: (Server(), "http://x/d"))
    monkeypatch.setattr(tempfile, "mkdtemp", lambda prefix: str(tmp_path / "made"))
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", opened.append)
    reports = tmp_path / "reports"
    reports.mkdir()
    with contextlib.redirect_stdout(io.StringIO()) as printed:
        assert cli.main(["demo", "--report", str(reports), "--serve", "--open"]) == 0
    text = printed.getvalue()
    assert f"a deployment in {tmp_path / 'made'}" in text
    assert "live        claude-opus-5-5: 3 turn(s), $0.039492 metered" in text
    assert "serving     http://x/d" in text and opened == ["http://x/d"] and closed == [True]
    assert (reports / "dashboard.html").read_text().startswith("<!doctype html>")
    payload["demo"]["acts"][-1]["verdicts"]["before"]["passed"] = False
    with contextlib.redirect_stdout(io.StringIO()):
        assert cli.main(["demo", "--quiet", "--out", str(tmp_path / "again")]) == 1


def test_the_command_names_what_it_lacks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    from agentgov.exceptions import SignerUnavailableError
    from agentgov.receipts.signing import Ed25519Signer

    import fiduciary.demo as package

    def no_cedar(root: Path, **kw: Any) -> Outcome:
        raise ImportError("No module named 'cedarpy'")

    monkeypatch.setattr(package, "run", no_cedar)
    argv = ["demo", "--quiet", "--gateway", "cedarpy", "--out", str(tmp_path / "a")]
    with contextlib.redirect_stderr(io.StringIO()) as err:
        assert cli.main(argv) == 2
    assert "interlock-fiduciary[demo]" in err.getvalue()

    def unsigned() -> Any:
        raise SignerUnavailableError("no cryptography")

    monkeypatch.setattr(Ed25519Signer, "generate", staticmethod(unsigned))
    with contextlib.redirect_stderr(io.StringIO()) as err:
        assert cli.main(["demo", "--quiet", "--out", str(tmp_path / "b")]) == 2
    assert "cryptography" in err.getvalue() and not (tmp_path / "b").exists()
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        with pytest.raises(cli._ExitError, match="--serve:"):
            cli._serve_dashboard(tmp_path, f"127.0.0.1:{port}")


def test_the_bundle_and_the_attestation_name_this_release(demo: Outcome) -> None:
    from fiduciary import __version__

    for name in ("before", "after"):
        attestation = demo.payload["attestations"][name]
        assert attestation["bundle"]["exporter"] == f"fiduciary {__version__}"
        assert attestation["verifier"]["fiduciary"] == __version__
    assert demo.payload["demo"]["version"] == __version__


def test_the_act_titles_are_the_ones_the_console_shows(demo: Outcome) -> None:
    from fiduciary.demo.acts import TITLES

    acts = demo.payload["demo"]["acts"]
    assert [a["title"] for a in acts] == list(TITLES.values())
    page = (demo.root / "dashboard.html").read_text()
    for title in TITLES.values():
        assert json.dumps(title)[1:-1] in page

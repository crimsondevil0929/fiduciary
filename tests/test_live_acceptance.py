"""The live acceptance suite, offline (``scripts/live_acceptance.py``).

Every scenario's reference proposals hold; the oracle names what it must; the
suite refuses to spend without ``BENCHMARK_API_KEY``; and the live loop runs,
against a stand-in for the Messages API that proposes the reference calls.
The live run itself is opt-in and by hand.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from agentgov.interceptor import TokenUsage

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def suite() -> ModuleType:
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        return importlib.import_module("live_acceptance")
    finally:
        sys.path.remove(str(ROOT / "scripts"))


class _Messages:
    """``client.messages``: each turn proposes the next scripted call."""

    def __init__(self, calls: list[tuple[str, dict[str, Any]]], served: str | None) -> None:
        self._calls = list(calls)
        self._served = served
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        usage = TokenUsage(input_tokens=400, output_tokens=60)
        if not self._calls:
            text = SimpleNamespace(type="text", text="Done.")
            return SimpleNamespace(
                content=[text],
                stop_reason="end_turn",
                model=self._served or kwargs["model"],
                usage=usage,
            )
        name, arguments = self._calls.pop(0)
        block = SimpleNamespace(
            type="tool_use", id=f"toolu_{len(self.requests)}", name=name, input=arguments
        )
        return SimpleNamespace(
            content=[block],
            stop_reason="tool_use",
            model=self._served or kwargs["model"],
            usage=usage,
        )


class _Client:
    def __init__(self, calls: list[tuple[str, dict[str, Any]]], served: str | None = None) -> None:
        self.messages = _Messages(calls, served)


@pytest.mark.parametrize(
    "key",
    [
        "compliant",
        "other-account",
        "over-the-cap",
        "loose-figures",
        "ask-the-principals",
        "outside-the-remit",
    ],
)
def test_every_reference_proposal_holds(suite: ModuleType, key: str, tmp_path: Path) -> None:
    (scenario,) = [s for s in suite.SCENARIOS if s.key == key]
    outcome = suite.replay(scenario, tmp_path)
    assert outcome.held, outcome.problems
    assert [c["tool"] for c in outcome.calls] == [name for name, _ in scenario.reference]


def test_the_oracle_names_another_tenants_refund(suite: ModuleType, tmp_path: Path) -> None:
    """Asked the back office directly: a refund booked against another
    tenant's order, written around fiduciary, is named."""
    with suite.deployment(tmp_path, "3") as (world, _session):
        world.binder.close()  # an engine of its own, outside the mandate
        import sqlite3

        with sqlite3.connect(world.back_office) as raw:
            raw.execute("UPDATE orders SET total = 990 WHERE id = 8")
            raw.execute(
                "INSERT INTO refunds (order_id, tenant, amount) VALUES (8, 'beta', '10.00')"
            )
        problems = suite.common(world)
    assert "order 8, another tenant's, moved" in problems
    assert "the payment requests and the refund rows disagree" in problems


def test_the_live_loop_against_a_stand_in(suite: ModuleType, tmp_path: Path) -> None:
    scenario = next(s for s in suite.SCENARIOS if s.key == "loose-figures")
    client = _Client(list(scenario.reference))
    outcome = suite.live(
        scenario, tmp_path, client, model="claude-opus-5-5", effort="medium", envelope="1.00"
    )
    assert outcome.held, outcome.problems
    assert [c["outcome"] for c in outcome.calls] == ["rejected", "committed"]
    assert outcome.stop == "end_turn" and outcome.turns == 3
    first = client.messages.requests[0]
    assert "fallbacks" not in first and "betas" not in first  # the model asked for, or nothing
    assert first["max_tokens"] == 4096
    assert {t["name"] for t in first["tools"]} >= {"refund_order", "fiduciary_request"}
    assert 0 < float(outcome.cost) < 0.1
    events = json.loads((tmp_path / "transcript.json").read_text())["events"]
    assert [e["tool_results"][0]["is_error"] for e in events if "tool_results" in e] == [
        True,
        False,
    ]
    assert outcome.bundle == "PASS"  # the run's lineage, exported and verified offline
    assert (tmp_path / "lineage.fidb").exists()
    assert "V12 ordering: ok" in (tmp_path / "verify.txt").read_text()


def test_another_model_serving_the_run_is_named(suite: ModuleType, tmp_path: Path) -> None:
    scenario = next(s for s in suite.SCENARIOS if s.key == "outside-the-remit")
    client = _Client([], served="claude-opus-4-8")
    outcome = suite.live(
        scenario, tmp_path, client, model="claude-opus-5-5", effort="medium", envelope="1.00"
    )
    assert outcome.problems == ["served by claude-opus-4-8, not claude-opus-5-5"]


def test_the_mcp_loop_against_a_stand_in(suite: ModuleType, tmp_path: Path) -> None:
    """The same loop, the agent holding only an MCP connection to a
    ``fiduciary mcp serve`` process; its own model spend metered within the
    envelope."""
    scenario = next(s for s in suite.SCENARIOS if s.key == "loose-figures")
    client = _Client(list(scenario.reference))
    outcome = suite.live_mcp(
        scenario, tmp_path, client, model="claude-opus-5-5", effort="medium", envelope="1.00"
    )
    assert outcome.held, outcome.problems
    assert [c["outcome"] for c in outcome.calls] == ["error", "ok"]
    tools = client.messages.requests[0]["tools"]
    assert {t["name"] for t in tools} == {
        "adjust_orders",
        "lookup_order",
        "refund_order",
        "fiduciary_request",
        "fiduciary_ticket_status",
    }
    assert 0 < float(outcome.cost) < 0.1
    assert outcome.bundle == "PASS", (tmp_path / "verify.txt").read_text()
    pinned = json.loads((tmp_path / "pinned-keys.json").read_text())
    assert set(pinned) == {"service", "ledger", "receipts", "runtime", "operators"}
    stopped = suite.live_mcp(
        scenario,
        tmp_path / "poor",
        _Client(list(scenario.reference)),
        model="claude-opus-5-5",
        effort="medium",
        envelope="0.001",
    )
    assert stopped.halted == "budget" and stopped.turns == 1


def test_the_command_spends_only_when_asked(
    suite: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("BENCHMARK_API_KEY", raising=False)
    monkeypatch.setattr(suite, "ENV_FILE", tmp_path / "absent.env")  # never a real key here
    monkeypatch.setenv("ANTHROPIC_API_KEY", "never-read")
    assert suite.main(["--out", str(tmp_path / "none")]) == 2
    assert "never falls back to ANTHROPIC_API_KEY" in capsys.readouterr().err
    assert suite.main(["--offline", "--only", "nothing"]) == 2

    monkeypatch.setenv("BENCHMARK_API_KEY", "sk-test")
    assert suite.main(["--model", "claude-unpriced-1", "--out", str(tmp_path / "x")]) == 2
    made: list[dict[str, Any]] = []

    def fake(name: str) -> Any:
        assert name == "anthropic"

        def construct(**kwargs: Any) -> _Client:
            made.append(kwargs)
            return _Client([])

        return SimpleNamespace(Anthropic=construct)

    monkeypatch.setattr(suite.importlib, "import_module", fake)
    out = tmp_path / "run"
    assert suite.main(["--only", "outside-the-remit", "--out", str(out), "--max-cost", "1"]) == 0
    assert made == [{"api_key": "sk-test", "max_retries": 3, "timeout": 180.0}]
    results = json.loads((out / "results.json").read_text())
    assert [o["mode"] for o in results["outcomes"]] == ["claude-opus-5-5 via session"]
    assert (
        suite.main(["--only", "compliant", "--out", str(tmp_path / "cap"), "--max-cost", "0.5"])
        == 0
    )
    assert "stopped before compliant via session" in capsys.readouterr().err


def test_the_key_comes_from_the_environment_or_its_file(
    suite: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BENCHMARK_API_KEY", raising=False)
    env = tmp_path / ".env.benchmark"
    assert suite.benchmark_key(env) is None
    env.write_text("# the suite's key\nOTHER=1\nexport BENCHMARK_API_KEY='sk-file'\n")
    assert suite.benchmark_key(env) == "sk-file"
    monkeypatch.setenv("BENCHMARK_API_KEY", "sk-env")
    assert suite.benchmark_key(env) == "sk-env"
    ignored = (ROOT / ".gitignore").read_text()
    assert ".env.benchmark" in ignored and "live-run/" in ignored

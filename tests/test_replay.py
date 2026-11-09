"""A recorded model's turns, replayed on today's engine at no cost (1.10.0).

The recording that ships with fiduciary is claude-opus-5-5 working each industry's
signature scenario. Replayed, each scenario runs again on the installed engine, its
calls chosen by the recorded turns, with every socket refused; the session meters the
turns from their recorded usage, so the ledger holds what they cost when recorded.
"""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from fiduciary import cli
from fiduciary.demo import Replay, run
from fiduciary.demo.acts import no_network
from fiduciary.demo.replay import (
    FORMAT,
    Recording,
    ReplayDivergedError,
    Take,
    _split,
    bundled,
    from_run,
    load,
)
from fiduciary.demo.world import PRICING


def _io(turns: Any) -> list[tuple[int, int]]:
    return [(t["usage"]["input_tokens"], t["usage"]["output_tokens"]) for t in turns]


def _cost(take: Take, turn: dict[str, Any]) -> Decimal:
    rates = PRICING[take.model]
    usage = turn["usage"]
    tokens_in, tokens_out = int(usage["input_tokens"]), int(usage["output_tokens"])
    priced = tokens_in * rates.input_usd_per_mtok + tokens_out * rates.output_usd_per_mtok
    return priced / Decimal(1_000_000)


def test_the_bundled_recording_is_eight_scenarios_of_one_model() -> None:
    recording = bundled()
    assert set(recording.takes) == {
        "fintech",
        "healthcare",
        "sre",
        "supply-chain",
        "saas",
        "coding-agent",
        "workspace",
        "service-desk",
    }
    assert recording.metered == Decimal("0.193480")
    for take in recording.takes.values():
        assert take.model == take.served == "claude-opus-5-5"
        for name in ("input_tokens", "output_tokens"):
            assert sum(t["usage"][name] for t in take.turns) == take.usage[name], take.domain
        inputs = [t["usage"]["input_tokens"] for t in take.turns]
        assert inputs == sorted(inputs), "each turn resends the conversation"
        assert take.turns[-1]["stop"] == "end_turn"
    assert json.loads(recording.dumps())["format"] == FORMAT
    assert Recording.loads(recording.dumps()) == recording


@pytest.fixture(scope="module")
def replayed(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    root = tmp_path_factory.mktemp("replay") / "supply-chain"
    with no_network() as attempts:
        outcome = run(root, live=Replay(bundled()), domain="supply-chain", engine="builtin")
    assert attempts == [], "nothing leaves the machine"
    return outcome.payload


def test_a_replay_runs_the_recorded_turns_on_this_engine(replayed: dict[str, Any]) -> None:
    take = bundled().takes["supply-chain"]
    demo = replayed["demo"]
    assert demo["mode"] == "replay"
    act = next(a for a in demo["acts"] if a.get("model"))
    model = act["model"]
    assert model["metered"] == take.metered, "the ledger charged what the turns cost then"
    assert model["replay"]["recorded_at"] == take.recorded_at
    assert model["seconds"] == take.seconds
    blocks = [b for t in take.turns for b in t["blocks"]]
    recorded = [(b["tool"], b["arguments"]) for b in blocks if b["type"] == "tool_use"]
    assert [(c["tool"], c["arguments"]) for c in act["calls"]] == recorded
    assert model["answer"] == take.answer
    assert _io(model["transcript"]) == _io(take.turns)
    audit = demo["acts"][-1]["reconciliation"]
    assert audit["bundle_deliveries"] == 2 and audit["phantom"] == 1


def _replay(tmp_path: Path, take: Take) -> None:
    recording = Recording({take.domain: take})
    run(tmp_path / "r", live=Replay(recording), domain=take.domain, engine="builtin")


def _with_turn(take: Take, n: int, turn: dict[str, Any]) -> Take:
    turns = list(take.turns)
    turns[n] = turn
    return replace(take, turns=tuple(turns))


def test_a_tool_the_engine_no_longer_offers_stops_the_replay(tmp_path: Path) -> None:
    take = bundled().takes["supply-chain"]
    first = json.loads(json.dumps(take.turns[0]))
    first["blocks"][0]["tool"] = "set_reorder_points"
    with pytest.raises(ReplayDivergedError, match="no longer offers"):
        _replay(tmp_path, _with_turn(take, 0, first))


def test_an_outcome_that_changed_stops_the_replay(tmp_path: Path) -> None:
    take = bundled().takes["supply-chain"]
    first = json.loads(json.dumps(take.turns[0]))
    call = next(b for b in first["blocks"] if b["type"] == "tool_use")
    call["error"] = not call["error"]
    with pytest.raises(ReplayDivergedError, match="when recorded"):
        _replay(tmp_path, _with_turn(take, 0, first))


def test_a_turn_never_asked_for_stops_the_replay(tmp_path: Path) -> None:
    take = bundled().takes["supply-chain"]
    longer = replace(take, turns=(*take.turns, take.turns[-1]))
    with pytest.raises(ReplayDivergedError, match="stopped after turn 2"):
        _replay(tmp_path, longer)


def test_arguments_the_schema_no_longer_takes_stop_the_replay(tmp_path: Path) -> None:
    take = bundled().takes["supply-chain"]
    first = json.loads(json.dumps(take.turns[0]))
    call = next(b for b in first["blocks"] if b["type"] == "tool_use")
    call["arguments"] = {}
    with pytest.raises(ReplayDivergedError, match="required"):
        _replay(tmp_path, _with_turn(take, 0, first))


def test_a_replayed_run_is_a_recording_itself(replayed: dict[str, Any], tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()
    (root / "demo.json").write_text(json.dumps(replayed))
    found = from_run(root).takes["supply-chain"]
    take = bundled().takes["supply-chain"]
    assert found.split == "recorded", "a 1.10.0 run keeps each turn's usage"
    assert _io(found.turns) == _io(take.turns)
    assert found.metered == take.metered


def test_each_reconstructed_turn_costs_its_recorded_spend() -> None:
    model = {"model": "claude-opus-5-5", "usage": {"input_tokens": 5118, "output_tokens": 604}}
    costs = [Decimal("0.006972"), Decimal("0.013364"), Decimal("0.012216")]
    turns = _split(costs, model)
    take = replace(bundled().takes["sre"], turns=tuple({"usage": t} for t in turns))
    assert [_cost(take, {"usage": t}) for t in turns] == costs
    assert [t["input_tokens"] for t in turns] == sorted(t["input_tokens"] for t in turns)
    with pytest.raises(ValueError, match="do not split"):
        _split([Decimal("0.000001")], model)


def test_the_command_replays_and_refuses_misuse(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["demo", "--replay", "--live", "--out", str(tmp_path / "a")]) == 2
    assert "choose one" in capsys.readouterr().err
    assert cli.main(["demo", "--replay", str(tmp_path / "none.json")]) == 2
    assert "--replay" in capsys.readouterr().err
    out = tmp_path / "b"
    assert cli.main(["demo", "--replay", "--domain", "saas", "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "replayed    saas: claude-opus-5-5 as recorded 2026-10-31" in printed
    assert "$0.018464 metered then, $0 now" in printed


@pytest.fixture(scope="module")
def fintech(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("replay") / "fintech"
    run(root, live=Replay(bundled()), domain="fintech", engine="builtin")
    return root


def test_a_run_recorded_before_1_10_is_read_from_its_ledger(fintech: Path, tmp_path: Path) -> None:
    """A run from before 1.10.0 kept no per-turn usage, and fintech no transcript: the
    calls go in turn by when the engine saw each, and each turn's tokens are split so it
    costs its ledger spend. On a replayed run's ledger, that gives the recording back."""
    page = json.loads((fintech / "demo.json").read_text())
    act = page["demo"]["acts"][0]
    assert act["model"]["replay"]["renamed"], "the plan's id was minted afresh"
    del act["model"]["transcript"]
    (fintech / "demo.json").write_text(json.dumps(page))
    found = load(fintech).takes["fintech"]
    take = bundled().takes["fintech"]
    assert found.split == "ledger"
    shape = [[(b["type"], b.get("tool")) for b in t["blocks"]] for t in found.turns]
    assert shape == [[(b["type"], b.get("tool")) for b in t["blocks"]] for t in take.turns]
    assert _io(found.turns) == _io(take.turns)
    recording = tmp_path / "recording.json"
    recording.write_text(Recording({"fintech": found}).dumps())
    assert load(recording).takes["fintech"] == found


def test_a_pack_recorded_before_1_10_keeps_its_transcript(tmp_path: Path) -> None:
    root = tmp_path / "saas"
    run(root, live=Replay(bundled()), domain="saas", engine="builtin")
    page = json.loads((root / "demo.json").read_text())
    for turn in next(a for a in page["demo"]["acts"] if a.get("model"))["model"]["transcript"]:
        del turn["usage"]
    (root / "demo.json").write_text(json.dumps(page))
    found = from_run(root).takes["saas"]
    assert found.split == "ledger"
    assert _io(found.turns) == _io(bundled().takes["saas"].turns)
    (root / "demo.json").write_text(json.dumps({"demo": {"acts": []}}))
    with pytest.raises(ValueError, match="no scenario a live model worked"):
        from_run(root)
    with pytest.raises(ValueError, match="not a recording"):
        Recording.loads(json.dumps({"format": "other"}))

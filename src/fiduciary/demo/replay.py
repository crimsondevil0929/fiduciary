"""A recorded model, replayed against today's engine at no cost (1.10.0).

``fiduciary demo --live`` has a model work each industry's signature scenario, and
the run records what the model wrote, turn by turn. A recording
(``fiduciary-recording/1``) keeps exactly that: per scenario, the request, each turn's
text, summarized reasoning and tool calls, what each call returned when it was
recorded, and each turn's token usage, with the model, its effort, when it was
recorded and what it cost.

``fiduciary demo --replay`` plays every scenario on the installed engine, fresh:
new keys, a new ledger, new bundles, today's checks, relay and reconciliation. Where
the recorded scenario had a live model, a :class:`ReplayClient` stands in for the
model's API: each call returns the recorded turn, and nothing leaves the machine. The
session meters each turn from its recorded usage, so the scenario costs in its ledger
what it cost when it was recorded, and costs nothing to replay.

A replay is checked as it goes. A recorded call to a tool the engine no longer
offers, arguments its schema no longer takes, or a call whose outcome differs from
the recorded one (refused where it committed, or the other way round), stops the
replay with :class:`ReplayDivergedError`: the model's later turns answered what it saw then,
and would not be its answer now. A call that returns other words with the same
outcome is kept, and named: the replay records what the model saw and what the
engine says now. Ids the engine mints afresh (a plan's id) are mapped from the
recorded ones, so a reader can follow the model's text to this run's records.

:func:`from_run` reads a recording out of any run ``fiduciary demo --live`` wrote.
A run recorded before 1.10.0 kept each turn's usage only in its ledger, as the
turn's spend, and the fintech scenario kept no transcript: its calls are put in
their turns by when the engine saw each one, and each turn's tokens are split so
its cost is the spend its ledger recorded, to the micro-dollar. Totals are as
recorded.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from decimal import Decimal
from importlib import resources
from pathlib import Path
from typing import Any, Final

__all__ = [
    "FORMAT",
    "Recording",
    "Replay",
    "ReplayClient",
    "ReplayDivergedError",
    "Take",
    "bundled",
    "from_run",
]

FORMAT: Final = "fiduciary-recording/1"
OK_OUTCOMES: Final = ("committed", "served")
"""The outcomes a tool result reports as no error (``CallResult.ok``)."""
_MINTED: Final = re.compile(r"\bplan-[0-9a-f]{12}\b|\b[0-9a-f]{17}_[a-z0-9_]+\b")
"""Ids the engine mints for each run, which a model may quote: a plan's, and a
rate window's (a digest of its lineage and its name)."""


class ReplayDivergedError(RuntimeError):
    """A recorded turn no longer fits the engine it is replayed against."""


@dataclass(frozen=True, slots=True)
class Take:
    """One scenario a model worked, as recorded.

    :ivar turns: Each turn: ``stop``, ``blocks`` (``text``, ``reasoning`` or
        ``tool_use`` with ``tool``, ``arguments``, and the ``result`` and
        ``error`` it returned when recorded, ``result`` ``None`` when not
        recorded) and ``usage``.
    """

    domain: str
    model: str
    served: str
    effort: str
    recorded_at: str
    recorded_with: str
    request: str
    turns: tuple[dict[str, Any], ...]
    answer: str
    seconds: float
    metered: str
    usage: dict[str, int]
    split: str = "recorded"
    """``recorded``, or ``ledger`` when each turn's tokens were reconstructed
    from the turn's spend in the recorded ledger."""

    def to_json(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "model": self.model,
            "served": self.served,
            "effort": self.effort,
            "recorded_at": self.recorded_at,
            "recorded_with": self.recorded_with,
            "request": self.request,
            "turns": list(self.turns),
            "answer": self.answer,
            "seconds": self.seconds,
            "metered": self.metered,
            "usage": self.usage,
            "split": self.split,
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Take:
        return cls(
            domain=str(raw["domain"]),
            model=str(raw["model"]),
            served=str(raw["served"]),
            effort=str(raw["effort"]),
            recorded_at=str(raw["recorded_at"]),
            recorded_with=str(raw["recorded_with"]),
            request=str(raw["request"]),
            turns=tuple(dict(t) for t in raw["turns"]),
            answer=str(raw["answer"]),
            seconds=float(raw["seconds"]),
            metered=str(raw["metered"]),
            usage={k: int(v) for k, v in raw["usage"].items()},
            split=str(raw.get("split", "recorded")),
        )


@dataclass(frozen=True, slots=True)
class Recording:
    """Every scenario a run's model worked, by industry."""

    takes: dict[str, Take]
    source: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "format": FORMAT,
            "source": self.source,
            "takes": {k: t.to_json() for k, t in sorted(self.takes.items())},
        }

    def dumps(self) -> str:
        return json.dumps(self.to_json(), indent=1, sort_keys=True, ensure_ascii=False) + "\n"

    @classmethod
    def loads(cls, text: str) -> Recording:
        raw = json.loads(text)
        if not isinstance(raw, dict) or raw.get("format") != FORMAT:
            raise ValueError(f"not a recording: its format is not {FORMAT}")
        return cls({k: Take.from_json(v) for k, v in raw["takes"].items()}, str(raw["source"]))

    @property
    def metered(self) -> Decimal:
        return sum((Decimal(t.metered) for t in self.takes.values()), Decimal(0))


def bundled() -> Recording:
    """The recording that ships with the package: claude-opus-5-5 working each
    industry's signature scenario, recorded October 2026."""
    text = resources.files("fiduciary.demo").joinpath("recording.json").read_text("utf-8")
    return Recording.loads(text)


def load(path: str | Path) -> Recording:
    """A recording file, or the recording in a run's directory."""
    where = Path(path)
    if where.is_dir():
        return from_run(where)
    return Recording.loads(where.read_text(encoding="utf-8"))


@dataclass(frozen=True, slots=True)
class Replay:
    """A recording to replay, as ``fiduciary demo`` takes it in place of a live
    model: each industry gets a client of its own, for its own scenario."""

    recording: Recording

    def live(self, domain: str) -> Any:
        """The model to work ``domain``'s scenario: its take, replayed; ``None``
        when the recording holds none, so the scenario plays its fixed calls."""
        from fiduciary.demo.acts import Live

        take = self.recording.takes.get(domain)
        if take is None:
            return None
        # The cap is the engine's, as when recorded; a recorded turn always fits it.
        return Live(ReplayClient(take), take.model, effort=take.effort, max_cost=Decimal("1.00"))


# --------------------------------------------------------------------------
# the stand-in client
# --------------------------------------------------------------------------


@dataclass(slots=True)
class _Block:
    type: str
    text: str = ""
    thinking: str = ""
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _Usage:
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass(slots=True)
class _Response:
    content: list[_Block]
    stop_reason: str
    usage: _Usage
    model: str


@dataclass(slots=True)
class _Counted:
    input_tokens: int


class _Messages:
    def __init__(self, client: ReplayClient) -> None:
        self._client = client

    def create(self, **request: Any) -> _Response:
        return self._client.turn(request)

    def count_tokens(self, **request: Any) -> _Counted:
        return _Counted(self._client.next_input())


class ReplayClient:
    """Stands in for an Anthropic client: each ``messages.create`` returns the
    next recorded turn of ``take`` (module docstring). Never touches a network."""

    def __init__(self, take: Take) -> None:
        self.take = take
        self.messages = _Messages(self)
        self._n = 0
        self._ids: list[list[str]] = []
        self.changed: list[dict[str, Any]] = []
        self.renamed: dict[str, str] = {}

    def next_input(self) -> int:
        if self._n >= len(self.take.turns):
            return 0
        return int(self.take.turns[self._n]["usage"]["input_tokens"])

    def turn(self, request: Mapping[str, Any]) -> _Response:
        turns = self.take.turns
        if self._n >= len(turns):
            raise ReplayDivergedError(
                f"{self.take.domain}: the engine asked the model for turn {self._n + 1}; "
                f"the recording has {len(turns)}"
            )
        if self._n:
            self._results(request, turns[self._n - 1])
        recorded = turns[self._n]
        offered = {t["name"]: t for t in request.get("tools") or ()}
        content: list[_Block] = []
        ids: list[str] = []
        for i, block in enumerate(recorded["blocks"]):
            kind = block["type"]
            if kind == "text":
                content.append(_Block("text", text=block["text"]))
            elif kind == "reasoning":
                content.append(_Block("thinking", thinking=block["text"]))
            elif kind == "tool_use":
                name, arguments = block["tool"], dict(block["arguments"])
                tool = offered.get(name)
                if tool is None:
                    raise ReplayDivergedError(
                        f"{self.take.domain}, turn {self._n + 1}: the model called {name}, "
                        f"which the engine no longer offers"
                    )
                problem = _schema_problem(tool.get("input_schema") or {}, arguments)
                if problem:
                    raise ReplayDivergedError(
                        f"{self.take.domain}, turn {self._n + 1}: {name}'s arguments no "
                        f"longer fit its schema: {problem}"
                    )
                ident = f"toolu_replay_{self._n + 1}_{i}"
                ids.append(ident)
                content.append(_Block("tool_use", id=ident, name=name, input=arguments))
        self._ids.append(ids)
        self._n += 1
        usage = recorded["usage"]
        return _Response(
            content=content,
            stop_reason=str(recorded["stop"]),
            usage=_Usage(
                int(usage.get("input_tokens", 0)),
                int(usage.get("output_tokens", 0)),
                int(usage.get("cache_read_input_tokens", 0)),
            ),
            model=self.take.served,
        )

    def _results(self, request: Mapping[str, Any], recorded: Mapping[str, Any]) -> None:
        """What the engine returned for the previous turn's calls, against what
        it returned when the turn was recorded."""
        messages = list(request.get("messages") or ())
        sent = messages[-1]["content"] if messages and messages[-1]["role"] == "user" else []
        by_id = {r["tool_use_id"]: r for r in sent if isinstance(r, dict)}
        uses = [b for b in recorded["blocks"] if b["type"] == "tool_use"]
        for ident, block in zip(self._ids[-1], uses, strict=True):
            now = by_id.get(ident)
            if now is None:
                raise ReplayDivergedError(
                    f"{self.take.domain}: no result for the call to {block['tool']}"
                )
            self._compare(block, str(now["content"]), bool(now["is_error"]))

    def _compare(self, block: Mapping[str, Any], text: str, error: bool) -> None:
        was = block.get("error")
        if was is not None and bool(was) != error:
            said = {True: "refused or failed", False: "went through"}
            raise ReplayDivergedError(
                f"{self.take.domain}: {block['tool']} {said[bool(was)]} when recorded and "
                f"{said[error]} now, so the model's later turns no longer follow"
            )
        recorded = block.get("result")
        if recorded is None or recorded == text:
            return
        old, new = _MINTED.findall(str(recorded)), _MINTED.findall(text)
        if len(old) == len(new):
            self.renamed.update({o: n for o, n in zip(old, new, strict=True) if o != n})
            if _MINTED.sub("*", str(recorded)) == _MINTED.sub("*", text):
                return
        self.changed.append({"tool": block["tool"], "recorded": recorded, "now": text})

    def replayed(self) -> dict[str, Any]:
        """What the act records of the replay: what it replayed, and how today's
        engine answered differently. Raises :class:`ReplayDivergedError` if a recorded turn
        was never asked for."""
        if self._n != len(self.take.turns):
            raise ReplayDivergedError(
                f"{self.take.domain}: the engine stopped after turn {self._n} of the "
                f"recording's {len(self.take.turns)}"
            )
        take = self.take
        return {
            "recorded_at": take.recorded_at,
            "recorded_with": take.recorded_with,
            "recorded_metered": take.metered,
            "usage_per_turn": take.split,
            "results_changed": self.changed,
            "renamed": dict(sorted(self.renamed.items())),
        }


def _schema_problem(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> str:
    properties = schema.get("properties") or {}
    missing = [k for k in schema.get("required") or () if k not in arguments]
    if missing:
        return f"{missing[0]} is required"
    if schema.get("additionalProperties") is False:
        extra = [k for k in arguments if k not in properties]
        if extra:
            return f"{extra[0]} is not one of its arguments"
    return ""


# --------------------------------------------------------------------------
# reading a recording out of a run
# --------------------------------------------------------------------------


def from_run(root: Path) -> Recording:
    """The recording in a run ``fiduciary demo --live`` wrote (module docstring)."""
    takes: dict[str, Take] = {}
    for where, page in _pages(root):
        demo = page["demo"]
        domain = (demo.get("domain") or {}).get("key", "fintech")
        for act in demo.get("acts", []):
            model = act.get("model")
            if model and act.get("mode") == "live":
                takes[domain] = _take(where, demo, act, model, domain)
    if not takes:
        raise ValueError(f"{root} holds no scenario a live model worked")
    return Recording(takes, source=root.name)


def _pages(root: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    top = json.loads((root / "demo.json").read_text(encoding="utf-8"))
    yield root, {k: v for k, v in top.items() if k != "domains"}
    for page in top.get("domains", []):
        key = page["demo"]["domain"]["key"]
        if key != "fintech" and (root / key / "demo.json").is_file():
            yield root / key, json.loads((root / key / "demo.json").read_text(encoding="utf-8"))


def _take(
    where: Path,
    demo: Mapping[str, Any],
    act: Mapping[str, Any],
    model: Mapping[str, Any],
    domain: str,
) -> Take:
    usage = {k: int(v) for k, v in model["usage"].items()}
    transcript = model.get("transcript")
    split = "recorded"
    if transcript and all("usage" in t for t in transcript):
        turns = [dict(t) for t in transcript]
    else:
        split = "ledger"
        turns = _legacy_turns(where, act, model, transcript)
    for turn in turns:
        turn.pop("turn", None)
    return Take(
        domain=domain,
        model=str(model["model"]),
        served=str(model["served"]),
        effort=str(model["effort"]),
        recorded_at=str(demo.get("generated_at", "")),
        recorded_with=str(demo.get("version", "")),
        request=str(act.get("request", "")),
        turns=tuple(turns),
        answer=str(model.get("answer", "")),
        seconds=float(model.get("seconds", 0)),
        metered=str(model.get("metered") or act.get("metered")),
        usage=usage,
        split=split,
    )


def _legacy_turns(
    where: Path,
    act: Mapping[str, Any],
    model: Mapping[str, Any],
    transcript: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Each turn of a run recorded before 1.10.0, with its usage: its blocks
    from the transcript, or, with none, its calls put in turn by when the
    engine saw each; its tokens split so its cost is its ledger's spend."""
    calls = list(act.get("calls") or [])
    sessions = {c["gateway"]["session"] for c in calls if c.get("gateway", {}).get("session")}
    if len(sessions) != 1:
        raise ValueError(f"{where}: the live scenario's calls name {len(sessions)} sessions")
    session = sessions.pop()
    spends = _ledger(where, f"FIDU1 session {session} model {model['model']}")
    if len(spends) != int(model["turns"]):
        raise ValueError(
            f"{where}: the ledger holds {len(spends)} model spends, the act {model['turns']} turns"
        )
    if transcript:
        turns = [{"stop": t["stop"], "blocks": [dict(b) for b in t["blocks"]]} for t in transcript]
    else:
        turns = _grouped(where, session, calls, [t for t, _ in spends], model)
    costs = [amount for _, amount in spends]
    for turn, tokens in zip(turns, _split(costs, model), strict=True):
        turn["usage"] = tokens
    return turns


def _ledger(where: Path, memo: str) -> list[tuple[str, Decimal]]:
    with closing(sqlite3.connect(f"file:{where / 'ledger.db'}?mode=ro", uri=True)) as conn:
        rows = conn.execute(
            "SELECT timestamp, amount FROM entries WHERE entry_type = 'spend' AND memo = ? "
            "ORDER BY sequence",
            (memo,),
        ).fetchall()
    return [(str(t), Decimal(str(a))) for t, a in rows]


def _grouped(
    where: Path,
    session: str,
    calls: Sequence[Mapping[str, Any]],
    starts: Sequence[str],
    model: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Calls put in the turn whose response came before the engine saw them:
    a plan when its chain admitted it, a passthrough call when the ledger
    charged for it, a call the gateway denied with the call before it."""
    admitted: dict[str, str] = {}
    for chain in sorted((where / "chains").glob("*.chain")):
        for line in chain.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record.get("record_type") == "plan_admitted":
                admitted.setdefault(str(record["plan_id"]), _instant(record["timestamp"]))
    charged: dict[str, list[str]] = {}
    seen = starts[0]
    turns: list[dict[str, Any]] = [{"stop": "tool_use", "blocks": []} for _ in starts]
    for call in calls:
        tool = call["tool"]
        plan = (call.get("measured") or {}).get("plan")
        if plan and plan in admitted:
            seen = admitted[plan]
        elif call.get("outcome") == "served":
            if tool not in charged:
                charged[tool] = [
                    t for t, _ in _ledger(where, f"FIDU1 session {session} tool {tool}")
                ]
            if charged[tool]:
                seen = charged[tool].pop(0)
        index = max(i for i, start in enumerate(starts) if start <= seen)
        feedback = call.get("feedback") or None
        turns[index]["blocks"].append(
            {
                "type": "tool_use",
                "tool": tool,
                "arguments": dict(call["arguments"]),
                "result": feedback,
                "error": call.get("outcome") not in OK_OUTCOMES,
            }
        )
    turns[-1]["stop"] = str(model["stop"])
    if model.get("answer"):
        turns[-1]["blocks"].append({"type": "text", "text": str(model["answer"])})
    if any(not t["blocks"] for t in turns):
        raise ValueError(f"{where}: a turn holds no call and no text")
    return turns


def _instant(text: str) -> str:
    """An ISO instant as the ledger writes it: UTC, ``Z``."""
    return text.replace("+00:00", "Z")


def _split(costs: Sequence[Decimal], model: Mapping[str, Any]) -> list[dict[str, int]]:
    """Each turn's input and output tokens: output in proportion to the turn's
    cost, input what makes the turn cost exactly its spend, and no turn's input
    smaller than the turn's before; together, the recorded totals."""
    from fiduciary.demo.world import PRICING

    rates = PRICING[str(model["model"])]
    total_in = int(model["usage"]["input_tokens"])
    total_out = int(model["usage"]["output_tokens"])
    million = Decimal(1_000_000)
    whole = sum(costs, Decimal(0))
    found: list[dict[str, int]] = []
    left_out = total_out
    for i, cost in enumerate(costs):
        last = i == len(costs) - 1
        out = left_out if last else round(Decimal(total_out) * cost / whole)
        priced_in, priced_out = rates.input_usd_per_mtok, rates.output_usd_per_mtok
        tokens_in = (cost * million - out * priced_out) / priced_in
        while tokens_in != tokens_in.to_integral_value() and out > 0 and not last:
            out -= 1
            tokens_in = (cost * million - out * priced_out) / priced_in
        found.append({"input_tokens": int(tokens_in), "output_tokens": out})
        left_out -= out
    # Each turn sends the whole conversation, so its input is never smaller than the
    # turn's before: move output tokens back a turn, which keeps both turns' costs,
    # until it is not. One output token is worth (output rate / input rate) input.
    ratio = int(rates.output_usd_per_mtok / rates.input_usd_per_mtok)
    for _ in range(len(found)):
        for i in range(len(found) - 1):
            a, b = found[i], found[i + 1]
            gap = a["input_tokens"] - b["input_tokens"]
            if gap > 0 and ratio > 0:
                shift = min(-(-gap // (2 * ratio)), b["output_tokens"])
                a["output_tokens"] += shift
                a["input_tokens"] -= shift * ratio
                b["output_tokens"] -= shift
                b["input_tokens"] += shift * ratio
    if sum(t["input_tokens"] for t in found) != total_in or any(
        t["input_tokens"] < 0 or t["output_tokens"] < 0 for t in found
    ):
        raise ValueError("the turns' spends do not split into the recorded token totals")
    return found

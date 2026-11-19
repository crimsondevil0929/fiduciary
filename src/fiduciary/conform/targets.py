"""What a conformance run talks to (1.22.0), and how a run is recorded and replayed.

A target answers one user turn at a time:

- ``openai``: an OpenAI-style ``POST <base>/chat/completions``. Point it at the endpoint
  whose output users see, safety layers included.
- ``http``: ``POST <url>`` with ``{"session", "messages"}``, answered ``{"reply": "..."}``
  (or the OpenAI shape).
- ``stand-in``: one of :mod:`fiduciary.conform.standins`, in process.
- ``replay``: a recording, answering exactly the conversation it was made from, at no cost.

With ``hooks`` the run also sends a test clock and the account's age: on ``openai`` as
string ``metadata`` (``conformance_session``, ``conformance_elapsed_seconds``,
``conformance_previous_elapsed_seconds``, ``conformance_minor``), on ``http`` as
``elapsed_seconds``, ``previous_elapsed_seconds`` and ``account: {"minor"}``. An endpoint
that honours them lets the three-hour checks run in seconds; without them, those checks are
untested. Without hooks, a minor says their age in the conversation.

A recording is the run's transcripts, with what the target was. It holds what the endpoint
said and nothing else: never a key, a header or the endpoint's address.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from fiduciary import fidu1
from fiduciary.conform import standins

__all__ = [
    "FORMAT",
    "ConformError",
    "HttpTarget",
    "OpenAITarget",
    "ReplayTarget",
    "StandInTarget",
    "Target",
    "converse",
    "recording",
]

FORMAT: Final = "fiduciary-conform-recording/1"
MAX_REPLY: Final = 1 << 20
"""The most a run reads of one answer."""
TIMEOUT: Final = 60.0


class ConformError(Exception):
    """A run could not go on: the endpoint failed, or a recording does not fit."""


class Target(Protocol):
    """Something that answers a user turn."""

    label: str
    kind: str
    model: str | None
    endpoint: str | None
    """The SHA-256 of the endpoint's address, never the address itself."""
    hooks: bool

    def answer(self, call: Call) -> str: ...


@dataclass(frozen=True, slots=True)
class Call:
    """One user turn, with what came before it."""

    scenario: str
    session: str
    turn: int
    history: Sequence[Mapping[str, str]]
    user: str
    at: int
    before: int | None
    minor: bool


def _address(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        raise ConformError(f"{url!r} is not an http or https address")
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _post(url: str, body: Mapping[str, Any], key: str | None) -> Any:
    headers = {"Content-Type": "application/json", "User-Agent": "fiduciary-conform"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(  # noqa: S310 - the scheme is checked in _address
        url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
            data = response.read(MAX_REPLY + 1)
    except urllib.error.HTTPError as exc:
        raise ConformError(f"the endpoint answered HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ConformError(f"the endpoint could not be reached: {exc}") from exc
    if len(data) > MAX_REPLY:
        raise ConformError("the endpoint's answer is larger than 1 MiB")
    try:
        return json.loads(data)
    except ValueError as exc:
        raise ConformError(f"the endpoint's answer is not JSON: {exc}") from exc


def _text(answer: Any) -> str:
    if isinstance(answer, dict):
        if isinstance(answer.get("reply"), str):
            return str(answer["reply"])
        try:
            content = answer["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            content = None
        if isinstance(content, str):
            return content
    raise ConformError('the endpoint\'s answer has no reply: expected {"reply": ...} or choices')


@dataclass
class OpenAITarget:
    """``POST <base>/chat/completions``."""

    url: str
    model: str | None = None
    key_env: str | None = None
    hooks: bool = False
    label: str = "openai endpoint"
    kind: str = field(default="openai", init=False)
    endpoint: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.endpoint = _address(self.url)

    def answer(self, call: Call) -> str:
        url = self.url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        body: dict[str, Any] = {
            "messages": [*call.history, {"role": "user", "content": call.user}],
        }
        if self.model:
            body["model"] = self.model
        if self.hooks:
            body["metadata"] = {
                "conformance_session": call.session,
                "conformance_elapsed_seconds": str(call.at),
                "conformance_previous_elapsed_seconds": str(
                    call.at if call.before is None else call.before
                ),
                "conformance_minor": "true" if call.minor else "false",
            }
        return _text(_post(url, body, _key(self.key_env)))


@dataclass
class HttpTarget:
    """``POST <url>`` with ``{"session", "messages"}``."""

    url: str
    key_env: str | None = None
    hooks: bool = False
    label: str = "http endpoint"
    model: str | None = None
    kind: str = field(default="http", init=False)
    endpoint: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.endpoint = _address(self.url)

    def answer(self, call: Call) -> str:
        body: dict[str, Any] = {
            "session": call.session,
            "messages": [*call.history, {"role": "user", "content": call.user}],
        }
        if self.hooks:
            body["elapsed_seconds"] = call.at
            body["previous_elapsed_seconds"] = call.at if call.before is None else call.before
            body["account"] = {"minor": call.minor}
        return _text(_post(self.url, body, _key(self.key_env)))


def _key(name: str | None) -> str | None:
    if name is None:
        return None
    value = os.environ.get(name)
    if not value:
        raise ConformError(f"${name} is not set: it should hold the endpoint's key")
    return value


@dataclass
class StandInTarget:
    """One of the stand-ins, in process."""

    bot: str
    hooks: bool = True
    kind: str = field(default="stand-in", init=False)
    model: str | None = field(default=None, init=False)
    endpoint: str | None = field(default=None, init=False)
    label: str = field(default="", init=False)

    def __post_init__(self) -> None:
        if self.bot not in standins.BOTS:
            raise ConformError(f"no stand-in {self.bot!r}: choose from {', '.join(standins.BOTS)}")
        self.label = f"stand-in {self.bot}: {standins.BOTS[self.bot]}"

    def answer(self, call: Call) -> str:
        return standins.respond(
            self.bot,
            call.history,
            call.user,
            at=call.at if self.hooks else None,
            before=(call.at if call.before is None else call.before) if self.hooks else None,
            minor=call.minor and self.hooks,
        )


class ReplayTarget:
    """A recording, answering only the conversation it was made from."""

    def __init__(self, recorded: Mapping[str, Any]) -> None:
        if not isinstance(recorded, dict) or recorded.get("format") != FORMAT:
            raise ConformError(f"not a recording: its format is not {FORMAT}")
        target = recorded["target"]
        self.label = str(target["label"])
        self.kind = str(target["kind"])
        self.model = target.get("model")
        self.endpoint = target.get("endpoint")
        self.hooks = bool(recorded["hooks"])
        self._turns = {t["scenario"]: t["turns"] for t in recorded["transcripts"]}

    def answer(self, call: Call) -> str:
        turns = self._turns.get(call.scenario)
        if turns is None or call.turn > len(turns):
            raise ConformError(
                f"the recording has no turn {call.turn} of {call.scenario}: record it again"
            )
        recorded = turns[call.turn - 1]
        if recorded["user"] != call.user or recorded["at"] != call.at:
            raise ConformError(
                f"turn {call.turn} of {call.scenario} differs from the recording: the suite "
                "changed since it was made, so record it again"
            )
        return str(recorded["reply"])


def converse(target: Target, suite: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Hold every scenario's conversation with ``target``: the transcripts, in order."""
    transcripts = []
    for scenario in suite["scenarios"]:
        session = uuid.uuid4().hex
        history: list[dict[str, str]] = []
        turns = []
        before = None
        for n, turn in enumerate(scenario["turns"], 1):
            reply = target.answer(
                Call(
                    scenario["id"],
                    session,
                    n,
                    tuple(history),
                    turn["user"],
                    turn["at"],
                    before,
                    bool(scenario["minor"]),
                )
            )
            if not isinstance(reply, str):  # pragma: no cover - every target returns text
                raise ConformError("the endpoint's reply is not text")
            turns.append({"at": turn["at"], "user": turn["user"], "reply": reply})
            history += [
                {"role": "user", "content": turn["user"]},
                {"role": "assistant", "content": reply},
            ]
            before = turn["at"]
        transcripts.append(
            {
                "scenario": scenario["id"],
                "minor": bool(scenario["minor"]),
                "hooks": target.hooks,
                "turns": turns,
            }
        )
    return transcripts


def recording(target: Target, transcripts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """A run's recording: what the target was, and what it said."""
    made: dict[str, Any] = fidu1.parse(
        fidu1.canonical(
            {
                "format": FORMAT,
                "target": {
                    "label": target.label,
                    "kind": target.kind,
                    "model": target.model,
                    "endpoint": target.endpoint,
                },
                "hooks": target.hooks,
                "transcripts": list(transcripts),
            }
        )
    )
    return made

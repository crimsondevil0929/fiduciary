"""Approvals in the client's own flow: MCP 2026-07-28 multi round-trip requests (1.20.0).

A protected write the mandate holds waits for its owner. Before this, the agent was told so in
words, and the owner approved in ``fiduciary console``; the call's answer never came back to
the agent. MCP 2026-07-28 lets a server answer a ``tools/call`` with ``resultType:
"input_required"``: requests the client fulfils (here one URL-mode elicitation, the console's
page for the call's ticket), and an opaque ``requestState`` it sends back when it retries the
same call. The proxy does that, for a client that says it can:

- **Who gets it.** Only a request in the 2026-07-28 revision whose client declares URL-mode
  elicitation (``clientCapabilities.elicitation.url``). Every other client is told in words, as
  before: nothing changes for a client that does not ask.
- **What the person sees.** The client shows the console's URL for the ticket and asks before
  it opens it. The approval is made there, as any approval is: signed by the owner, with a
  passkey when the owner has one. The client only carries the URL; it signs nothing.
- **The retry.** The client sends the call again with ``requestState``. The proxy checks the
  state (an HMAC under a key the deployment keeps, binding the plan, the tool, the arguments and
  an expiry), then looks at the plan's requests: made, and the server's own answer goes back,
  as for a write that was never held; still held, and the proxy waits up to
  :data:`WAIT` seconds, then asks again; declined or expired, and the agent is told nothing was
  sent. The plan was committed once, on the first call: a retry never makes another.

Which clients do this today is the open question (the official Python SDK's client does; see
the README). The proxy offers it only to a client that declares it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from interlock.relay import Relay

    from fiduciary.bridge import CallResult, Fiduciary

__all__ = ["CONSOLE", "TTL", "WAIT", "Approvals", "url_capable"]

CONSOLE: Final = "http://localhost:8740"
"""Where ``fiduciary console`` listens unless told otherwise."""
WAIT: Final = 20.0
"""How long one retry waits for the owner, in seconds, before the proxy asks again."""
TTL: Final = 900
"""How long a ``requestState`` is good for, in seconds."""
KEY: Final = "approvals.key"
_META: Final = "io.modelcontextprotocol/"


def url_capable(params: Mapping[str, Any]) -> bool:
    """Whether a request's client declares URL-mode elicitation in its ``_meta``."""
    meta = params.get("_meta")
    capabilities = meta.get(_META + "clientCapabilities") if isinstance(meta, Mapping) else None
    elicitation = capabilities.get("elicitation") if isinstance(capabilities, Mapping) else None
    return isinstance(elicitation, Mapping) and "url" in elicitation


def _digest(name: str, arguments: Mapping[str, Any]) -> str:
    text = json.dumps({"tool": name, "arguments": arguments}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Approvals:
    """The in-band approvals of one protected deployment (module docstring).

    :param root: The deployment: where its key is kept, and what the console calls it.
    :param console: The console's origin, for the URL.
    """

    def __init__(self, root: Path, *, console: str = CONSOLE) -> None:
        self.root = root
        self.console = console.rstrip("/")
        self._key = self._load_key()

    def _load_key(self) -> bytes:
        path = self.root / KEY
        if not path.is_file():
            temporary = self.root / f"{KEY}.{os.getpid()}.tmp"
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w") as out:
                out.write(secrets.token_hex(32))
            if not path.exists():
                temporary.replace(path)
            else:
                temporary.unlink()
        return bytes.fromhex(path.read_text(encoding="utf-8").strip())

    # -- the state the client carries ---------------------------------------------------

    def _state(self, plan_id: str, name: str, arguments: Mapping[str, Any]) -> str:
        body = json.dumps(
            {"plan": plan_id, "call": _digest(name, arguments), "until": int(time.time()) + TTL},
            sort_keys=True,
        ).encode("utf-8")
        mac = hmac.new(self._key, body, hashlib.sha256).hexdigest()
        return base64.urlsafe_b64encode(body).decode("ascii") + "." + mac

    def _plan_of(self, state: object, name: str, arguments: Mapping[str, Any]) -> str | None:
        """The plan a ``requestState`` names, when this deployment gave it for this very
        call and it has not expired; ``None`` otherwise."""
        if not isinstance(state, str) or state.count(".") != 1:
            return None
        encoded, mac = state.split(".")
        try:
            body = base64.urlsafe_b64decode(encoded.encode("ascii"))
            found = json.loads(body)
        except (ValueError, UnicodeError):
            return None
        expected = hmac.new(self._key, body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(mac, expected) or not isinstance(found, dict):
            return None
        if (
            found.get("call") != _digest(name, arguments)
            or int(found.get("until", 0)) < time.time()
        ):
            return None
        plan = found.get("plan")
        return plan if isinstance(plan, str) else None

    # -- the first answer, and the retries ----------------------------------------------

    def ask(
        self, fid: Fiduciary, result: CallResult, name: str, arguments: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        """The ``input_required`` answer to a write the mandate held, or ``None`` for any
        other result."""
        value = result.value
        if not (result.committed and isinstance(value, dict) and value.get("held")):
            return None
        plan_id = str(value["plan_id"])
        return self._input_required(fid, plan_id, name, arguments)

    def _input_required(
        self, fid: Fiduciary, plan_id: str, name: str, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        from fiduciary.protect import plan_messages

        ticket = self._ticket(fid, [str(m) for m in plan_messages(self.root, plan_id)])
        query = {"d": _ident(self.root), **({"t": ticket} if ticket else {})}
        url = f"{self.console}/#/desk?{urllib.parse.urlencode(query)}"
        config = self.root.resolve() / "fiduciary.toml"
        return {
            "resultType": "input_required",
            "inputRequests": {
                "approval": {
                    "method": "elicitation/create",
                    "params": {
                        "mode": "url",
                        "url": url,
                        "message": (
                            f"fiduciary holds {name} until its owner approves it: nothing was "
                            "sent. Open the console to approve or decline it, then come back. "
                            f"If the page does not open, start it: fiduciary console "
                            f"--deployment {config} --accept-approvals"
                        ),
                    },
                }
            },
            "requestState": self._state(plan_id, name, arguments),
        }

    def _ticket(self, fid: Fiduciary, messages: Sequence[str]) -> str | None:
        """The open ticket that names one of ``messages``, raised now if none does yet."""
        from fiduciary.documents import Ticket
        from fiduciary.exceptions import FiduciaryError
        from fiduciary.service import raise_triggers

        if fid.outbox is not None:
            raise_triggers(fid)
        state = fid.log.state()
        for row in state.open_tickets():
            try:
                ticket = fid.escalation.decide.load(row.digest, Ticket)
            except FiduciaryError:
                continue
            named = {m.message_id for o in ticket.options for m in o.resolution.messages or ()}
            if named & set(messages):
                return str(ticket.ticket_id)
        return None

    def resume(
        self,
        fid: Fiduciary,
        relay: Relay | None,
        params: Mapping[str, Any],
        name: str,
        arguments: Mapping[str, Any],
        *,
        serve: Callable[[], None],
        wait: float | None = None,
    ) -> dict[str, Any]:
        """The answer to a retry that carries ``requestState``: the server's answer once the
        requests were made, words when they will not be, or ``input_required`` again."""
        from fiduciary.protect import plan_messages

        plan_id = self._plan_of(params.get("requestState"), name, arguments)
        if plan_id is None:
            return _words(
                "Not run: this retry carries a request state this server did not give for this "
                "call, or it has expired. Nothing was sent.",
                error=True,
            )
        responses = params.get("inputResponses")
        answer = responses.get("approval") if isinstance(responses, Mapping) else None
        action = answer.get("action") if isinstance(answer, Mapping) else None
        if action in ("decline", "cancel"):
            return _words(
                f"Still held for the owner's approval: nothing was sent. The owner can approve "
                f"{name} in fiduciary console; fiduciary_ticket_status says where it stands."
            )
        messages = plan_messages(self.root, plan_id)
        deadline = time.monotonic() + (WAIT if wait is None else wait)
        while True:
            serve()
            found = _made(relay, messages)
            if found is not None:
                return found
            if time.monotonic() >= deadline:
                return self._input_required(fid, plan_id, name, arguments)
            time.sleep(0.2)


def _ident(root: Path) -> str:
    import re

    return re.sub(r"[^a-z0-9_-]+", "-", root.resolve().name.lower()).strip("-") or "deployment"


def _words(text: str, *, error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": error}


def _made(relay: Relay | None, messages: Sequence[uuid.UUID]) -> dict[str, Any] | None:
    """What became of a plan's requests: the server's answers once all were made, words when
    one will never be, ``None`` while one is still held."""
    if relay is None:
        return _words("Not made: this server has no relay to make it here.", error=True)
    answers: list[dict[str, Any]] = []
    structured: Any = None
    error = False
    for message in messages:
        done = relay.deliver(message, wait=timedelta(seconds=5))
        if done.state == "held":
            return None
        if done.state in ("dead", "cancelled"):
            return _words(
                "Not made: the owner declined it, or its approval ran out. Nothing was sent.",
                error=True,
            )
        if not done.delivered:
            return None
        body = done.result.body if done.result is not None else None
        if body is None:
            answers.append(
                {
                    "type": "text",
                    "text": "Made, after its owner approved it. Its answer went to the "
                    "relay's log before this retry asked for it.",
                }
            )
            continue
        found = json.loads(body)
        answers.extend(c for c in found.get("content") or () if isinstance(c, dict))
        error = error or bool(found.get("isError"))
        if isinstance(found.get("structuredContent"), dict):
            structured = found["structuredContent"]
    out: dict[str, Any] = {"content": answers, "isError": error}
    if structured is not None and len(messages) == 1:
        out["structuredContent"] = structured
    return out

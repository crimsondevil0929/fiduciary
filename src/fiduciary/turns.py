"""Turns (1.17.0): several tool calls, one outcome for the requests they enqueue.

An agent's turn often makes more than one call: it refunds an order, then emails the
customer, then updates the ticket. Each call is a plan, judged and committed on its own;
without a turn, the email is sent as soon as its plan commits, even when the next call of
the same turn is refused.

Within a turn, every request a plan enqueues (an email, a Stripe call, a webhook, an MCP
server's write) enters the outbox held for the turn (Interlock 1.9.0). When the turn
ends, :func:`close` writes a ``turn.closed`` record and then, under one operator intent
that cites it:

- **releases** them, when every plan of the turn committed: the relay makes them;
- **cancels** them, when a plan of the turn was refused, or the turn ended in an
  exception: none is ever made.

What a turn does not do: put back what the turn's committed plans changed in the
systems they wrote to. A refused call leaves the earlier calls' rows changed; only the
requests wait. Where turns come from:

- your own code: ``with gov.turn(): ...`` (:meth:`fiduciary.sdk.Governor.turn`), around a
  framework's run as much as around a few calls;
- a protected MCP server in Claude Code: ``fiduciary turn open`` when a prompt is
  submitted, ``fiduciary turn close`` when Claude stops (``fiduciary protect
  --claude-code`` writes both hooks). The proxy serving the server holds its deployment,
  so the hook asks, and the proxy closes the turn on its next turn (within a second when
  it is idle); with no proxy running, the hook closes it itself.

MCP itself has no turn: Claude Desktop and Cursor say nothing a server could tell one by.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from fiduciary.exceptions import FiduciaryError

if TYPE_CHECKING:
    from fiduciary.bridge import Fiduciary

__all__ = [
    "CURRENT",
    "Closed",
    "TurnError",
    "close",
    "close_file",
    "closing",
    "current",
    "new_id",
    "note_refused",
    "open_turn",
    "request_close",
]

CURRENT: Final = "turn.json"
"""Where a deployment keeps the turn open now, for a proxy in another process to read."""


class TurnError(FiduciaryError):
    """A turn could not be opened or closed."""


@dataclass(frozen=True, slots=True)
class Closed:
    """What closing a turn did."""

    turn: str
    outcome: str
    """``released`` or ``cancelled``."""
    messages: tuple[str, ...]
    record: int
    """The ``turn.closed`` record's sequence number."""


def new_id() -> str:
    return f"turn-{uuid.uuid4().hex[:16]}"


def close(
    fid: Fiduciary, mandate_id: str, turn: str, *, release: bool, why: str | None = None
) -> Closed | None:
    """End ``turn``: record it, then release (or cancel) every request held for it.
    ``None`` when it held none: nothing is recorded.

    :raises TurnError: Without an outbox to hold requests in.
    """
    outbox = fid.outbox
    if outbox is None:
        return None
    held = outbox.turn_held(turn).get(turn, [])
    if not held:
        return None
    outcome = "released" if release else "cancelled"
    log = fid.log
    with fid.lock:
        with log.store.transaction():
            body = fid._authority.decide.close_turn(
                log.state(),
                {
                    "turn": turn,
                    "mandate_id": mandate_id,
                    "outcome": outcome,
                    "messages": [str(m) for m in held],
                    "why": why,
                },
            )
            record = log.append("turn.closed", body, issued_at=log.now())
        reason = (
            f"turn {turn} {'committed' if release else 'did not finish'}: "
            f"FIDU1 turn record {record.seq}"
        )
        try:
            fid.executor.close_turn(turn, release=release, reason=reason)
        except Exception as exc:
            raise TurnError(
                f"turn {turn} was recorded {outcome}, and the outbox said: {exc}"
            ) from exc
    return Closed(turn, outcome, tuple(str(m) for m in held), record.seq)


# -- a turn another process opens (Claude Code's hooks) ---------------------------------


def open_turn(root: Path, turn: str | None = None) -> str:
    """Open a turn for the deployment at ``root``: a proxy serving it reads it before each
    call. One already open is kept: a prompt submitted again within a turn is the same."""
    path = root / CURRENT
    found = current(root)
    if found is not None:
        return str(found["turn"])
    turn = turn or new_id()
    temporary = path.with_name(f"{CURRENT}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps({"turn": turn, "refused": None}) + "\n")
    os.replace(temporary, path)
    return turn


def current(root: Path) -> dict[str, Any] | None:
    """The turn open now at ``root``, or ``None``."""
    try:
        found = json.loads((root / CURRENT).read_text())
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) and isinstance(found.get("turn"), str) else None


def note_refused(root: Path, why: str) -> None:
    """Say that a call of the open turn was refused: it will be cancelled, not released."""
    found = current(root)
    if found is None or found.get("refused"):
        return
    found["refused"] = why
    temporary = root / f"{CURRENT}.{os.getpid()}.tmp"
    temporary.write_text(json.dumps(found) + "\n")
    os.replace(temporary, root / CURRENT)


def request_close(root: Path) -> bool:
    """Ask for the open turn at ``root`` to be closed: by the proxy serving the deployment,
    which holds it while it runs, on its next turn; or by whoever opens it next. Whether
    a turn was open."""
    found = current(root)
    if found is None:
        return False
    found["close"] = True
    temporary = root / f"{CURRENT}.{os.getpid()}.tmp"
    temporary.write_text(json.dumps(found) + "\n")
    os.replace(temporary, root / CURRENT)
    return True


def closing(root: Path) -> bool:
    """Whether the turn open at ``root`` was asked to close."""
    found = current(root)
    return found is not None and bool(found.get("close"))


def close_file(fid: Fiduciary, root: Path, mandate_id: str) -> Closed | None:
    """Close the turn the deployment at ``root`` has open: released unless a call of it
    was refused. The turn file is removed either way."""
    found = current(root)
    if found is None:
        return None
    try:
        refused = found.get("refused")
        return close(fid, mandate_id, str(found["turn"]), release=not refused, why=refused or None)
    finally:
        (root / CURRENT).unlink(missing_ok=True)

"""``fiduciary undo`` (1.18.0): put a committed plan back, as the principals decide.

A plan's commit kept, in the system it changed, its before and after images (Interlock
1.10.0, ``[undo] days`` in ``interlock.toml``). An undo is an ``undo`` ticket whose option
puts the plan back from them: Interlock stages the inverse, measures it, and commits it only
as exactly that inverse (then without the size bounds, which would refuse undoing a large
mistake); its delivered requests are compensated where their sink has a compensation, and
those not sent yet are cancelled. The ticket's quorum is the mandate's ``compensate`` (in a
solo deployment, the owner, who approves it here with a key file or a touch of a passkey).

What cannot be undone is said before anything is asked: a request a relay delivered with no
compensation (an email that was sent). The undo is refused then, unless ``partial`` says to
put back the rest and record what was not.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fiduciary import fidu1
from fiduciary.exceptions import FiduciaryError

if TYPE_CHECKING:
    from fiduciary.bridge import Fiduciary
    from fiduciary.documents import Ticket

__all__ = ["Plan", "UndoError", "approve_solo", "ask", "find"]


class UndoError(FiduciaryError):
    """A plan this deployment cannot undo, or not as asked."""


@dataclass
class Plan:
    """A committed plan, and what undoing it would do."""

    plan_id: str
    mandate_id: str
    changes: int
    """How many resources its commit changed, and kept."""
    kept: tuple[str, int] | None
    compensate: list[str] = field(default_factory=list)
    """Requests a relay delivered, each with a compensation that undoes it."""
    cancel: list[str] = field(default_factory=list)
    """Requests not sent yet."""
    cannot: list[tuple[str, str]] = field(default_factory=list)
    """``(message id, effect id)``: sent, and nothing undoes it."""


def find(fid: Fiduciary, plan_id: str, mandate_id: str | None = None) -> Plan:
    """What undoing committed plan ``plan_id`` would do, and in which lineage.

    :raises UndoError: When no lineage of the deployment kept anything of it.
    """
    from interlock.undo import changes_hash

    from fiduciary.escalation import UNSENT

    engines = fid.binder.engines()
    lineages = [mandate_id] if mandate_id is not None else sorted(engines)
    for lineage in lineages:
        engine = engines.get(lineage)
        kept = engine.kept(plan_id) if engine is not None else None
        if kept is None:
            continue
        plan = Plan(
            plan_id, lineage, len(kept.changes), (changes_hash(kept.digests()), len(kept.changes))
        )
        for c in fid.outbox.compensables(plan_id) if fid.outbox is not None else []:
            if c.state == "delivered" and c.compensated_by is None:
                if c.compensation is not None:
                    plan.compensate.append(str(c.message_id))
                else:
                    plan.cannot.append((str(c.message_id), str(c.effect_id)))
            elif c.state in UNSENT:
                plan.cancel.append(str(c.message_id))
        return plan
    raise UndoError(
        f"nothing is kept to undo plan {plan_id}: it was not committed here, changed nothing, "
        f"is older than the undo window, or its deployment keeps no undo ([undo] days in "
        f"interlock.toml)"
    )


def ask(fid: Fiduciary, plan: Plan, *, partial: bool = False) -> Ticket:
    """Raise the ``undo`` ticket for ``plan``.

    :raises UndoError: When a request cannot be undone and ``partial`` is false, or the
        lineage has as many open tickets as it allows.
    """
    from fiduciary.escalation import EscalationError

    if plan.cannot and not partial:
        raise UndoError(
            f"plan {plan.plan_id} sent {len(plan.cannot)} request(s) nothing undoes: "
            + ", ".join(f"{effect} (message {message})" for message, effect in plan.cannot)
            + ". Put back the rest anyway with --partial"
        )
    try:
        ticket, _ = fid.escalation.raise_undo(
            plan.mandate_id,
            fid.outbox,
            plan.plan_id,
            kept=plan.kept,
            partial=partial,
        )
    except EscalationError as exc:
        raise UndoError(str(exc)) from exc
    if ticket is None:
        raise UndoError("not raised: this mandate has as many open tickets as it allows")
    return ticket


def approve_solo(fid: Fiduciary, root: Path, ticket: Ticket, *, passkey: Any = None) -> bool:
    """In a deployment with one principal, approve the ticket's ``undo`` option as that
    principal (its key file, or its passkey), and serve the deployment once: the warrant
    forms and executes. Whether it was a solo deployment."""
    from fiduciary.documents import Approval
    from fiduciary.passkeys import signer_for

    charter = fid._authority.decide.charter(fid.log.state())
    if len(charter.principals) != 1:
        return False
    owner = charter.principals[0].name
    option = next(o for o in ticket.options if o.resolution.action == "undo")
    now = fid.log.now()
    approval = Approval.from_json(
        {
            "v": "FIDU1",
            "type": "approval",
            "approval_id": str(uuid.uuid4()),
            "charter": {"charter_id": charter.charter_id, "version": charter.version},
            "principal": owner,
            "subject": {"type": "ticket", "digest": ticket.digest},
            "resolution": option.digest,
            "decision": "approve",
            "issued_at": fidu1.format_instant(now),
            "not_after": fidu1.format_instant(now + timedelta(hours=1)),
        },
        signed=False,
    ).signed(signer_for(fid, root, owner, passkey=passkey))
    fid.log.store.submit(approval.canonical())
    fid.serve_once()
    return True


def outcome(fid: Fiduciary, ticket: Ticket) -> dict[str, Any] | None:
    """What the ticket's warrant did, once it executed: its ``execution.applied`` or
    ``execution.refused`` body, with ``event``; ``None`` while it has not."""
    state = fid.log.state()
    for digest, row in state.warrants.items():
        if row.ticket_id != ticket.ticket_id:
            continue
        for event, body, _ in state.executions.get(digest, ()):
            if event in ("applied", "refused", "abandoned"):
                return {"event": event, **body}
    return None

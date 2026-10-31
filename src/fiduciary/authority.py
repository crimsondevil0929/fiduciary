"""Who decides: approvals in, charters adopted, keys suspended, mandates issued.

Every decision here is a pure function of the log's state before it, the
documents stored, and the instant it is taken (:class:`Decide`). The service
takes it and records the result; :func:`replay` takes every recorded decision
again from the state before its record and requires the same result, so a
record the log holds that its own rules would not have written is named.

The rules (``docs/FIDUCIARY_SPEC.md``):

- An approval counts only as recorded: its signature verifies under the key
  the charter it names pins for its principal, that key is not suspended, its
  subject is one the store holds, and it was recorded before its own
  ``not_after`` (F3-4, F4-5, §7.3). Anything else is recorded as refused, and
  never counts.
- Charter version 1 is adopted by ``quorums.charter`` of its own principals,
  when configuration pins its digest; version n+1 by ``quorums.charter`` of
  version n's (F4-3). The deployment's ledger never changes.
- A key is suspended by ``quorums.revoke`` principals of the charter in force (F4-5).
- A root mandate is issued by ``quorums.issue`` of the charter's principals, and
  amended by ``roster.quorums.amend`` of its previous version's roster. A child
  issued by quorum needs its parent's ``roster.quorums.amend``; a derived child
  needs no approval, only to be one (F4-7, F5-2).

Quorums count principals, never keys or approvals (F7-5): each principal's
latest decision recorded on the subject.
"""

from __future__ import annotations

import hashlib
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Final, TypeVar

from agentgov.receipts.signing import Signer, Verifier

from fiduciary import fidu1
from fiduciary.checkers import admits_scope
from fiduciary.documents import (
    Approval,
    Charter,
    Document,
    Mandate,
    Record,
    Resolution,
    Ticket,
    TicketRef,
    Warrant,
    WarrantBasis,
    execution_steps,
    quorum_for_resolution,
    suspension_digest,
    warrant_not_after,
)
from fiduciary.documents.mandate import AutoRule
from fiduciary.documents.ticket import (
    AUTO_ACTIONS,
    AmendEvidence,
    DeadEvidence,
    DiffItem,
    ExtensionEvidence,
    RefusedEvidence,
)
from fiduciary.exceptions import AuthorityError, FiduciaryError, MalformedError, SignatureError
from fiduciary.log import Log
from fiduciary.refines import delegation_problems, refines
from fiduciary.store import State, Store, Withheld

__all__ = ["BUILTIN_TOOLS", "REFUSAL_CODES", "Authority", "Decide", "replay", "replay_each"]

BUILTIN_TOOLS: Final = ("fiduciary_request", "fiduciary_ticket_status")
"""The two tools every session lists beside its pinned bindings (F8-4)."""
REFUSAL_CODES: Final = (
    "arguments",  # outside the binding's schema, or the canonical domain (F4-13)
    "max_open",  # the lineage's open tickets are at escalation.max_open (F7-2)
    "model",  # a model the mandate does not list, or no price is configured for
    "revoked",  # a tool a recovery step revoked
    "tenant",  # a binding that stamps the session's tenant, in a session with none
    "unpinned",  # no binding the version pins, and no built-in (F4-16)
    "velocity",  # past governance.max_calls in governance.seconds (F6-5)
)
"""Why a session refused a call before anything was built or metered."""


D = TypeVar("D", bound=Document)


class _Documents:
    """Documents read from a store, parsed, by digest: a document never
    changes under its digest, so one read serves every decision after it.
    Only bytes that hash to their digest are kept; the least recently used
    go first past ``size``."""

    def __init__(self, size: int = 4096) -> None:
        self._found: OrderedDict[str, Document] = OrderedDict()
        self._size = size
        self._lock = threading.Lock()

    def get(self, digest: str) -> Document | None:
        with self._lock:
            found = self._found.get(digest)
            if found is not None:
                self._found.move_to_end(digest)
            return found

    def put(self, digest: str, document: Document) -> None:
        with self._lock:
            self._found[digest] = document
            if len(self._found) > self._size:
                self._found.popitem(last=False)


@dataclass(frozen=True, slots=True)
class Decide:
    """The decisions, as functions of a state, a store and an instant."""

    store: Store
    pinned: str
    """The digest of charter version 1, as configuration pins it."""
    service_key: Verifier
    """The key derived mandates are signed with."""
    _documents: _Documents = field(default_factory=_Documents, repr=False, compare=False)

    def load(self, digest: str, cls: type[D]) -> D:
        """The document ``digest``, a ``cls``, as the store holds it.

        :raises AuthorityError: When the store holds none, or another type.
        """
        found = self._documents.get(digest)
        if found is None:
            held = self.store.document(digest)
            if held is None:
                raise AuthorityError(f"the store holds no document {digest[:16]}")
            kind, data = held
            if kind != cls.TYPE:
                raise AuthorityError(f"document {digest[:16]} is a {kind}, not a {cls.TYPE}")
            found = cls.loads(data)
            if hashlib.sha256(data).hexdigest() == digest:
                self._documents.put(digest, found)
        if not isinstance(found, cls):
            raise AuthorityError(f"document {digest[:16]} is a {found.TYPE}, not a {cls.TYPE}")
        return found

    # -- charters ------------------------------------------------------------------

    def charter(self, state: State, version: int | None = None) -> Charter:
        """An adopted charter: the one in force, or ``version``."""
        if not state.charters:
            raise AuthorityError("no charter is adopted yet")
        for found, digest, _ in reversed(state.charters):
            if version is None or found == version:
                charter: Charter = self.load(digest, Charter)
                return charter
        raise AuthorityError(f"charter version {version} was never adopted")

    def _signing_charter(self, state: State, charter_id: str, version: int) -> Charter:
        """The charter an approval names: the one in force, or, before any is,
        the pinned version 1."""
        if state.charter is None:
            pinned: Charter = self.load(self.pinned, Charter)
            if (pinned.charter_id, pinned.version) != (charter_id, version):
                raise AuthorityError(
                    f"no charter is in force, and the approval names charter {charter_id} "
                    f"version {version}, not the pinned version 1"
                )
            return pinned
        current = self.charter(state)
        if (current.charter_id, current.version) != (charter_id, version):
            raise AuthorityError(
                f"the approval names charter version {version}; version {current.version} "
                f"is in force"
            )
        return current

    # -- approvals ----------------------------------------------------------------

    def approval(
        self, state: State, intake: int, data: bytes, now: datetime
    ) -> tuple[str, dict[str, Any], Approval | None]:
        """Judge one submission: ``("approval.recorded", body, approval)`` or
        ``("approval.refused", body, None)``."""
        refused_digest = hashlib.sha256(data).hexdigest()
        try:
            approval = Approval.loads(data)
            charter = self._signing_charter(state, approval.charter_id, approval.charter_version)
            principal = charter.principal(approval.principal)
            if principal is None:
                raise AuthorityError(
                    f"{approval.principal!r} is no principal of the charter it names"
                )
            if (principal.name, principal.key) in state.suspended:
                raise AuthorityError(f"{principal.name}'s key is suspended")
            approval.verify(principal.public_key)
            self._subject_known(state, approval, now)
            if now > approval.not_after:
                raise AuthorityError(f"it expired at {fidu1.format_instant(approval.not_after)}")
        except (FiduciaryError, ValueError) as exc:
            return (
                "approval.refused",
                {"intake": intake, "digest": refused_digest, "why": _why(exc)},
                None,
            )
        body = {
            "intake": intake,
            "approval": approval.digest,
            "principal": approval.principal,
            "decision": approval.decision,
            "subject": approval.subject.to_json(),
            "resolution": approval.resolution,
        }
        return "approval.recorded", body, approval

    def _subject_known(self, state: State, approval: Approval, now: datetime | None = None) -> None:
        subject = approval.subject
        if subject.type in ("charter", "mandate"):
            found = self.store.document(subject.digest)
            if found is None or found[0] != subject.type:
                raise AuthorityError(
                    f"the store holds no {subject.type} {subject.digest[:16]}: propose it first"
                )
        elif subject.type == "revocation":
            if subject.digest not in state.issued:
                raise AuthorityError(f"no issued mandate has digest {subject.digest[:16]}")
        elif subject.type == "ticket":
            ticket_id = state.tickets_by_digest.get(subject.digest)
            if ticket_id is None:
                raise AuthorityError(f"no ticket is open with digest {subject.digest[:16]}")
            status = state.ticket_status(ticket_id)
            if status in ("lapsed", "declined"):
                raise AuthorityError(f"ticket {subject.digest[:16]} is {status}")
            ticket: Ticket = self.load(subject.digest, Ticket)
            if now is not None and now > ticket.not_after:
                raise AuthorityError(
                    f"ticket {subject.digest[:16]} expired at "
                    f"{fidu1.format_instant(ticket.not_after)}"
                )
            assert approval.resolution is not None
            self.resolution_for_ticket(ticket, approval.resolution)

    def resolution_for_ticket(self, ticket: Ticket, res_digest: str) -> Resolution:
        """The :class:`Resolution` of ``ticket`` whose digest is ``res_digest``:
        an exact option, or a stored narrowing of one (F4-20)."""
        option = ticket.option_by_digest(res_digest)
        if option is not None:
            return option.resolution
        found = self.store.document(res_digest)
        if found is not None and found[0] == "resolution":
            res = Resolution.read(fidu1.parse(found[1]))
            if ticket.option_for(res) is not None:
                return res
            raise AuthorityError(
                f"resolution {res_digest[:16]} does not narrow any option of "
                f"ticket {ticket.ticket_id}"
            )
        raise AuthorityError(
            f"resolution {res_digest[:16]} is no option of ticket {ticket.ticket_id}"
        )

    # -- counting --------------------------------------------------------------------

    def count(
        self,
        state: State,
        subject_type: str,
        subject_digest: str,
        approvers: Charter,
        roster: Sequence[str] | None = None,
    ) -> list[str]:
        """The digests of the approvals that count: one per principal of
        ``approvers`` (and of ``roster``, if given), each that principal's
        latest decision, an approval, made under ``approvers``."""
        allowed = set(approvers.names) if roster is None else set(roster) & set(approvers.names)
        counted = []
        for principal, row in sorted(state.decisions(subject_type, subject_digest).items()):
            if principal not in allowed or row.decision != "approve":
                continue
            approval: Approval = self.load(row.digest, Approval)
            if (approval.charter_id, approval.charter_version) != (
                approvers.charter_id,
                approvers.version,
            ):
                continue
            key = approvers.principal(principal)
            if key is None or self._suspended_before(state, key.name, key.key, row.seq):
                continue
            counted.append(row.digest)
        return sorted(counted)

    @staticmethod
    def _suspended_before(state: State, principal: str, key: str, seq: int) -> bool:
        suspended = state.suspended.get((principal, key))
        return suspended is not None and suspended < seq

    # -- charters, keys ---------------------------------------------------------------

    def adopt(self, state: State, digest: str) -> dict[str, Any]:
        """The ``charter.adopted`` body for charter ``digest``.

        :raises AuthorityError: When it is not the next version, or approvals fall short.
        """
        charter: Charter = self.load(digest, Charter)
        if state.charter is None:
            if digest != self.pinned or charter.version != 1:
                raise AuthorityError(
                    "the first charter adopted is version 1, the one configuration pins"
                )
            approvers = charter
        else:
            approvers = self.charter(state)
            if (charter.charter_id, charter.version, charter.prev) != (
                approvers.charter_id,
                approvers.version + 1,
                state.charter[1],
            ):
                raise AuthorityError(
                    f"charter {digest[:16]} is not version {approvers.version + 1} of charter "
                    f"{approvers.charter_id}, naming the version in force"
                )
            if charter.deployment.ledger != approvers.deployment.ledger:
                raise AuthorityError("a charter binds one ledger: a new version keeps it")
        if charter.signature is None or charter.signature.key_id not in {
            p.public_key.key_id for p in charter.principals
        }:
            raise AuthorityError("a charter is signed by one of its own principals")
        author = next(
            p for p in charter.principals if p.public_key.key_id == charter.signature.key_id
        )
        charter.verify(author.public_key)
        counted = self.count(state, "charter", digest, approvers)
        _enough(counted, approvers.charter_quorum, f"charter version {charter.version}")
        return {
            "charter": digest,
            "charter_id": charter.charter_id,
            "version": charter.version,
            "approvals": counted,
        }

    def suspend(self, state: State, principal: str) -> dict[str, Any]:
        charter = self.charter(state)
        found = charter.principal(principal)
        if found is None:
            raise AuthorityError(f"{principal!r} is no principal of the charter in force")
        if (found.name, found.key) in state.suspended:
            raise AuthorityError(f"{principal}'s key is suspended already")
        subject = suspension_digest(charter.charter_id, found.name, found.key)
        counted = self.count(state, "suspension", subject, charter)
        _enough(counted, charter.revoke_quorum, f"suspending {principal}'s key")
        return {
            "charter_id": charter.charter_id,
            "principal": found.name,
            "key": found.key,
            "approvals": counted,
        }

    # -- mandates -------------------------------------------------------------------

    def issue(self, state: State, digest: str, now: datetime) -> dict[str, Any]:
        """The ``mandate.issued`` body for mandate ``digest``."""
        mandate: Mandate = self.load(digest, Mandate)
        charter = self.charter(state)
        problems = mandate.charter_problems(charter)
        if problems:
            raise AuthorityError(f"mandate {digest[:16]}: {'; '.join(problems)}")
        if now >= mandate.not_after:
            raise AuthorityError(f"mandate {digest[:16]}'s term ended")
        head = state.head(mandate.mandate_id)
        if head is not None and not state.in_force(mandate.mandate_id):
            raise AuthorityError(
                f"lineage {mandate.mandate_id} is {state.status(mandate.mandate_id)}: it takes "
                f"no new version"
            )
        if head is None and mandate.version != 1:
            raise AuthorityError(
                f"lineage {mandate.mandate_id} has no version {mandate.version - 1}"
            )
        if head is not None and (mandate.version, mandate.prev) != (head[0] + 1, head[1]):
            raise AuthorityError(
                f"mandate {digest[:16]} is not version {head[0] + 1} of its lineage, "
                f"naming version {head[0]}"
            )
        counted: list[str] = []
        if mandate.parent is None:
            if head is None:
                approvers, quorum, roster = charter, charter.issue_quorum, None
            else:
                previous: Mandate = self.load(head[1], Mandate)
                approvers, quorum, roster = (
                    charter,
                    previous.roster.quorum("amend"),
                    previous.roster.principals,
                )
            self._signed_by_principal(mandate, charter)
            counted = self.count(state, "mandate", digest, approvers, roster)
            if len(counted) < quorum and head is not None:
                counted = (
                    self._amend_warrant_approvals(state, mandate.mandate_id, digest, now) or counted
                )
            _enough(counted, quorum, f"mandate {mandate.mandate_id} version {mandate.version}")
        else:
            parent_head = state.head(mandate.parent.mandate_id)
            if parent_head is None or parent_head[1] != mandate.parent.digest:
                raise AuthorityError("a child names its parent's version in force")
            if not state.in_force(mandate.parent.mandate_id):
                raise AuthorityError(
                    f"its parent lineage is {state.status(mandate.parent.mandate_id)}"
                )
            parent: Mandate = self.load(mandate.parent.digest, Mandate)
            found = delegation_problems(mandate, parent)
            if found:
                raise AuthorityError(
                    f"mandate {digest[:16]} does not refine its parent: "
                    + "; ".join(map(str, found))
                )
            if head is None:
                assert parent.delegation is not None
                children = self._children(state, parent.mandate_id)
                if len(children) >= parent.delegation.max_children:
                    raise AuthorityError(
                        f"the parent allows {parent.delegation.max_children} children, "
                        f"and has {len(children)}"
                    )
            if mandate.issuance == "derived":
                mandate.verify(self.service_key)
            else:
                self._signed_by_principal(mandate, charter)
                counted = self.count(state, "mandate", digest, charter, parent.roster.principals)
                if len(counted) < parent.roster.quorum("amend") and head is not None:
                    counted = (
                        self._amend_warrant_approvals(state, mandate.mandate_id, digest, now)
                        or counted
                    )
                _enough(
                    counted, parent.roster.quorum("amend"), f"child mandate {mandate.mandate_id}"
                )
        return {
            "mandate": digest,
            "mandate_id": mandate.mandate_id,
            "version": mandate.version,
            "prev": mandate.prev,
            "parent": None if mandate.parent is None else mandate.parent.digest,
            "issuance": mandate.issuance,
            "approvals": counted,
        }

    # -- the lineage after issuance (§5.4, §5.5) ---------------------------------

    def head_mandate(self, state: State, mandate_id: str) -> Mandate:
        head = state.head(mandate_id)
        if head is None:
            raise AuthorityError(f"no lineage {mandate_id} is issued")
        found: Mandate = self.load(head[1], Mandate)
        return found

    def bindable(self, state: State, digest: str) -> Mandate:
        """The mandate ``digest``, when it may be bound: the head of a lineage
        in force, whose parent lineage, if any, is bound and in force."""
        mandate_id = state.issued.get(digest)
        if mandate_id is None:
            raise AuthorityError(f"no issued mandate has digest {digest[:16]}")
        head = state.head(mandate_id)
        assert head is not None
        if head[1] != digest:
            raise AuthorityError(f"mandate {digest[:16]} is not its lineage's version in force")
        if not state.in_force(mandate_id):
            raise AuthorityError(f"lineage {mandate_id} is {state.status(mandate_id)}")
        parent = state.parent_lineage(mandate_id)
        if parent is not None and state.status(parent) != "bound":
            raise AuthorityError(
                f"its parent lineage {parent} is {state.status(parent)}, not bound"
            )
        mandate: Mandate = self.load(digest, Mandate)
        return mandate

    def rebase(self, state: State, mandate_id: str, parent: str) -> tuple[str, dict[str, Any]]:
        """What a parent's new version ``parent`` does to its child lineage
        (F5-7): ``mandate.rebased`` when the child's version in force still
        refines it, ``mandate.suspended`` with every reason when not."""
        child = self.head_mandate(state, mandate_id)
        new: Mandate = self.load(parent, Mandate)
        if state.issued.get(parent) != state.parent_lineage(mandate_id):
            raise AuthorityError(f"{parent[:16]} is no version of lineage {mandate_id}'s parent")
        problems = [str(p) for p in delegation_problems(child, new) if p.path != "parent"]
        if not problems:
            return "mandate.rebased", {
                "mandate": child.digest,
                "mandate_id": mandate_id,
                "parent": parent,
            }
        return "mandate.suspended", {
            "mandate": child.digest,
            "mandate_id": mandate_id,
            "cause": parent,
            "why": problems,
        }

    def revoke(self, state: State, digest: str) -> dict[str, Any]:
        """The ``mandate.revoked`` body for the version in force ``digest``
        (F5-8): approvals from the revocation quorum of its own roster, of any
        ancestor in force, or of the charter."""
        mandate_id = state.issued.get(digest)
        head = None if mandate_id is None else state.head(mandate_id)
        if mandate_id is None or head is None or head[1] != digest:
            raise AuthorityError(f"{digest[:16]} is no lineage's version in force")
        if not state.in_force(mandate_id):
            raise AuthorityError(f"lineage {mandate_id} is {state.status(mandate_id)} already")
        charter = self.charter(state)
        candidates: list[tuple[str, Sequence[str] | None, int]] = []
        current: str | None = mandate_id
        while current is not None:
            if state.in_force(current):
                found = self.head_mandate(state, current)
                by = "roster" if current == mandate_id else current
                candidates.append((by, found.roster.principals, found.roster.quorum("revoke")))
            current = state.parent_lineage(current)
        candidates.append(("charter", None, charter.revoke_quorum))
        best: list[str] = []
        for by, roster, quorum in candidates:
            counted = self.count(state, "revocation", digest, charter, roster)
            if len(counted) >= quorum:
                return {
                    "mandate": digest,
                    "mandate_id": mandate_id,
                    "approvals": counted,
                    "by": by,
                    "cascade": None,
                }
            best = max(best, counted, key=len)
        raise AuthorityError(
            f"revoking mandate {mandate_id} needs its roster's, an ancestor's or the charter's "
            f"revocation quorum; {len(best)} approvals count"
        )

    def cascade(self, state: State, mandate_id: str, cause: str, kind: str) -> dict[str, Any]:
        """A descendant's ``mandate.revoked`` or ``mandate.expired``, caused by
        an ancestor's (F5-8)."""
        ancestor = state.issued.get(cause)
        if ancestor is None or state.status(ancestor) not in ("revoked", "expired"):
            raise AuthorityError(f"{cause[:16]} was not revoked or expired")
        if mandate_id not in state.descendants(ancestor):
            raise AuthorityError(f"lineage {mandate_id} does not descend from {ancestor}")
        if not state.in_force(mandate_id):
            raise AuthorityError(f"lineage {mandate_id} is {state.status(mandate_id)} already")
        head = self.head_mandate(state, mandate_id)
        body: dict[str, Any] = {"mandate": head.digest, "mandate_id": mandate_id, "cascade": cause}
        if kind == "mandate.revoked":
            body.update(approvals=[], by="cascade")
        return body

    def expire(self, state: State, mandate_id: str, now: datetime) -> dict[str, Any]:
        """The ``mandate.expired`` body of a lineage whose term has ended."""
        if not state.in_force(mandate_id):
            raise AuthorityError(f"lineage {mandate_id} is {state.status(mandate_id)}")
        head = self.head_mandate(state, mandate_id)
        if now < head.not_after:
            raise AuthorityError(
                f"lineage {mandate_id}'s term runs to {fidu1.format_instant(head.not_after)}"
            )
        return {"mandate": head.digest, "mandate_id": mandate_id, "cascade": None}

    # -- tickets, quorum, warrants, execution (§7.1-7.5) --------------------------

    def _ticket_roster(self, state: State, ticket: Ticket) -> tuple[str, ...]:
        """The roster governing ``ticket`` (§7.3 rule 2): the roster of the
        mandate version ``ticket`` names, or, if that lineage is revoked, the
        nearest ancestor in force (or the charter's principals for a root)."""
        mandate: Mandate = self.load(ticket.mandate.digest, Mandate)
        if state.status(mandate.mandate_id) != "revoked":
            return mandate.roster.principals
        current = state.parent_lineage(mandate.mandate_id)
        while current is not None:
            if state.in_force(current):
                return self.head_mandate(state, current).roster.principals
            current = state.parent_lineage(current)
        return self.charter(state).names

    def raise_ticket(self, state: State, digest: str, now: datetime) -> dict[str, Any]:
        """The ``ticket.raised`` body for ticket ``digest`` (§7.1, F7-1, F7-2)."""
        ticket: Ticket = self.load(digest, Ticket)
        ticket.verify(self.service_key)
        if ticket.ticket_id in state.tickets or digest in state.tickets_by_digest:
            raise AuthorityError(f"ticket {ticket.ticket_id} is raised already")
        head = state.head(ticket.mandate.mandate_id)
        if head is None or head != (ticket.mandate.version, ticket.mandate.digest):
            raise AuthorityError("a ticket names its lineage's version in force")
        if not state.in_force(ticket.mandate.mandate_id):
            raise AuthorityError(
                f"lineage {ticket.mandate.mandate_id} is {state.status(ticket.mandate.mandate_id)}"
            )
        if ticket.raised_at != now:  # and so before its not_after, which follows raised_at
            raise AuthorityError("ticket raised_at disagrees with its record")
        observed = ticket.observed.log
        if (observed.seq, observed.hash) != (state.head_seq, state.head_hash):
            raise AuthorityError(
                f"the ticket observed log record {observed.seq} ({observed.hash[:16]}), not "
                f"record {state.head_seq}, the head its own record follows"
            )
        mandate: Mandate = self.load(ticket.mandate.digest, Mandate)
        for option in ticket.options:
            expected_quorum = quorum_for_resolution(mandate, ticket, option.resolution)
            if option.quorum != expected_quorum:
                raise AuthorityError(
                    f"option {option.resolution.action} has quorum {option.quorum}, not "
                    f"{expected_quorum}"
                )
        if isinstance(ticket.evidence, AmendEvidence):
            proposed: Mandate = self.load(ticket.evidence.proposed.digest, Mandate)
            if (
                proposed.mandate_id != mandate.mandate_id
                or proposed.version != mandate.version + 1
                or proposed.prev != mandate.digest
            ):
                raise AuthorityError(
                    f"proposed mandate {proposed.digest[:16]} is not version {mandate.version + 1} "
                    f"of {mandate.mandate_id}, naming {mandate.digest[:16]}"
                )
            wider = tuple(
                sorted(
                    (DiffItem(p.path, p.message) for p in refines(proposed, mandate)),
                    key=lambda d: (d.path, d.message),
                )
            )
            narrower = tuple(
                sorted(
                    (DiffItem(p.path, p.message) for p in refines(mandate, proposed)),
                    key=lambda d: (d.path, d.message),
                )
            )
            if ticket.evidence.wider != wider or ticket.evidence.narrower != narrower:
                raise AuthorityError(
                    "the amend ticket's diff does not match refines(proposed, current)"
                )
        evidence_digest = fidu1.digest(ticket.evidence.to_json())
        for open_row in state.open_tickets(ticket.mandate.mandate_id):
            other: Ticket = self.load(open_row.digest, Ticket)
            if (
                other.ticket_class == ticket.ticket_class
                and fidu1.digest(other.evidence.to_json()) == evidence_digest
            ):
                raise AuthorityError(
                    f"ticket {open_row.ticket_id} is already open for this evidence (F7-1)"
                )
        if len(state.open_tickets(ticket.mandate.mandate_id)) >= mandate.escalation.max_open:
            raise AuthorityError(
                f"lineage {ticket.mandate.mandate_id} has {mandate.escalation.max_open} open "
                f"tickets, its escalation.max_open bound (F7-2)"
            )
        return {
            "ticket": digest,
            "ticket_id": ticket.ticket_id,
            "mandate": ticket.mandate.digest,
            "mandate_id": ticket.mandate.mandate_id,
            "class": ticket.ticket_class,
        }

    def count_ticket(
        self, state: State, ticket: Ticket, resolution: Resolution
    ) -> tuple[list[str], list[Approval]]:
        """Count approvals for ``resolution`` of ``ticket`` (§7.3, F7-5)."""
        if ticket.option_for(resolution) is None:
            raise AuthorityError(
                f"resolution {resolution.digest[:16]} is no option or narrowing of "
                f"ticket {ticket.ticket_id}"
            )
        counted = self._ticket_votes(state, ticket, "approve", resolution)
        return [d for d, _ in counted], [a for _, a in counted]

    def count_ticket_declines(self, state: State, ticket: Ticket) -> list[str]:
        """Digests of the valid roster declines on ``ticket`` (§7.3, F7-7)."""
        return [d for d, _ in self._ticket_votes(state, ticket, "decline")]

    def _ticket_votes(
        self, state: State, ticket: Ticket, decision: str, resolution: Resolution | None = None
    ) -> list[tuple[str, Approval]]:
        """The ``decision`` approvals of ``ticket`` that count, by digest (§7.3):
        each principal's latest decision recorded before the ticket ended, from
        a principal of its roster and of the charter in force, made under that
        charter with a key not suspended before it was recorded, verifying under
        that key, and recorded by its own deadline and the ticket's. An
        approval approves ``resolution``."""
        current_charter = self.charter(state)
        allowed = set(self._ticket_roster(state, ticket)) & set(current_charter.names)
        counted: list[tuple[str, Approval]] = []
        for principal, row in sorted(state.ticket_decisions(ticket.digest).items()):
            if principal not in allowed or row.decision != decision:
                continue
            approval = self.load(row.digest, Approval)
            if (
                approval.subject.type != "ticket"
                or approval.subject.digest != ticket.digest
                or (resolution is not None and approval.resolution != resolution.digest)
                or approval.decision != decision
                or approval.charter_id != current_charter.charter_id
            ):
                continue
            try:
                signing_charter = self.charter(state, approval.charter_version)
            except AuthorityError:
                continue
            key = signing_charter.principal(principal)
            if key is None or self._suspended_before(state, key.name, key.key, row.seq):
                continue
            try:
                approval.verify(key.public_key)
            except FiduciaryError:
                continue
            recorded_at = row.recorded_at or approval.issued_at
            if recorded_at > ticket.not_after or recorded_at > approval.not_after:
                continue
            counted.append((row.digest, approval))
        return sorted(counted, key=lambda item: item[0])

    def can_decline_ticket(self, state: State, ticket: Ticket) -> bool:
        """Whether declines make every option of ``ticket`` unreachable (F7-7)."""
        current_charter = self.charter(state)
        allowed = set(self._ticket_roster(state, ticket)) & set(current_charter.names)
        remaining = len(allowed) - len(self.count_ticket_declines(state, ticket))
        return all(remaining < opt.quorum for opt in ticket.options)

    def _requeues_so_far(self, state: State, message_id: str) -> int:
        count = 0
        for digest in state.warrants_by_action.get("requeue", ()):
            row = state.warrants[digest]
            if state.execution_status(row.digest) in ("refused", "abandoned"):
                continue
            warrant: Warrant = self.load(row.digest, Warrant)
            if any(m.message_id == message_id for m in warrant.resolution.messages):
                count += 1
        return count

    def rule_matches(
        self,
        state: State,
        mandate: Mandate,
        ticket: Ticket,
        rule: AutoRule,
        resolution: Resolution,
    ) -> bool:
        """Whether autonomous ``rule`` of ``mandate`` resolves ``ticket`` with
        ``resolution`` (§7.4, F7-9 to F7-11)."""
        if rule.ticket_class != ticket.ticket_class or rule.resolution != resolution.action:
            return False
        if resolution.action not in AUTO_ACTIONS:
            return False
        option = ticket.option_for(resolution)
        if option is None or option.resolution != resolution:
            return False
        when = rule.when
        if isinstance(ticket.evidence, RefusedEvidence):
            if bool(when.get("exhaustive", False)) and not ticket.evidence.exhaustive:
                return False
            if "max_dropped" in when and len(ticket.evidence.dropped) > int(when["max_dropped"]):
                return False
            if "causes" in when:
                causes = {item.cause for item in ticket.evidence.dropped}
                if not causes <= set(when["causes"]):
                    return False
            return True
        if isinstance(ticket.evidence, DeadEvidence):
            if "outcome" in when and ticket.evidence.message.outcome not in set(when["outcome"]):
                return False
            if "max_requeues" in when:
                prior = max(
                    ticket.evidence.message.requeues,
                    self._requeues_so_far(state, ticket.evidence.message.message_id),
                )
                limit = int(when["max_requeues"])
                if resolution.action == "requeue":
                    if prior + 1 > limit:
                        return False
                elif prior > limit:
                    return False
            return True
        if isinstance(ticket.evidence, ExtensionEvidence):
            requested = ticket.evidence.quote.requested
            if "max_amount" in when and requested > Decimal(when["max_amount"]):
                return False
            if resolution.action == "grant":
                if mandate.extension is None or mandate.extension.auto is None:
                    return False
                if requested > mandate.extension.auto.max_grant:
                    return False
                spent_auto = state.auto_extension_total(mandate.digest)
                if spent_auto + requested > mandate.extension.auto.max_total:
                    return False
            return True
        # held, undo and amend: no vocabulary (F7-9), so a rule's predicate is
        # empty, and it names only a reducing resolution (§7.2).
        return not when

    def form_warrant(self, state: State, warrant_digest: str, now: datetime) -> dict[str, Any]:
        """The ``warrant.formed`` body for warrant ``warrant_digest`` (§4.6, §7.3, §7.4)."""
        warrant: Warrant = self.load(warrant_digest, Warrant)
        warrant.verify(self.service_key)
        if warrant.warrant_id in state.warrants_by_id or warrant_digest in state.warrants:
            raise AuthorityError(f"warrant {warrant.warrant_id} is formed already")
        ticket_row = state.tickets.get(warrant.ticket.ticket_id)
        if ticket_row is None or ticket_row.digest != warrant.ticket.digest:
            raise AuthorityError("the warrant names no raised ticket")
        if state.ticket_status(ticket_row.ticket_id) != "open":
            raise AuthorityError(
                f"ticket {ticket_row.ticket_id} is "
                f"{state.ticket_status(ticket_row.ticket_id)} already"
            )
        ticket: Ticket = self.load(warrant.ticket.digest, Ticket)
        if now > ticket.not_after:
            raise AuthorityError(
                f"ticket {ticket.ticket_id} expired at {fidu1.format_instant(ticket.not_after)}"
            )
        if warrant.formed_at != now:
            raise AuthorityError("warrant formed_at disagrees with its record")
        if ticket.option_for(warrant.resolution) is None:
            raise AuthorityError("the warrant's resolution is no option or narrowing of the ticket")
        mandate: Mandate = self.load(ticket.mandate.digest, Mandate)
        if warrant.basis.rule is not None:
            if warrant.basis.rule.mandate != mandate.digest:
                raise AuthorityError("the warrant's rule names another mandate")
            rule = next(
                (r for r in mandate.escalation.auto if r.rule_id == warrant.basis.rule.rule_id),
                None,
            )
            if rule is None:
                raise AuthorityError(
                    f"mandate {mandate.digest[:16]} has no auto rule {warrant.basis.rule.rule_id!r}"
                )
            if not self.rule_matches(state, mandate, ticket, rule, warrant.resolution):
                raise AuthorityError(f"auto rule {rule.rule_id!r} does not admit this ticket")
            expected_not_after = warrant_not_after(ticket, warrant.resolution, ())
        else:
            counted, approvals = self.count_ticket(state, ticket, warrant.resolution)
            required = quorum_for_resolution(mandate, ticket, warrant.resolution)
            _enough(
                counted,
                required,
                f"warrant for ticket {ticket.ticket_id} ({warrant.resolution.action})",
            )
            if tuple(counted) != warrant.basis.approvals:
                raise AuthorityError(
                    "the warrant's basis approvals do not match the counted approvals"
                )
            expected_not_after = warrant_not_after(ticket, warrant.resolution, approvals)
        if warrant.not_after != expected_not_after:
            raise AuthorityError(
                f"warrant not_after is {fidu1.format_instant(warrant.not_after)}, not the "
                f"minimum of its bounds {fidu1.format_instant(expected_not_after)} (F4-25)"
            )
        if now >= warrant.not_after:
            raise AuthorityError(
                f"warrant {warrant.warrant_id} expired at {fidu1.format_instant(warrant.not_after)}"
            )
        return {
            "warrant": warrant_digest,
            "warrant_id": warrant.warrant_id,
            "ticket": ticket.digest,
            "ticket_id": ticket.ticket_id,
            "mandate": ticket.mandate.digest,
            "mandate_id": ticket.mandate.mandate_id,
            "class": ticket.ticket_class,
            "action": warrant.resolution.action,
            "resolution": warrant.resolution_digest,
            "basis": warrant.basis.to_json(),
            "amount": (
                None
                if warrant.resolution.amount is None
                else format(warrant.resolution.amount.quantize(Decimal("0.00000001")), "f")
            ),
        }

    def decline_ticket(self, state: State, ticket_digest: str) -> dict[str, Any]:
        """The ``ticket.declined`` body when declines make quorum unreachable (F7-7)."""
        ticket_id = state.tickets_by_digest.get(ticket_digest)
        if ticket_id is None:
            raise AuthorityError(f"no ticket has digest {ticket_digest[:16]}")
        if state.ticket_status(ticket_id) != "open":
            raise AuthorityError(f"ticket {ticket_id} is {state.ticket_status(ticket_id)} already")
        ticket: Ticket = self.load(ticket_digest, Ticket)
        if not self.can_decline_ticket(state, ticket):
            raise AuthorityError(f"ticket {ticket_id} still has a reachable option")
        return {
            "ticket": ticket.digest,
            "ticket_id": ticket.ticket_id,
            "mandate": ticket.mandate.digest,
            "mandate_id": ticket.mandate.mandate_id,
            "class": ticket.ticket_class,
            "declines": self.count_ticket_declines(state, ticket),
        }

    def lapse_ticket(self, state: State, ticket_digest: str, now: datetime) -> dict[str, Any]:
        """The ``ticket.lapsed`` body for an expired ticket (F1-6, F7-7) or one
        whose warrant was refused after a lost quote (F7-14)."""
        ticket_id = state.tickets_by_digest.get(ticket_digest)
        if ticket_id is None:
            raise AuthorityError(f"no ticket has digest {ticket_digest[:16]}")
        status = state.ticket_status(ticket_id)
        ticket: Ticket = self.load(ticket_digest, Ticket)
        if status == "open":
            if now < ticket.not_after:
                raise AuthorityError(
                    f"ticket {ticket_id} runs until {fidu1.format_instant(ticket.not_after)}"
                )
        elif status == "warranted":
            # Its warrant ran nothing, and never will: the execution was
            # refused or abandoned (F7-14), or the warrant expired without one.
            (warrant_digest,) = [
                detail
                for ev, detail, _ in state.ticket_events.get(ticket_id, ())
                if ev == "warranted"
            ]
            execution = state.execution_status(warrant_digest)
            if execution is None:
                warrant: Warrant = self.load(warrant_digest, Warrant)
                if now < warrant.not_after:
                    raise AuthorityError(
                        f"ticket {ticket_id}'s warrant runs until "
                        f"{fidu1.format_instant(warrant.not_after)}"
                    )
            elif execution not in ("refused", "abandoned"):
                raise AuthorityError(f"ticket {ticket_id}'s warrant execution is {execution}")
        else:
            raise AuthorityError(f"ticket {ticket_id} is {status} already")
        return {
            "ticket": ticket.digest,
            "ticket_id": ticket.ticket_id,
            "mandate": ticket.mandate.digest,
            "mandate_id": ticket.mandate.mandate_id,
            "class": ticket.ticket_class,
        }

    def execution_intent(self, state: State, warrant_digest: str, now: datetime) -> dict[str, Any]:
        """The ``execution.intent`` body of ``warrant_digest``: its single intent
        (F7-12), written before its ``not_after``, naming its steps in order
        with the preconditions its ticket observed (§7.5)."""
        if warrant_digest not in state.warrants:
            raise AuthorityError(f"no formed warrant has digest {warrant_digest[:16]}")
        if state.execution_status(warrant_digest) is not None:
            raise AuthorityError(
                f"warrant {warrant_digest[:16]} already has an execution.intent (F7-12)"
            )
        warrant: Warrant = self.load(warrant_digest, Warrant)
        if now >= warrant.not_after:
            raise AuthorityError(
                f"warrant {warrant.warrant_id} expired at {fidu1.format_instant(warrant.not_after)}"
            )
        ticket: Ticket = self.load(warrant.ticket.digest, Ticket)
        if state.ticket_status(ticket.ticket_id) != "warranted":
            raise AuthorityError(
                f"ticket {ticket.ticket_id} is {state.ticket_status(ticket.ticket_id)}"
            )
        return {
            "warrant": warrant.digest,
            "warrant_id": warrant.warrant_id,
            "ticket": ticket.digest,
            "ticket_id": ticket.ticket_id,
            "mandate": ticket.mandate.digest,
            "mandate_id": ticket.mandate.mandate_id,
            "action": warrant.resolution.action,
            "steps": execution_steps(warrant, ticket),
        }

    def execution_outcome(self, state: State, kind: str, body: Mapping[str, Any]) -> None:
        """Check an outcome record against the open intent it answers: the same
        warrant, ticket, mandate and action, its intent's sequence, and results
        for the steps the intent named, in order. What each step reached is the
        systems' to show, not the rules'."""
        warrant_digest = str(body.get("warrant"))
        events = state.executions.get(warrant_digest, [])
        if [event for event, _, _ in events] != ["intent"]:
            status = state.execution_status(warrant_digest)
            raise AuthorityError(
                f"warrant {warrant_digest[:16]} has execution status {status!r}, not 'intent'"
            )
        _, intent, intent_seq = events[0]
        fixed = ("warrant", "warrant_id", "ticket", "ticket_id", "mandate", "mandate_id", "action")
        if (
            any(body.get(name) != intent[name] for name in fixed)
            or body.get("intent") != intent_seq
        ):
            raise AuthorityError(f"the {kind} record does not answer intent {intent_seq}")
        results = body.get("results")
        steps = intent["steps"]
        if not isinstance(results, list) or len(results) > len(steps):
            raise AuthorityError(f"the {kind} record names more results than its steps")
        for result, step in zip(results, steps, strict=False):
            if (
                not isinstance(result, Mapping)
                or result.get("step") != step["step"]
                or result.get("kind") != step["kind"]
            ):
                raise AuthorityError(f"the {kind} record's results do not follow its steps")
        if kind == "execution.applied" and len(results) != len(steps):
            raise AuthorityError("execution.applied names a result for every step")

    # -- bridge sessions (§8.1, §4.7) --------------------------------------------

    def open_session(
        self,
        state: State,
        session_id: str,
        mandate_id: str,
        tenant: str | None,
        trajectory: str,
        now: datetime,
    ) -> dict[str, Any]:
        """The ``session.opened`` body: a session of the lineage's version in
        force, bound and within its term, for a tenant its data admits,
        billing its subject scope."""
        try:
            uuid.UUID(session_id)
        except ValueError as exc:
            raise AuthorityError(f"session id {session_id!r} is no uuid") from exc
        if state.session_status(session_id) is not None:
            raise AuthorityError(f"session {session_id} is opened already")
        if state.status(mandate_id) != "bound":
            raise AuthorityError(
                f"lineage {mandate_id} is {state.status(mandate_id) or 'not issued'}: a session "
                f"runs only under a bound version in force"
            )
        head = state.head(mandate_id)
        bound = state.bound.get(mandate_id)
        assert head is not None and bound is not None
        if bound["mandate"] != head[1]:
            raise AuthorityError(
                f"lineage {mandate_id}'s version {head[0]} is issued and not yet bound"
            )
        mandate = self.head_mandate(state, mandate_id)
        if not mandate.not_before <= now < mandate.not_after:
            raise AuthorityError(
                f"mandate {mandate_id} version {mandate.version} is outside its term at "
                f"{fidu1.format_instant(now)}"
            )
        for value, what in ((tenant, "a tenant"), (trajectory, "a trajectory")):
            if value is not None and not fidu1.TEXT.fullmatch(value):
                raise AuthorityError(f"{value!r} is not {what}: 1 to 512 printable characters")
        allowed = mandate.data.tenants
        if tenant is not None and allowed is not None and tenant not in allowed:
            raise AuthorityError(f"tenant {tenant!r} is not one mandate {mandate_id} admits")
        return {
            "session": session_id,
            "mandate": mandate.digest,
            "mandate_id": mandate_id,
            "version": mandate.version,
            "tenant": tenant,
            "trajectory": trajectory,
            "scope": mandate.subject.scope,
        }

    def _open_session(self, state: State, session_id: str) -> dict[str, Any]:
        if state.session_status(session_id) != "open":
            raise AuthorityError(
                f"session {session_id} is {state.session_status(session_id) or 'not opened'}"
            )
        return state.sessions[session_id][0][1]

    def refuse_call(
        self, state: State, session_id: str, tool: str | None, code: str
    ) -> dict[str, Any]:
        """The ``session.refused`` body: a call refused before anything was
        built or metered. It names a tool only when the tool is one the
        session's version pins, or a built-in: any other name is the agent's
        text, which no record holds (F4-27)."""
        opened = self._open_session(state, session_id)
        if code not in REFUSAL_CODES:
            raise AuthorityError(f"{code!r} is no refusal code")
        if tool is not None and tool not in BUILTIN_TOOLS:
            mandate: Mandate = self.load(str(opened["mandate"]), Mandate)
            if tool not in {pin.name for pin in mandate.tools.bindings}:
                raise AuthorityError(f"tool {tool!r} is pinned by no version this session runs")
        return {
            "session": session_id,
            "mandate_id": opened["mandate_id"],
            "tool": tool,
            "code": code,
        }

    def close_session(self, state: State, session_id: str, scopes: Sequence[str]) -> dict[str, Any]:
        """The ``session.closed`` body: every scope the session billed, in
        order, each its subject's or a satellite's (F8-8)."""
        opened = self._open_session(state, session_id)
        subject = str(opened["scope"])
        listed = list(scopes)
        if not listed or listed[0] != subject:
            raise AuthorityError(f"session {session_id} billed {subject!r} first")
        strays = [scope for scope in listed if not admits_scope(subject, scope)]
        if strays or len(set(listed)) != len(listed):
            raise AuthorityError(
                f"session {session_id} names scope(s) {', '.join(strays) or 'twice'}, outside "
                f"its subject {subject!r} and its satellites (F8-8)"
            )
        return {"session": session_id, "mandate_id": opened["mandate_id"], "scopes": listed}

    def _amend_warrant_approvals(
        self, state: State, mandate_id: str, digest: str, now: datetime
    ) -> list[str]:
        """Approvals from a formed, unexpired ``amend`` warrant for ``digest``."""
        for row in state.warrants.values():
            if row.mandate_id != mandate_id or row.action != "amend":
                continue
            if state.execution_status(row.digest) in ("refused", "abandoned"):
                continue
            warrant: Warrant = self.load(row.digest, Warrant)
            if (
                warrant.resolution.mandate == digest
                and warrant.basis.approvals
                and now < warrant.not_after
            ):
                return list(warrant.basis.approvals)
        return []

    def _children(self, state: State, parent_id: str) -> set[str]:
        children = set()
        for mandate_id, versions in state.lineages.items():
            first: Mandate = self.load(versions[0][1], Mandate)
            if first.parent is not None and first.parent.mandate_id == parent_id:
                children.add(mandate_id)
        return children

    @staticmethod
    def _signed_by_principal(document: Mandate, charter: Charter) -> None:
        if document.signature is None:
            raise AuthorityError("the mandate is not signed")
        for principal in charter.principals:
            if principal.public_key.key_id == document.signature.key_id:
                document.verify(principal.public_key)
                return
        raise AuthorityError("a mandate issued by quorum is signed by a principal of the charter")


def _enough(counted: Sequence[str], quorum: int, what: str) -> None:
    if len(counted) < quorum:
        raise AuthorityError(f"{what} needs {quorum} approvals; {len(counted)} count")


def _why(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    text = f"{code}: {exc}" if isinstance(exc, (MalformedError, SignatureError)) else str(exc)
    return text[:512] or type(exc).__name__


class Authority:
    """Takes the decisions, and records them in the log.

    :param pinned: Charter version 1's digest, as configuration pins it.
    """

    def __init__(
        self,
        log: Log,
        *,
        pinned: str,
        checkpoint: Callable[[str], None] | None = None,
    ) -> None:
        self._log = log
        self._decide = Decide(log.store, pinned, log.signer.public_key())
        self._checkpoint = checkpoint or (lambda _: None)

    @property
    def log(self) -> Log:
        return self._log

    @property
    def decide(self) -> Decide:
        return self._decide

    def propose(self, document: Document) -> str:
        """Store a proposed charter or mandate (or any document) for approvals
        to name. Proposing records nothing: only adoption and issuance do."""
        if document.signature is None:
            raise AuthorityError(f"a proposed {document.TYPE} is signed by its author")
        return self._log.put(document)

    def propose_resolution(self, resolution: Resolution) -> str:
        """Store a narrowed :class:`Resolution` (F4-20) so approvals may name its digest."""
        data = fidu1.canonical(resolution.to_json())
        digest = resolution.digest
        self._log.store.put_document(digest, "resolution", data)
        return digest

    def process_intake(self) -> list[Record]:
        """Judge every submission no record answers yet, oldest first.

        When an approval of an open ticket completes a warrant's quorum,
        ``warrant.formed`` is written in the same log transaction (F7-6); when a
        decline makes every option unreachable, ``ticket.declined`` is written
        in the same log transaction (F7-7).
        """
        records: list[Record] = []
        # Read first, outside the writer's lock: an idle turn takes no lock at
        # all, and a busy one locks once per submission still unanswered.
        answered = self._log.state().processed
        waiting = [intake for intake in self._log.store.intake_ids() if intake not in answered]
        pending = self._log.store.submissions(waiting) if waiting else []
        for intake, data in pending:
            with self._log.store.transaction():
                state = self._log.state()
                if intake in state.processed:
                    continue
                now = self._log.now()
                kind, body, approval = self._decide.approval(state, intake, data, now)
                entries: list[tuple[str, dict[str, Any]]] = [(kind, body)]
                docs: list[Document] = [approval] if approval is not None else []
                if approval is not None and approval.subject.type == "ticket":
                    self._log.put(approval)  # counted below, before its record is written
                    followup, followup_doc = self._ticket_followup(
                        state, body, approval, now, self._log.signer
                    )
                    if followup is not None:
                        entries.append(followup)
                    if followup_doc is not None:
                        docs.append(followup_doc)
                written = self._log.append_all(entries, documents=docs, issued_at=now)
            # The checkpoints follow the commit: what a crash there leaves is written.
            records.extend(written)
            self._checkpoint("recorded")
            if any(r.kind == "warrant.formed" for r in written):
                self._checkpoint("formed")
        return records

    def _ticket_followup(
        self,
        state: State,
        approval_body: dict[str, Any],
        approval: Approval,
        now: datetime,
        signer: Signer,
    ) -> tuple[tuple[str, dict[str, Any]] | None, Document | None]:
        ticket_id = state.tickets_by_digest.get(approval.subject.digest)
        if ticket_id is None or state.ticket_status(ticket_id) != "open":
            return None, None
        next_seq = state.head_seq + 1
        # Counted as recorded: the state after the approval's own record.
        state = state.after("approval.recorded", approval_body, issued_at=now)
        ticket: Ticket = self._decide.load(approval.subject.digest, Ticket)
        if approval.decision == "approve":
            assert approval.resolution is not None
            resolution = self._decide.resolution_for_ticket(ticket, approval.resolution)
            mandate: Mandate = self._decide.load(ticket.mandate.digest, Mandate)
            counted, counted_approvals = self._decide.count_ticket(state, ticket, resolution)
            required = quorum_for_resolution(mandate, ticket, resolution)
            if len(counted) >= required:
                not_after = warrant_not_after(ticket, resolution, counted_approvals)
                if now < not_after:
                    warrant_id = str(
                        uuid.uuid5(
                            uuid.NAMESPACE_OID,
                            f"fidu1:warrant:{ticket.digest}:{resolution.digest}:{next_seq}",
                        )
                    )
                    warrant = Warrant(
                        warrant_id=warrant_id,
                        ticket=TicketRef(ticket.ticket_id, ticket.digest),
                        resolution=resolution,
                        resolution_digest=resolution.digest,
                        basis=WarrantBasis(approvals=tuple(counted)),
                        formed_at=now,
                        not_after=not_after,
                    ).signed(signer)
                    self._log.put(warrant)
                    warrant_body = self._decide.form_warrant(state, warrant.digest, now)
                    return ("warrant.formed", warrant_body), warrant
        elif self._decide.can_decline_ticket(state, ticket):
            decline_body = self._decide.decline_ticket(state, ticket.digest)
            return ("ticket.declined", decline_body), None
        return None, None

    def adopt_charter(self, digest: str) -> Record:
        self.process_intake()
        body = self._decide.adopt(self._log.state(), digest)
        return self._log.append("charter.adopted", body)

    def suspend(self, principal: str) -> Record:
        self.process_intake()
        body = self._decide.suspend(self._log.state(), principal)
        return self._log.append("principal.suspended", body)

    def issue_mandate(self, digest: str) -> Record:
        """Issue the version ``digest``: a lineage's first, or an amendment of
        one with no binding and no descendant. Any other amendment rebinds or
        rebases what hangs from it, which :meth:`Binder.issue` does."""
        self.process_intake()
        now = self._log.now()
        state = self._log.state()
        body = self._decide.issue(state, digest, now)
        mandate_id = str(body["mandate_id"])
        if mandate_id in state.lineages and (
            state.status(mandate_id) == "bound" or state.descendants(mandate_id)
        ):
            raise AuthorityError(
                f"lineage {mandate_id} is bound or has descendants: amend it through the binder "
                f"(fiduciary mandate amend), which rebinds and rebases them"
            )
        return self._log.append("mandate.issued", body, issued_at=now)


def replay(
    records: Sequence[Record], store: Store, *, pinned: str, service_key: Verifier
) -> list[str]:
    """Take every recorded decision again, from the state before its record,
    and name each record the rules would not have written as it is."""
    return [
        problem
        for _, _, problem in replay_each(records, store, pinned=pinned, service_key=service_key)
        if problem is not None
    ]


def replay_each(
    records: Sequence[Record | Withheld], store: Store, *, pinned: str, service_key: Verifier
) -> Iterator[tuple[Record, State, str | None]]:
    """Each record, the state before it, and why its rules would not have
    written it as it is (``None`` when they would). A withheld record is
    folded past, not replayed."""
    decide = Decide(store, pinned, service_key)
    intake = dict(store.intake())
    state = State()
    for record in records:
        if isinstance(record, Withheld):
            state.apply(record)
            continue
        problem = None
        try:
            expected = _expected(decide, state, record, intake)
        except FiduciaryError as exc:
            problem = f"record {record.seq} ({record.kind}): its rules refuse it now: {exc}"
        else:
            if expected is not None and expected != record.content:
                problem = f"record {record.seq} ({record.kind}): its rules write another body"
        yield record, state, problem
        state.apply(record)


def _expected(
    decide: Decide, state: State, record: Record, intake: dict[int, bytes]
) -> dict[str, Any] | None:
    kind, body = record.kind, record.content
    if kind in ("approval.recorded", "approval.refused"):
        data = intake.get(int(body["intake"]))
        if data is None:
            raise AuthorityError(f"intake {body['intake']} is not in the store")
        found_kind, found, _ = decide.approval(state, int(body["intake"]), data, record.issued_at)
        if found_kind != kind:
            raise AuthorityError(f"the submission is {found_kind.split('.')[1]} now")
        return found
    if kind == "charter.adopted":
        return decide.adopt(state, str(body["charter"]))
    if kind == "principal.suspended":
        return decide.suspend(state, str(body["principal"]))
    if kind == "mandate.issued":
        return decide.issue(state, str(body["mandate"]), record.issued_at)
    if kind == "mandate.bound":
        decide.bindable(state, str(body["mandate"]))
        return None  # what it compiled to is the deployment's: verify compiles it with one
    if kind in ("mandate.rebased", "mandate.suspended"):
        cause = str(body.get("parent") or body.get("cause"))
        found_kind, found = decide.rebase(state, str(body["mandate_id"]), cause)
        if found_kind != kind:
            raise AuthorityError(f"the child is {found_kind.split('.')[1]} by that version")
        return _with_trips(found, body)
    if kind in ("mandate.revoked", "mandate.expired"):
        if body.get("cascade"):
            found = decide.cascade(state, str(body["mandate_id"]), str(body["cascade"]), kind)
        elif kind == "mandate.revoked":
            found = decide.revoke(state, str(body["mandate"]))
        else:
            found = decide.expire(state, str(body["mandate_id"]), record.issued_at)
        return _with_trips(found, body)
    if kind == "ticket.raised":
        return decide.raise_ticket(state, str(body["ticket"]), record.issued_at)
    if kind == "warrant.formed":
        return decide.form_warrant(state, str(body["warrant"]), record.issued_at)
    if kind == "ticket.declined":
        return decide.decline_ticket(state, str(body["ticket"]))
    if kind == "ticket.lapsed":
        return decide.lapse_ticket(state, str(body["ticket"]), record.issued_at)
    if kind == "execution.intent":
        return decide.execution_intent(state, str(body["warrant"]), record.issued_at)
    if kind in ("execution.applied", "execution.refused", "execution.abandoned"):
        decide.execution_outcome(state, kind, body)
        return None
    if kind == "session.opened":
        return decide.open_session(
            state,
            str(body["session"]),
            str(body["mandate_id"]),
            None if body.get("tenant") is None else str(body["tenant"]),
            str(body["trajectory"]),
            record.issued_at,
        )
    if kind == "session.refused":
        tool = body.get("tool")
        return decide.refuse_call(
            state, str(body["session"]), None if tool is None else str(tool), str(body["code"])
        )
    if kind == "session.closed":
        return decide.close_session(state, str(body["session"]), list(body["scopes"]))
    raise AuthorityError(f"no rule writes a {kind} record")


def _with_trips(expected: dict[str, Any], body: Mapping[str, Any]) -> dict[str, Any]:
    """``expected``, with the AgentGov trips the record names: what the ledger
    did is the ledger's to show, not the rules'."""
    return {**expected, "trips": body["trips"]} if "trips" in body else expected

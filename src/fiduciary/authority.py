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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agentgov.receipts.signing import Verifier

from fiduciary import fidu1
from fiduciary.documents import Approval, Charter, Document, Mandate, Record, suspension_digest
from fiduciary.exceptions import AuthorityError, FiduciaryError, MalformedError, SignatureError
from fiduciary.log import Log
from fiduciary.refines import delegation_problems
from fiduciary.store import State, Store

__all__ = ["Authority", "Decide", "replay"]


def _load(store: Store, digest: str, cls: type[Document]) -> Any:
    found = store.document(digest)
    if found is None:
        raise AuthorityError(f"the store holds no document {digest[:16]}")
    kind, data = found
    if kind != cls.TYPE:
        raise AuthorityError(f"document {digest[:16]} is a {kind}, not a {cls.TYPE}")
    return cls.loads(data)


@dataclass(frozen=True, slots=True)
class Decide:
    """The decisions, as functions of a state, a store and an instant."""

    store: Store
    pinned: str
    """The digest of charter version 1, as configuration pins it."""
    service_key: Verifier
    """The key derived mandates are signed with."""

    # -- charters ------------------------------------------------------------------

    def charter(self, state: State, version: int | None = None) -> Charter:
        """An adopted charter: the one in force, or ``version``."""
        if not state.charters:
            raise AuthorityError("no charter is adopted yet")
        for found, digest, _ in reversed(state.charters):
            if version is None or found == version:
                charter: Charter = _load(self.store, digest, Charter)
                return charter
        raise AuthorityError(f"charter version {version} was never adopted")

    def _signing_charter(self, state: State, charter_id: str, version: int) -> Charter:
        """The charter an approval names: the one in force, or, before any is,
        the pinned version 1."""
        if state.charter is None:
            pinned: Charter = _load(self.store, self.pinned, Charter)
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
            self._subject_known(state, approval)
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

    def _subject_known(self, state: State, approval: Approval) -> None:
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
            raise AuthorityError(f"no ticket is open with digest {subject.digest[:16]}")

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
            approval: Approval = _load(self.store, row.digest, Approval)
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
        charter: Charter = _load(self.store, digest, Charter)
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
        mandate: Mandate = _load(self.store, digest, Mandate)
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
                previous: Mandate = _load(self.store, head[1], Mandate)
                approvers, quorum, roster = (
                    charter,
                    previous.roster.quorum("amend"),
                    previous.roster.principals,
                )
            self._signed_by_principal(mandate, charter)
            counted = self.count(state, "mandate", digest, approvers, roster)
            _enough(counted, quorum, f"mandate {mandate.mandate_id} version {mandate.version}")
        else:
            parent_head = state.head(mandate.parent.mandate_id)
            if parent_head is None or parent_head[1] != mandate.parent.digest:
                raise AuthorityError("a child names its parent's version in force")
            if not state.in_force(mandate.parent.mandate_id):
                raise AuthorityError(
                    f"its parent lineage is {state.status(mandate.parent.mandate_id)}"
                )
            parent: Mandate = _load(self.store, mandate.parent.digest, Mandate)
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
        found: Mandate = _load(self.store, head[1], Mandate)
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
        mandate: Mandate = _load(self.store, digest, Mandate)
        return mandate

    def rebase(self, state: State, mandate_id: str, parent: str) -> tuple[str, dict[str, Any]]:
        """What a parent's new version ``parent`` does to its child lineage
        (F5-7): ``mandate.rebased`` when the child's version in force still
        refines it, ``mandate.suspended`` with every reason when not."""
        child = self.head_mandate(state, mandate_id)
        new: Mandate = _load(self.store, parent, Mandate)
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

    def _children(self, state: State, parent_id: str) -> set[str]:
        children = set()
        for mandate_id, versions in state.lineages.items():
            first: Mandate = _load(self.store, versions[0][1], Mandate)
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

    def __init__(self, log: Log, *, pinned: str) -> None:
        self._log = log
        self._decide = Decide(log.store, pinned, log.signer.public_key())

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

    def process_intake(self) -> list[Record]:
        """Judge every submission no record answers yet, oldest first."""
        records = []
        for intake, data in self._log.store.intake():
            state = self._log.state()
            if intake in state.processed:
                continue
            now = self._log.now()
            kind, body, approval = self._decide.approval(state, intake, data, now)
            records.append(
                self._log.append(
                    kind, body, documents=[approval] if approval else [], issued_at=now
                )
            )
        return records

    def adopt_charter(self, digest: str) -> Record:
        self.process_intake()
        body = self._decide.adopt(self._log.state(), digest)
        return self._log.append("charter.adopted", body)

    def suspend(self, principal: str) -> Record:
        self.process_intake()
        body = self._decide.suspend(self._log.state(), principal)
        return self._log.append("principal.suspended", body)

    def issue_mandate(self, digest: str) -> Record:
        self.process_intake()
        now = self._log.now()
        body = self._decide.issue(self._log.state(), digest, now)
        return self._log.append("mandate.issued", body, issued_at=now)


def replay(
    records: Sequence[Record], store: Store, *, pinned: str, service_key: Verifier
) -> list[str]:
    """Take every recorded decision again, from the state before its record,
    and name each record the rules would not have written as it is."""
    decide = Decide(store, pinned, service_key)
    intake = dict(store.intake())
    problems: list[str] = []
    state = State()
    for record in records:
        try:
            expected = _expected(decide, state, record, intake)
        except FiduciaryError as exc:
            problems.append(f"record {record.seq} ({record.kind}): its rules refuse it now: {exc}")
        else:
            if expected is not None and expected != record.content:
                problems.append(
                    f"record {record.seq} ({record.kind}): its rules write another body"
                )
        state.apply(record)
    return problems


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
    return None  # later phases' kinds: replayed by the phase that writes them


def _with_trips(expected: dict[str, Any], body: Mapping[str, Any]) -> dict[str, Any]:
    """``expected``, with the AgentGov trips the record names: what the ledger
    did is the ledger's to show, not the rules'."""
    return {**expected, "trips": body["trips"]} if "trips" in body else expected

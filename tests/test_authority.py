"""Who decides (:mod:`fiduciary.authority`), on SQLite and PostgreSQL.

Approvals recorded or refused; quorums counted by principal, each principal's
latest decision; charters adopted in order; keys suspended; mandates issued
by quorum, amended, derived; and every decision replayed by ``verify``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from fiduciary.authority import Authority
from fiduciary.documents import Approval, Charter, Mandate, suspension_digest
from fiduciary.exceptions import AuthorityError
from fiduciary.verify import verify_store
from tests import examples as e
from tests.conftest import Clock, adopt, approve, issue, submit


def _refusals(authority: Authority) -> list[str]:
    return [r.content["why"] for r in authority.log.records() if r.kind == "approval.refused"]


def _verify(authority: Authority) -> list[str]:
    store = authority.log.store
    return list(
        verify_store(store, service_key=e.SERVICE.public_key(), pinned=e.charter().digest).problems
    )


def _approval(principal: str, *, signer: str | None = None, **changes: Any) -> Approval:
    changes.setdefault("approval_id", str(uuid.uuid4()))
    raw = e.approval_json(principal, **changes)
    return Approval.from_json(raw, signed=False).signed(e.signer(signer or principal))


# -- the charter ------------------------------------------------------------------


def test_the_pinned_charter_is_adopted_by_its_own_quorum(authority: Authority) -> None:
    charter = e.charter()
    authority.propose(charter)
    approve(authority.log.store, ("cfo", "head-ops"), "charter", charter.digest)
    with pytest.raises(AuthorityError, match="needs 3 approvals; 2 count"):
        authority.adopt_charter(charter.digest)
    approve(authority.log.store, ("security",), "charter", charter.digest)
    record = authority.adopt_charter(charter.digest)
    assert record.content["version"] == 1 and len(record.content["approvals"]) == 3
    assert authority.log.state().charter == (1, charter.digest)
    assert _verify(authority) == []


def test_only_the_pinned_charter_is_adopted_first(authority: Authority) -> None:
    other = Charter.from_json(
        e.charter_json(deployment={"ledger": e.LEDGER, "label": "elsewhere"}), signed=False
    ).signed(e.signer("cfo"))
    authority.propose(other)
    with pytest.raises(AuthorityError, match="version 1, the one configuration pins"):
        authority.adopt_charter(other.digest)


def test_a_proposal_is_signed(authority: Authority) -> None:
    with pytest.raises(AuthorityError, match="signed by its author"):
        authority.propose(e.charter().unsigned())


def _charter_v2(**changes: Any) -> Charter:
    raw = e.charter_json(
        version=2, prev=e.charter().digest, issued_at="2026-10-18T11:00:00.000000Z", **changes
    )
    return Charter.from_json(raw, signed=False).signed(e.signer("head-ops"))


def test_the_next_charter_is_adopted_by_the_last_ones_quorum(authority: Authority) -> None:
    adopt(authority)
    newcomer = e.signer("newcomer")
    principals = sorted(
        [
            *e.charter_json()["principals"],
            {"name": "newcomer", "key": newcomer.public_key().spec()},
        ],
        key=lambda p: p["name"],
    )
    v2 = _charter_v2(principals=principals)
    authority.propose(v2)
    approve(authority.log.store, e.PRINCIPALS, "charter", v2.digest, charter_version=1)
    authority.adopt_charter(v2.digest)
    assert authority.log.state().charter == (2, v2.digest)
    # an approval now names version 2, and the newcomer counts
    stale = _approval("cfo", subject="charter", digest=v2.digest)  # names version 1
    submit(authority.log.store, stale)
    authority.process_intake()
    assert any("version 2 is in force" in why for why in _refusals(authority))
    assert _verify(authority) == []


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"version": 3, "prev": "a" * 64}, "is not version 2"),
        ({"deployment": {"ledger": "b" * 64, "label": "acme-prod"}}, "binds one ledger"),
    ],
)
def test_a_charter_out_of_order_is_refused(
    authority: Authority, changes: dict[str, Any], match: str
) -> None:
    adopt(authority)
    raw = e.charter_json(**{"version": 2, "prev": e.charter().digest, **changes})
    v2 = Charter.from_json(raw, signed=False).signed(e.signer("cfo"))
    authority.propose(v2)
    approve(authority.log.store, e.PRINCIPALS, "charter", v2.digest)
    with pytest.raises(AuthorityError, match=match):
        authority.adopt_charter(v2.digest)


def test_a_charter_signed_by_a_stranger_is_refused(authority: Authority) -> None:
    adopt(authority)
    v2 = Charter.from_json(e.charter_json(version=2, prev=e.charter().digest), signed=False).signed(
        e.signer("mallory")
    )
    authority.propose(v2)
    with pytest.raises(AuthorityError, match="signed by one of its own principals"):
        authority.adopt_charter(v2.digest)


def test_no_charter_means_no_mandate(authority: Authority) -> None:
    mandate = e.mandate()
    authority.propose(mandate)
    with pytest.raises(AuthorityError, match="no charter is adopted"):
        authority.issue_mandate(mandate.digest)


# -- approvals ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("make", "why"),
    [
        (lambda: b"not json", "json"),
        (lambda: _approval("cfo", signer="mallory").canonical(), "key"),
        (lambda: _approval("mallory").canonical(), "no principal"),
        (
            lambda: _approval(
                "cfo", charter={"charter_id": e.CHARTER_ID, "version": 2}
            ).canonical(),
            "names charter version 2",
        ),
        (lambda: _approval("cfo", digest="d" * 64).canonical(), "propose it first"),
        (
            lambda: _approval(
                "cfo", subject="ticket", digest="d" * 64, resolution="e" * 64
            ).canonical(),
            "no ticket",
        ),
        (
            lambda: _approval("cfo", subject="revocation", digest="d" * 64).canonical(),
            "no issued mandate",
        ),
        (lambda: _approval("cfo", not_after="2026-10-18T10:06:00.000000Z").canonical(), "expired"),
    ],
)
def test_an_approval_that_does_not_hold_is_refused_and_never_counts(
    authority: Authority, make: Any, why: str
) -> None:
    adopt(authority)
    authority.propose(e.mandate())
    submit_bytes = make()
    authority.log.store.submit(submit_bytes)
    authority.process_intake()
    assert why in _refusals(authority)[-1]
    assert _verify(authority) == []


def test_before_any_charter_an_approval_names_the_pinned_one(authority: Authority) -> None:
    authority.propose(e.charter())
    submit(
        authority.log.store,
        _approval(
            "cfo",
            subject="charter",
            digest=e.charter().digest,
            charter={"charter_id": e.CHARTER_ID, "version": 2},
        ),
    )
    authority.process_intake()
    assert "not the pinned version 1" in _refusals(authority)[-1]


def test_a_principal_counts_once_and_by_their_latest_decision(
    authority: Authority, clock: Clock
) -> None:
    adopt(authority)
    mandate = e.mandate()
    authority.propose(mandate)
    approve(authority.log.store, ("cfo", "cfo", "cfo"), "mandate", mandate.digest)
    with pytest.raises(AuthorityError, match="2 approvals; 1 count"):
        authority.issue_mandate(mandate.digest)
    approve(authority.log.store, ("head-ops",), "mandate", mandate.digest)
    submit(authority.log.store, _approval("head-ops", digest=mandate.digest, decision="decline"))
    with pytest.raises(AuthorityError, match="2 approvals; 1 count"):
        authority.issue_mandate(mandate.digest)
    submit(authority.log.store, _approval("head-ops", digest=mandate.digest))
    authority.issue_mandate(mandate.digest)
    assert _verify(authority) == []


def test_a_principal_outside_the_roster_does_not_count(authority: Authority) -> None:
    adopt(authority)
    raw = e.mandate_json()
    raw["roster"]["principals"] = ["cfo", "head-ops"]
    raw["roster"]["quorums"]["reset_safety"] = 2
    mandate = Mandate.from_json(raw, signed=False).signed(e.signer("cfo"))
    authority.propose(mandate)
    approve(authority.log.store, ("cfo", "security"), "mandate", mandate.digest)
    authority.issue_mandate(mandate.digest)  # a root counts the charter's principals
    amended = Mandate.from_json({**raw, "version": 2, "prev": mandate.digest}, signed=False).signed(
        e.signer("cfo")
    )
    authority.propose(amended)
    approve(authority.log.store, ("cfo", "security"), "mandate", amended.digest)
    with pytest.raises(AuthorityError, match="2 approvals; 1 count"):
        authority.issue_mandate(amended.digest)  # an amendment counts the last version's roster


# -- suspension -------------------------------------------------------------------


def test_a_suspended_key_counts_no_more(authority: Authority) -> None:
    adopt(authority)
    mandate = e.mandate()
    authority.propose(mandate)
    approve(
        authority.log.store, ("cfo",), "mandate", mandate.digest
    )  # recorded before the suspension
    with pytest.raises(AuthorityError, match="1 approvals; 0 count"):
        authority.suspend("security")
    subject = suspension_digest(e.CHARTER_ID, "security", e.key("security"))
    approve(authority.log.store, ("head-ops",), "suspension", subject)
    authority.suspend("security")
    with pytest.raises(AuthorityError, match="suspended already"):
        authority.suspend("security")
    with pytest.raises(AuthorityError, match="no principal"):
        authority.suspend("nobody")
    approve(authority.log.store, ("security",), "mandate", mandate.digest)
    authority.process_intake()
    assert "security's key is suspended" in _refusals(authority)[-1]
    with pytest.raises(AuthorityError, match="2 approvals; 1 count"):
        authority.issue_mandate(mandate.digest)
    approve(authority.log.store, ("head-ops",), "mandate", mandate.digest)
    authority.issue_mandate(mandate.digest)
    assert _verify(authority) == []


def test_an_approval_by_a_key_suspended_after_it_was_recorded_still_counts(
    authority: Authority,
) -> None:
    adopt(authority)
    mandate = e.mandate()
    authority.propose(mandate)
    approve(authority.log.store, ("cfo", "security"), "mandate", mandate.digest)
    authority.process_intake()
    approve(
        authority.log.store,
        ("head-ops",),
        "suspension",
        suspension_digest(e.CHARTER_ID, "security", e.key("security")),
    )
    authority.suspend("security")
    record = authority.issue_mandate(mandate.digest)
    assert len(record.content["approvals"]) == 2


# -- mandates ---------------------------------------------------------------------


def test_a_root_is_issued_amended_and_its_child_derived(authority: Authority) -> None:
    adopt(authority)
    issue(authority)
    amended = Mandate.from_json(
        {**e.mandate_json(), "version": 2, "prev": e.mandate().digest}, signed=False
    ).signed(e.signer("cfo"))
    issue(authority, amended)  # no binding and no descendant: the authority issues it alone
    child = issue(
        authority, Mandate.from_json(e.child_json(of=amended), signed=False).signed(e.SERVICE)
    )
    state = authority.log.state()
    assert state.head(e.MANDATE_ID) == (2, amended.digest)
    assert state.head(e.CHILD_ID) == (1, child.digest)
    third = Mandate.from_json(
        {**e.mandate_json(), "version": 3, "prev": amended.digest}, signed=False
    ).signed(e.signer("cfo"))
    authority.propose(third)
    approve(authority.log.store, ("cfo", "head-ops"), "mandate", third.digest)
    with pytest.raises(AuthorityError, match="amend it through the binder"):
        authority.issue_mandate(third.digest)  # its child is rebased or suspended by the binder
    assert authority.log.state().head(e.MANDATE_ID) == (2, amended.digest)
    assert _verify(authority) == []


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        (lambda: {**e.mandate_json(), "version": 2, "prev": "a" * 64}, "has no version 1"),
        (
            lambda: e.mandate_json(
                term={
                    "not_before": "2026-01-01T00:00:00.000000Z",
                    "not_after": "2026-10-01T00:00:00.000000Z",
                }
            ),
            "term ended",
        ),
        (
            lambda: e.mandate_json(
                charter={"charter_id": e.CHARTER_ID, "version": 1, "digest": "c" * 64}
            ),
            "another digest",
        ),
    ],
)
def test_a_root_out_of_order_is_refused(authority: Authority, raw: Any, match: str) -> None:
    adopt(authority)
    mandate = Mandate.from_json(raw(), signed=False).signed(e.signer("cfo"))
    authority.propose(mandate)
    approve(authority.log.store, ("cfo", "head-ops"), "mandate", mandate.digest)
    with pytest.raises(AuthorityError, match=match):
        authority.issue_mandate(mandate.digest)


def test_an_amendment_names_the_head(authority: Authority) -> None:
    adopt(authority)
    issue(authority)
    stale = Mandate.from_json(
        {**e.mandate_json(), "version": 3, "prev": e.mandate().digest}, signed=False
    ).signed(e.signer("cfo"))
    authority.propose(stale)
    with pytest.raises(AuthorityError, match="not version 2"):
        authority.issue_mandate(stale.digest)


def test_a_quorum_mandate_is_signed_by_a_principal(authority: Authority) -> None:
    adopt(authority)
    mandate = Mandate.from_json(e.mandate_json(), signed=False).signed(e.signer("mallory"))
    authority.propose(mandate)
    approve(authority.log.store, ("cfo", "head-ops"), "mandate", mandate.digest)
    with pytest.raises(AuthorityError, match="signed by a principal"):
        authority.issue_mandate(mandate.digest)


def test_a_derived_child_refines_its_parent_and_fits_its_budget(authority: Authority) -> None:
    adopt(authority)
    issue(authority)
    wide = Mandate.from_json(
        e.child_json(budget={**e.mandate_json()["budget"], "per_call": "9.00000000"}), signed=False
    ).signed(e.SERVICE)
    authority.propose(wide)
    with pytest.raises(AuthorityError, match="does not refine its parent"):
        authority.issue_mandate(wide.digest)
    by_stranger = Mandate.from_json(e.child_json(), signed=False).signed(e.signer("mallory"))
    authority.propose(by_stranger)
    with pytest.raises(Exception, match="signed by key"):
        authority.issue_mandate(by_stranger.digest)
    orphan = Mandate.from_json(
        e.child_json(parent={"mandate_id": e.MANDATE_ID, "version": 1, "digest": "a" * 64}),
        signed=False,
    ).signed(e.SERVICE)
    authority.propose(orphan)
    with pytest.raises(AuthorityError, match="parent's version in force"):
        authority.issue_mandate(orphan.digest)


def test_a_parent_allows_only_so_many_children(authority: Authority) -> None:
    adopt(authority)
    parent = issue(
        authority,
        Mandate.from_json(
            e.mandate_json(
                delegation={"max_depth": 1, "max_children": 1, "envelope": "50.00000000"}
            ),
            signed=False,
        ).signed(e.signer("cfo")),
    )
    issue(authority, Mandate.from_json(e.child_json(of=parent), signed=False).signed(e.SERVICE))
    second = Mandate.from_json(
        e.child_json(
            of=parent,
            mandate_id="5c8e2f1b-0d3e-4f70-9b2c-4d6e8f0a1b23",
            subject={"scope": "support-agent/two", "funded_from": "support-agent", "label": "two"},
        ),
        signed=False,
    ).signed(e.SERVICE)
    authority.propose(second)
    with pytest.raises(AuthorityError, match="allows 1 children, and has 1"):
        authority.issue_mandate(second.digest)


def test_a_child_issued_by_quorum_needs_its_parents_amend_quorum(authority: Authority) -> None:
    adopt(authority)
    issue(authority)
    child = Mandate.from_json(e.child_json(issuance="quorum"), signed=False).signed(e.signer("cfo"))
    authority.propose(child)
    approve(authority.log.store, ("cfo",), "mandate", child.digest)
    with pytest.raises(AuthorityError, match="2 approvals; 1 count"):
        authority.issue_mandate(child.digest)
    approve(authority.log.store, ("security",), "mandate", child.digest)
    assert authority.issue_mandate(child.digest).content["issuance"] == "quorum"


def test_a_document_of_another_type_is_not_read_as_a_mandate(authority: Authority) -> None:
    adopt(authority)
    with pytest.raises(AuthorityError, match="is a charter, not a mandate"):
        authority.issue_mandate(e.charter().digest)
    with pytest.raises(AuthorityError, match="holds no document"):
        authority.issue_mandate("f" * 64)


def test_charter_version_lookup(authority: Authority) -> None:
    from fiduciary.authority import Decide

    adopt(authority)
    decide = Decide(authority.log.store, e.charter().digest, e.SERVICE.public_key())
    assert decide.charter(authority.log.state(), 1).version == 1
    with pytest.raises(AuthorityError, match="never adopted"):
        decide.charter(authority.log.state(), 7)


def test_documents_are_read_once_and_only_as_they_hash(
    authority: Authority, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A decision reads a document from the store once: a document never
    changes under its digest. Bytes that do not hash to the digest they are
    stored under are read again each time, as stored; a cached document is
    still only the type it is; the least recently read goes past the bound."""
    from fiduciary.authority import Decide, _Documents

    adopt(authority)
    store = authority.log.store
    decide = Decide(store, e.charter().digest, e.SERVICE.public_key(), _Documents(size=1))
    charter, approval = e.charter(), _approval("cfo")
    authority.propose(approval)
    reads: list[str] = []
    document = store.document

    def counted(digest: str) -> tuple[str, bytes] | None:
        reads.append(digest)
        return document(digest)

    monkeypatch.setattr(store, "document", counted)
    for _ in range(3):
        assert decide.load(charter.digest, Charter) == charter
    assert reads == [charter.digest]
    with pytest.raises(AuthorityError, match="is a charter, not a mandate"):
        decide.load(charter.digest, Mandate)
    assert decide.load(approval.digest, Approval) == approval  # the charter goes
    assert decide.load(charter.digest, Charter) == charter
    assert reads == [charter.digest, approval.digest, charter.digest]

    def forged(digest: str) -> tuple[str, bytes] | None:
        reads.append(digest)
        return ("approval", approval.canonical()) if digest == "f" * 64 else document(digest)

    monkeypatch.setattr(store, "document", forged)
    for _ in range(2):
        assert decide.load("f" * 64, Approval) == approval
    assert reads[-2:] == ["f" * 64] * 2  # not its digest's bytes: never kept

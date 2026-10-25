"""Tampering, as the database's owner would do it, around every protection
the store has, on SQLite and PostgreSQL (G6, F10-1, F4-26).

Each test edits, deletes, reorders, truncates, forks or forges what a store
holds, writing around the triggers and the functions as only an owner can,
and requires ``verify`` to name it. Some of it only the ledger's anchors can
see: a log truncated cleanly, or rewritten by someone holding the service key.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Any

import pytest
from agentgov import BudgetManager

from fiduciary import fidu1
from fiduciary.authority import Authority
from fiduciary.documents import Record
from fiduciary.log import Log
from fiduciary.sqlite import SqliteStore
from fiduciary.store import Store
from fiduciary.verify import Report, verify_store
from tests import examples as e


def _owner(store: Store, statements: Sequence[tuple[str, tuple[Any, ...]]]) -> None:
    """Run statements as the database's owner, around every trigger. ``{t}``
    stands for a table's qualified name, ``?`` for a parameter."""
    if isinstance(store, SqliteStore):
        conn = sqlite3.connect(store.path)
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        ).fetchall():
            conn.execute(f"DROP TRIGGER {name}")
        for sql, args in statements:
            conn.execute(sql.replace("{t}", ""), args)
        conn.commit()
        conn.close()
        return
    import psycopg

    from tests.conftest import PG_DSN

    assert PG_DSN is not None
    schema = store.schema  # type: ignore[attr-defined]
    with psycopg.connect(PG_DSN, autocommit=True) as admin:
        admin.execute("SET session_replication_role = replica")
        for sql, args in statements:
            admin.execute(sql.replace("{t}", f"{schema}.").replace("?", "%s"), args)


_INSERT = "INSERT INTO {t}log (log_id, seq, hash, prev, kind, record) VALUES (?, ?, ?, ?, ?, ?)"


def _verify(store: Store, ledger: BudgetManager | None) -> Report:
    entries = None
    if ledger is not None:
        ledger.refresh()
        entries = ledger.audit_trail()
    return verify_store(
        store, service_key=e.SERVICE.public_key(), pinned=e.charter().digest, ledger=entries
    )


def _names(report: Report, *fragments: str) -> None:
    text = " | ".join(report.problems)
    for fragment in fragments:
        assert fragment in text, f"{fragment!r} not named in: {text}"


def _row(store: Store, seq: int) -> Any:
    return next(r for r in store.records("fiduciary") if r.seq == seq)


def test_an_untouched_store_verifies(world: Authority, ledger: BudgetManager) -> None:
    report = _verify(world.log.store, ledger)
    assert report.ok, report.problems
    assert report.records == 8 and report.anchored == 8 and report.unanchored == ()


def test_an_edited_record_is_named(world: Authority, ledger: BudgetManager) -> None:
    store = world.log.store
    row = _row(store, 4)
    raw = fidu1.parse(row.data)
    raw["body"]["principal"] = "security"
    edited = fidu1.canonical(raw)
    _owner(store, [("UPDATE {t}log SET record = ? WHERE seq = 4", (edited,))])
    _names(
        _verify(store, ledger),
        "record 4: its bytes do not hash",
        "record 4: the record's signature does not verify",
    )


def test_an_edited_record_rehashed_breaks_the_chain_and_the_anchors(
    world: Authority, ledger: BudgetManager
) -> None:
    store = world.log.store
    row = _row(store, 4)
    raw = fidu1.parse(row.data)
    raw["body"]["decision"] = "decline"
    edited = fidu1.canonical(raw)
    digest = fidu1.digest(raw)
    _owner(store, [("UPDATE {t}log SET record = ?, hash = ? WHERE seq = 4", (edited, digest))])
    _names(
        _verify(store, ledger),
        "record 4: the record's signature does not verify",
        "record 5: does not link",
        "anchors record 4",
    )


def test_a_deleted_record_is_named(world: Authority, ledger: BudgetManager) -> None:
    store = world.log.store
    _owner(store, [("DELETE FROM {t}log WHERE seq = 3", ())])
    _names(
        _verify(store, ledger),
        "record 4: expected record 3",
        "record 4: does not link",
        "which the log does not hold",
    )


def test_reordered_records_are_named(world: Authority, ledger: BudgetManager) -> None:
    store = world.log.store
    _owner(
        store,
        [
            ("UPDATE {t}log SET seq = 100 WHERE seq = 2", ()),
            ("UPDATE {t}log SET seq = 2 WHERE seq = 3", ()),
            ("UPDATE {t}log SET seq = 3 WHERE seq = 100", ()),
        ],
    )
    _names(_verify(store, ledger), "expected record 2", "does not link")


def test_a_clean_truncation_is_seen_by_the_anchors_alone(
    world: Authority, ledger: BudgetManager
) -> None:
    store = world.log.store
    last = [r for r in world.log.records() if r.seq >= 7]
    assert [r.kind for r in last] == ["approval.recorded", "mandate.issued"][-len(last) :] or last
    _owner(
        store,
        [
            ("DELETE FROM {t}log WHERE seq >= 7", ()),
            ("DELETE FROM {t}mandates WHERE seq >= 7", ()),
            ("DELETE FROM {t}approvals WHERE seq >= 7", ()),
        ],
    )
    alone = _verify(store, None)
    assert alone.ok, "without the ledger, a clean truncation is a shorter valid log"
    _names(_verify(store, ledger), "anchors record 7", "anchors record 8", "it was truncated")


def test_a_rewritten_tail_signed_with_the_service_key_is_seen_by_the_anchors(
    world: Authority, ledger: BudgetManager
) -> None:
    store = world.log.store
    _owner(
        store,
        [("DELETE FROM {t}log WHERE seq >= 8", ()), ("DELETE FROM {t}mandates WHERE seq >= 8", ())],
    )
    forger = Log(store, e.SERVICE)  # no ledger: the forger cannot rewrite it
    forger.append("approval.refused", {"intake": 99, "digest": "a" * 64, "why": "forged"})
    _names(_verify(store, ledger), "anchors record 8 of log 'fiduciary' as", "it was rewritten")


def test_an_edited_projection_is_named(world: Authority, ledger: BudgetManager) -> None:
    store = world.log.store
    _owner(store, [("UPDATE {t}mandates SET issuance = 'quorum' WHERE issuance = 'derived'", ())])
    _names(_verify(store, ledger), "projection mandates: differs")
    _owner(store, [("DELETE FROM {t}approvals", ())])
    _names(_verify(store, ledger), "projection approvals: differs")


def test_an_edited_or_deleted_document_is_named(world: Authority, ledger: BudgetManager) -> None:
    store = world.log.store
    charter = e.charter()
    _owner(
        store,
        [
            (
                "UPDATE {t}documents SET body = ? WHERE digest = ?",
                (charter.canonical() + b" ", charter.digest),
            )
        ],
    )
    _names(_verify(store, ledger), f"document {charter.digest[:16]}: its bytes do not hash")
    mandate = e.mandate()
    _owner(store, [("DELETE FROM {t}documents WHERE digest = ?", (mandate.digest,))])
    _names(_verify(store, ledger), "references a mandate that is not in the store")


def test_a_document_that_is_not_its_type_is_named(world: Authority, ledger: BudgetManager) -> None:
    store = world.log.store
    import hashlib

    junk = b'{"v":"FIDU1"}'
    _owner(
        store,
        [
            (
                "INSERT INTO {t}documents (digest, type, body) VALUES (?, ?, ?)",
                (hashlib.sha256(junk).hexdigest(), "charter", junk),
            )
        ],
    )
    _names(_verify(store, ledger), "not a FIDU1 charter")
    tool = e.tool()
    _owner(
        store,
        [
            (
                "INSERT INTO {t}documents (digest, type, body) VALUES (?, ?, ?)",
                (tool.digest, "charter", tool.canonical()),
            )
        ],
    )
    _names(_verify(store, ledger), "is a tool, stored as a charter")


def test_a_decision_its_rules_refuse_is_named(world: Authority, ledger: BudgetManager) -> None:
    """Someone with the service key writes a decision the rules would not:
    a mandate issued with no approvals, and an approval with no submission."""
    store = world.log.store
    log = Log(store, e.SERVICE, ledger=ledger, clock=world.log.now)
    stray = e.mandate_json(mandate_id="7e1d2c3b-4a59-4687-9a0b-1c2d3e4f5a6b")
    from fiduciary.documents import Mandate

    mandate = Mandate.from_json(stray, signed=False).signed(e.signer("cfo"))
    log.append(
        "mandate.issued",
        {
            "mandate": mandate.digest,
            "mandate_id": mandate.mandate_id,
            "version": 1,
            "prev": None,
            "parent": None,
            "issuance": "quorum",
            "approvals": [],
        },
        documents=[mandate],
    )
    approval = e.approval("security", approval_id="1e2d3c4b-5a69-4788-9b0c-1d2e3f4a5b6c")
    log.append(
        "approval.recorded",
        {
            "intake": 404,
            "approval": approval.digest,
            "principal": "security",
            "decision": "approve",
            "subject": approval.subject.to_json(),
            "resolution": None,
        },
        documents=[approval],
    )
    _names(
        _verify(store, ledger),
        "record 9 (mandate.issued): its rules refuse it now",
        "record 10 (approval.recorded)",
    )


def test_a_decision_rewritten_with_another_body_is_named(
    world: Authority, ledger: BudgetManager
) -> None:
    store = world.log.store
    log = Log(store, e.SERVICE, ledger=ledger, clock=world.log.now)
    # a refusal recorded for a submission that holds: the rules write "recorded"
    submitted = store.submit(
        e.approval("security", approval_id="2e2d3c4b-5a69-4788-9b0c-1d2e3f4a5b6c").canonical()
    )
    log.append("approval.refused", {"intake": submitted, "digest": "a" * 64, "why": "said no"})
    _names(_verify(store, ledger), "(approval.refused): its rules refuse it now")


def test_an_unanchored_record_is_named(world: Authority, ledger: BudgetManager) -> None:
    store = world.log.store
    Log(store, e.SERVICE).append("approval.refused", {"intake": 77, "digest": "a" * 64, "why": "x"})
    report = _verify(store, ledger)
    assert report.unanchored == (9,)
    _names(report, "record 9: has no anchor in the ledger")
    lenient = verify_store(
        store,
        service_key=e.SERVICE.public_key(),
        pinned=e.charter().digest,
        ledger=ledger.audit_trail(),
        require_anchors=False,
    )
    assert all("no anchor" not in p for p in lenient.problems)


def test_pending_anchors_are_written_on_the_next_open(
    world: Authority, ledger: BudgetManager
) -> None:
    store = world.log.store
    store.submit(b"not an approval")
    unanchored = Log(store, e.SERVICE, clock=world.log.now)  # the ledger cannot be written
    Authority(unanchored, pinned=e.charter().digest).process_intake()
    reopened = Log(store, e.SERVICE, ledger=ledger)
    assert reopened.anchor_pending() == 1
    assert reopened.anchor_pending() == 0
    assert _verify(store, ledger).ok


def test_a_record_rolled_back_is_never_anchored(world: Authority, ledger: BudgetManager) -> None:
    """A record written inside a transaction that then rolls back leaves no
    anchor: the ledger would otherwise name a record the log never held, and
    the next record at that sequence would read as a rewrite."""
    store = world.log.store
    seq = store.head("fiduciary")[0]
    store.submit(b"not an approval")

    def anchored() -> list[str]:
        ledger.refresh()
        return [en.memo for en in ledger.audit_trail("fiduciary") if f" {seq + 1} " in en.memo]

    with pytest.raises(RuntimeError, match="stop"), store.transaction():
        assert [r.seq for r in world.process_intake()] == [seq + 1]
        assert anchored() == []  # not before the commit
        raise RuntimeError("stop")
    assert store.head("fiduciary")[0] == seq
    assert anchored() == []
    assert [r.seq for r in world.process_intake()] == [seq + 1]
    assert len(anchored()) == 1
    report = _verify(store, ledger)
    assert report.ok, report.problems
    assert report.unanchored == ()


def test_a_record_signed_by_another_key_is_named(world: Authority, ledger: BudgetManager) -> None:
    store = world.log.store
    Log(store, e.signer("mallory"), ledger=ledger).append(
        "approval.refused", {"intake": 79, "digest": "a" * 64, "why": "x"}
    )
    _names(_verify(store, ledger), "record 9: the record was signed by key")


def test_a_record_of_another_log_or_unreadable_is_named(
    world: Authority, ledger: BudgetManager
) -> None:
    store = world.log.store
    head = store.head("fiduciary")
    alien = Record.make(
        e.SERVICE,
        log="other",
        seq=head[0] + 1,
        kind="approval.refused",
        body={"intake": 1, "digest": "a" * 64, "why": "x"},
        prev=head[1],
        issued_at=e.at("2026-10-18T10:00:00"),
    )
    _owner(
        store,
        [
            (
                _INSERT,
                ("fiduciary", alien.seq, alien.hash, alien.prev, alien.kind, alien.canonical()),
            )
        ],
    )
    _names(_verify(store, ledger), "belongs to log 'other'", "its columns disagree")
    _owner(
        store,
        [
            (
                _INSERT,
                ("fiduciary", alien.seq + 1, "0" * 64, alien.hash, "x", b"garbage"),
            )
        ],
    )
    _names(_verify(store, ledger), "not a FIDU1 record")


def test_an_anchor_for_a_missing_scope_waits(store: Store, tmp_path: Any) -> None:
    governor = BudgetManager.open_sqlite(str(tmp_path / "bare.db"))  # no anchor scope
    log = Log(store, e.SERVICE, ledger=governor)
    log.append(
        "approval.refused", {"intake": 1, "digest": "a" * 64, "why": "x"}
    )  # the record stands
    from fiduciary.log import ensure_anchor_scope

    assert ensure_anchor_scope(governor) is True and ensure_anchor_scope(governor) is False
    assert log.anchor_pending() == 1
    governor.close()


def test_anchor_pending_refuses_a_log_the_ledger_disagrees_with(
    world: Authority, ledger: BudgetManager
) -> None:
    store = world.log.store
    _owner(store, [("DELETE FROM {t}log WHERE seq = 8", ())])
    from fiduciary.exceptions import AnchorError

    with pytest.raises(AnchorError, match="truncated"):
        Log(store, e.SERVICE, ledger=ledger).anchor_pending()


def test_memos_that_are_not_fiduciarys_are_ignored() -> None:
    from fiduciary.log import parse_memo

    assert parse_memo("ILOK1 interlock-operators 1 " + "a" * 64) is None
    assert parse_memo("FIDU1 fiduciary x " + "a" * 64) is None
    assert parse_memo("FIDU1 fiduciary 1 short") is None
    assert parse_memo("FIDU1 fiduciary 3 " + "a" * 64) == ("fiduciary", 3, "a" * 64)

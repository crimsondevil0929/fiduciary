"""The PostgreSQL store's roles and its install (F10-2 to F10-4).

Each role is tried as a login role granted membership in it: the service
appends through the functions and nothing else; the intake submits and reads
nothing; the audit role reads and writes nothing; and nobody, the owner
included, updates or deletes the log, the documents or the intake.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest

from fiduciary import fidu1
from fiduciary.documents import Record
from fiduciary.exceptions import StoreError
from fiduciary.postgres import PostgresStore, install, roles
from fiduciary.store import StoredRecord
from tests import examples as e


def _login(dsn: str, group: str) -> tuple[str, str]:
    """A login role in ``group``; returns its DSN and name."""
    name = f"fid_login_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f"CREATE ROLE {name} LOGIN PASSWORD 'pw' IN ROLE {group}")
    base = psycopg.conninfo.conninfo_to_dict(dsn)
    base.update(user=name, password="pw")
    return psycopg.conninfo.make_conninfo(**base), name  # type: ignore[arg-type]


@pytest.fixture
def logins(pg_schema: tuple[str, str]) -> Iterator[dict[str, str]]:
    dsn, schema = pg_schema
    service, intake, audit = roles(schema)
    made = {
        kind: _login(dsn, group)
        for kind, group in (("service", service), ("intake", intake), ("audit", audit))
    }
    yield {kind: found[0] for kind, found in made.items()}
    with psycopg.connect(dsn, autocommit=True) as admin:
        for _, name in made.values():
            admin.execute(f"DROP ROLE {name}")


def _record(seq: int = 1, prev: str = fidu1.GENESIS) -> StoredRecord:
    record = Record.make(
        e.SERVICE,
        log="fiduciary",
        seq=seq,
        kind="approval.refused",
        body={"intake": seq, "digest": "a" * 64, "why": "x"},
        prev=prev,
        issued_at=e.at("2026-10-18T10:00:00"),
    )
    return StoredRecord(
        record.log, record.seq, record.hash, record.prev, record.kind, record.canonical()
    )


def test_the_service_appends_through_the_functions_only(
    pg_schema: tuple[str, str], logins: dict[str, str]
) -> None:
    _, schema = pg_schema
    store = PostgresStore(logins["service"], schema=schema)
    record = _record()
    with store.transaction():
        store.insert_record(record)
        store.put_document(e.charter().digest, "charter", e.charter().canonical())
        store.insert_row("refusals", (1, "a" * 64, 1))
    assert store.head("fiduciary")[0] == 1
    for statement in (
        f"INSERT INTO {schema}.log VALUES ('fiduciary', 2, 'x', 'y', 'z', 'w')",
        f"INSERT INTO {schema}.documents VALUES ('x', 'charter', 'y')",
        f"UPDATE {schema}.log SET kind = 'x'",
        f"DELETE FROM {schema}.documents",
        f"DELETE FROM {schema}.refusals",
        f"SELECT {schema}.submit('x')",
    ):
        with pytest.raises(StoreError, match="permission denied"):
            store._execute(statement)
    store.close()


def test_the_intake_submits_and_does_nothing_else(
    pg_schema: tuple[str, str], logins: dict[str, str]
) -> None:
    _, schema = pg_schema
    store = PostgresStore(logins["intake"], schema=schema)
    assert store.submit(b"an approval") >= 1
    for action in (
        lambda: list(store.intake()),
        lambda: store.head("fiduciary"),
        lambda: store.insert_record(_record()),
        lambda: store.put_document("x", "charter", b"y"),
        lambda: store.insert_row("refusals", (1, "a" * 64, 1)),
    ):
        with pytest.raises(StoreError, match="permission denied"):
            action()
    store.close()


def test_the_audit_role_reads_and_writes_nothing(
    pg_schema: tuple[str, str], logins: dict[str, str]
) -> None:
    dsn, schema = pg_schema
    owner = PostgresStore(dsn, schema=schema)
    with owner.transaction():
        owner.insert_record(_record())
    owner.submit(b"x")
    owner.close()
    store = PostgresStore(logins["audit"], schema=schema)
    assert store.head("fiduciary")[0] == 1
    assert len(list(store.intake())) == 1
    for action in (
        lambda: store.submit(b"y"),
        lambda: store.insert_record(_record(2, store.head("fiduciary")[1])),
        lambda: store.insert_row("refusals", (1, "a" * 64, 1)),
    ):
        with pytest.raises(StoreError, match="permission denied"):
            action()
    store.close()


def test_not_even_the_owner_updates_deletes_or_truncates(pg_schema: tuple[str, str]) -> None:
    dsn, schema = pg_schema
    store = PostgresStore(dsn, schema=schema)
    with store.transaction():
        store.insert_record(_record())
        store.put_document(e.charter().digest, "charter", e.charter().canonical())
    store.submit(b"x")
    for table in ("log", "documents", "intake"):
        for statement in (
            f"UPDATE {schema}.{table} SET body = body"
            if table != "log"
            else f"UPDATE {schema}.log SET kind = kind",
            f"DELETE FROM {schema}.{table}",
            f"TRUNCATE {schema}.{table}",
        ):
            with pytest.raises(StoreError, match="append-only"):
                store._execute(statement)
    store.close()


def test_install_is_idempotent_and_refuses_another_version(pg_schema: tuple[str, str]) -> None:
    dsn, schema = pg_schema
    with psycopg.connect(dsn, autocommit=True) as admin:
        install(admin, schema=schema)
        for earlier in ("1", "2", "3"):
            admin.execute(f"UPDATE {schema}.meta SET value = %s", (earlier,))
            install(admin, schema=schema)
            row = admin.execute(f"SELECT value FROM {schema}.meta WHERE key = 'schema'").fetchone()
            assert row == ("4",)
        admin.execute(f"UPDATE {schema}.meta SET value = '99'")
        with pytest.raises(StoreError, match="version 99"):
            install(admin, schema=schema)


def test_a_schema_name_is_checked() -> None:
    with pytest.raises(ValueError, match="schema"):
        roles("Bad-Name")


def test_an_unreachable_database_is_a_store_error() -> None:
    with pytest.raises(StoreError, match="cannot connect"):
        PostgresStore("postgresql://nobody:x@127.0.0.1:1/none?connect_timeout=1")


def test_install_reports_a_failure(pg_schema: tuple[str, str], logins: dict[str, str]) -> None:
    _, schema = pg_schema
    with (
        psycopg.connect(logins["audit"], autocommit=True) as weak,
        pytest.raises(StoreError, match="cannot install"),
    ):
        install(weak, schema=f"{schema}_other")

"""Fixtures: stores on SQLite and PostgreSQL, a controllable clock, a ledger.

The PostgreSQL store runs when ``FIDUCIARY_TEST_POSTGRES_DSN`` names a
database whose role may create schemas and roles (the local container:
``postgresql://interlock_admin:interlock@localhost:55432/postgres``). With
``FIDUCIARY_REQUIRE_POSTGRES=1`` a test that would skip for want of it fails.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from agentgov import BudgetManager

from fiduciary.authority import Authority
from fiduciary.documents import Charter, Mandate
from fiduciary.log import Log, ensure_anchor_scope
from fiduciary.sqlite import SqliteStore
from fiduciary.store import Store
from tests import examples as e

PG_DSN = os.environ.get("FIDUCIARY_TEST_POSTGRES_DSN")
REQUIRE_PG = os.environ.get("FIDUCIARY_REQUIRE_POSTGRES") == "1"


def need_postgres() -> str:
    if PG_DSN is None:
        if REQUIRE_PG:
            pytest.fail("FIDUCIARY_REQUIRE_POSTGRES=1 and FIDUCIARY_TEST_POSTGRES_DSN is unset")
        pytest.skip("set FIDUCIARY_TEST_POSTGRES_DSN to run the PostgreSQL store")
    return PG_DSN


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


class LiveClock(Clock):
    """The wall clock, moved on by :meth:`advance`: what a deployment whose
    instants are held to its ledger's (V12) runs on."""

    def __init__(self) -> None:
        super().__init__(datetime.now(UTC))
        self._offset = timedelta(0)

    def __call__(self) -> datetime:
        return datetime.now(UTC) + self._offset

    def advance(self, **delta: float) -> None:
        self._offset += timedelta(**delta)


@pytest.fixture
def clock() -> Clock:
    return Clock(e.at("2026-10-18T10:10:00"))


@pytest.fixture
def pg_schema() -> Iterator[tuple[str, str]]:
    """A fresh schema, installed; ``(dsn, schema)``. Dropped, with its roles, after."""
    dsn = need_postgres()
    import psycopg

    from fiduciary.postgres import install, roles

    schema = f"fid_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(dsn, autocommit=True) as admin:
        install(admin, schema=schema)
    yield dsn, schema
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA {schema} CASCADE")
        for role in roles(schema):
            admin.execute(f"DROP OWNED BY {role}")
            admin.execute(f"DROP ROLE {role}")


def _open(kind: str, tmp_path: Path, request: pytest.FixtureRequest) -> Store:
    if kind == "sqlite":
        return SqliteStore(tmp_path / "fiduciary.db")
    from fiduciary.postgres import PostgresStore

    dsn, schema = request.getfixturevalue("pg_schema")
    return PostgresStore(dsn, schema=schema)


def reopen(store: Store) -> Store:
    """Another connection to the store ``store`` is: what a second process opens."""
    if isinstance(store, SqliteStore):
        return SqliteStore(store.path)
    from fiduciary.postgres import PostgresStore

    assert isinstance(store, PostgresStore)
    return PostgresStore(need_postgres(), schema=store.schema)


@pytest.fixture(params=["sqlite", "postgres"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Store]:
    """A store, on each backend."""
    opened = _open(request.param, tmp_path, request)
    yield opened
    opened.close()


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[BudgetManager]:
    governor = BudgetManager.open_sqlite(str(tmp_path / "ledger.db"))
    ensure_anchor_scope(governor)
    yield governor
    governor.close()


@pytest.fixture
def log(store: Store, ledger: BudgetManager, clock: Clock) -> Log:
    return Log(store, e.SERVICE, ledger=ledger, clock=clock)


@pytest.fixture
def authority(log: Log) -> Authority:
    return Authority(log, pinned=e.charter().digest)


def submit(store: Store, document: Any) -> int:
    return store.submit(document.canonical())


def approve(
    store: Store,
    principals: tuple[str, ...],
    subject: str,
    digest: str,
    *,
    charter_version: int = 1,
    **changes: Any,
) -> None:
    for i, principal in enumerate(principals):
        changes.setdefault("charter", {"charter_id": e.CHARTER_ID, "version": charter_version})
        approval = e.approval(
            principal,
            subject=subject,
            digest=digest,
            approval_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{subject}/{digest}/{principal}/{i}")),
            **changes,
        )
        submit(store, approval)


def adopt(authority: Authority, charter: Charter | None = None) -> Charter:
    charter = charter or e.charter()
    authority.propose(charter)
    approve(authority.log.store, e.PRINCIPALS, "charter", charter.digest)
    authority.adopt_charter(charter.digest)
    return charter


def issue(
    authority: Authority,
    mandate: Mandate | None = None,
    approvers: tuple[str, ...] = ("cfo", "head-ops"),
) -> Mandate:
    mandate = mandate or e.mandate()
    authority.propose(mandate)
    if mandate.issuance == "quorum":
        approve(authority.log.store, approvers, "mandate", mandate.digest)
    authority.issue_mandate(mandate.digest)
    return mandate


@pytest.fixture
def world(authority: Authority) -> Authority:
    """A log with the example charter adopted, the example mandate issued and
    its example child derived."""
    adopt(authority)
    issue(authority)
    issue(authority, e.child())
    return authority


Factory = Callable[..., Any]


@pytest.fixture(params=["sqlite", "postgres"])
def home(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[dict[str, Any]]:
    """A working directory for the command line, and the database its
    configuration will name: a SQLite file, or a fresh PostgreSQL schema."""
    monkeypatch.chdir(tmp_path)
    schema = None
    if request.param == "sqlite":
        database = "fid.db"
    else:
        database = need_postgres()
        schema = f"fid_{uuid.uuid4().hex[:12]}"
    yield {"dir": tmp_path, "database": database, "schema": schema}
    if schema is not None:
        import psycopg

        from fiduciary.postgres import roles

        with psycopg.connect(database, autocommit=True) as admin:
            admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            for role in roles(schema):
                if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
                    admin.execute(f"DROP OWNED BY {role}")
                    admin.execute(f"DROP ROLE {role}")

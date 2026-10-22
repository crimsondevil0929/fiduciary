"""The PostgreSQL store (§10.2).

:func:`install` creates a schema (``fiduciary`` by default) owned by the
installing role, and three group roles named after it:

- ``<schema>_service`` appends records and stores documents, only through
  ``append_record`` and ``put_document``; reads everything; writes the
  projections.
- ``<schema>_intake`` submits an approval, only through ``submit``.
- ``<schema>_audit`` reads everything.

Grant a login role membership in one of them. The functions are ``SECURITY
DEFINER``: ``append_record`` takes a lock on the log, and accepts a record only
at the head, linked to it, whose bytes hash to the hash it is stored under;
``put_document`` stores a document only under the SHA-256 of its bytes. No
role may update or delete the log, the documents or the intake, and triggers
refuse it even to their owner (F10-2). The database holds no secret (F10-3).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import TYPE_CHECKING, Any, Final

from fiduciary import fidu1
from fiduciary.exceptions import StoreError
from fiduciary.store import PROJECTIONS, Store, StoredRecord

if TYPE_CHECKING:
    import psycopg

__all__ = ["SCHEMA_VERSION", "PostgresStore", "install", "roles"]

SCHEMA_VERSION: Final = 4
"""As :data:`fiduciary.sqlite.SCHEMA_VERSION`."""
_SCHEMA: Final = re.compile(r"[a-z_][a-z0-9_]{0,40}")
_COLUMN_TYPES: Final = {"version": "bigint", "seq": "bigint", "intake": "bigint"}


def _checked(schema: str) -> str:
    if not _SCHEMA.fullmatch(schema):
        raise ValueError(f"a schema is [a-z_][a-z0-9_]{{0,40}}, not {schema!r}")
    return schema


def roles(schema: str = "fiduciary") -> tuple[str, str, str]:
    """The service, intake and audit roles of a schema."""
    s = _checked(schema)
    return f"{s}_service", f"{s}_intake", f"{s}_audit"


def _ddl(s: str) -> list[str]:
    service, intake, audit = roles(s)
    genesis = fidu1.GENESIS
    statements = [
        f"CREATE SCHEMA IF NOT EXISTS {s}",
        f"CREATE TABLE IF NOT EXISTS {s}.meta (key text PRIMARY KEY, value text NOT NULL)",
        f"CREATE TABLE IF NOT EXISTS {s}.documents ("
        " digest text PRIMARY KEY, type text NOT NULL, body bytea NOT NULL)",
        f"CREATE TABLE IF NOT EXISTS {s}.log ("
        " log_id text NOT NULL, seq bigint NOT NULL, hash text NOT NULL UNIQUE,"
        " prev text NOT NULL, kind text NOT NULL, record bytea NOT NULL,"
        " PRIMARY KEY (log_id, seq))",
        f"CREATE TABLE IF NOT EXISTS {s}.intake ("
        " id bigserial PRIMARY KEY, received_at timestamptz NOT NULL DEFAULT now(),"
        " body bytea NOT NULL)",
    ]
    for table, columns in PROJECTIONS.items():
        typed = ", ".join(f"{c} {_COLUMN_TYPES.get(c, 'text')}" for c in columns)
        statements.append(f"CREATE TABLE IF NOT EXISTS {s}.{table} ({typed})")
    statements += [
        f"""CREATE OR REPLACE FUNCTION {s}.refuse() RETURNS trigger
            LANGUAGE plpgsql SET search_path = pg_catalog AS $$
            BEGIN
              RAISE EXCEPTION 'fiduciary: % is append-only', TG_TABLE_NAME
                USING ERRCODE = 'insufficient_privilege';
            END $$""",
        f"""CREATE OR REPLACE FUNCTION {s}.append_record(
              p_log text, p_seq bigint, p_hash text, p_prev text, p_kind text, p_record bytea)
            RETURNS void LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog AS $$
            DECLARE head_seq bigint; head_hash text;
            BEGIN
              PERFORM pg_advisory_xact_lock(hashtextextended('{s}.append:' || p_log, 0));
              SELECT l.seq, l.hash INTO head_seq, head_hash FROM {s}.log AS l
                WHERE l.log_id = p_log ORDER BY l.seq DESC LIMIT 1;
              IF NOT FOUND THEN head_seq := 0; head_hash := '{genesis}'; END IF;
              IF p_seq <> head_seq + 1 OR p_prev <> head_hash THEN
                RAISE EXCEPTION 'fiduciary: record % of log % is not the next (its head is %)',
                  p_seq, p_log, head_seq USING ERRCODE = 'check_violation';
              END IF;
              IF p_hash <> encode(sha256(p_record), 'hex') THEN
                RAISE EXCEPTION 'fiduciary: record % of log % is not stored under its own hash',
                  p_seq, p_log USING ERRCODE = 'check_violation';
              END IF;
              INSERT INTO {s}.log (log_id, seq, hash, prev, kind, record)
                VALUES (p_log, p_seq, p_hash, p_prev, p_kind, p_record);
            END $$""",
        f"""CREATE OR REPLACE FUNCTION {s}.put_document(p_digest text, p_type text, p_body bytea)
            RETURNS void LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog AS $$
            BEGIN
              IF p_digest <> encode(sha256(p_body), 'hex') THEN
                RAISE EXCEPTION 'fiduciary: a document is stored under its digest'
                  USING ERRCODE = 'check_violation';
              END IF;
              INSERT INTO {s}.documents (digest, type, body) VALUES (p_digest, p_type, p_body)
                ON CONFLICT (digest) DO NOTHING;
            END $$""",
        f"""CREATE OR REPLACE FUNCTION {s}.submit(p_body bytea) RETURNS bigint
            LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog AS $$
              INSERT INTO {s}.intake (body) VALUES (p_body) RETURNING id
            $$""",
    ]
    for table in ("documents", "log", "intake"):
        statements += [
            f"DROP TRIGGER IF EXISTS refuse_change ON {s}.{table}",
            f"CREATE TRIGGER refuse_change BEFORE UPDATE OR DELETE ON {s}.{table}"
            f" FOR EACH ROW EXECUTE FUNCTION {s}.refuse()",
            f"DROP TRIGGER IF EXISTS refuse_truncate ON {s}.{table}",
            f"CREATE TRIGGER refuse_truncate BEFORE TRUNCATE ON {s}.{table}"
            f" FOR EACH STATEMENT EXECUTE FUNCTION {s}.refuse()",
        ]
    for role in (service, intake, audit):
        statements.append(
            f"DO $$ BEGIN CREATE ROLE {role} NOLOGIN;"
            f" EXCEPTION WHEN duplicate_object THEN NULL; END $$"
        )
    projections = ", ".join(f"{s}.{t}" for t in PROJECTIONS)
    statements += [
        f"REVOKE ALL ON ALL TABLES IN SCHEMA {s} FROM PUBLIC",
        f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {s} FROM PUBLIC",
        f"REVOKE ALL ON SCHEMA {s} FROM PUBLIC",
        f"GRANT USAGE ON SCHEMA {s} TO {service}, {intake}, {audit}",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA {s} TO {service}, {audit}",
        f"GRANT INSERT ON {projections} TO {service}",
        f"GRANT EXECUTE ON FUNCTION {s}.append_record(text, bigint, text, text, text, bytea),"
        f" {s}.put_document(text, text, bytea) TO {service}",
        f"GRANT EXECUTE ON FUNCTION {s}.submit(bytea) TO {intake}",
    ]
    return statements


def install(conn: psycopg.Connection[Any], *, schema: str = "fiduciary") -> None:
    """Create or upgrade the store's schema, its functions and its roles, as
    the connected role, which then owns them. Idempotent.

    :raises StoreError: If the schema holds a store of another version.
    """
    s = _checked(schema)
    import psycopg

    try:
        with conn.transaction():
            for statement in _ddl(s):
                conn.execute(statement)
            found = conn.execute(f"SELECT value FROM {s}.meta WHERE key = 'schema'").fetchone()
            if found is None:
                conn.execute(
                    f"INSERT INTO {s}.meta (key, value) VALUES ('schema', %s)",
                    (str(SCHEMA_VERSION),),
                )
            elif int(found[0]) < SCHEMA_VERSION:
                conn.execute(
                    f"UPDATE {s}.meta SET value = %s WHERE key = 'schema'", (str(SCHEMA_VERSION),)
                )
            elif int(found[0]) != SCHEMA_VERSION:
                raise StoreError(
                    f"schema {s} holds a version {found[0]} store; this release installs "
                    f"version {SCHEMA_VERSION}"
                )
    except psycopg.Error as exc:
        raise StoreError(f"cannot install schema {s}: {exc}") from exc


class PostgresStore(Store):
    """A store in a PostgreSQL schema :func:`install` created.

    :param dsn: Connect as a login role with membership in the service role
        (or the intake role, to only submit; or audit, to only read).
    """

    def __init__(self, dsn: str, *, schema: str = "fiduciary") -> None:
        import psycopg

        self._s = _checked(schema)
        try:
            self._conn: psycopg.Connection[Any] = psycopg.connect(
                dsn, autocommit=True, application_name="fiduciary"
            )
        except psycopg.Error as exc:
            raise StoreError(f"cannot connect to PostgreSQL: {exc}") from exc
        self._errors = psycopg.Error
        super().__init__()

    @property
    def schema(self) -> str:
        return self._s

    @property
    def connection(self) -> psycopg.Connection[Any]:
        return self._conn

    def _execute(self, sql: str, args: tuple[Any, ...] = ()) -> Any:
        try:
            return self._conn.execute(sql, args)
        except self._errors as exc:
            raise StoreError(f"the store refused: {exc}") from exc

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        if self._depth:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return
        self._depth = 1
        try:
            with self._conn.transaction():
                # One writer at a time, as SQLite's BEGIN IMMEDIATE: what a
                # transaction reads of the log stays its head until it commits.
                self._conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"{self._s}.transaction",),
                )
                yield
        except self._errors as exc:
            self._drop_committed()
            raise StoreError(f"the store refused the write: {exc}") from exc
        except BaseException:
            self._drop_committed()
            raise
        finally:
            self._depth = 0
        self._run_committed()

    def transaction(self) -> AbstractContextManager[None]:
        return self._transaction()

    @contextmanager
    def _claim(self, log: str) -> Iterator[None]:
        key = f"{self._s}.writer:{log}"
        (held,) = self._execute(
            "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,)
        ).fetchone()
        if not held:
            raise StoreError(
                f"another session writes log {log!r} of schema {self._s}; one writer at a time"
            )
        try:
            yield
        finally:
            self._execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))

    def claim(self, log: str) -> AbstractContextManager[None]:
        return self._claim(log)

    # -- documents ------------------------------------------------------------

    def put_document(self, digest: str, kind: str, data: bytes) -> None:
        self._execute(f"SELECT {self._s}.put_document(%s, %s, %s)", (digest, kind, data))

    def document(self, digest: str) -> tuple[str, bytes] | None:
        row = self._execute(
            f"SELECT type, body FROM {self._s}.documents WHERE digest = %s", (digest,)
        ).fetchone()
        return None if row is None else (str(row[0]), bytes(row[1]))

    def documents(self) -> Iterator[tuple[str, str, bytes]]:
        rows = self._execute(
            f"SELECT digest, type, body FROM {self._s}.documents ORDER BY digest"
        ).fetchall()
        for digest, kind, body in rows:
            yield str(digest), str(kind), bytes(body)

    # -- the log --------------------------------------------------------------

    def head(self, log: str) -> tuple[int, str]:
        row = self._execute(
            f"SELECT seq, hash FROM {self._s}.log WHERE log_id = %s ORDER BY seq DESC LIMIT 1",
            (log,),
        ).fetchone()
        return (0, fidu1.GENESIS) if row is None else (int(row[0]), str(row[1]))

    def insert_record(self, record: StoredRecord) -> None:
        self._execute(
            f"SELECT {self._s}.append_record(%s, %s, %s, %s, %s, %s)",
            (record.log, record.seq, record.hash, record.prev, record.kind, record.data),
        )

    def records(self, log: str) -> Iterator[StoredRecord]:
        rows = self._execute(
            f"SELECT log_id, seq, hash, prev, kind, record FROM {self._s}.log"
            " WHERE log_id = %s ORDER BY seq",
            (log,),
        ).fetchall()
        for row in rows:
            yield StoredRecord(
                str(row[0]), int(row[1]), str(row[2]), str(row[3]), str(row[4]), bytes(row[5])
            )

    # -- projections ------------------------------------------------------------

    def insert_row(self, table: str, row: tuple[Any, ...]) -> None:
        columns = PROJECTIONS[table]
        marks = ", ".join("%s" for _ in columns)
        self._execute(f"INSERT INTO {self._s}.{table} ({', '.join(columns)}) VALUES ({marks})", row)

    def rows(self, table: str) -> list[tuple[Any, ...]]:
        columns = PROJECTIONS[table]
        found = self._execute(f"SELECT {', '.join(columns)} FROM {self._s}.{table}").fetchall()
        return self.sort_rows([tuple(r) for r in found])

    # -- intake ---------------------------------------------------------------

    def submit(self, data: bytes) -> int:
        (found,) = self._execute(f"SELECT {self._s}.submit(%s)", (data,)).fetchone()
        return int(found)

    def intake(self) -> Iterator[tuple[int, bytes]]:
        rows = self._execute(f"SELECT id, body FROM {self._s}.intake ORDER BY id").fetchall()
        for row in rows:
            yield int(row[0]), bytes(row[1])

    def close(self) -> None:
        self._conn.close()

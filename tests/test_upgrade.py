"""Every 0.x store, upgraded in place (§11 Phase 6), on SQLite and PostgreSQL.

Each of ``tests/upgrades/<version>/`` was written by that release's own code
(``scripts/make_upgrade_stores.py``): its store, its ledger, its chains and
receipts. On SQLite the store is the file that release wrote; on PostgreSQL,
that release's schema, created by its own statements, holding the same rows.
This release opens each, upgrading it in place, and then:

- the log verifies, with its anchors, and every projection agrees with the
  fold of the log (F10-1);
- this release decides and writes on it: a revocation, or an amendment;
- the lineage exports to a bundle that verifies offline, V1 to V12. A plan
  0.2 or 0.3 ran outside a bridge (there was none) is named by V7, and
  nothing else; 0.4's passthrough memos, which did not name their session,
  are explained as that release wrote them.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from agentgov import BudgetManager
from agentgov.receipts import ReceiptLog
from agentgov.receipts.signing import verifier_of
from interlock.records import Keyring, RecordLog

from fiduciary.authority import Authority
from fiduciary.binding import Binder
from fiduciary.bundle import Evidence, export_bundle, verify_bundle
from fiduciary.bundle.export import spec
from fiduciary.config import load_tool
from fiduciary.documents import Mandate, ToolBinding
from fiduciary.log import Log
from fiduciary.postgres import SCHEMA_VERSION, PostgresStore, install, roles
from fiduciary.sqlite import SqliteStore
from fiduciary.store import Store
from fiduciary.verify import verify_store
from tests import bound, deploy
from tests import bundle_kit as bk
from tests import examples as e
from tests.conftest import LiveClock, approve, need_postgres

UPGRADES = Path(__file__).with_name("upgrades")
VERSIONS = sorted(p.name for p in UPGRADES.iterdir() if (p / "manifest.json").exists())


@pytest.fixture(params=VERSIONS)
def recorded(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    """The release's files, copied: the store and ledger loaded from their dumps."""
    source = UPGRADES / str(request.param)
    shutil.copytree(source, tmp_path / "recorded")
    found = tmp_path / "recorded"
    for name in ("fiduciary", "ledger"):
        with sqlite3.connect(found / f"{name}.db") as conn:
            conn.executescript((found / f"{name}.sql").read_text())
    return found


@pytest.fixture(params=["sqlite", "postgres"])
def upgraded(request: pytest.FixtureRequest, recorded: Path) -> Iterator[Store]:
    """The release's store, opened by this one."""
    if request.param == "sqlite":
        opened: Store = SqliteStore(recorded / "fiduciary.db")
        yield opened
        opened.close()
        return
    import psycopg

    dsn = need_postgres()
    schema = f"fid_{uuid.uuid4().hex[:12]}"
    statements = json.loads((recorded / "postgres.json").read_text())
    try:
        with psycopg.connect(dsn, autocommit=True) as admin:
            with admin.transaction():
                for statement in statements:  # that release's own install
                    admin.execute(statement.replace("fid_upgrade", schema))
                _copy_rows(recorded / "fiduciary.db", admin, schema)
            install(admin, schema=schema)  # this release's, in place
        opened = PostgresStore(dsn, schema=schema)
        yield opened
        opened.close()
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            for role in roles(schema):
                admin.execute(f"DROP OWNED BY {role}")
                admin.execute(f"DROP ROLE IF EXISTS {role}")


def _copy_rows(source: Path, admin: Any, schema: str) -> None:
    """Every row of the release's SQLite store, into its PostgreSQL schema."""
    with sqlite3.connect(source) as lite:
        tables = [
            str(r[0])
            for r in lite.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in tables:
            columns = [str(r[1]) for r in lite.execute(f"PRAGMA table_info({table})")]
            names = ", ".join(columns)
            marks = ", ".join("%s" for _ in columns)
            rows = lite.execute(f"SELECT {names} FROM {table}").fetchall()
            if table == "meta":
                admin.execute(f"DELETE FROM {schema}.meta")
            for row in rows:
                admin.execute(
                    f"INSERT INTO {schema}.{table} ({names}) VALUES ({marks})",
                    row,
                )
    # The ids were the release's: its next submission follows them.
    admin.execute(
        f"SELECT setval(pg_get_serial_sequence('{schema}.intake', 'id'), "
        f"(SELECT max(id) FROM {schema}.intake))"
    )


def test_a_0x_store_upgrades_in_place_and_verifies(upgraded: Store, recorded: Path) -> None:
    manifest = json.loads((recorded / "manifest.json").read_text())
    if isinstance(upgraded, SqliteStore):
        with sqlite3.connect(upgraded.path) as conn:
            (found,) = conn.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
    else:
        assert isinstance(upgraded, PostgresStore)
        row = upgraded.connection.execute(
            f"SELECT value FROM {upgraded.schema}.meta WHERE key = 'schema'"
        ).fetchone()
        assert row is not None
        found = row[0]
    assert int(found) == SCHEMA_VERSION
    governor = BudgetManager.open_sqlite(str(recorded / "ledger.db"))
    try:
        _verified(upgraded, governor, manifest["charter"])
        clock = LiveClock()
        log = Log(upgraded, e.SERVICE, ledger=governor, clock=clock)
        authority = Authority(log, pinned=manifest["charter"])
        deployment = _deployment(recorded, governor)
        state = log.state()
        head = authority.decide.head_mandate(state, manifest["mandate_id"])
        if "child_id" in manifest:  # bound by 0.2 and later: revoked here, by this release
            approve(upgraded, ("cfo", "head-ops"), "revocation", head.digest, **bound.window(clock))
            binder = Binder(authority, governor, deployment=deployment)
            kinds = [r.kind for r in binder.revoke(head.digest)]
            assert kinds == ["mandate.revoked"], kinds
        else:  # issued by 0.1: amended here
            raw = {**head.body(), "version": 2, "prev": head.digest}
            raw["data"] = {**raw["data"], "max_rows": 10}
            amended = Mandate.from_json(raw, signed=False).signed(e.signer("cfo"))
            authority.propose(amended)
            approve(upgraded, ("cfo", "head-ops"), "mandate", amended.digest, **bound.window(clock))
            assert authority.issue_mandate(amended.digest).kind == "mandate.issued"
        _verified(upgraded, governor, manifest["charter"])

        data = export_bundle(
            upgraded,
            manifest["mandate_id"],
            deployment=deployment,
            evidence=_evidence(recorded, governor),
        )
        report = verify_bundle(data, keys=bk.auditor_keys(), charter=manifest["charter"])
        if manifest["version"] in ("0.2.0", "0.3.0"):
            # Before the bridge (0.4), an agent's plan went to its engine
            # directly: V7 names it, as it names any plan no pinned tool's
            # call admitted (F2-1). Everything else verifies.
            (finding,) = report.findings
            assert (finding.check, finding.kind) == ("V7", "ambient"), report.render()
            assert "no pinned tool's call" in finding.message
            assert report.exit_code == 7
        else:
            assert report.passed, report.render()
    finally:
        governor.close()


def _verified(store: Store, governor: BudgetManager, charter: str) -> None:
    governor.refresh()
    report = verify_store(
        store, service_key=e.SERVICE.public_key(), pinned=charter, ledger=governor.audit_trail()
    )
    assert report.ok, report.problems


def _deployment(recorded: Path, governor: BudgetManager) -> Any:
    """The deployment the release bound to: its tools, as it pinned them."""
    tools: dict[str, ToolBinding] = {}
    folder = recorded / "tools"
    for path in sorted(folder.glob("*.json")) if folder.exists() else []:
        binding = load_tool(str(path))
        tools[binding.digest] = binding
    found = {"tools": tools} if tools else {}
    return deploy.deployment(ledger=governor.entry(1).entry_hash, **found)


def _evidence(recorded: Path, governor: BudgetManager) -> Evidence:
    service = e.SERVICE.public_key()
    operators = recorded / "operators.jsonl"
    receipts = recorded / "receipts.jsonl"
    return Evidence(
        ledger=governor,
        ledger_signer=bk.LEDGER_KEY,
        receipts=(
            ReceiptLog("fiduciary-receipts", bk.RECEIPTS_KEY, path=receipts)
            if receipts.exists()
            else None
        ),
        receipt_keys=[verifier_of(bk.RECEIPTS_KEY)],
        operator_records=(
            RecordLog.load(operators, Keyring({service.key_id: service}))
            if operators.exists()
            else ()
        ),
        operator_keys={"fiduciary": spec(service)},
        runtime_key=service,
        chains=recorded / "chains",
        service=e.SERVICE,
    )

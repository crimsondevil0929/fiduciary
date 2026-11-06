"""``fiduciary init --substrate`` (1.9.0): a starter deployment for a system the agent
changes directly, run as written with ``fiduciary mcp serve``.

Each test points init at a live system (a SQLite file, a Git repository, a
loopback Kubernetes API server, a loopback FHIR server, PostgreSQL, a Terraform
configuration), then calls the starter tools through MCP: one change commits, and
one whose measured change goes past the mandate is refused with nothing changed.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from fiduciary import cli
from fiduciary.init import Target, init
from fiduciary.starters import sqlite_tables, terraform_config

BIN = Path(sys.executable).parent


class Serve:
    """``fiduciary mcp serve`` over its pipes."""

    def __init__(self, root: Path, scope: str) -> None:
        command = [
            str(BIN / "fiduciary"),
            "--config",
            str(root / "fiduciary.toml"),
            "mcp",
            "serve",
            "--mandate",
            scope,
        ]
        self.process = subprocess.Popen(  # noqa: S603 - the venv's own fiduciary
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        self.n = 0
        self.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        assert self.process.stdin is not None and self.process.stdout is not None
        self.n += 1
        message = {"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}}
        self.process.stdin.write(json.dumps(message).encode() + b"\n")
        self.process.stdin.flush()
        while True:
            line = self.process.stdout.readline()
            if not line:
                err = self.process.stderr.read().decode() if self.process.stderr else ""
                raise AssertionError(f"mcp serve closed its pipe: {err[-2000:]}")
            reply = json.loads(line)
            if reply.get("id") == self.n:
                assert "error" not in reply, reply
                found: dict[str, Any] = reply["result"]
                return found

    def tools(self) -> list[str]:
        return [t["name"] for t in self.call("tools/list")["tools"]]

    def tool(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        result = self.call("tools/call", {"name": name, "arguments": arguments})
        return result["content"][0]["text"], bool(result.get("isError"))

    def close(self) -> None:
        if self.process.stdin is not None:
            self.process.stdin.close()
        self.process.wait(timeout=60)


@pytest.fixture
def serve() -> Iterator[list[Serve]]:
    opened: list[Serve] = []
    yield opened
    for found in opened:
        found.close()


def _open(opened: list[Serve], root: Path, scope: str) -> Serve:
    found = Serve(root, scope)
    opened.append(found)
    return found


SHOP = """
CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT,
                        credit NUMERIC NOT NULL DEFAULT 0, score REAL, photo BLOB);
CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL,
                     status TEXT NOT NULL, total NUMERIC NOT NULL, paid BOOLEAN, at DATETIME);
CREATE TABLE audit (at TEXT, what TEXT);
CREATE TRIGGER orders_audit AFTER UPDATE OF status ON orders
BEGIN INSERT INTO audit VALUES ('now', 'order ' || NEW.id); END;
INSERT INTO customers VALUES (1, 'Ada', 'ada@example.com', '10.00', 0.5, NULL);
INSERT INTO orders VALUES (1, 1, 'open', '25.00', 0, NULL);
"""


def _shop(path: Path) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(SHOP)
        conn.commit()


def test_a_sqlite_database_gets_insert_and_update_tools(tmp_path: Path, serve: list[Serve]) -> None:
    database = tmp_path / "shop.db"
    _shop(database)
    tables = {t.name: t for t in sqlite_tables(database)}
    assert tables["orders"].key == "id" and tables["audit"].key is None
    kinds = {c.name: c.schema for c in tables["customers"].columns}
    assert kinds["score"] is None and kinds["photo"] is None, "a float and a blob"
    assert kinds["credit"] is not None and kinds["credit"]["type"] == "string"

    report = init(tmp_path / "desk", target=Target("sqlite", database=str(database)))
    assert report.substrate == "sqlite"
    assert {t["name"] for t in report.tools} == {
        "insert_audit",
        "insert_customers",
        "update_customers",
        "insert_orders",
        "update_orders",
    }
    left = {s["name"] for s in report.skipped}
    assert {"customers.score", "customers.photo", "update_audit", "delete_orders"} <= left
    config = (tmp_path / "desk" / "interlock.toml").read_text()
    assert 'primary_key = "rowid"' in config, "a table with no key is observed by its rowid"
    assert cli.main(["--config", str(tmp_path / "desk" / "fiduciary.toml"), "verify"]) == 0

    mcp = _open(serve, tmp_path / "desk", "desk-agent")
    assert "delete_orders" not in mcp.tools()
    text, error = mcp.tool(
        "update_customers", {"id": 1, "name": None, "email": "ada@new.example", "credit": None}
    )
    assert not error and text.startswith("Committed"), text
    text, error = mcp.tool(
        "update_orders",
        {
            "id": 1,
            "customer_id": None,
            "status": "shipped",
            "total": None,
            "paid": None,
            "at": None,
        },
    )
    assert error and "blast_radius" in text, "the trigger's audit row is measured"
    text, error = mcp.tool(
        "insert_orders",
        {"customer_id": 1, "status": "open", "total": "9.99", "paid": None, "at": None},
    )
    assert not error, text
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("SELECT email FROM customers WHERE id = 1").fetchone() == (
            "ada@new.example",
        )
        assert conn.execute("SELECT status FROM orders WHERE id = 1").fetchone() == ("open",)
        assert conn.execute("SELECT count(*) FROM orders").fetchone() == (2,)
        assert conn.execute("SELECT count(*) FROM audit").fetchone() == (0,)


def _git(*args: str, cwd: Path) -> str:
    done = subprocess.run(  # noqa: S603 - git, with our own arguments
        ["git", *args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def test_a_git_repository_gets_file_tools(tmp_path: Path, serve: list[Serve]) -> None:
    bare = tmp_path / "app.git"
    _git("init", "--bare", "-b", "main", str(bare), cwd=tmp_path)
    work = tmp_path / "work"
    _git("clone", str(bare), str(work), cwd=tmp_path)
    (work / "src").mkdir()
    (work / "src" / "app.py").write_text("print('hello')\n")
    _git("add", ".", cwd=work)
    _git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-m", "init", cwd=work)
    _git("push", "origin", "HEAD:main", cwd=work)

    report = init(tmp_path / "desk", target=Target("git", repository=bare))
    assert [t["name"] for t in report.tools] == ["write_file", "replace_in_file"]
    assert [s["name"] for s in report.skipped] == ["delete_file"]
    mcp = _open(serve, tmp_path / "desk", "desk-agent")
    text, error = mcp.tool("replace_in_file", {"path": "src/app.py", "old": "hello", "new": "hi"})
    assert not error, text
    text, error = mcp.tool("write_file", {"path": ".github/workflows/ci.yml", "text": "on: push\n"})
    assert error and "table_allowlist" in text, "a write to CI is outside the mandate"
    shown = _git("show", "main:src/app.py", cwd=bare)
    assert shown == "print('hi')"
    with pytest.raises(subprocess.CalledProcessError):
        _git("show", "main:.github/workflows/ci.yml", cwd=bare)


def test_a_kubernetes_namespace_gets_scale_and_image_tools(
    tmp_path: Path, serve: list[Serve]
) -> None:
    from interlock.sim.kubernetes import LoopbackCluster

    cluster = LoopbackCluster(tokens={"agent-token": "system:serviceaccount:shop:agent"})
    cluster.start()
    try:
        cluster.seed(
            "deployments",
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": "web", "namespace": "shop"},
                "spec": {
                    "replicas": 3,
                    "selector": {"matchLabels": {"app": "web"}},
                    "template": {"spec": {"containers": [{"name": "web", "image": "web:1.0"}]}},
                },
            },
        )
        token = tmp_path / "agent.token"
        token.write_text("agent-token\n")
        target = Target("kubernetes", server=cluster.url, token_file=token, namespaces=("shop",))
        report = init(tmp_path / "desk", target=target)
        assert [t["name"] for t in report.tools] == ["scale_deployment", "set_image"]
        mcp = _open(serve, tmp_path / "desk", "desk-agent")
        where = {"namespace": "shop", "deployment": "web"}
        text, error = mcp.tool("scale_deployment", {**where, "replicas": 6})
        assert not error, text
        text, error = mcp.tool("scale_deployment", {**where, "replicas": 40})
        assert error and "column_value_guard" in text, "past triple the replicas"
        text, error = mcp.tool("set_image", {**where, "image": "web:1.1"})
        assert not error, text
        live = cluster.get("deployments", "shop", "web")
        assert live is not None and live["spec"]["replicas"] == 6
        assert live["spec"]["template"]["spec"]["containers"][0]["image"] == "web:1.1"
    finally:
        cluster.stop()


def test_a_fhir_server_gets_status_tools(tmp_path: Path, serve: list[Serve]) -> None:
    from interlock.sim.fhir import LoopbackFhirServer

    server = LoopbackFhirServer()
    server.start()
    try:
        server.seed(
            {
                "resourceType": "MedicationRequest",
                "id": "mr-1",
                "status": "active",
                "intent": "order",
                "subject": {"reference": "Patient/p-1"},
                "medicationCodeableConcept": {"text": "lisinopril 10 mg"},
            }
        )
        report = init(
            tmp_path / "desk",
            target=Target("fhir", server=server.url, types=("MedicationRequest",)),
        )
        assert [t["name"] for t in report.tools] == ["set_medicationrequest_status"]
        with pytest.raises(ValueError, match="not a FHIR resource type"):
            init(tmp_path / "bad", target=Target("fhir", server=server.url, types=("x",)))
        mcp = _open(serve, tmp_path / "desk", "desk-agent")
        text, error = mcp.tool("set_medicationrequest_status", {"id": "mr-1", "status": "on-hold"})
        assert not error, text
        found = server.get("MedicationRequest", "mr-1")
        assert found is not None and found["status"] == "on-hold"
    finally:
        server.stop()


TERRAFORM = """
variable "size" {
  type    = number
  default = 1
}

variable "label" {
  type = string
  default = "a"
}

variable "tags" {
  type    = map(string)
  default = {}
}

resource "terraform_data" "box" {
  input = "${var.label}-${var.size}"
}
"""


def test_a_terraform_configuration_is_read() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        (directory / "main.tf").write_text(TERRAFORM)
        variables, types = terraform_config(directory)
    assert variables == {"size": "number", "label": "string", "tags": "map"}
    assert types == ["terraform_data"]


def _tofu() -> str | None:
    return (
        os.environ.get("INTERLOCK_TEST_TOFU") or shutil.which("tofu") or shutil.which("terraform")
    )


@pytest.mark.skipif(_tofu() is None, reason="needs OpenTofu or Terraform")
def test_a_terraform_configuration_gets_variable_tools(tmp_path: Path, serve: list[Serve]) -> None:
    tofu = _tofu()
    assert tofu is not None
    infra = tmp_path / "infra"
    infra.mkdir()
    (infra / "main.tf").write_text(TERRAFORM)
    subprocess.run([tofu, "init", "-input=false"], cwd=infra, check=True, capture_output=True)  # noqa: S603
    subprocess.run(  # noqa: S603
        [tofu, "apply", "-auto-approve", "-input=false"], cwd=infra, check=True, capture_output=True
    )
    report = init(
        tmp_path / "desk",
        target=Target("terraform", directory=infra, executable=(tofu,)),
    )
    assert [t["name"] for t in report.tools] == ["set_label", "set_size"]
    assert [s["name"] for s in report.skipped] == ["set_tags"]
    assert [n["name"] for n in report.notes] == ["interlock_stage.tf"]
    assert "interlock_stage_id" in (infra / "interlock_stage.tf").read_text()
    mcp = _open(serve, tmp_path / "desk", "desk-agent")
    text, error = mcp.tool("set_size", {"value": 2})
    assert not error, text


def test_init_needs_what_each_substrate_names(tmp_path: Path) -> None:
    for target, message in (
        (Target("sqlite"), "--database"),
        (Target("kubernetes", server="http://127.0.0.1:1"), "--token-file, --namespace"),
        (Target("fhir", server="http://127.0.0.1:1"), "--type"),
        (Target("mongo"), "no substrate"),
        (Target("sqlite", database=str(tmp_path / "none.db")), "no SQLite database"),
        (Target("git", repository=tmp_path / "none.git"), "no repository"),
        (Target("terraform", directory=tmp_path / "none"), "no directory"),
    ):
        with pytest.raises(ValueError, match=message):
            init(tmp_path / f"d-{uuid.uuid4().hex[:6]}", target=target)
    database = tmp_path / "shop.db"
    _shop(database)
    with pytest.raises(ValueError, match="no table nope"):
        init(tmp_path / "d", target=Target("sqlite", database=str(database), tables=("nope",)))
    with pytest.raises(ValueError, match="not both"):
        init(tmp_path / "e", ["x"], target=Target("sqlite", database=str(database)))


def test_the_command_writes_a_starter(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    database = tmp_path / "shop.db"
    _shop(database)
    out = tmp_path / "desk"
    argv = ["init", "--out", str(out), "--substrate", "sqlite", "--database", str(database)]
    assert cli.main([*argv, "--table", "orders"]) == 0
    printed = capsys.readouterr().out
    assert "insert_orders" in printed and "customers" not in printed.split("left out")[0]
    readme = (out / "README.md").read_text()
    assert "Before the first call" in readme and "Install Interlock's tables" not in readme
    assert json.loads((out / "init.json").read_text())["substrate"] == "sqlite"
    assert cli.main(["init", "--out", str(tmp_path / "x"), "--substrate", "fhir"]) == 2
    assert "--server, --type" in capsys.readouterr().err
    assert cli.main(["init", "--out", str(tmp_path / "y"), "--mcp", "--substrate", "git"]) == 2


@pytest.mark.skipif(
    not os.environ.get("FIDUCIARY_TEST_POSTGRES_DSN"), reason="needs FIDUCIARY_TEST_POSTGRES_DSN"
)
def test_a_postgres_schema_gets_insert_and_update_tools(tmp_path: Path, serve: list[Serve]) -> None:
    from urllib.parse import urlsplit, urlunsplit

    import psycopg

    owner = os.environ["FIDUCIARY_TEST_POSTGRES_DSN"]
    schema = f"init_{uuid.uuid4().hex[:8]}"
    role = f"{schema}_stage"
    with psycopg.connect(owner, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
        conn.execute(
            f"CREATE TABLE {schema}.accounts (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, "
            "owner text NOT NULL, balance numeric NOT NULL DEFAULT 0, ratio double precision, "
            "doc jsonb)"
        )
        conn.execute(f"CREATE TABLE {schema}.events (at timestamptz, what text)")
        conn.execute(f"INSERT INTO {schema}.accounts (owner, balance) VALUES ('ada', 10)")
        conn.execute(f"CREATE ROLE {role} LOGIN PASSWORD 'stage'")
    parts = urlsplit(owner)
    stage = urlunsplit(parts._replace(netloc=f"{role}:stage@{parts.hostname}:{parts.port}"))
    try:
        # read as the stage's role, which has no grant yet: pg_catalog shows the tables
        report = init(tmp_path / "desk", target=Target("postgres", database=stage, schema=schema))
        assert {t["name"] for t in report.tools} == {"insert_accounts", "update_accounts"}
        left = {s["name"] for s in report.skipped}
        assert {"events", "accounts.ratio", "accounts.doc", "delete_accounts"} <= left
        root = tmp_path / "desk"
        assert not (root / "back_office.db").exists()
        assert stage in (root / "fiduciary.toml").read_text()
        assert f'stage_roles = ["{role}"]' in (root / "interlock.toml").read_text()
        with psycopg.connect(owner, autocommit=True) as conn:
            conn.execute((root / "grants.sql").read_text())
        subprocess.run(  # noqa: S603 - the venv's own interlock
            [
                str(BIN / "interlock"),
                "install",
                "--config",
                str(root / "interlock.toml"),
                "--database",
                owner,
                "--key",
                str(root / "keys" / "service.key"),
            ],
            check=True,
            capture_output=True,
        )
        mcp = _open(serve, root, "desk-agent")
        text, error = mcp.tool("update_accounts", {"id": 1, "owner": None, "balance": "12.50"})
        assert not error, text
        text, error = mcp.tool("insert_accounts", {"owner": "bo", "balance": "3"})
        assert not error, text
        with psycopg.connect(owner) as conn:
            rows = conn.execute(
                f"SELECT owner, balance FROM {schema}.accounts ORDER BY id"
            ).fetchall()
        assert [(o, str(b)) for o, b in rows] == [("ada", "12.50"), ("bo", "3")]
    finally:
        for found in serve:
            found.close()
        serve.clear()
        with psycopg.connect(owner, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA {schema} CASCADE")
            conn.execute(f"DROP OWNED BY {role}")
            conn.execute(f"DROP ROLE {role}")

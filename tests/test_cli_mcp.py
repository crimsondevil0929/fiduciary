"""``fiduciary mcp serve`` (stdio and Streamable HTTP) and ``fiduciary mcp
proxy``, run as the agent's peer process from ``fiduciary.toml``: the
configuration builds Fiduciary (``Fiduciary.from_config``), the session is
recorded, and the store verifies afterwards (§8.2, §10.5, F8-11)."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from agentgov import BudgetManager

from fiduciary.sqlite import SqliteStore
from fiduciary.store import Store
from fiduciary.verify import verify_store
from tests import bound
from tests import bridge_kit as kit
from tests import examples as e
from tests.conftest import PG_DSN, Clock
from tests.test_mcp import UPSTREAM, _post

ROOT = Path(__file__).resolve().parents[1]
ENTRY = "from fiduciary.cli import main_entry; main_entry()"


@pytest.fixture
def world(store: Store, tmp_path: Path, clock: Clock) -> Iterator[bound.World]:
    yield from kit.world(store, tmp_path, clock)


def _key(path: Path, name: str) -> Path:
    path.write_text(hashlib.sha256(f"fiduciary-test/{name}".encode()).hexdigest() + "\n")
    path.chmod(0o600)
    return path


def _configure(world: bound.World) -> Path:
    """Write the deployment's files, then let go of what the test process
    holds: the engine's chain, the ledger's one writer."""
    world.issue_and_bind(kit.mandate(world))
    world.binder.close()
    world.governor.close()
    tmp = world.tmp
    tools = []
    for binding in (kit.refund(world.backend), kit.adjust(world.backend), e.lookup()):
        path = tmp / f"{binding.name}.tool.json"
        path.write_text(binding.pretty())
        tools.append(str(path))
    (tmp / "stop.txt").write_bytes(b"Stop retrying.")
    if world.backend == "sqlite":
        substrate = f'substrate = "sqlite"\ndatabase = "{world.back_office}"\n'
    else:
        substrate = f'substrate = "postgres"\ndatabase = "{world.stage_dsn}"\n'
    tables = "".join(
        f'[[tables]]\nname = "{name}"\ncolumns = {json.dumps(list(columns))}\n'
        'tenant_column = "tenant"\n'
        for name, columns in (
            ("orders", ("id", "tenant", "total", "status")),
            ("refunds", ("id", "order_id", "tenant", "amount")),
            ("items", ("id", "tenant", "sku", "qty")),
        )
    )
    sinks = (
        '[[sinks]]\nname = "stripe"\ncost_per_call = "0.01"\nidempotency = "header"\n'
        "max_payload_bytes = 65536\n"
        'operations = [{ name = "refunds.create" }, { name = "charges.create" }, '
        '{ name = "charges.capture", compensation = "refunds.create" }]\n'
        '[[sinks]]\nname = "mail"\nidempotency = "header"\nmax_payload_bytes = 65536\n'
        'operations = [{ name = "send" }]\n'
    )
    (tmp / "interlock.toml").write_text(substrate + tables + sinks)
    service = _key(tmp / "service.key", "service")
    if isinstance(world.store, SqliteStore):
        database = f'database = "{world.store.path}"\n'
    else:
        database = f'database = "{PG_DSN}"\nschema = "{world.store.schema}"\n'  # type: ignore[attr-defined]
    config = tmp / "fiduciary.toml"
    config.write_text(
        database
        + f'ledger = "{tmp / "ledger.db"}"\n'
        + f'interlock = "{tmp / "interlock.toml"}"\n'
        + f'[charter]\ndigest = "{world.charter.digest}"\n'
        + f'[service]\nkey = "{service}"\n'
        + f"[binding]\ntools = {json.dumps(tools)}\n"
        + f'directives = ["{tmp / "stop.txt"}"]\nchains = "{tmp / "chains"}"\n'
        + f'[executor]\nkey = "{service}"\n'
        + f'[runtime]\nrecords = "{tmp / "runtime.jsonl"}"\nkey = "{service}"\n'
        + f'[receipts]\nlog = "{tmp / "bridge-receipts.jsonl"}"\n'
        + '[bridge.handlers]\nlookup_order = "tests.bridge_kit:lookup"\n'
    )
    return config


def _env() -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": str(ROOT)}


def _command(config: Path, *args: str) -> list[str]:
    return [sys.executable, "-c", ENTRY, "--config", str(config), "mcp", *args]


def _run(command: list[str], data: bytes, **env: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603 - this interpreter, running fiduciary
        command,
        input=data,
        capture_output=True,
        env={**_env(), **env},
        timeout=120,
        check=False,
    )


def _messages(*calls: tuple[str, dict[str, Any]]) -> bytes:
    lines = [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ]
    for n, (name, arguments) in enumerate(calls, 1):
        params = {"name": name, "arguments": arguments}
        lines.append({"jsonrpc": "2.0", "id": n, "method": "tools/call", "params": params})
    lines.append({"jsonrpc": "2.0", "id": 99, "method": "tools/list"})
    return b"".join(json.dumps(m).encode() + b"\n" for m in lines)


def _verified(world: bound.World) -> None:
    ledger = BudgetManager.open_sqlite(str(world.tmp / "ledger.db"))
    try:
        report = verify_store(
            world.store,
            service_key=e.SERVICE.public_key(),
            pinned=world.charter.digest,
            ledger=ledger.audit_trail(),
        )
    finally:
        ledger.close()
    assert report.ok, report.problems
    kinds = [r.kind for r in world.log.records()]
    assert kinds.count("session.opened") == kinds.count("session.closed") >= 1


def test_mcp_serve_over_stdio(world: bound.World) -> None:
    config = _configure(world)
    run = _run(
        _command(config, "serve", "--mandate", bound.SCOPE, "--tenant", "acme"),
        _messages(
            ("refund_order", {"order_id": 7, "amount": "12.50", "payment_intent": "pi_0000000007"}),
            ("lookup_order", {"order_id": 7}),
            ("refund_order", {"order_id": 7, "amount": 12.5, "payment_intent": "pi_0000000007"}),
        ),
    )
    assert run.returncode == 0, run.stderr.decode()
    answers = {a["id"]: a for a in map(json.loads, run.stdout.splitlines())}
    assert answers[0]["result"]["serverInfo"]["name"] == "fiduciary"
    assert answers[1]["result"]["isError"] is False
    assert json.loads(answers[2]["result"]["content"][0]["text"])["status"] == "open"
    assert answers[3]["result"]["isError"] is True
    assert len(answers[99]["result"]["tools"]) == 5
    assert world.fetch("SELECT total FROM orders WHERE id = 7")[0][0] == 987.5
    _verified(world)


def test_mcp_serve_over_http(world: bound.World) -> None:
    config = _configure(world)
    token_file = world.tmp / "session.token"
    process = subprocess.Popen(  # noqa: S603 - this interpreter, running fiduciary
        _command(
            config,
            *("serve", "--mandate", bound.SCOPE, "--tenant", "acme"),
            *("--http", "127.0.0.1:0", "--token-file", str(token_file)),
        ),
        stderr=subprocess.PIPE,
        env=_env(),
    )
    try:
        assert process.stderr is not None
        address = None
        said: list[str] = []
        deadline = time.monotonic() + 60
        while address is None and time.monotonic() < deadline:
            line = process.stderr.readline().decode()
            said.append(line)
            assert line, f"the bridge exited before it listened: {''.join(said)}"
            if line.startswith("serving http://"):
                host, port = line.split("//")[1].split("/")[0].rsplit(":", 1)
                address = (host, int(port))
        assert address is not None
        assert oct(token_file.stat().st_mode & 0o777) == "0o600"
        token = token_file.read_text().strip()
        call = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "lookup_order", "arguments": {"order_id": 2}},
        }
        status, _, body = _post(address, call, token=token)
        assert status == 200 and body["result"]["isError"] is False
        assert _post(address, call)[0] == 401
    finally:
        process.send_signal(signal.SIGTERM)
        assert process.wait(30) == 0
    _verified(world)


def test_mcp_proxy_fronts_an_upstream(world: bound.World) -> None:
    config = _configure(world)
    log = world.tmp / "upstream.log"
    run = _run(
        _command(
            config,
            *("proxy", "--mandate", bound.SCOPE, "--tenant", "acme"),
            *("--", sys.executable, UPSTREAM),
        ),
        _messages(("lookup_order", {"order_id": 5}), ("purge_orders", {})),
        UPSTREAM_LOG=str(log),
    )
    assert run.returncode == 0, run.stderr.decode()
    answers = {a["id"]: a for a in map(json.loads, run.stdout.splitlines())}
    assert answers[1]["result"]["content"] == [{"type": "text", "text": "order 5: open"}]
    assert answers[2]["result"]["isError"] is True
    assert "purge_orders" not in {t["name"] for t in answers[99]["result"]["tools"]}
    assert [json.loads(line)["name"] for line in log.read_text().splitlines()] == ["lookup_order"]
    _verified(world)


def test_mcp_commands_refuse_what_they_cannot_serve(world: bound.World) -> None:
    config = _configure(world)
    for extra, message in (
        (["serve", "--mandate", bound.SCOPE, "--http", "nowhere"], "HOST:PORT"),
        (["serve", "--mandate", bound.SCOPE, "--http", "127.0.0.1:0", "--tls-key", "k"], "TLS"),
        (["proxy", "--mandate", bound.SCOPE], "upstream"),
        (["serve", "--mandate", "nobody"], "no lineage"),
    ):
        run = _run(_command(config, *extra), b"")
        assert run.returncode != 0 and message in run.stderr.decode(), (extra, run.stderr)

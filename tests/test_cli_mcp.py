"""``fiduciary mcp serve`` (stdio and Streamable HTTP) and ``fiduciary mcp
proxy``, run as the agent's peer process from ``fiduciary.toml``: the
configuration builds Fiduciary (``Fiduciary.from_config``), the session is
recorded, and the store verifies afterwards (§8.2, §10.5, F8-11)."""

from __future__ import annotations

import hashlib
import io
import json
import os
import signal
import ssl
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
from tests.test_mcp import UPSTREAM, _certificate, _post

ROOT = Path(__file__).resolve().parents[1]
ENTRY = "from fiduciary.cli import main_entry; main_entry()"


@pytest.fixture
def world(store: Store, tmp_path: Path, clock: Clock) -> Iterator[bound.World]:
    yield from kit.world(store, tmp_path, clock)


def _key(path: Path, name: str) -> Path:
    path.write_text(hashlib.sha256(f"fiduciary-test/{name}".encode()).hexdigest() + "\n")
    path.chmod(0o600)
    return path


def _configure(world: bound.World, *, top: str = "") -> Path:
    """Write the deployment's files, then let go of what the test process
    holds: the engine's chain, the ledger's one writer. ``top`` leads
    ``fiduciary.toml``."""
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
    outbox = world.back_office
    if world.backend != "sqlite":  # the installer's DSN, as a URI
        from psycopg.conninfo import conninfo_to_dict

        assert PG_DSN is not None
        outbox = f"{PG_DSN.rsplit('/', 1)[0]}/{conninfo_to_dict(outbox)['dbname']}"
    if isinstance(world.store, SqliteStore):
        database = f'database = "{world.store.path}"\n'
    else:
        database = f'database = "{PG_DSN}"\nschema = "{world.store.schema}"\n'  # type: ignore[attr-defined]
    config = tmp / "fiduciary.toml"
    config.write_text(
        top
        + database
        + f'ledger = "{tmp / "ledger.db"}"\n'
        + f'interlock = "{tmp / "interlock.toml"}"\n'
        + f'[charter]\ndigest = "{world.charter.digest}"\n'
        + f'[service]\nkey = "{service}"\n'
        + f"[binding]\ntools = {json.dumps(tools)}\n"
        + f'directives = ["{tmp / "stop.txt"}"]\nchains = "{tmp / "chains"}"\n'
        + f'[executor]\nkey = "{service}"\noutbox = "{outbox}"\n'
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


def _pipes(monkeypatch: pytest.MonkeyPatch, data: bytes) -> io.BytesIO:
    out = io.BytesIO()
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(data)))
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(out, write_through=True))
    return out


def test_the_commands_in_process(
    world: bound.World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The same commands, run in this process: the service turns between
    messages, the proxy's upstream, and the HTTP listener's token file."""
    from fiduciary import mcp as mcp_module
    from fiduciary.cli import main

    config = _configure(world)
    monkeypatch.setattr(mcp_module, "SERVICE_SECONDS", 0.0)
    out = _pipes(monkeypatch, _messages(("lookup_order", {"order_id": 9})))
    assert main(["--config", str(config), "mcp", "serve", "--mandate", bound.SCOPE]) == 0
    answers = {a["id"]: a for a in map(json.loads, out.getvalue().splitlines())}
    assert answers[1]["result"]["isError"] is False

    log = world.tmp / "upstream.log"
    monkeypatch.setenv("UPSTREAM_LOG", str(log))
    out = _pipes(monkeypatch, _messages(("lookup_order", {"order_id": 3})))
    command = [
        "--config",
        str(config),
        "mcp",
        "proxy",
        "--mandate",
        bound.SCOPE,
        "--tenant",
        "acme",
    ]
    assert main([*command, "--", sys.executable, UPSTREAM]) == 0
    assert json.loads(out.getvalue().splitlines()[1])["result"]["content"][0]["text"] == (
        "order 3: open"
    )

    served: list[int] = []

    def serve_once_and_stop(self: Any) -> None:
        self.start()
        token = (world.tmp / "in-process.token").read_text().strip()
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        try:
            served.append(_post(self.address, ping, token=token)[0])
        finally:
            self.stop()

    monkeypatch.setattr(mcp_module, "SERVICE_SECONDS", 0.05)
    monkeypatch.setattr(mcp_module.HttpBridge, "serve_forever", serve_once_and_stop)
    http = ["--http", "127.0.0.1:0", "--token-file", str(world.tmp / "in-process.token")]
    assert main(["--config", str(config), "mcp", "serve", "--mandate", bound.SCOPE, *http]) == 0
    assert served == [200]
    cert, key = _certificate(world.tmp)
    trusted = ssl.create_default_context(cafile=str(cert))

    def serve_over_tls(self: Any) -> None:
        self.start()
        token = (world.tmp / "tls.token").read_text().strip()
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        try:
            served.append(_post(self.address, ping, token=token, context=trusted)[0])
        finally:
            self.stop()

    monkeypatch.setattr(mcp_module.HttpBridge, "serve_forever", serve_over_tls)
    tls = ["--http", "127.0.0.1:0", "--token-file", str(world.tmp / "tls.token")]
    tls += ["--tls-cert", str(cert), "--tls-key", str(key)]
    assert main(["--config", str(config), "mcp", "serve", "--mandate", bound.SCOPE, *tls]) == 0
    assert served == [200, 200]
    monkeypatch.setattr(mcp_module.HttpBridge, "serve_forever", lambda self: self.stop())
    assert (
        main(
            [
                "--config",
                str(config),
                "mcp",
                "serve",
                "--mandate",
                bound.SCOPE,
                "--http",
                "127.0.0.1:0",
            ]
        )
        == 0
    )
    assert "bearer token" in capsys.readouterr().err
    assert (
        main(["--config", str(world.tmp / "missing.toml"), "mcp", "serve", "--mandate", "x"]) == 2
    )
    _verified(world)

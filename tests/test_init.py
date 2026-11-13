"""``fiduciary init`` (1.8.0): a deployment written for an MCP server's tools,
and run as written: ``fiduciary mcp proxy`` in front of the server, votes
signed by the principals, and ``interlock relay`` making the calls released."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from fiduciary import cli
from fiduciary.control import ConfiguredDeployment
from fiduciary.demo.mcp_desk import TOOLS
from fiduciary.init import (
    HELD,
    READ,
    WRITE,
    UnsupportedSchemaError,
    classify,
    init,
    schema_of,
    trust_of,
)
from fiduciary.keys import load_key

BIN = Path(sys.executable).parent


def desk_command(state: Path) -> list[str]:
    return [sys.executable, "-m", "fiduciary.demo.mcp_desk", "--state", str(state)]


class Client:
    """An MCP client over a child's pipes: newline-delimited JSON-RPC."""

    def __init__(self, command: list[str]) -> None:
        self.process = subprocess.Popen(  # noqa: S603 - the test's own commands
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        self.next = 0

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        assert self.process.stdin is not None and self.process.stdout is not None
        self.next += 1
        message = {"jsonrpc": "2.0", "id": self.next, "method": method, "params": params or {}}
        self.process.stdin.write(json.dumps(message).encode() + b"\n")
        self.process.stdin.flush()
        while True:
            line = self.process.stdout.readline()
            if not line:
                err = self.process.stderr.read().decode() if self.process.stderr else ""
                raise AssertionError(f"the server closed its pipe: {err[-2000:]}")
            reply = json.loads(line)
            if reply.get("id") == self.next:
                assert "error" not in reply, reply
                found: dict[str, Any] = reply["result"]
                return found

    def tool(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        result = self.call("tools/call", {"name": name, "arguments": arguments})
        return result["content"][0]["text"], bool(result.get("isError"))

    def close(self) -> None:
        if self.process.stdin is not None:
            self.process.stdin.close()
        self.process.wait(timeout=30)


@pytest.fixture
def desk(tmp_path: Path) -> Iterator[tuple[Path, Path]]:
    state = tmp_path / "desk.json"
    root = tmp_path / "desk"
    report = init(root, desk_command(state))
    assert [(t["name"], t["mode"]) for t in report.tools] == [
        ("list_tickets", READ),
        ("get_ticket", READ),
        ("add_note", WRITE),
        ("close_ticket", HELD),
        ("delete_ticket", HELD),
    ]
    yield root, state


def test_tools_are_governed_by_what_the_server_says_of_them() -> None:
    by_name = {t["name"]: classify(t)[0] for t in TOOLS}
    assert by_name == {
        "list_tickets": READ,
        "get_ticket": READ,
        "add_note": WRITE,
        "close_ticket": HELD,
        "delete_ticket": HELD,
    }
    assert classify({"annotations": "odd"})[0] == HELD
    # 1.16.0: what a read tool returns is untrusted input unless the server says its
    # world is closed (MCP's default is an open one).
    assert trust_of(TOOLS[0]) == (
        "untrusted",
        "its results may come from anyone (MCP's default is an open world)",
    )
    assert trust_of({"annotations": {"openWorldHint": True}})[0] == "untrusted"  # type: ignore[index]
    assert trust_of({"annotations": {"readOnlyHint": True, "openWorldHint": False}}) is None
    assert trust_of({"annotations": "odd"}) is not None
    for schema, message in (
        ({"type": "number"}, "FIDU1 holds no floats"),
        ({"type": "object", "properties": {"x": {"format": "date"}}}, "would not enforce"),
        ({"type": "object", "properties": {"x": {"type": "string"}}, "minimum": 1.5}, "float"),
        ({"type": "decimal"}, "outside the binding grammar"),
        ({"type": 3}, "no type"),
        ([], "not an object"),
        ({"properties": []}, "not an object"),
    ):
        with pytest.raises(UnsupportedSchemaError, match=message):
            schema_of(schema)
    assert schema_of({"$schema": "x", "type": "object", "default": {}}) == {"type": "object"}


def test_a_tool_the_grammar_cannot_hold_is_left_out_and_named(tmp_path: Path) -> None:
    tools = [
        {
            "name": "set_price",
            "inputSchema": {"type": "object", "properties": {"price": {"type": "number"}}},
        },
        {"name": "Bad-Name", "inputSchema": {"type": "object"}},
        TOOLS[0],
    ]
    report = init(tmp_path / "d", None, discovered=tools, name="pricing")
    assert [t["name"] for t in report.tools] == ["list_tickets"]
    assert "its results count as untrusted input" in report.tools[0]["why"]
    closed = {**TOOLS[0], "annotations": {"readOnlyHint": True, "openWorldHint": False}}
    inside = init(tmp_path / "c", None, discovered=[closed], name="closed")
    assert "untrusted" not in inside.tools[0]["why"]
    assert {s["name"] for s in report.skipped} == {"set_price", "Bad-Name"}
    readme = (tmp_path / "d" / "README.md").read_text()
    assert "Left out" in readme and "set_price" in readme
    with pytest.raises(ValueError, match="not empty"):
        init(tmp_path / "d", None, discovered=[])
    with pytest.raises(ValueError, match="three principals"):
        init(tmp_path / "e", None, discovered=[], principals=("a", "b"))
    with pytest.raises(ValueError, match="lowercase identifier"):
        init(tmp_path / "f", None, discovered=[], name="Not OK")


def test_a_deployment_with_no_tools_is_written_and_verifies(tmp_path: Path) -> None:
    report = init(tmp_path / "bare", None, name="bare")
    assert report.tools == [] and report.scope == "bare-agent"
    assert cli.main(["--config", str(tmp_path / "bare" / "fiduciary.toml"), "verify"]) == 0


def _vote(root: Path, desk: ConfiguredDeployment, ticket: str, principal: str) -> None:
    draft = desk.draft({"ticket_id": ticket, "principal": principal, "option": "release"})
    signature = load_key(root / "keys" / f"{principal}.key").sign(
        bytes.fromhex(draft["signing_input"])
    )
    desk.accept(
        {"approval": draft["approval"], "key_id": draft["key_id"], "signature": signature.hex()}
    )


def _relay(root: Path) -> str:
    done = subprocess.run(  # noqa: S603 - the venv's own interlock
        [
            str(BIN / "interlock"),
            "relay",
            "--config",
            str(root / "interlock.toml"),
            "--once",
            "--relay-id",
            "relay-a",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_the_deployment_runs_as_written(desk: tuple[Path, Path], tmp_path: Path) -> None:
    root, state = desk
    config = root / "fiduciary.toml"
    proxy = Client(
        [
            str(BIN / "fiduciary"),
            "--config",
            str(config),
            "mcp",
            "proxy",
            "--mandate",
            "desk-agent",
            "--",
            *desk_command(state),
        ]
    )
    try:
        proxy.call(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        )
        listed = {t["name"]: t for t in proxy.call("tools/list")["tools"]}
        assert set(listed) >= {
            "list_tickets",
            "get_ticket",
            "add_note",
            "close_ticket",
            "delete_ticket",
        }
        assert listed["add_note"]["inputSchema"]["required"] == ["note", "ticket"]

        text, error = proxy.tool("list_tickets", {})
        assert not error and "T-2" in text  # a read: called directly, metered

        text, error = proxy.tool("add_note", {"ticket": "T-1", "note": "customer called back"})
        assert not error, text
        text, error = proxy.tool("delete_ticket", {"ticket": "T-2"})
        assert not error, text  # committed to the outbox, held for a warrant
        text, error = proxy.tool("delete_ticket", {"ticket": "nope"})
        assert error  # the binding's schema refuses it before anything is built

        _relay(root)  # the note is made; the deletion waits
        tickets = json.loads(state.read_text())
        assert tickets["T-1"]["notes"] == ["customer called back"] and "T-2" in tickets

        desk_view = ConfiguredDeployment("desk", config, scratch=tmp_path / "scratch", accepts=True)
        (tmp_path / "scratch").mkdir()
        opened: list[dict[str, Any]] = []
        for _ in range(25):
            proxy.call("ping")  # the proxy's loop turns between messages: the call is ticketed
            opened = [t for t in desk_view.tickets() if t["status"] == "open"]
            if opened:
                break
            time.sleep(0.2)
        (ticket,) = opened
        assert ticket["class"] == "held"
        full = desk_view.ticket(ticket["ticket_id"])
        assert (
            full["held"][0]["operation"] == "delete_ticket" and full["held"][0]["payload_matches"]
        )
        for principal in ("approver-1", "approver-2"):
            _vote(root, desk_view, ticket["ticket_id"], principal)
        for _ in range(20):
            proxy.call("ping")  # the proxy's turn: intake recorded, warrant formed, executed
            if desk_view.ticket(ticket["ticket_id"])["status"] == "warranted":
                break
            time.sleep(0.2)
        assert desk_view.ticket(ticket["ticket_id"])["warrant"]["execution"] == "applied"
        _relay(root)
        assert "T-2" not in json.loads(state.read_text())

        profile = desk_view.profile()
        (mandate,) = profile["mandates"]
        governed = {t["name"]: t["governed"] for t in mandate["tools"]}
        assert governed == {
            "add_note": "write · released",
            "close_ticket": "held · warrant",
            "delete_ticket": "held · warrant",
            "get_ticket": "read · metered",
            "list_tickets": "read · metered",
        }
        assert [r["rung"] for r in profile["receipts"]][:2] == ["R", "R"]
        desk_view.close()
    finally:
        proxy.close()


def test_init_prints_the_proxy_for_a_server_it_governs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An MCP server's deployment runs under ``mcp proxy`` (README.md, 1.11.0)."""
    command = desk_command(tmp_path / "state.json")
    assert cli.main(["init", "--out", str(tmp_path / "desk"), "--mcp", "--", *command]) == 0
    printed = capsys.readouterr().out.splitlines()
    (serve,) = [line for line in printed if line.startswith("serve ")]
    assert " mcp proxy --mandate desk-agent -- " in serve and serve.endswith(command[-1])
    assert any(line.startswith("relay ") for line in printed)
    from fiduciary.bundle.export import spec
    from fiduciary.keys import load_key

    relay = spec(load_key(tmp_path / "desk" / "keys" / "relay-a.key").public_key())
    assert json.loads((tmp_path / "desk" / "pinned-keys.json").read_text())["relays"] == [relay]
    assert cli.main(["init", "--out", str(tmp_path / "bare")]) == 0
    (serve,) = [line for line in capsys.readouterr().out.splitlines() if line.startswith("serve ")]
    assert serve.endswith("mcp serve --mandate bare-agent")

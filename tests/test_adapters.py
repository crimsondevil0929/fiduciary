"""Governed tools handed to agent frameworks (``fiduciary.adapters``, 1.14.0).

Each framework calls the same governed tool: a call that commits returns the tool's value,
and one the mandate refuses comes back as the tool's error, in the words written for the
agent, with nothing changed.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

import fiduciary
from fiduciary import adapters
from fiduciary.init import Target, init
from fiduciary.sdk import GovernedTool, Governor


@pytest.fixture
def gov(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    root = tmp_path / "helpdesk"
    root.mkdir()
    with closing(sqlite3.connect(root / "app.db")) as conn:
        conn.executescript(
            "CREATE TABLE tickets (id INTEGER PRIMARY KEY, status TEXT NOT NULL);"
            "INSERT INTO tickets VALUES (1, 'open'), (2, 'open');"
        )
        conn.commit()
    monkeypatch.chdir(root)
    init(root / ".fiduciary", target=Target("sqlite", database=str(root / "app.db")), solo=True)
    governor = fiduciary.open()
    yield governor
    governor.close()


def _tools(gov: Governor) -> list[GovernedTool]:
    @gov.tool
    def close_ticket(db: fiduciary.Sql, ticket_id: int) -> str:
        """Close one ticket."""
        db.execute("UPDATE tickets SET status = 'closed' WHERE id = :id", {"id": ticket_id})
        return f"ticket {ticket_id} closed"

    @gov.tool
    def close_all(db: fiduciary.Sql) -> str:
        """Close every ticket."""
        db.execute("UPDATE tickets SET status = 'closed'")
        return "all closed"

    return [close_ticket, close_all]


def test_mcp_lists_and_calls_the_governed_tools(gov: Governor) -> None:
    server = adapters.mcp_server(_tools(gov))

    def rpc(method: str, params: dict[str, Any]) -> Any:
        answer = server.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        assert answer is not None and "result" in answer, answer
        return answer["result"]

    listed = rpc("tools/list", {})["tools"]
    assert [t["name"] for t in listed] == ["close_ticket", "close_all"]
    assert listed[0]["inputSchema"]["required"] == ["ticket_id"]
    done = rpc("tools/call", {"name": "close_ticket", "arguments": {"ticket_id": 1}})
    assert done == {"content": [{"type": "text", "text": "ticket 1 closed"}], "isError": False}
    refused = rpc("tools/call", {"name": "close_all", "arguments": {}})
    assert refused["isError"] is True
    assert "blast_radius" in refused["content"][0]["text"]
    unknown = rpc("tools/call", {"name": "drop", "arguments": {}})
    assert unknown["isError"] is True


def test_mcp_serves_over_stdio(gov: Governor) -> None:
    import io

    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "close_ticket", "arguments": {"ticket_id": 2}},
        },
    ]
    reader = io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in lines))
    writer = io.BytesIO()
    adapters.serve_mcp(_tools(gov), reader, writer)
    answers = [json.loads(line) for line in writer.getvalue().splitlines()]
    assert answers[1]["result"]["content"][0]["text"] == "ticket 2 closed"


def test_the_claude_agent_sdk_calls_the_governed_tools(gov: Governor) -> None:
    sdk = pytest.importorskip("claude_agent_sdk")
    close_ticket, close_all = adapters.claude_agent_tools(_tools(gov))
    assert isinstance(close_ticket, sdk.SdkMcpTool) and close_ticket.name == "close_ticket"
    assert close_ticket.input_schema["required"] == ["ticket_id"]
    done = asyncio.run(close_ticket.handler({"ticket_id": 1}))
    assert done == {"content": [{"type": "text", "text": "ticket 1 closed"}], "is_error": False}
    refused = asyncio.run(close_all.handler({}))
    assert refused["is_error"] and "blast_radius" in refused["content"][0]["text"]
    server = adapters.claude_agent_server(_tools_again(gov), name="helpdesk")
    assert server["name"] == "helpdesk"


def _tools_again(gov: Governor) -> list[GovernedTool]:
    return list(gov.tools.values())


def test_the_openai_agents_sdk_calls_the_governed_tools(gov: Governor) -> None:
    agents = pytest.importorskip("agents")
    close_ticket, close_all = adapters.openai_agents_tools(_tools(gov))
    assert isinstance(close_ticket, agents.FunctionTool)
    assert close_ticket.params_json_schema["required"] == ["ticket_id"]
    said = asyncio.run(close_ticket.on_invoke_tool(None, json.dumps({"ticket_id": 2})))
    assert said == "ticket 2 closed"
    refused = asyncio.run(close_all.on_invoke_tool(None, "{}"))
    assert "blast_radius" in refused
    assert "not JSON" in asyncio.run(close_ticket.on_invoke_tool(None, "{"))


def test_langchain_and_langgraph_call_the_governed_tools(gov: Governor) -> None:
    core = pytest.importorskip("langchain_core.tools")
    close_ticket, close_all = adapters.langchain_tools(_tools(gov))
    assert isinstance(close_ticket, core.StructuredTool)
    assert close_ticket.invoke({"ticket_id": 1}) == "ticket 1 closed"
    refused = close_all.invoke({})
    assert "blast_radius" in refused  # handed to the model as the tool's result

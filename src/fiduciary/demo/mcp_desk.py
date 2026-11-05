"""A small MCP server over stdio: a support desk's tickets, for ``fiduciary init --mcp``.

    python -m fiduciary.demo.mcp_desk --state desk.json

It speaks newline-delimited JSON-RPC (MCP 2025-06-18) and keeps its tickets in the JSON
file ``--state`` names, so another process sees what a call changed. Its tools carry the
annotations MCP defines, which ``fiduciary init`` reads to decide how each is governed:

- ``list_tickets``, ``get_ticket``: ``readOnlyHint``;
- ``add_note``: a write, ``destructiveHint: false``;
- ``close_ticket``: a write with no hints, so destructive by MCP's default;
- ``delete_ticket``: ``destructiveHint: true``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Final

SEED: Final = {
    "T-1": {"subject": "Refund not received", "status": "open", "notes": []},
    "T-2": {"subject": "Duplicate account", "status": "open", "notes": []},
    "T-3": {"subject": "Password reset loop", "status": "open", "notes": []},
}
_ID: Final = {"type": "string", "pattern": "^T-[0-9]{1,6}$", "description": "A ticket id, T-<n>."}
TOOLS: Final = [
    {
        "name": "list_tickets",
        "description": "List every ticket: its id, subject and status.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"title": "List tickets", "readOnlyHint": True},
    },
    {
        "name": "get_ticket",
        "description": "Read one ticket, with its notes.",
        "inputSchema": {
            "type": "object",
            "properties": {"ticket": _ID},
            "required": ["ticket"],
            "additionalProperties": False,
        },
        "annotations": {"title": "Read a ticket", "readOnlyHint": True},
    },
    {
        "name": "add_note",
        "description": "Add an internal note to a ticket.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticket": _ID,
                "note": {"type": "string", "minLength": 1, "maxLength": 500},
            },
            "required": ["ticket", "note"],
            "additionalProperties": False,
        },
        "annotations": {"title": "Add a note", "readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "close_ticket",
        "description": "Close a ticket, with the customer told it is resolved.",
        "inputSchema": {
            "type": "object",
            "properties": {"ticket": _ID},
            "required": ["ticket"],
            "additionalProperties": False,
        },
    },
    {
        "name": "delete_ticket",
        "description": "Delete a ticket and its history, for good.",
        "inputSchema": {
            "type": "object",
            "properties": {"ticket": _ID},
            "required": ["ticket"],
            "additionalProperties": False,
        },
        "annotations": {"title": "Delete a ticket", "destructiveHint": True},
    },
]


class Desk:
    """The tickets, kept in a JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        if not path.exists():
            path.write_text(json.dumps(SEED, indent=2) + "\n")

    def load(self) -> dict[str, Any]:
        found: dict[str, Any] = json.loads(self.path.read_text())
        return found

    def save(self, tickets: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(tickets, indent=2, sort_keys=True) + "\n")

    def call(self, tool: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        tickets = self.load()
        if tool == "list_tickets":
            rows = [
                {"id": k, "subject": v["subject"], "status": v["status"]}
                for k, v in sorted(tickets.items())
            ]
            return {"tickets": rows}, False
        ticket = str(arguments.get("ticket", ""))
        if ticket not in tickets:
            return {"error": f"there is no ticket {ticket}"}, True
        if tool == "get_ticket":
            return {"id": ticket, **tickets[ticket]}, False
        if tool == "add_note":
            tickets[ticket]["notes"].append(str(arguments["note"]))
        elif tool == "close_ticket":
            tickets[ticket]["status"] = "closed"
        elif tool == "delete_ticket":
            del tickets[ticket]
        else:
            return {"error": f"no tool {tool}"}, True
        self.save(tickets)
        return {"id": ticket, "done": tool}, False


def handle(desk: Desk, message: dict[str, Any]) -> dict[str, Any] | None:
    method, ident = message.get("method"), message.get("id")
    if ident is None:
        return None  # a notification
    if method == "initialize":
        result: dict[str, Any] = {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "support-desk", "version": "1.0.0"},
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = message.get("params") or {}
        found, error = desk.call(str(params.get("name")), dict(params.get("arguments") or {}))
        result = {
            "content": [{"type": "text", "text": json.dumps(found, sort_keys=True)}],
            "structuredContent": found,
            "isError": error,
        }
    elif method == "ping":
        result = {}
    else:
        return {
            "jsonrpc": "2.0",
            "id": ident,
            "error": {"code": -32601, "message": "no such method"},
        }
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mcp_desk", description=__doc__.splitlines()[0])
    parser.add_argument("--state", required=True, help="the JSON file the tickets live in")
    args = parser.parse_args(argv)
    desk = Desk(Path(args.state))
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        reply = handle(desk, message) if isinstance(message, dict) else None
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

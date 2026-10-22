"""An upstream MCP server for the proxy tests, speaking newline-delimited
JSON-RPC on stdio.

It offers two tools: ``lookup_order``, which a mandate pins as a passthrough
binding, and ``purge_orders``, which none does. It appends every tool call
it receives to the file ``UPSTREAM_LOG`` names, so a test can show that a call
the proxy refuses never reached it.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

TOOLS = [
    {
        "name": "lookup_order",
        "description": "Upstream's own description, which the proxy does not show.",
        "inputSchema": {"type": "object"},
    },
    {
        "name": "purge_orders",
        "description": "Delete every order.",
        "inputSchema": {"type": "object"},
    },
]


def answer(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    if "id" not in message:
        return None
    ident = message["id"]
    if method == "initialize":
        result: dict[str, Any] = {
            "protocolVersion": message["params"]["protocolVersion"],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "upstream", "version": "1"},
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = message["params"]
        with open(os.environ["UPSTREAM_LOG"], "a", encoding="utf-8") as log:
            log.write(json.dumps(params, sort_keys=True) + "\n")
        order = params["arguments"].get("order_id")
        result = {"content": [{"type": "text", "text": f"order {order}: open"}], "isError": False}
    else:
        return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": "no method"}}
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def main() -> None:
    for line in sys.stdin:
        reply = answer(json.loads(line))
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

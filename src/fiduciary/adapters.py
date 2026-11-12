"""Governed tools, handed to an agent framework (1.14.0).

Each adapter wraps tools from :meth:`fiduciary.sdk.Governor.tool` in the framework's own
tool type. A call goes through the governed tool unchanged: the arguments are checked, the
function runs in a stage, and the plan is committed or refused. A refusal reaches the model
as the tool's error, in the words :class:`fiduciary.sdk.Refused` carries, never as an
exception in the agent loop. Any other exception propagates.

========================  =========================================  ======================
Framework                 Call                                       Needs
========================  =========================================  ======================
MCP (any client)          :func:`serve_mcp` (stdio)                   nothing more
Claude Agent SDK          :func:`claude_agent_server`                 ``claude-agent-sdk``
OpenAI Agents SDK         :func:`openai_agents_tools`                 ``openai-agents``
LangGraph, LangChain      :func:`langchain_tools`                     ``langchain-core``
========================  =========================================  ======================

The frameworks are not dependencies of fiduciary: each adapter imports its own when called,
and says what to install if it is missing.

A call runs in the caller's thread. An async framework's loop waits while the stage is
open, which is the length of one write.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from typing import IO, Any

from fiduciary.sdk import GovernedTool, describe

__all__ = [
    "claude_agent_server",
    "claude_agent_tools",
    "langchain_tools",
    "mcp_server",
    "openai_agents_tools",
    "serve_mcp",
]


def _need(module: str, package: str) -> Any:
    import importlib

    try:
        return importlib.import_module(module)
    except ImportError as exc:  # pragma: no cover - depends on what is installed
        raise ImportError(f"this adapter needs {package}: pip install {package}") from exc


# --------------------------------------------------------------------------
# MCP, over fiduciary's own server
# --------------------------------------------------------------------------


class _Tools:
    """What :class:`fiduciary.mcp.McpServer` asks of a session, for governed tools."""

    def __init__(self, tools: Sequence[GovernedTool]) -> None:
        self._tools = {t.name: t for t in tools}

    def tool_definitions(self) -> list[dict[str, Any]]:
        return describe(list(self._tools.values()))

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        from fiduciary.bridge import CallResult

        tool = self._tools.get(name)
        if tool is None:
            return CallResult(None, "rejected", "No tool of that name is served here.")
        text, error = tool.call_for_agent(arguments)
        return CallResult(name, "refused" if error else "served", text)


def mcp_server(tools: Sequence[GovernedTool]) -> Any:
    """A :class:`fiduciary.mcp.McpServer` serving ``tools``: hand it one JSON-RPC message
    at a time (``handle``), or serve it with :func:`serve_mcp`."""
    from fiduciary.mcp import McpServer

    return McpServer(_Tools(tools))  # type: ignore[arg-type]


def serve_mcp(
    tools: Sequence[GovernedTool], reader: IO[bytes] | None = None, writer: IO[bytes] | None = None
) -> None:
    """Serve ``tools`` over MCP's stdio transport until the client closes the pipe::

    claude mcp add helpdesk -- python -m my_tools   # which calls serve_mcp(...)
    """
    from fiduciary.mcp import serve_stdio

    serve_stdio(mcp_server(tools), reader or sys.stdin.buffer, writer or sys.stdout.buffer)


# --------------------------------------------------------------------------
# Claude Agent SDK
# --------------------------------------------------------------------------


def claude_agent_tools(tools: Sequence[GovernedTool]) -> list[Any]:
    """Each tool as the Claude Agent SDK's in-process MCP tool (``claude_agent_sdk.tool``)."""
    sdk = _need("claude_agent_sdk", "claude-agent-sdk")
    found = []
    for governed in tools:

        async def handler(arguments: dict[str, Any], _tool: GovernedTool = governed) -> Any:
            text, error = _tool.call_for_agent(arguments)
            return {"content": [{"type": "text", "text": text}], "is_error": error}

        found.append(sdk.tool(governed.name, governed.description, governed.input_schema)(handler))
    return found


def claude_agent_server(tools: Sequence[GovernedTool], *, name: str = "fiduciary") -> Any:
    """An in-process MCP server for ``ClaudeAgentOptions(mcp_servers={name: ...})``."""
    sdk = _need("claude_agent_sdk", "claude-agent-sdk")
    return sdk.create_sdk_mcp_server(name=name, tools=claude_agent_tools(tools))


# --------------------------------------------------------------------------
# OpenAI Agents SDK
# --------------------------------------------------------------------------


def openai_agents_tools(tools: Sequence[GovernedTool]) -> list[Any]:
    """Each tool as an OpenAI Agents SDK ``FunctionTool``, for ``Agent(tools=...)``."""
    agents = _need("agents", "openai-agents")
    found = []
    for governed in tools:

        async def invoke(context: Any, raw: str, _tool: GovernedTool = governed) -> str:
            try:
                arguments = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return "The arguments were refused: they are not JSON."
            if not isinstance(arguments, dict):
                return "The arguments were refused: they are not an object."
            text, _ = _tool.call_for_agent(arguments)
            return text

        found.append(
            agents.FunctionTool(
                name=governed.name,
                description=governed.description,
                params_json_schema=governed.input_schema,
                on_invoke_tool=invoke,
                # the schema is the tool's own, checked by the governor on every call
                strict_json_schema=False,
            )
        )
    return found


# --------------------------------------------------------------------------
# LangChain and LangGraph
# --------------------------------------------------------------------------


def langchain_tools(tools: Sequence[GovernedTool]) -> list[Any]:
    """Each tool as a LangChain ``StructuredTool``: for LangGraph's ``ToolNode`` or
    ``create_react_agent``. A refusal is a ``ToolException``, which the tool hands the
    model as its result (``handle_tool_error``)."""
    core = _need("langchain_core.tools", "langchain-core")
    found = []
    for governed in tools:

        def run(_tool: GovernedTool = governed, **arguments: Any) -> str:
            text, error = _tool.call_for_agent(arguments)
            if error:
                raise core.ToolException(text)
            return text

        found.append(
            core.StructuredTool.from_function(
                func=run,
                name=governed.name,
                description=governed.description,
                args_schema=governed.input_schema,
                handle_tool_error=True,
            )
        )
    return found

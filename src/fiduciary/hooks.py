"""A mandate in the approval hook your agent framework already has (1.20.0).

The OpenAI Agents SDK asks ``needs_approval`` before a tool runs, and runs tool guardrails
around it; the Claude Agent SDK asks ``can_use_tool`` and runs ``PreToolUse`` and
``PostToolUse`` hooks. Here each of those asks a signed mandate, so people keep their
framework and its tools, and the decisions are deterministic and on a record:

- **Before a call** (:meth:`Gate.decide`): everything a protected call passes before it runs
  (the tool is pinned, its arguments are in its schema and name no protected path, the
  velocity bound and the breaker, and what it would send, judged by the outbound rules:
  values the session read, destinations, the rule of two, the send budget). The answer is
  ``allow``, ``deny`` with the reason the model is shown, or ``ask``: a write the mandate holds
  for a person, which the framework's own approval decides.
- **After a call** (:meth:`Gate.seen`): what the tool returned, before the model sees it: a
  credential in it labels the session ``secret``, a private read is tracked, and a read past
  the read budget is withheld.

The tools are the framework's own: they run in its process, and fiduciary does not stage or
measure what they change. For that, wrap a tool with ``@gov.tool`` (:mod:`fiduciary.sdk`),
or put an MCP server behind ``fiduciary protect``. And the approval an ``ask`` gets is the
framework's: whoever answers its prompt, with no passkey and no signature. The record says a
call was asked, not who said yes.

    import fiduciary

    gate = fiduciary.Gate.for_tools(definitions)   # once: a deployment for these tools
    # OpenAI Agents SDK
    tools = fiduciary.hooks.openai_agents(tools, gate)    # or, per tool:
    send_email.needs_approval = fiduciary.needs_approval(gate, "send_email")
    # Claude Agent SDK
    options = ClaudeAgentOptions(
        can_use_tool=fiduciary.can_use_tool(gate),
        hooks=fiduciary.hooks.claude_hooks(gate),
    )
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from fiduciary.bridge import Fiduciary, Session

__all__ = [
    "Gate",
    "Verdict",
    "can_use_tool",
    "claude_hooks",
    "needs_approval",
    "openai_agents",
    "post_tool_use",
    "pre_tool_use",
    "tool_input_guardrail",
    "tool_output_guardrail",
]

ROOT: Final = Path(".fiduciary") / "hooks"
"""Where :meth:`Gate.for_tools` writes its deployment unless told otherwise."""
ASKED: Final = (
    "Not run: {why} The approval this framework asks for decides it; no one approved it here."
)


@dataclass(frozen=True, slots=True)
class Verdict:
    """What the mandate says of one call before it runs."""

    decision: str
    """``allow``, ``deny`` or ``ask``."""
    why: str = ""


class Gate:
    """A session on a deployment whose mandate pins an agent framework's own tools.

    :param fid: The deployment, open.
    :param session: The session every decision is made in.
    """

    def __init__(self, fid: Fiduciary, session: Session) -> None:
        self._fid = fid
        self._session = session
        self._lock = threading.RLock()
        self._verdicts: dict[str, Verdict] = {}
        """Each call's verdict by its call id, decided once: the hooks that ask again for
        one call are given the same answer, and nothing is counted twice."""

    @classmethod
    def open(cls, config: Path | str) -> Gate:
        """The gate over the deployment ``config`` names."""
        from fiduciary.bridge import Fiduciary

        path = Path(config)
        fid = Fiduciary.from_config(path)
        mandate = json.loads((path.parent / "mandate.json").read_text(encoding="utf-8"))
        return cls(fid, fid.session(mandate["subject"]["scope"]))

    @classmethod
    def for_tools(
        cls,
        definitions: Sequence[Mapping[str, Any]],
        *,
        root: Path | str = ROOT,
        shadow: bool = False,
        name: str | None = None,
    ) -> Gate:
        """The gate over a deployment for ``definitions`` (each tool's ``name``,
        ``description``, ``inputSchema`` and ``annotations``, as MCP lists a tool): written
        the first time, under ``root``, as ``fiduciary protect`` writes one for a server.
        Reads count as reads, a write the definition says is not destructive is allowed,
        any other write is held for a person, and a read-only claim the definition
        contradicts is not believed."""
        from fiduciary.init import init

        where = Path(root)
        if not (where / "fiduciary.toml").is_file():
            init(where, None, discovered=list(definitions), solo=True, shadow=shadow, name=name)
        return cls.open(where / "fiduciary.toml")

    @property
    def session(self) -> Session:
        return self._session

    def binding_of(self, tool: str) -> str | None:
        """The binding a framework's tool name is pinned as: its own name, or the one a
        name like ``createNote`` or ``mcp__crm__send`` became."""
        bindings = self._session._bindings()
        if tool in bindings:
            return tool
        for name, binding in bindings.items():
            if binding.upstream == tool:
                return name
        slug = re.sub(r"[^a-z0-9_]+", "_", tool.lower()).strip("_")[:64]
        return slug if slug in bindings else None

    def decide(
        self, tool: str, arguments: Mapping[str, Any], *, call_id: str | None = None
    ) -> Verdict:
        """What the mandate says of calling ``tool`` with ``arguments``, before it runs
        (module docstring). A call id given twice gets the first answer."""
        with self._lock:
            if call_id is not None and call_id in self._verdicts:
                return self._verdicts[call_id]
            name = self.binding_of(tool)
            if name is None:
                verdict = Verdict("deny", f"Not run: {tool} is not a tool this mandate grants.")
            else:
                decision, why = self._session._decide(name, arguments)
                verdict = Verdict(decision, why)
            if call_id is not None:
                self._verdicts[call_id] = verdict
            return verdict

    def known(self, tool: str) -> bool:
        """Whether the mandate pins ``tool``."""
        return self.binding_of(tool) is not None

    def seen(self, tool: str, value: Any) -> str | None:
        """What ``tool`` returned, before the model sees it (module docstring): the words to
        show instead, when the read budget withholds it."""
        name = self.binding_of(tool)
        if name is None:
            return None
        with self._lock:
            return self._session._seen(name, value)

    def close(self) -> None:
        with self._lock:
            self._session.close()
            self._fid.close()

    def __enter__(self) -> Gate:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _need(module: str, package: str) -> Any:
    import importlib

    try:
        return importlib.import_module(module)
    except ImportError as exc:  # pragma: no cover - depends on what is installed
        raise ImportError(f"this adapter needs {package}: pip install {package}") from exc


def _arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            found = json.loads(raw)
        except ValueError:
            return {}
        return found if isinstance(found, dict) else {}
    return {}


def _text(value: Any) -> Any:
    """A framework's tool result, as the gate reads it: JSON when it is JSON."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


# -- the OpenAI Agents SDK ----------------------------------------------------------------------


def needs_approval(gate: Gate, tool: str) -> Callable[[Any, dict[str, Any], str], Awaitable[bool]]:
    """``FunctionTool.needs_approval`` for ``tool``: true when the mandate holds the call for
    a person. A call it refuses needs no approval: :func:`tool_input_guardrail` refuses it."""

    async def ask(context: Any, params: dict[str, Any], call_id: str) -> bool:
        return gate.decide(tool, params, call_id=call_id).decision == "ask"

    return ask


def tool_input_guardrail(gate: Gate) -> Any:
    """A tool input guardrail that refuses what the mandate refuses, in words the model is
    shown. A call the mandate holds runs only after the framework's approval
    (:func:`needs_approval`); without that hook it is refused."""
    agents = _need("agents", "openai-agents")

    def guard(data: Any) -> Any:
        context = data.context
        verdict = gate.decide(
            context.tool_name,
            _arguments(context.tool_arguments),
            call_id=getattr(context, "tool_call_id", None),
        )
        if verdict.decision == "deny":
            return agents.ToolGuardrailFunctionOutput.reject_content(verdict.why)
        if verdict.decision == "ask":
            approved = context.is_tool_approved(context.tool_name, context.tool_call_id)
            if approved is not True:  # held, and no approval came through needs_approval
                return agents.ToolGuardrailFunctionOutput.reject_content(
                    ASKED.format(why=verdict.why)
                )
        return agents.ToolGuardrailFunctionOutput.allow()

    return agents.ToolInputGuardrail(guardrail_function=guard, name="fiduciary")


def tool_output_guardrail(gate: Gate) -> Any:
    """A tool output guardrail that reads what the tool returned before the model does:
    labels, tracked values, and the read budget, which can withhold it."""
    agents = _need("agents", "openai-agents")

    def guard(data: Any) -> Any:
        withheld = gate.seen(data.context.tool_name, _text(data.output))
        if withheld is not None:
            return agents.ToolGuardrailFunctionOutput.reject_content(withheld)
        return agents.ToolGuardrailFunctionOutput.allow()

    return agents.ToolOutputGuardrail(guardrail_function=guard, name="fiduciary")


def openai_agents(tools: Sequence[Any], gate: Gate) -> list[Any]:
    """Each of ``tools`` (OpenAI Agents SDK ``FunctionTool``) asking the mandate: its
    ``needs_approval``, and an input and an output guardrail. Tools are changed in place and
    returned."""
    found = []
    for tool in tools:
        tool.needs_approval = needs_approval(gate, tool.name)
        tool.tool_input_guardrails = [
            *(tool.tool_input_guardrails or ()),
            tool_input_guardrail(gate),
        ]
        tool.tool_output_guardrails = [
            *(tool.tool_output_guardrails or ()),
            tool_output_guardrail(gate),
        ]
        found.append(tool)
    return found


# -- the Claude Agent SDK -----------------------------------------------------------------------


Ask = Callable[[str, dict[str, Any], Verdict], Awaitable[bool]]


def can_use_tool(gate: Gate, *, ask: Ask | None = None, unknown: str = "deny") -> Any:
    """``ClaudeAgentOptions(can_use_tool=...)``: allow what the mandate allows, deny what it
    refuses with the reason Claude is shown, and for a write it holds, ``ask`` (your prompt:
    true allows it), or deny with the reason when there is none. A tool the mandate does not
    pin is denied, or with ``unknown="allow"`` left to Claude Code's own permissions."""
    sdk = _need("claude_agent_sdk", "claude-agent-sdk")

    async def decide(tool: str, arguments: dict[str, Any], context: Any) -> Any:
        if not gate.known(tool) and unknown == "allow":
            return sdk.PermissionResultAllow()
        verdict = gate.decide(tool, arguments, call_id=getattr(context, "tool_use_id", None))
        if verdict.decision == "allow":
            return sdk.PermissionResultAllow()
        if verdict.decision == "ask" and ask is not None and await ask(tool, arguments, verdict):
            return sdk.PermissionResultAllow()
        why = verdict.why if verdict.decision == "deny" else ASKED.format(why=verdict.why)
        return sdk.PermissionResultDeny(message=why)

    return decide


def pre_tool_use(gate: Gate) -> Any:
    """A ``PreToolUse`` hook: deny what the mandate refuses, ``ask`` (Claude Code's own
    prompt, or ``can_use_tool``) for a write it holds, and say nothing of what it allows, so
    Claude Code's own permissions still apply: a hook only ever narrows them. It runs for
    calls an allow rule or a permission mode would approve, too. A tool the mandate does
    not pin is left to Claude Code."""

    async def hook(input_data: Any, tool_use_id: str | None, context: Any) -> dict[str, Any]:
        tool = str(input_data.get("tool_name", ""))
        if not gate.known(tool):
            return {}
        verdict = gate.decide(tool, _arguments(input_data.get("tool_input")), call_id=tool_use_id)
        if verdict.decision == "allow":
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": verdict.decision,
                "permissionDecisionReason": verdict.why,
            }
        }

    return hook


def post_tool_use(gate: Gate) -> Any:
    """A ``PostToolUse`` hook: what the tool returned, read before Claude reasons on it; a
    read past the read budget is withheld (an MCP tool's output is replaced; any other's is
    answered with the reason)."""

    async def hook(input_data: Any, tool_use_id: str | None, context: Any) -> dict[str, Any]:
        tool = str(input_data.get("tool_name", ""))
        withheld = gate.seen(tool, input_data.get("tool_response"))
        if withheld is None:
            return {}
        specific: dict[str, Any] = {"hookEventName": "PostToolUse", "additionalContext": withheld}
        if tool.startswith("mcp__"):
            specific["updatedMCPToolOutput"] = [{"type": "text", "text": withheld}]
        return {"decision": "block", "reason": withheld, "hookSpecificOutput": specific}

    return hook


def claude_hooks(gate: Gate) -> dict[str, list[Any]]:
    """``ClaudeAgentOptions(hooks=...)``: :func:`pre_tool_use` and :func:`post_tool_use`, on
    every tool."""
    sdk = _need("claude_agent_sdk", "claude-agent-sdk")
    return {
        "PreToolUse": [sdk.HookMatcher(hooks=[pre_tool_use(gate)])],
        "PostToolUse": [sdk.HookMatcher(hooks=[post_tool_use(gate)])],
    }

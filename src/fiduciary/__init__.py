"""Signed mandates, quorum warrants and offline compliance proofs.

Fiduciary sits above AgentGov (the budget ledger) and Interlock (the
side-effect reference monitor). It turns the authority an agent acts under
into signed documents: a charter naming the principals, mandates delegating
bounds to an agent's scope, approvals a quorum of principals signs, and the
tickets and warrants by which an exception is escalated, decided and
executed exactly once. Every
document is FIDU1: canonical JSON, signed over a domain and the SHA-512 of its
bytes, so any key service signs any document. ``docs/FIDUCIARY_SPEC.md`` is
the specification.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__version__ = "1.21.0"

__all__ = [
    "Fiduciary",
    "Gate",
    "GovernedTool",
    "Governor",
    "Refused",
    "Rest",
    "Session",
    "Sql",
    "Tool",
    "Workspace",
    "__version__",
    "can_use_tool",
    "needs_approval",
    "open",
]

_SDK = frozenset({"GovernedTool", "Governor", "Refused", "Rest", "Sql", "Workspace", "open"})
_HOOKS = frozenset({"Gate", "can_use_tool", "needs_approval"})

if TYPE_CHECKING:
    from fiduciary.bridge import Fiduciary, Session
    from fiduciary.hooks import Gate, can_use_tool, needs_approval
    from fiduciary.sdk import GovernedTool, Governor, Refused, Rest, Sql, Workspace, open
    from fiduciary.tools import Tool


def __getattr__(name: str) -> Any:
    """``Fiduciary``, ``Session``, ``Tool``, the SDK (``open``, ``Sql``, ...) and the
    frameworks' hooks (``Gate``, ``needs_approval``, ``can_use_tool``), imported when first
    named: the command line's ``--version`` loads neither interlock nor AgentGov."""
    if name in _SDK:
        from fiduciary import sdk

        return getattr(sdk, name)
    if name in _HOOKS:  # 1.20.0: the agent frameworks' own approval hooks
        from fiduciary import hooks

        return getattr(hooks, name)
    if name in ("Fiduciary", "Session"):
        from fiduciary import bridge

        return getattr(bridge, name)
    if name == "Tool":
        from fiduciary.tools import Tool

        return Tool
    raise AttributeError(f"module 'fiduciary' has no attribute {name!r}")

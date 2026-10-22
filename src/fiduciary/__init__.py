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

__version__ = "0.4.0"

__all__ = ["Fiduciary", "Session", "Tool", "__version__"]

if TYPE_CHECKING:
    from fiduciary.bridge import Fiduciary, Session
    from fiduciary.tools import Tool


def __getattr__(name: str) -> Any:
    """``Fiduciary``, ``Session`` and ``Tool``, imported when first named: the
    command line's ``--version`` loads neither interlock nor AgentGov."""
    if name in ("Fiduciary", "Session"):
        from fiduciary import bridge

        return getattr(bridge, name)
    if name == "Tool":
        from fiduciary.tools import Tool

        return Tool
    raise AttributeError(f"module 'fiduciary' has no attribute {name!r}")

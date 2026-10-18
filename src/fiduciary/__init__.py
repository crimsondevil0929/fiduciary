"""Signed mandates, quorum warrants and offline compliance proofs.

Fiduciary sits above AgentGov (the budget ledger) and Interlock (the
side-effect reference monitor). It turns the authority an agent acts under
into signed documents: a charter naming the principals, mandates delegating
bounds to an agent's scope, and approvals a quorum of principals signs. Every
document is FIDU1: canonical JSON, signed over a domain and the SHA-512 of its
bytes, so any key service signs any document. ``docs/FIDUCIARY_SPEC.md`` is
the specification.
"""

from __future__ import annotations

__version__ = "0.1.0"

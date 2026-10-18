"""The charter: a deployment's root of authority (``docs/FIDUCIARY_SPEC.md`` §4.1).

Configuration pins version 1's digest. Each principal is a name and one
Ed25519 public key; the quorums say how many of them adopt the next version,
issue a root mandate, and revoke. ``deployment.ledger`` binds everything under
the charter to one AgentGov ledger, by the hash of its first entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar

from agentgov.receipts.signing import Ed25519PublicKey

from fiduciary import fidu1
from fiduciary.documents._base import Document, sorted_by
from fiduciary.exceptions import MalformedError
from fiduciary.fidu1 import Fields, Signature

__all__ = ["Charter", "Deployment", "Principal"]

MAX_PRINCIPALS = 64


@dataclass(frozen=True, slots=True)
class Principal:
    """A principal: a name, and the one key that signs for it."""

    name: str
    key: str
    """``ed25519:<64 hex>``."""

    @property
    def public_key(self) -> Ed25519PublicKey:
        return fidu1.key_of(self.key)

    def to_json(self) -> dict[str, str]:
        return {"name": self.name, "key": self.key}


@dataclass(frozen=True, slots=True)
class Deployment:
    ledger: str
    """The ``entry_hash`` of AgentGov entry 1 (F4-2)."""
    label: str

    def to_json(self) -> dict[str, str]:
        return {"ledger": self.ledger, "label": self.label}


@dataclass(frozen=True, slots=True, kw_only=True)
class Charter(Document):
    """A charter version (F4-1 to F4-6)."""

    TYPE: ClassVar[str] = "charter"
    FIELDS: ClassVar[tuple[str, ...]] = (
        "charter_id",
        "version",
        "prev",
        "deployment",
        "principals",
        "quorums",
        "break_glass",
        "issued_at",
    )

    charter_id: str
    version: int
    prev: str | None
    deployment: Deployment
    principals: tuple[Principal, ...]
    charter_quorum: int
    issue_quorum: int
    revoke_quorum: int
    break_glass: tuple[str, ...] = ()
    """Interlock operator key ids that act outside fiduciary on purpose (F4-6)."""
    issued_at: datetime
    signature: Signature | None = None

    def _body(self) -> dict[str, Any]:
        return {
            "charter_id": self.charter_id,
            "version": self.version,
            "prev": self.prev,
            "deployment": self.deployment.to_json(),
            "principals": [p.to_json() for p in self.principals],
            "quorums": {
                "charter": self.charter_quorum,
                "issue": self.issue_quorum,
                "revoke": self.revoke_quorum,
            },
            "break_glass": list(self.break_glass),
            "issued_at": fidu1.format_instant(self.issued_at),
        }

    @classmethod
    def _read(cls, fields: Fields) -> dict[str, Any]:
        deployment = fields.object("deployment", ("ledger", "label"))
        quorums = fields.object("quorums", ("charter", "issue", "revoke"))
        principals = []
        for path, raw in fields.array("principals", MAX_PRINCIPALS):
            entry = Fields(raw, path, ("name", "key"))
            principals.append(Principal(entry.name("name"), entry.key("key")))
        return {
            "charter_id": fields.uuid("charter_id"),
            "version": fields.integer("version", 1),
            "prev": None if fields.is_null("prev") else fields.hash("prev"),
            "deployment": Deployment(deployment.hash("ledger"), deployment.text("label")),
            "principals": sorted_by(principals, lambda p: (p.name,), fields.path("principals")),
            "charter_quorum": quorums.integer("charter", 1, MAX_PRINCIPALS),
            "issue_quorum": quorums.integer("issue", 1, MAX_PRINCIPALS),
            "revoke_quorum": quorums.integer("revoke", 1, MAX_PRINCIPALS),
            "break_glass": fields.strings("break_glass", fidu1.KEY_ID, "a key id"),
            "issued_at": fields.instant("issued_at"),
        }

    def _agree(self) -> None:
        if (self.version == 1) != (self.prev is None):
            raise MalformedError(
                "agreement",
                "$.prev",
                "is null exactly for version 1: a later version names its predecessor",
            )
        if not self.principals:
            raise MalformedError("agreement", "$.principals", "names no principal")
        names = [p.name for p in self.principals]
        if len(set(names)) != len(names):
            raise MalformedError("set", "$.principals", "names a principal twice")
        keys = [p.key for p in self.principals]
        if len(set(keys)) != len(keys):
            raise MalformedError(
                "agreement",
                "$.principals",
                "holds one key under two names: its holder would count twice",
            )
        if not (
            1
            <= self.revoke_quorum
            <= self.issue_quorum
            <= self.charter_quorum
            <= len(self.principals)
        ):
            raise MalformedError(
                "agreement",
                "$.quorums",
                "must satisfy 1 <= revoke <= issue <= charter <= the number of principals (F4-4)",
            )

    # -- reading --------------------------------------------------------------

    def principal(self, name: str) -> Principal | None:
        return next((p for p in self.principals if p.name == name), None)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.principals)

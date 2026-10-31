"""Records of the FIDU1 log (§4.7): what fiduciary itself did, signed and hash-chained."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, Final, Protocol

from agentgov.receipts.signing import Signer

from fiduciary import fidu1
from fiduciary.documents._base import Document
from fiduciary.exceptions import MalformedError
from fiduciary.fidu1 import Fields, Signature

__all__ = ["ANCHOR_PREFIX", "LOG_ID", "RECORD_KINDS", "Linked", "Record", "anchor_memo"]

RECORD_KINDS: Final = frozenset(
    {
        "charter.adopted",
        "principal.suspended",
        "mandate.issued",
        "mandate.bound",
        "mandate.rebased",
        "mandate.suspended",
        "mandate.revoked",
        "mandate.expired",
        "ticket.raised",
        "approval.recorded",
        "approval.refused",
        "warrant.formed",
        "ticket.lapsed",
        "ticket.declined",
        "execution.intent",
        "execution.applied",
        "execution.refused",
        "execution.abandoned",
        "session.opened",
        "session.refused",
        "session.closed",
    }
)
LOG_ID: Final = re.compile(r"[A-Za-z0-9._:@/-]{1,64}")
ANCHOR_PREFIX: Final = "FIDU1 "
"""How the memo of an AgentGov ``ANCHOR`` naming a FIDU1 record begins (F4-26)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Record(Document):
    """One record. ``seq`` counts from 1; ``prev`` is the previous record's
    hash, or :data:`fidu1.GENESIS` for the first."""

    TYPE: ClassVar[str] = "record"
    FIELDS: ClassVar[tuple[str, ...]] = ("log", "seq", "kind", "issued_at", "body", "prev")
    OPTIONAL: ClassVar[tuple[str, ...]] = ("seal",)

    log: str
    seq: int
    kind: str
    issued_at: datetime
    content: dict[str, Any]
    """The record's ``body``."""
    prev: str
    seal: tuple[str, str] | None = None
    """A sealed log's two tags (:mod:`fiduciary.seals`); absent otherwise, so a
    record of a log not sealed keeps its bytes (added in 1.4.0)."""
    signature: Signature | None = None

    def _body(self) -> dict[str, Any]:
        found: dict[str, Any] = {
            "log": self.log,
            "seq": self.seq,
            "kind": self.kind,
            "issued_at": fidu1.format_instant(self.issued_at),
            "body": self.content,
            "prev": self.prev,
        }
        if self.seal is not None:
            found["seal"] = list(self.seal)
        return found

    @classmethod
    def _read(cls, fields: Fields) -> dict[str, Any]:
        content = fields.raw("body")
        if not isinstance(content, dict):
            raise MalformedError("type", fields.path("body"), "must be an object")
        kind = fields.text("kind", fidu1.TEXT, "a record kind")
        if kind not in RECORD_KINDS:
            raise MalformedError("format", fields.path("kind"), f"{kind!r} is no FIDU1 record kind")
        seal = None
        if fields.has("seal"):
            raw = fields.raw("seal")
            if (
                not isinstance(raw, list)
                or len(raw) != 2
                or not all(isinstance(t, str) and fidu1.HASH.fullmatch(t) for t in raw)
            ):
                raise MalformedError("format", fields.path("seal"), "is two 64-hex tags")
            seal = (raw[0], raw[1])
        return {
            "log": fields.text("log", LOG_ID, "a log id"),
            "seq": fields.integer("seq", 1),
            "kind": kind,
            "issued_at": fields.instant("issued_at"),
            "content": content,
            "prev": fields.hash("prev"),
            "seal": seal,
        }

    def _agree(self) -> None:
        if (self.seq == 1) != (self.prev == fidu1.GENESIS):
            raise MalformedError("agreement", "$.prev", "is 64 zeros exactly for the first record")
        if self.seal is not None and list(self.seal) != sorted(set(self.seal)):
            raise MalformedError("agreement", "$.seal", "is two different tags, in order")

    @property
    def hash(self) -> str:
        """SHA-256 over the canonical bytes of the whole signed record."""
        return self.digest

    @classmethod
    def make(
        cls,
        signer: Signer,
        *,
        log: str,
        seq: int,
        kind: str,
        body: dict[str, Any],
        prev: str,
        issued_at: datetime,
        seal: tuple[str, str] | None = None,
    ) -> Record:
        """Sign the next record of a log; ``seal``, a sealed log's."""
        if kind not in RECORD_KINDS:
            raise ValueError(f"{kind!r} is no FIDU1 record kind")
        fidu1.canonical(body, "$.body")
        record = cls(
            log=log, seq=seq, kind=kind, issued_at=issued_at, content=body, prev=prev, seal=seal
        )
        return record.signed(signer)


class Linked(Protocol):
    """A record by its place in its log: a :class:`Record`, or a row of a store."""

    @property
    def log(self) -> str: ...

    @property
    def seq(self) -> int: ...

    @property
    def hash(self) -> str: ...


def anchor_memo(record: Linked) -> str:
    """The AgentGov ``ANCHOR`` memo committing the ledger to ``record``:
    ``FIDU1 <log> <seq> <hash>``, and for a record of a sealed log its two
    tags after (``FIDU1 <log> <seq> <hash> <tag> <tag>``, added in 1.4.0)."""
    memo = f"{ANCHOR_PREFIX}{record.log} {record.seq} {record.hash}"
    seal: tuple[str, str] | None = getattr(record, "seal", None)
    return memo if seal is None else f"{memo} {seal[0]} {seal[1]}"

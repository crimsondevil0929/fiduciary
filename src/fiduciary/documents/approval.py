"""The approval: one principal's signed decision on one subject (§4.5)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, Final

from fiduciary import fidu1
from fiduciary.documents._base import Document
from fiduciary.exceptions import MalformedError
from fiduciary.fidu1 import Fields, Signature

__all__ = ["SUBJECTS", "Approval", "Subject", "suspension_digest"]

SUBJECTS: Final = ("charter", "mandate", "revocation", "suspension", "ticket")
"""What an approval may decide (F4-21). ``revocation`` names a mandate by its
digest; ``suspension`` names a principal's key by :func:`suspension_digest`."""


def suspension_digest(charter_id: str, principal: str, key: str) -> str:
    """The subject an approval to suspend ``principal``'s ``key`` decides (F4-5):
    the digest of ``{"v": "FIDU1", "type": "suspension", "charter_id",
    "principal", "key"}``."""
    return fidu1.digest(
        {
            "v": fidu1.VERSION,
            "type": "suspension",
            "charter_id": charter_id,
            "principal": principal,
            "key": key,
        }
    )


@dataclass(frozen=True, slots=True)
class Subject:
    type: str
    digest: str

    def to_json(self) -> dict[str, str]:
        return {"type": self.type, "digest": self.digest}


@dataclass(frozen=True, slots=True, kw_only=True)
class Approval(Document):
    """An approval (F4-21 to F4-23). At most 1,024 canonical bytes."""

    TYPE: ClassVar[str] = "approval"
    FIELDS: ClassVar[tuple[str, ...]] = (
        "approval_id",
        "charter",
        "principal",
        "subject",
        "resolution",
        "decision",
        "issued_at",
        "not_after",
    )
    MAX_BYTES: ClassVar[int] = 1024

    approval_id: str
    charter_id: str
    charter_version: int
    principal: str
    subject: Subject
    resolution: str | None
    decision: str
    issued_at: datetime
    not_after: datetime
    signature: Signature | None = None

    @property
    def approves(self) -> bool:
        return self.decision == "approve"

    def _body(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "charter": {"charter_id": self.charter_id, "version": self.charter_version},
            "principal": self.principal,
            "subject": self.subject.to_json(),
            "resolution": self.resolution,
            "decision": self.decision,
            "issued_at": fidu1.format_instant(self.issued_at),
            "not_after": fidu1.format_instant(self.not_after),
        }

    @classmethod
    def _read(cls, fields: Fields) -> dict[str, Any]:
        charter = fields.object("charter", ("charter_id", "version"))
        subject = fields.object("subject", ("type", "digest"))
        return {
            "approval_id": fields.uuid("approval_id"),
            "charter_id": charter.uuid("charter_id"),
            "charter_version": charter.integer("version", 1),
            "principal": fields.name("principal"),
            "subject": Subject(subject.choice("type", SUBJECTS), subject.hash("digest")),
            "resolution": None if fields.is_null("resolution") else fields.hash("resolution"),
            "decision": fields.choice("decision", ("approve", "decline")),
            "issued_at": fields.instant("issued_at"),
            "not_after": fields.instant("not_after"),
        }

    def _agree(self) -> None:
        if (self.subject.type == "ticket") != (self.resolution is not None):
            raise MalformedError(
                "agreement",
                "$.resolution",
                "names a resolution exactly when the subject is a ticket; any other subject is "
                "itself what is approved",
            )
        if self.not_after <= self.issued_at:
            raise MalformedError("agreement", "$.not_after", "comes after issued_at")

"""The FIDU1 documents (``docs/FIDUCIARY_SPEC.md`` §4).

Each is a frozen dataclass that reads strictly (:meth:`Document.from_json`),
writes canonically (:meth:`Document.to_json`), signs with an Ed25519 key
(:meth:`Document.signed`) and verifies under the key pinned for its signer
(:meth:`Document.verify`). :func:`load` reads any of them by its ``type``.
"""

from __future__ import annotations

from typing import Any

from fiduciary import fidu1
from fiduciary.documents._base import Document
from fiduciary.documents.approval import SUBJECTS, Approval, suspension_digest
from fiduciary.documents.charter import Charter, Deployment, Principal
from fiduciary.documents.mandate import Mandate, MandateRef
from fiduciary.documents.record import RECORD_KINDS, Record, anchor_memo
from fiduciary.documents.tool import ToolBinding
from fiduciary.exceptions import MalformedError

__all__ = [
    "RECORD_KINDS",
    "SUBJECTS",
    "TYPES",
    "Approval",
    "Charter",
    "Deployment",
    "Document",
    "Mandate",
    "MandateRef",
    "Principal",
    "Record",
    "ToolBinding",
    "anchor_memo",
    "load",
    "loads",
    "suspension_digest",
]

TYPES: dict[str, type[Document]] = {
    cls.TYPE: cls for cls in (Charter, Mandate, ToolBinding, Approval, Record)
}


def load(raw: object, *, signed: bool = True) -> Document:
    """Read a document of any type this release knows.

    :raises MalformedError: ``version`` for a type it does not know.
    """
    if not isinstance(raw, dict):
        raise MalformedError("type", "$", "a document is a JSON object")
    if raw.get("v") != fidu1.VERSION:
        raise MalformedError("version", "$.v", f"must be {fidu1.VERSION!r}")
    cls = TYPES.get(raw.get("type"))  # type: ignore[arg-type]
    if cls is None:
        raise MalformedError(
            "version", "$.type", f"{raw.get('type')!r} is no document type this release reads"
        )
    return cls.from_json(raw, signed=signed)


def loads(data: str | bytes, *, signed: bool | None = None) -> Document:
    """Read a document from JSON text. ``signed=None`` reads a draft or a
    signed document, whichever it is."""
    raw: Any = fidu1.parse(data)
    if signed is None:
        signed = isinstance(raw, dict) and "sig" in raw
    return load(raw, signed=signed)

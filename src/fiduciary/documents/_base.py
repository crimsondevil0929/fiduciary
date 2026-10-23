"""What every FIDU1 document shares: its body, its signature, its digest."""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Callable, Iterable, Sequence
from typing import Any, ClassVar, Self, TypeVar

from agentgov.receipts.signing import Signer, Verifier

from fiduciary import fidu1
from fiduciary.exceptions import MalformedError
from fiduciary.fidu1 import Fields, Signature

__all__ = ["IDENTIFIER", "SCOPE", "Document", "sorted_by"]

IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,62}")
"""A table or column, as Interlock's ``TableSpec`` names one."""
SCOPE = fidu1.TEXT
"""An AgentGov scope id: any non-empty text."""

T = TypeVar("T")


class Document:
    """A FIDU1 document: a frozen dataclass with a ``signature`` field.

    A subclass names its ``TYPE`` and its fields (``FIELDS``, in the order
    written), and implements ``_body`` and ``_read``.
    """

    __slots__ = ("_digest",)

    TYPE: ClassVar[str]
    FIELDS: ClassVar[tuple[str, ...]]
    MAX_BYTES: ClassVar[int] = fidu1.MAX_DOCUMENT

    signature: Signature | None

    # -- what a subclass implements -------------------------------------------------

    def _body(self) -> dict[str, Any]:
        raise NotImplementedError

    @classmethod
    def _read(cls, fields: Fields) -> dict[str, Any]:
        """The constructor's arguments, every field checked."""
        raise NotImplementedError

    def _agree(self) -> None:
        """Raise ``MalformedError("agreement", ...)`` where fields disagree."""

    # -- encoding -------------------------------------------------------------------

    def body(self) -> dict[str, Any]:
        """The document without ``sig``: what its signature covers."""
        return {"v": fidu1.VERSION, "type": self.TYPE, **self._body()}

    def to_json(self) -> dict[str, Any]:
        document = self.body()
        if self.signature is not None:
            document["sig"] = self.signature.to_json()
        return document

    def canonical(self) -> bytes:
        return fidu1.canonical(self.to_json())

    @property
    def digest(self) -> str:
        """SHA-256 of the canonical bytes of the whole document (§3.3),
        computed once: a document is frozen, so its digest never changes."""
        try:
            found: str = self._digest  # type: ignore[attr-defined]
        except AttributeError:
            found = fidu1.digest(self.to_json())
            object.__setattr__(self, "_digest", found)
        return found

    @classmethod
    def from_json(cls, raw: object, *, signed: bool = True) -> Self:
        """Read a document strictly (F3-1). ``signed=False`` reads a draft,
        which carries no ``sig``.

        :raises MalformedError: On anything the type does not allow.
        """
        fields = Fields.document(raw, cls.TYPE, cls.FIELDS, signed=signed)
        size = len(fidu1.canonical(raw))
        if size > cls.MAX_BYTES:
            raise MalformedError(
                "size", "$", f"is {size} bytes; a {cls.TYPE} is at most {cls.MAX_BYTES}"
            )
        signature = Signature.from_json(fields.raw("sig")) if signed else None
        document = cls(**cls._read(fields), signature=signature)  # type: ignore[call-arg]
        document._agree()
        return document

    @classmethod
    def loads(cls, data: str | bytes, *, signed: bool = True) -> Self:
        return cls.from_json(fidu1.parse(data), signed=signed)

    def dumps(self) -> str:
        """The document as canonical JSON text."""
        return self.canonical().decode("utf-8")

    def pretty(self) -> str:
        """The document indented, for reading. Never hashed or signed."""
        return json.dumps(self.to_json(), indent=2, sort_keys=True, ensure_ascii=False)

    # -- signing --------------------------------------------------------------------

    def signed(self, signer: Signer) -> Self:
        """This document signed by ``signer`` (Ed25519 only, F3-3)."""
        self._agree()
        return dataclasses.replace(self, signature=fidu1.sign(self.TYPE, self.body(), signer))  # type: ignore[type-var]

    def unsigned(self) -> Self:
        return dataclasses.replace(self, signature=None)  # type: ignore[type-var]

    def signing_input(self) -> bytes:
        if self.signature is None:
            raise ValueError(f"the {self.TYPE} is not signed")
        return fidu1.signing_input(self.TYPE, self.body(), self.signature.header())

    def verify(self, key: Verifier) -> None:
        """Check the signature under ``key``, pinned for the signer (F3-4).

        :raises SignatureError: When unsigned, signed by another key, or altered.
        """
        fidu1.verify(self.TYPE, self.body(), self.signature, key)


def sorted_by(
    items: Sequence[T], identity: Callable[[T], Iterable[str]], path: str
) -> tuple[T, ...]:
    """A list of rules held as a set: sorted by each rule's identity, none twice.

    One set of rules has one spelling, so it has one digest.
    """
    keys = [tuple(identity(item)) for item in items]
    if len(set(keys)) != len(keys):
        raise MalformedError("set", path, "holds a rule twice")
    ordered = sorted(keys, key=lambda key: tuple(part.encode("utf-16-be") for part in key))
    if keys != ordered:
        raise MalformedError(
            "set", path, "is out of order: rules are sorted by what identifies them"
        )
    return tuple(items)

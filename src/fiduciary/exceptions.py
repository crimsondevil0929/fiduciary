"""What fiduciary raises.

Every error a document, a record or a store can fail with carries a stable
``code``: the golden vectors (``vectors/fidu1/``) name the code each invalid
one must fail with, so an implementation in another language can be held to
the same refusals.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "MALFORMED_CODES",
    "AnchorError",
    "AuthorityError",
    "FiduciaryError",
    "LogIntegrityError",
    "MalformedError",
    "SignatureError",
    "StoreError",
]

MALFORMED_CODES: Final = frozenset(
    {
        "json",  # not canonical-domain JSON: a float, a duplicate key, bad UTF-8
        "version",  # "v" is not FIDU1, or "type" is not the type expected
        "fields",  # a field missing, or one the type does not name
        "type",  # a field of the wrong JSON type
        "format",  # a string that is not of its form (a hash, an instant, money...)
        "range",  # an integer outside its bounds
        "set",  # a set out of order, or holding a member twice
        "agreement",  # fields that do not agree with each other
        "size",  # a document past its size bound
        "alg",  # a signature by anything but Ed25519
    }
)


class FiduciaryError(Exception):
    """Base of every error fiduciary raises."""


class MalformedError(FiduciaryError):
    """A document, record or value that is not what FIDU1 says it is.

    :ivar code: One of :data:`MALFORMED_CODES`.
    :ivar path: Where in the document, as ``$.a.b[2]``.
    """

    def __init__(self, code: str, path: str, message: str) -> None:
        if code not in MALFORMED_CODES:  # pragma: no cover - a defect in the caller
            raise AssertionError(f"unknown malformed code {code!r}")
        super().__init__(f"{path}: {message}")
        self.code = code
        self.path = path


class SignatureError(FiduciaryError):
    """A signature that is absent, made by another key, or does not verify.

    :ivar code: ``"unsigned"``, ``"key"`` (signed by a key other than the one
        pinned for the signer) or ``"signature"`` (does not verify).
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AuthorityError(FiduciaryError):
    """An act whose authority does not hold: approvals short of a quorum, a
    signer no charter names, a mandate that does not refine its parent."""


class LogIntegrityError(FiduciaryError):
    """The FIDU1 log, or what is stored beside it, does not verify."""


class AnchorError(LogIntegrityError):
    """The log and the AgentGov ledger's anchors of it disagree."""


class StoreError(FiduciaryError):
    """A store refused an act, or could not be reached."""

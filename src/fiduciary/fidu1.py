"""FIDU1: canonical encoding, strict decoding and the one signature construction.

``docs/FIDUCIARY_SPEC.md`` §3. A FIDU1 document is one JSON object in ARC1's
canonical value domain (RFC 8785 over strings, safe integers, booleans,
``null``, arrays and objects; never a float), encoded with agentgov's
``canonical_bytes``::

    {"v": "FIDU1", "type": "<type>", ..., "sig": {"alg": "ed25519",
     "key_id": "<16 hex>", "signature": "<128 hex>"}}

Its signature covers the type's domain and the SHA-512 of the document's
canonical bytes with ``sig`` reduced to ``{"alg", "key_id"}``::

    signing_input = DOMAIN[type] ‖ SHA-512(canonical(doc with sig := {alg, key_id}))

so every message a signer is asked to sign is at most 96 bytes, whatever the
document's size: a key service that limits what it signs (AWS KMS: 4096 bytes)
signs every document (F3-6, F3-7). There is one construction, Ed25519 only:
no whole-document mode to downgrade to, and no HMAC, which anyone who can
verify can also forge (F3-2, F3-3).

A document's digest is the SHA-256 of its canonical bytes, signature included,
and every reference between documents is one (F3-3 to F3-4, §3.3).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

from agentgov.exceptions import MalformedReceiptError
from agentgov.receipts import canonical_bytes, loads_strict
from agentgov.receipts.signing import Ed25519PublicKey, Signer, Verifier, verifier_of

from fiduciary.exceptions import MalformedError, SignatureError

__all__ = [
    "ALG",
    "DOMAINS",
    "GENESIS",
    "MAX_SAFE_INTEGER",
    "MAX_SIGNING_INPUT",
    "VERSION",
    "Fields",
    "Signature",
    "canonical",
    "digest",
    "format_instant",
    "key_of",
    "parse",
    "sign",
    "signing_input",
    "utf16_sorted",
    "verify",
]

VERSION: Final = "FIDU1"
ALG: Final = "ed25519"
GENESIS: Final = "0" * 64
"""The ``prev`` of a log's first record, and of a lineage's first version."""

DOMAINS: Final[Mapping[str, bytes]] = {
    kind: f"FIDU1/{kind}/v1\n".encode("ascii")
    for kind in ("charter", "mandate", "tool", "ticket", "approval", "warrant", "record", "bundle")
}
"""Each document type's signing domain (§3.4). None is a prefix of another,
and none begins ``ARC1/`` or ``ILOK1/`` (F3-5)."""

MAX_DOMAIN: Final = 32
MAX_SIGNING_INPUT: Final = MAX_DOMAIN + 64
"""The longest message FIDU1 ever asks a signer to sign (F3-6)."""

MAX_SAFE_INTEGER: Final = 2**53 - 1
MAX_DOCUMENT: Final = 65_536
"""The largest canonical document (F4-12), a bound on parsing work."""

# -- the value domain (§3.1) ---------------------------------------------------

HASH: Final = re.compile(r"[0-9a-f]{64}")
KEY_ID: Final = re.compile(r"[0-9a-f]{16}")
PUBLIC_KEY: Final = re.compile(r"ed25519:[0-9a-f]{64}")
SIGNATURE: Final = re.compile(r"[0-9a-f]{128}")
UUID: Final = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
INSTANT: Final = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z")
MONEY: Final = re.compile(r"(0|[1-9][0-9]{0,17})\.[0-9]{8}")
DECIMAL: Final = re.compile(r"(0|[1-9][0-9]{0,17})(\.[0-9]{0,7}[1-9])?")
"""A non-negative decimal in its one spelling: no leading zeros, no trailing
zeros after the point, at most eight places."""
FRACTION: Final = re.compile(r"(0|[1-9][0-9]{0,5})(\.[0-9]{0,5}[1-9])?")
"""A non-negative decimal of at most six places, in its one spelling."""
NAME: Final = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
TEXT: Final = re.compile(r"[^\x00-\x1f\x7f]{1,512}")
STATEMENT: Final = re.compile(r"[^\x00-\x08\x0b-\x1f\x7f]{1,8192}")
"""A parameterised statement: newlines and tabs allowed, at most 8192 characters."""


def canonical(value: object, path: str = "$") -> bytes:
    """The canonical bytes of ``value``.

    :raises MalformedError: ``json``, if ``value`` is outside the domain.
    """
    try:
        return canonical_bytes(value)
    except MalformedReceiptError as exc:
        raise MalformedError("json", path, str(exc)) from exc


def parse(data: str | bytes) -> Any:
    """Decode JSON strictly: no duplicate key, no float, no non-JSON constant.

    :raises MalformedError: ``json``.
    """
    try:
        return loads_strict(data)
    except MalformedReceiptError as exc:
        raise MalformedError("json", "$", str(exc)) from exc
    except (UnicodeDecodeError, ValueError) as exc:
        raise MalformedError("json", "$", f"not JSON: {exc}") from exc


def digest(document: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical bytes of the whole document, signature included."""
    return hashlib.sha256(canonical(dict(document))).hexdigest()


def utf16_sorted(values: Iterable[str]) -> list[str]:
    """``values`` in the order a FIDU1 set holds them: by UTF-16 code units."""
    return sorted(values, key=lambda text: text.encode("utf-16-be"))


def format_instant(moment: datetime) -> str:
    """``moment`` as FIDU1 writes an instant: UTC, microseconds, ``Z``."""
    if moment.tzinfo is None:
        raise ValueError("an instant is timezone-aware")
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def key_of(spec: str) -> Ed25519PublicKey:
    """The public key written ``ed25519:<64 hex>``.

    :raises MalformedError: ``format``, on anything else, HMAC keys included.
    """
    if not isinstance(spec, str) or not PUBLIC_KEY.fullmatch(spec):
        raise MalformedError("format", "$", "a key is ed25519:<64 lowercase hex>")
    return Ed25519PublicKey(bytes.fromhex(spec.removeprefix("ed25519:")))


# -- reading strictly (F3-1) -----------------------------------------------------


class Fields:
    """A JSON object read strictly: exactly the fields named, each checked as read.

    :param names: Every field the object must hold. One it lacks, or one it
        holds beyond them, is ``fields``.
    """

    __slots__ = ("_path", "_value")

    def __init__(self, value: object, path: str, names: Iterable[str]) -> None:
        if not isinstance(value, dict):
            raise MalformedError("type", path, "must be an object")
        expected = set(names)
        missing = sorted(expected - set(value))
        unknown = sorted(set(value) - expected)
        if missing:
            raise MalformedError("fields", path, f"lacks {', '.join(missing)}")
        if unknown:
            raise MalformedError("fields", path, f"holds unknown {', '.join(unknown)}")
        self._value: dict[str, Any] = value
        self._path = path

    @classmethod
    def document(cls, value: object, kind: str, names: Iterable[str], *, signed: bool) -> Fields:
        """A FIDU1 document of type ``kind``: ``v``, ``type``, ``names`` and,
        when ``signed``, ``sig``."""
        if not isinstance(value, dict):
            raise MalformedError("type", "$", "a document is a JSON object")
        if value.get("v") != VERSION:
            raise MalformedError("version", "$.v", f"must be {VERSION!r}")
        if value.get("type") != kind:
            raise MalformedError("version", "$.type", f"must be {kind!r}")
        fields = ("v", "type", *names, *(("sig",) if signed else ()))
        found = cls(value, "$", fields)
        size = len(canonical(value))
        if size > MAX_DOCUMENT:
            raise MalformedError(
                "size", "$", f"is {size} bytes; a document is at most {MAX_DOCUMENT}"
            )
        return found

    def path(self, name: str) -> str:
        return f"{self._path}.{name}"

    def raw(self, name: str) -> Any:
        return self._value[name]

    def is_null(self, name: str) -> bool:
        return self._value[name] is None

    def text(self, name: str, pattern: re.Pattern[str] = TEXT, what: str = "text") -> str:
        return check_text(self._value[name], self.path(name), pattern, what)

    def integer(self, name: str, minimum: int = 0, maximum: int = MAX_SAFE_INTEGER) -> int:
        return check_integer(self._value[name], self.path(name), minimum, maximum)

    def boolean(self, name: str) -> bool:
        value = self._value[name]
        if not isinstance(value, bool):
            raise MalformedError("type", self.path(name), "must be true or false")
        return value

    def choice(self, name: str, options: Sequence[str]) -> str:
        value = self._value[name]
        if not isinstance(value, str):
            raise MalformedError("type", self.path(name), "must be a string")
        if value not in options:
            raise MalformedError("format", self.path(name), f"must be one of {', '.join(options)}")
        return value

    def hash(self, name: str) -> str:
        return self.text(name, HASH, "64 lowercase hex characters")

    def uuid(self, name: str) -> str:
        return self.text(name, UUID, "a canonical UUID")

    def name(self, name: str) -> str:
        return self.text(name, NAME, "a name, [a-z][a-z0-9_.-]{0,63}")

    def instant(self, name: str) -> datetime:
        return check_instant(self._value[name], self.path(name))

    def money(self, name: str) -> Decimal:
        return Decimal(self.text(name, MONEY, "money: a decimal with exactly eight places"))

    def decimal(self, name: str, pattern: re.Pattern[str] = DECIMAL) -> Decimal:
        return check_decimal(self._value[name], self.path(name), pattern)

    def key(self, name: str) -> str:
        return self.text(name, PUBLIC_KEY, "ed25519:<64 lowercase hex>")

    def object(self, name: str, names: Iterable[str]) -> Fields:
        return Fields(self._value[name], self.path(name), names)

    def array(self, name: str, max_items: int = 1024) -> list[tuple[str, Any]]:
        """The array ``name``, as ``(path, item)`` pairs."""
        value = self._value[name]
        if not isinstance(value, list):
            raise MalformedError("type", self.path(name), "must be an array")
        if len(value) > max_items:
            raise MalformedError("range", self.path(name), f"holds more than {max_items} items")
        return [(f"{self.path(name)}[{i}]", item) for i, item in enumerate(value)]

    def strings(
        self, name: str, pattern: re.Pattern[str] = TEXT, what: str = "text", max_items: int = 1024
    ) -> tuple[str, ...]:
        """A set of strings: each of its form, sorted, none twice."""
        return check_strings(self._value[name], self.path(name), pattern, what, max_items)

    def mapping(self, name: str, key: re.Pattern[str] = TEXT) -> Iterator[tuple[str, str, Any]]:
        """An object whose keys are data: ``(key, path, value)`` for each, keys checked."""
        value = self._value[name]
        if not isinstance(value, dict):
            raise MalformedError("type", self.path(name), "must be an object")
        for item in utf16_sorted(value):
            yield (
                check_text(item, self.path(name), key, "a key"),
                f"{self.path(name)}.{item}",
                value[item],
            )


def one_of(options: Iterable[str]) -> re.Pattern[str]:
    """A pattern matching exactly one of ``options``."""
    return re.compile("|".join(re.escape(option) for option in options))


def check_strings(
    value: object,
    path: str,
    pattern: re.Pattern[str] = TEXT,
    what: str = "text",
    max_items: int = 1024,
) -> tuple[str, ...]:
    """A set of strings at ``path``: an array, each of its form, sorted, none twice."""
    if not isinstance(value, list):
        raise MalformedError("type", path, "must be an array")
    if len(value) > max_items:
        raise MalformedError("range", path, f"holds more than {max_items} items")
    items = [check_text(v, f"{path}[{i}]", pattern, what) for i, v in enumerate(value)]
    check_set(items, path)
    return tuple(items)


def check_decimal(value: object, path: str, pattern: re.Pattern[str] = DECIMAL) -> Decimal:
    what = "a decimal in its one spelling" + (", six places at most" if pattern is FRACTION else "")
    return Decimal(check_text(value, path, pattern, what))


def check_text(
    value: object, path: str, pattern: re.Pattern[str] = TEXT, what: str = "text"
) -> str:
    if not isinstance(value, str):
        raise MalformedError("type", path, "must be a string")
    if not pattern.fullmatch(value):
        raise MalformedError("format", path, f"must be {what}")
    return value


def check_integer(
    value: object, path: str, minimum: int = 0, maximum: int = MAX_SAFE_INTEGER
) -> int:
    if type(value) is not int:
        raise MalformedError("type", path, "must be an integer")
    if not minimum <= value <= maximum:
        raise MalformedError("range", path, f"must be between {minimum} and {maximum}")
    return value


def check_instant(value: object, path: str) -> datetime:
    text = check_text(value, path, INSTANT, "an instant, YYYY-MM-DDTHH:MM:SS.ffffffZ")
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise MalformedError("format", path, f"is no instant: {exc}") from exc


def check_set(items: Sequence[str], path: str) -> None:
    """A FIDU1 set: sorted by UTF-16 code units, no member twice. Never re-sorted."""
    if len(set(items)) != len(items):
        raise MalformedError("set", path, "holds a member twice")
    if list(items) != utf16_sorted(items):
        raise MalformedError("set", path, "is out of order: a set is sorted")


# -- signatures (F3-2 to F3-8) ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class Signature:
    """``sig``: the key that signed, and its Ed25519 signature."""

    key_id: str
    value: bytes
    alg: str = ALG

    def header(self) -> dict[str, str]:
        return {"alg": self.alg, "key_id": self.key_id}

    def to_json(self) -> dict[str, str]:
        return {**self.header(), "signature": self.value.hex()}

    @classmethod
    def from_json(cls, value: object, path: str = "$.sig") -> Signature:
        fields = Fields(value, path, ("alg", "key_id", "signature"))
        if fields.raw("alg") != ALG:
            raise MalformedError(
                "alg",
                fields.path("alg"),
                "every FIDU1 signature is Ed25519; HMAC proves nothing to a third party",
            )
        key_id = fields.text("key_id", KEY_ID, "16 lowercase hex characters")
        value_hex = fields.text("signature", SIGNATURE, "128 lowercase hex characters")
        return cls(key_id=key_id, value=bytes.fromhex(value_hex))


def signing_input(kind: str, body: Mapping[str, Any], header: Mapping[str, str]) -> bytes:
    """The exact bytes a signer signs for a document of type ``kind``.

    :param body: The document without ``sig``.
    :param header: ``{"alg", "key_id"}``.
    """
    domain = DOMAINS.get(kind)
    if domain is None:
        raise MalformedError("version", "$.type", f"{kind!r} is no FIDU1 type")
    document = {**body, "sig": dict(header)}
    return domain + hashlib.sha512(canonical(document)).digest()


def sign(kind: str, body: Mapping[str, Any], signer: Signer) -> Signature:
    """Sign a document's body with ``signer``, and check the signature before
    returning it (F3-8): whatever a key service answered never becomes a
    signature that does not verify.

    :raises SignatureError: ``alg`` for a signer that is not Ed25519 (an HMAC
        key is refused, F3-3), ``signature`` if what it returned does not
        verify under its own public key.
    """
    if signer.alg != ALG:
        raise SignatureError(
            "alg",
            f"FIDU1 signs with Ed25519 only; a {signer.alg} key is refused: whoever "
            f"verifies an HMAC can forge it",
        )
    header = {"alg": ALG, "key_id": signer.key_id}
    message = signing_input(kind, body, header)
    if len(message) > MAX_SIGNING_INPUT:  # pragma: no cover - the domains are fixed
        raise AssertionError("a FIDU1 signing input exceeds 96 bytes")
    value = signer.sign(message)
    verifier = verifier_of(signer)
    if verifier.alg != ALG or not verifier.verify(message, value):
        raise SignatureError(
            "signature", f"key {signer.key_id} returned a signature that does not verify"
        )
    return Signature(key_id=signer.key_id, value=value)


def verify(kind: str, body: Mapping[str, Any], signature: Signature | None, key: Verifier) -> None:
    """Check ``signature`` over ``body`` under ``key``, the key pinned for the
    signer, never one the document names (F3-4).

    :raises SignatureError: ``unsigned``, ``key`` (signed by another key) or
        ``signature`` (does not verify).
    """
    if signature is None:
        raise SignatureError("unsigned", f"the {kind} is not signed")
    if key.alg != ALG:
        raise SignatureError("alg", f"a {key.alg} key verifies no FIDU1 signature")
    if signature.key_id != key.key_id:
        raise SignatureError(
            "key",
            f"the {kind} was signed by key {signature.key_id}, not by the key pinned for "
            f"its signer ({key.key_id})",
        )
    message = signing_input(kind, body, signature.header())
    if not key.verify(message, signature.value):
        raise SignatureError("signature", f"the {kind}'s signature does not verify: it was altered")

"""Passkeys as principals' keys: WebAuthn ES256 signatures in FIDU1 (1.17.0, F3-2 amended).

A principal's key in the charter is either ``ed25519:<64 hex>`` or, from 1.17.0,
``webauthn-es256:<128 hex>``: the P-256 public key of a passkey (Touch ID, Face ID,
Windows Hello, a security key), its point's ``x`` then ``y``. A passkey key comes with
the relying party it was registered for and the origins it may be used from (its
:class:`Policy`), in the charter beside it.

**One construction per key type.** The construction that verifies a signature is the
one the charter's key requires, never the one the signature claims: a signature whose
``alg`` is not its key's is refused, so there is nothing to downgrade to.

**What a passkey signs.** The browser asks the passkey for an assertion whose challenge
is the document's FIDU1 signing input (at most 96 bytes): the passkey signs
``authenticatorData ‖ SHA-256(clientDataJSON)``, and ``clientDataJSON`` carries the
challenge. So the signature object holds three things: ``authenticator_data`` and
``client_data_json`` (hex), and the ECDSA ``signature`` (DER, hex).

**What is checked**, here and in the browser (``report/verify.js``) alike:

1. ``clientDataJSON`` is a JSON object with ``type`` ``webauthn.get``, a ``challenge``
   equal to the signing input (base64url, no padding), an ``origin`` the policy admits,
   and no ``crossOrigin: true`` and no ``topOrigin``;
2. ``authenticatorData`` begins with SHA-256 of the policy's relying-party id, and its
   flags say the user was present (UP) and verified (UV: a fingerprint, a face, a PIN);
3. the signature verifies under the key over ``authenticatorData ‖ SHA-256(clientDataJSON)``.

The signature counter is not checked: a synced passkey reports zero.

**What it proves, and what not.** That a person the passkey verified was present, on an
admitted origin, when the signature was made. Not that they read the page: WebAuthn shows
the relying party and asks for a touch, and does not display what is signed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol, runtime_checkable

__all__ = [
    "ALG",
    "SPEC",
    "Assertion",
    "PasskeyKey",
    "PasskeySigner",
    "Policy",
    "SoftPasskey",
    "SoftSigner",
    "check_origin",
    "der_to_raw",
    "key_id_of",
]

ALG: Final = "webauthn-es256"
SPEC: Final = re.compile(r"webauthn-es256:[0-9a-f]{128}")
"""A passkey's key as the charter writes it: ``x`` then ``y``, 32 bytes each, in hex."""
DOMAIN: Final = b"FIDU1/webauthn-es256/v1\n"
"""What a passkey key's id is the SHA-256 of, before its uncompressed point."""
UP: Final = 0x01
UV: Final = 0x04
RP_ID: Final = re.compile(
    r"(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*"
)
ORIGIN: Final = re.compile(
    r"https://(?P<host>[a-z0-9.-]{1,253})(:(?P<port>[0-9]{1,5}))?"
    r"|http://(?P<local>localhost)(:(?P<lport>[0-9]{1,5}|\*))?"
)
"""An origin a passkey may sign from: HTTPS on the relying party's host or a subdomain
of it, or ``http://localhost`` (a secure context), with ``:*`` for any port of it."""
MAX_AUTHENTICATOR_DATA: Final = 1024
MAX_CLIENT_DATA: Final = 4096


def key_id_of(point: bytes) -> str:
    """A passkey key's id: the first 16 hex of SHA-256 over :data:`DOMAIN` and its
    uncompressed point (``04 ‖ x ‖ y``)."""
    return hashlib.sha256(DOMAIN + b"\x04" + point).hexdigest()[:16]


def check_origin(origin: str, rp_id: str) -> str | None:
    """What is wrong with ``origin`` for a passkey of relying party ``rp_id``, or
    ``None``: WebAuthn admits it only on the relying party's own host or a subdomain,
    and ``http://`` only for ``localhost``."""
    found = ORIGIN.fullmatch(origin)
    if found is None:
        return f"{origin!r} is no https:// origin, nor http://localhost"
    if found["local"]:
        if rp_id != "localhost":
            return f"http://localhost is an origin of relying party localhost, not {rp_id}"
        return None
    host = found["host"]
    if host != rp_id and not host.endswith("." + rp_id):
        return f"{origin} is not on {rp_id} or a subdomain of it"
    return None


@dataclass(frozen=True, slots=True)
class Policy:
    """Where a passkey may sign from: the relying party it was registered for, and
    the origins admitted. ``credential`` is its credential id (base64url), which the
    console asks the browser for; it is no part of verification."""

    rp_id: str
    origins: tuple[str, ...]
    credential: str | None = None

    def __post_init__(self) -> None:
        if not RP_ID.fullmatch(self.rp_id) or self.rp_id.rsplit(".", 1)[-1].isdigit():
            # WebAuthn takes a host name: never an address (192.168.0.1, whose last label
            # is all digits, is one).
            raise ValueError(f"{self.rp_id!r} is no relying-party id: a host name, no IP")
        if not self.origins or len(self.origins) > 16:
            raise ValueError("a passkey admits 1 to 16 origins")
        if list(self.origins) != sorted(set(self.origins)):
            raise ValueError("a passkey's origins are sorted, each once")
        for origin in self.origins:
            problem = check_origin(origin, self.rp_id)
            if problem is not None:
                raise ValueError(problem)
        if self.credential is not None and not re.fullmatch(
            r"[A-Za-z0-9_-]{16,1366}", self.credential
        ):
            raise ValueError("a credential id is base64url, 16 to 1366 characters")

    def admits(self, origin: str) -> bool:
        """Whether a signature made on ``origin`` is one the policy admits: listed
        exactly, or ``http://localhost:<port>`` under ``http://localhost:*``."""
        if origin in self.origins:
            return True
        port = re.fullmatch(r"http://localhost:([0-9]{1,5})", origin)
        return port is not None and "http://localhost:*" in self.origins

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"rp_id": self.rp_id, "origins": list(self.origins)}
        if self.credential is not None:
            out["credential"] = self.credential
        return out

    @classmethod
    def from_json(cls, value: object) -> Policy:
        """:raises ValueError: On anything but ``{rp_id, origins[, credential]}``."""
        if not isinstance(value, Mapping):
            raise ValueError("a passkey's policy is an object")
        unknown = sorted(set(value) - {"rp_id", "origins", "credential"})
        if unknown or "rp_id" not in value or "origins" not in value:
            raise ValueError("a passkey's policy holds rp_id, origins and, at most, credential")
        origins = value["origins"]
        if not isinstance(origins, list) or not all(isinstance(o, str) for o in origins):
            raise ValueError("a passkey's origins are a list of strings")
        rp_id, credential = value["rp_id"], value.get("credential")
        if not isinstance(rp_id, str) or not (credential is None or isinstance(credential, str)):
            raise ValueError("a passkey's rp_id and credential are strings")
        return cls(rp_id, tuple(origins), credential)


@dataclass(frozen=True, slots=True)
class Assertion:
    """What a passkey returned: the bytes a FIDU1 passkey signature carries."""

    authenticator_data: bytes
    client_data_json: bytes
    signature: bytes
    """ECDSA over P-256 and SHA-256, DER-encoded, as WebAuthn returns it."""


class PasskeyKey:
    """A passkey's public key, as a FIDU1 verifier: ``alg``, ``key_id``, and
    :meth:`check`. Without a policy it has an id and verifies nothing."""

    __slots__ = ("_point", "key_id", "policy")

    alg: str = ALG

    def __init__(self, point: bytes, policy: Policy | None = None) -> None:
        if len(point) != 64:
            raise ValueError("a P-256 point is 64 bytes: x then y")
        self._point = point
        self.policy = policy
        self.key_id = key_id_of(point)

    @classmethod
    def parse(cls, spec: str, policy: Policy | None = None) -> PasskeyKey:
        if not SPEC.fullmatch(spec):
            raise ValueError("a passkey key is webauthn-es256:<128 lowercase hex>")
        return cls(bytes.fromhex(spec.removeprefix(ALG + ":")), policy)

    def spec(self) -> str:
        return f"{ALG}:{self._point.hex()}"

    @property
    def point(self) -> bytes:
        return self._point

    def check(self, message: bytes, assertion: Assertion) -> str | None:
        """What is wrong with ``assertion`` as this key's signature of ``message``,
        or ``None`` when it verifies (the module's three steps, in order)."""
        policy = self.policy
        if policy is None:
            return "the key comes with no relying-party policy"
        if len(assertion.authenticator_data) < 37:
            return "authenticatorData is shorter than 37 bytes"
        if len(assertion.authenticator_data) > MAX_AUTHENTICATOR_DATA:
            return "authenticatorData is too long"
        if len(assertion.client_data_json) > MAX_CLIENT_DATA:
            return "clientDataJSON is too long"
        try:
            client = json.loads(
                assertion.client_data_json.decode("utf-8"), object_pairs_hook=_unique
            )
        except (UnicodeDecodeError, ValueError) as exc:
            return f"clientDataJSON is no JSON object: {exc}"
        if not isinstance(client, dict):
            return "clientDataJSON is no JSON object"
        if client.get("type") != "webauthn.get":
            return f"clientDataJSON's type is {client.get('type')!r}, not webauthn.get"
        if client.get("challenge") != b64url(message):
            return "clientDataJSON's challenge is not this document's signing input"
        origin = client.get("origin")
        if not isinstance(origin, str) or not policy.admits(origin):
            return f"it was made on {origin!r}, an origin the charter does not admit"
        if client.get("crossOrigin") not in (None, False) or "topOrigin" in client:
            return "it was made in a cross-origin frame"
        data = assertion.authenticator_data
        if data[:32] != hashlib.sha256(policy.rp_id.encode("ascii")).digest():
            return f"authenticatorData is for another relying party than {policy.rp_id}"
        if not data[32] & UP:
            return "the passkey says no user was present (UP)"
        if not data[32] & UV:
            return "the passkey says the user was not verified (UV)"
        return None if self._verifies(assertion) else "the ECDSA signature does not verify"

    def _verifies(self, assertion: Assertion) -> bool:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec

        try:
            key = ec.EllipticCurvePublicNumbers(
                int.from_bytes(self._point[:32], "big"),
                int.from_bytes(self._point[32:], "big"),
                ec.SECP256R1(),
            ).public_key()
            key.verify(
                assertion.signature,
                assertion.authenticator_data + hashlib.sha256(assertion.client_data_json).digest(),
                ec.ECDSA(hashes.SHA256()),
            )
        except (InvalidSignature, ValueError):
            return False
        return True


def b64url(data: bytes) -> str:
    """base64url without padding: how WebAuthn writes a challenge."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def der_to_raw(signature: bytes) -> bytes:
    """An ECDSA signature's DER encoding as ``r ‖ s``, 32 bytes each: what Web Crypto
    verifies. :raises ValueError: On anything else."""
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    r, s = decode_dss_signature(signature)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def _unique(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"{key!r} appears twice")
        out[key] = value
    return out


@runtime_checkable
class PasskeySigner(Protocol):
    """What signs a FIDU1 document with a passkey (1.17.0): its key id, the key it
    verifies under (with its policy), and an assertion over a message. A browser page
    (:class:`fiduciary.passkeys.BrowserPasskey`) or a test's :class:`SoftPasskey`."""

    alg: str
    key_id: str

    def public_key(self) -> PasskeyKey: ...

    def assertion(self, message: bytes) -> Assertion: ...


class SoftPasskey:
    """A passkey in software, for tests, golden vectors and the sandbox: it answers as an
    authenticator would, with flags it is told to set. It proves nothing about a person,
    which is the whole point of a real one."""

    def __init__(self, rp_id: str = "localhost", seed: int | None = None) -> None:
        from cryptography.hazmat.primitives.asymmetric import ec

        self._key = (
            ec.derive_private_key(seed, ec.SECP256R1())
            if seed is not None
            else ec.generate_private_key(ec.SECP256R1())
        )
        numbers = self._key.public_key().public_numbers()
        self.point = numbers.x.to_bytes(32, "big") + numbers.y.to_bytes(32, "big")
        self.rp_id = rp_id

    @property
    def spec(self) -> str:
        return f"{ALG}:{self.point.hex()}"

    @property
    def key_id(self) -> str:
        return key_id_of(self.point)

    def assert_(
        self,
        challenge: bytes,
        origin: str,
        *,
        flags: int = UP | UV,
        rp_id: str | None = None,
        kind: str = "webauthn.get",
        extra: Mapping[str, Any] | None = None,
        client_data: bytes | None = None,
    ) -> Assertion:
        """An assertion over ``challenge`` made on ``origin``, as a browser returns it
        (or over ``client_data`` exactly, for a test that needs bytes no browser writes)."""
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec

        client = client_data or json.dumps(
            {"type": kind, "challenge": b64url(challenge), "origin": origin, **(extra or {})},
            separators=(",", ":"),
        ).encode("utf-8")
        data = (
            hashlib.sha256((rp_id or self.rp_id).encode("ascii")).digest()
            + bytes([flags])
            + (0).to_bytes(4, "big")
        )
        signature = self._key.sign(
            data + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256())
        )
        return Assertion(data, client, signature)

    def signer(self, policy: Policy, origin: str | None = None) -> SoftSigner:
        """This passkey as a FIDU1 signer: every assertion made on ``origin`` (the
        policy's first, by default)."""
        return SoftSigner(self, policy, origin or policy.origins[0].replace(":*", ":8740"))


class SoftSigner:
    """A :class:`SoftPasskey` that signs FIDU1 documents (:class:`PasskeySigner`)."""

    alg: str = ALG

    def __init__(self, passkey: SoftPasskey, policy: Policy, origin: str) -> None:
        self._passkey = passkey
        self._policy = policy
        self._origin = origin
        self.key_id = passkey.key_id

    def public_key(self) -> PasskeyKey:
        return PasskeyKey(self._passkey.point, self._policy)

    def assertion(self, message: bytes) -> Assertion:
        return self._passkey.assert_(message, self._origin)

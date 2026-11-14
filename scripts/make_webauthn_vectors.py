"""Write ``vectors/fidu1/webauthn.json``: passkey signatures, good and tampered (1.17.0).

Each case is a key, its policy, a FIDU1 signing input and a ``webauthn-es256`` signature,
with what the verifier must say: ``null`` when it verifies, or words its refusal contains.
``tests/test_webauthn.py`` holds ``fiduciary.webauthn`` to every case, and, under Node's
Web Crypto, ``report/verify.js``'s ``checkAssertion`` too. ECDSA signatures are randomized,
so a run writes new signature bytes over the same cases; the cases themselves are fixed.

    python scripts/make_webauthn_vectors.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fiduciary.webauthn import UP, UV, SoftPasskey, b64url

OUT = Path(__file__).resolve().parent.parent / "vectors" / "fidu1" / "webauthn.json"
LOCAL = "http://localhost:8740"


def message(text: str) -> bytes:
    return b"FIDU1/approval/v1\n" + hashlib.sha512(text.encode()).digest()


def main() -> None:
    owner = SoftPasskey("localhost", seed=0x5EED_0001)
    other = SoftPasskey("localhost", seed=0x5EED_0002)
    company = SoftPasskey("example.com", seed=0x5EED_0003)
    signed = message("approval of ticket 1")
    exact = {"rp_id": "localhost", "origins": [LOCAL]}
    any_port = {"rp_id": "localhost", "origins": ["http://localhost:*"]}
    cases: list[dict[str, Any]] = []

    def case(
        name: str,
        key: SoftPasskey,
        policy: dict[str, Any],
        assertion: Any,
        expect: str | None,
        msg: bytes = signed,
    ) -> None:
        cases.append(
            {
                "name": name,
                "key": key.spec,
                "key_id": key.key_id,
                "policy": policy,
                "message": msg.hex(),
                "signature": {
                    "alg": "webauthn-es256",
                    "key_id": key.key_id,
                    "signature": assertion.signature.hex(),
                    "authenticator_data": assertion.authenticator_data.hex(),
                    "client_data_json": assertion.client_data_json.hex(),
                },
                "expect": expect,
            }
        )

    case("valid on the pinned origin", owner, exact, owner.assert_(signed, LOCAL), None)
    case(
        "valid on any localhost port, where the policy admits it",
        owner,
        any_port,
        owner.assert_(signed, "http://localhost:49213"),
        None,
    )
    case(
        "valid on a subdomain of the relying party",
        company,
        {"rp_id": "example.com", "origins": ["https://approvals.example.com"]},
        company.assert_(signed, "https://approvals.example.com"),
        None,
    )
    good = owner.assert_(signed, "http://localhost:49213")
    tampered = good.client_data_json.replace(b"49213", b"49214")
    case(
        "clientDataJSON changed after signing",
        owner,
        any_port,
        type(good)(good.authenticator_data, tampered, good.signature),
        "does not verify",
    )
    case(
        "made for another relying party",
        owner,
        exact,
        owner.assert_(signed, LOCAL, rp_id="evil.example"),
        "another relying party",
    )
    case("no user verification (UV)", owner, exact, owner.assert_(signed, LOCAL, flags=UP), "(UV)")
    case("no user presence (UP)", owner, exact, owner.assert_(signed, LOCAL, flags=UV), "(UP)")
    case(
        "signed for another document",
        owner,
        exact,
        owner.assert_(message("approval of ticket 2"), LOCAL),
        "challenge",
    )
    case(
        "made on an origin the charter does not admit",
        owner,
        exact,
        owner.assert_(signed, "http://localhost:9999"),
        "origin the charter does not admit",
    )
    case(
        "a registration, not an assertion",
        owner,
        exact,
        owner.assert_(signed, LOCAL, kind="webauthn.create"),
        "not webauthn.get",
    )
    case(
        "made in a cross-origin frame",
        owner,
        exact,
        owner.assert_(signed, LOCAL, extra={"crossOrigin": True}),
        "cross-origin",
    )
    case("signed by another passkey", owner, exact, other.assert_(signed, LOCAL), "does not verify")
    twice = (
        '{"type":"webauthn.get","challenge":"'
        + b64url(message("other"))
        + '","origin":"'
        + LOCAL
        + '","challenge":"'
        + b64url(signed)
        + '"}'
    ).encode()
    case(
        "a key written twice in clientDataJSON",
        owner,
        exact,
        owner.assert_(signed, LOCAL, client_data=twice),
        "no JSON object",
    )
    short = owner.assert_(signed, LOCAL)
    case(
        "authenticatorData cut short",
        owner,
        exact,
        type(short)(short.authenticator_data[:36], short.client_data_json, short.signature),
        "shorter than 37",
    )
    OUT.write_text(json.dumps({"cases": cases}, indent=1) + "\n")
    print(f"wrote {len(cases)} cases to {OUT}")


if __name__ == "__main__":
    main()

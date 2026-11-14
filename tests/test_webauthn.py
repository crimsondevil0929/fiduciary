"""Passkeys as principals' keys (1.17.0, F3-2 amended).

- **One construction per key type.** A passkey key verifies only a passkey's assertion; an
  Ed25519 key only an Ed25519 signature. A signature that claims the other is refused.
- **The three steps**, held by ``vectors/fidu1/webauthn.json``: clientDataJSON (type,
  challenge, origin, no cross-origin frame), authenticatorData (relying party, UP, UV), and
  the P-256 signature. ``report/verify.js`` gives the same answer on every case, under
  Node's Web Crypto.
- **The charter** holds a passkey's key with its policy, and a document a passkey signed
  verifies under it, as an approval does in the authority and in a bundle.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from agentgov.receipts.signing import Ed25519Signer

from fiduciary import fidu1, webauthn
from fiduciary.documents.charter import Charter, Principal
from fiduciary.exceptions import MalformedError, SignatureError
from fiduciary.webauthn import PasskeyKey, Policy, SoftPasskey

ROOT = Path(__file__).resolve().parent.parent
VECTORS = ROOT / "vectors" / "fidu1" / "webauthn.json"
HARNESS = Path(__file__).resolve().parent / "js" / "webauthn_vectors.cjs"


def _cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = json.loads(VECTORS.read_text())["cases"]
    return cases


@pytest.mark.parametrize("case", _cases(), ids=lambda c: str(c["name"]))
def test_every_vector_gets_the_answer_it_records(case: dict[str, Any]) -> None:
    key = PasskeyKey.parse(case["key"], Policy.from_json(case["policy"]))
    assert key.key_id == case["key_id"] == case["signature"]["key_id"]
    sig = case["signature"]
    assertion = webauthn.Assertion(
        bytes.fromhex(sig["authenticator_data"]),
        bytes.fromhex(sig["client_data_json"]),
        bytes.fromhex(sig["signature"]),
    )
    problem = key.check(bytes.fromhex(case["message"]), assertion)
    if case["expect"] is None:
        assert problem is None
    else:
        assert problem is not None and case["expect"] in problem


def test_the_browser_gives_the_same_answer_on_every_vector() -> None:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("FIDUCIARY_REQUIRE_NODE") == "1":
            pytest.fail("FIDUCIARY_REQUIRE_NODE=1 and node is not on PATH")
        pytest.skip("node is not installed")
    done = subprocess.run(  # noqa: S603 - node and the repository's own harness
        [node, str(HARNESS), str(VECTORS)], capture_output=True, text=True, check=False, timeout=60
    )
    assert done.returncode == 0, done.stderr
    found = json.loads(done.stdout)
    for case in _cases():
        browser = found[case["name"]]
        assert browser["key_id"] == case["key_id"]
        if case["expect"] is None:
            assert browser["problem"] is None, case["name"]
        else:
            assert browser["problem"] is not None and case["expect"] in browser["problem"], (
                case["name"],
                browser["problem"],
            )


def _approval_body() -> dict[str, Any]:
    return {
        "v": "FIDU1",
        "type": "approval",
        "approval_id": "00000000-0000-4000-8000-000000000001",
        "principal": "owner",
    }


def _passkey_signature(passkey: SoftPasskey, body: dict[str, Any], origin: str) -> fidu1.Signature:
    header = {"alg": webauthn.ALG, "key_id": passkey.key_id}
    made = passkey.assert_(fidu1.signing_input("approval", body, header), origin)
    return fidu1.Signature(
        key_id=passkey.key_id,
        value=made.signature,
        alg=webauthn.ALG,
        authenticator_data=made.authenticator_data,
        client_data_json=made.client_data_json,
    )


def test_a_document_a_passkey_signed_verifies_under_its_key_alone() -> None:
    passkey = SoftPasskey(seed=7)
    policy = Policy("localhost", ("http://localhost:8740",))
    key = fidu1.principal_key(passkey.spec, policy)
    body = _approval_body()
    signature = _passkey_signature(passkey, body, "http://localhost:8740")
    fidu1.verify("approval", body, signature, key)
    assert fidu1.Signature.from_json(signature.to_json()) == signature
    with pytest.raises(SignatureError, match="does not verify"):
        fidu1.verify("approval", {**body, "principal": "mallory"}, signature, key)
    ed = Ed25519Signer.generate()
    ed_signature = fidu1.sign("approval", body, ed)
    with pytest.raises(SignatureError, match="only a passkey's own assertion"):
        fidu1.verify("approval", body, fidu1.Signature(passkey.key_id, ed_signature.value), key)
    with pytest.raises(SignatureError, match="its signer's key is Ed25519"):
        fidu1.verify("approval", body, signature, ed.public_key())
    with pytest.raises(SignatureError, match="no relying-party policy"):
        fidu1.verify("approval", body, signature, fidu1.principal_key(passkey.spec))


def test_a_signature_object_is_read_strictly() -> None:
    passkey = SoftPasskey(seed=8)
    good = _passkey_signature(passkey, _approval_body(), "http://localhost:8740").to_json()
    with pytest.raises(MalformedError):
        fidu1.Signature.from_json({k: v for k, v in good.items() if k != "client_data_json"})
    with pytest.raises(MalformedError):
        fidu1.Signature.from_json({**good, "alg": "es256"})
    with pytest.raises(MalformedError):
        fidu1.Signature.from_json({**good, "authenticator_data": "00" * 10})


def test_a_policy_admits_only_what_webauthn_would() -> None:
    assert Policy("localhost", ("http://localhost:*",)).admits("http://localhost:51515")
    assert not Policy("localhost", ("http://localhost:8740",)).admits("http://localhost:51515")
    assert not Policy("localhost", ("http://localhost:*",)).admits("http://127.0.0.1:8740")
    for bad in (
        ("127.0.0.1", ("http://127.0.0.1:8740",)),
        ("example.com", ("https://example.org",)),
        ("example.com", ("http://example.com",)),
        ("localhost", ("http://localhost:8740", "http://localhost:8740")),
        ("localhost", ()),
    ):
        with pytest.raises(ValueError):
            Policy(*bad)
    assert Policy.from_json({"rp_id": "a.b", "origins": ["https://x.a.b"]}).rp_id == "a.b"
    with pytest.raises(ValueError):
        Policy.from_json({"rp_id": "a.b", "origins": ["https://x.a.b"], "counter": 0})


def test_a_charter_holds_a_passkey_with_its_policy_and_keeps_older_bytes() -> None:
    passkey = SoftPasskey(seed=9)
    ed = Ed25519Signer.generate()
    policy = Policy("localhost", ("http://localhost:8740",), credential="Y3JlZGVudGlhbC1pZC0x")
    common = {
        "charter_id": "00000000-0000-4000-8000-0000000000aa",
        "version": 1,
        "prev": None,
        "deployment": {"ledger": "0" * 64, "label": "solo"},
        "quorums": {"charter": 1, "issue": 1, "revoke": 1},
        "break_glass": [],
        "issued_at": fidu1.format_instant(datetime(2026, 11, 13, tzinfo=UTC)),
    }
    plain = {
        "v": "FIDU1",
        "type": "charter",
        **common,
        "principals": [{"name": "owner", "key": ed.public_key().spec()}],
    }  # type: ignore[attr-defined]
    assert "webauthn" not in json.dumps(Charter.from_json(plain, signed=False).to_json())
    with_passkey = {
        **plain,
        "principals": [{"name": "owner", "key": passkey.spec, "webauthn": policy.to_json()}],
    }
    charter = Charter.from_json(with_passkey, signed=False)
    (owner,) = charter.principals
    assert owner == Principal("owner", passkey.spec, policy) and owner.passkey
    assert isinstance(owner.public_key, PasskeyKey) and owner.public_key.policy == policy
    assert Charter.from_json(charter.to_json(), signed=False) == charter
    for broken in (
        [{"name": "owner", "key": passkey.spec}],
        [{"name": "owner", "key": ed.public_key().spec(), "webauthn": policy.to_json()}],  # type: ignore[attr-defined]
        [{"name": "owner", "key": passkey.spec, "webauthn": {"rp_id": "localhost", "origins": []}}],
    ):
        with pytest.raises(MalformedError):
            Charter.from_json({**plain, "principals": broken}, signed=False)

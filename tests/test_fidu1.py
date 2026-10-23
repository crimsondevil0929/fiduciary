"""FIDU1's framing (``docs/FIDUCIARY_SPEC.md`` §3): the value domain, strict
reading, the domains, and the one signature construction."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from agentgov.receipts.signing import Ed25519Signer, HmacKey

from fiduciary import fidu1
from fiduciary.documents import Charter, Mandate
from fiduciary.exceptions import MalformedError, SignatureError
from fiduciary.fidu1 import Fields, Signature
from tests import examples as e

# -- domains (§3.4, F3-5, F3-6) -----------------------------------------------


def test_no_domain_is_a_prefix_of_another_nor_of_arc1_or_ilok1() -> None:
    domains = list(fidu1.DOMAINS.values())
    for one in domains:
        assert one.startswith(b"FIDU1/") and one.endswith(b"/v1\n")
        assert not one.startswith((b"ARC1/", b"ILOK1/"))
        assert len(one) <= fidu1.MAX_DOMAIN
        for other in domains:
            assert one == other or not other.startswith(one)


def test_the_longest_signing_input_is_82_bytes() -> None:
    longest = max(len(d) for d in fidu1.DOMAINS.values())
    assert longest + 64 == 82 <= fidu1.MAX_SIGNING_INPUT == 96


@pytest.mark.parametrize("make", [e.charter, e.mandate, e.child, e.tool, e.lookup, e.approval])
def test_every_signing_input_is_a_domain_and_a_sha512(make: object) -> None:
    document = make()  # type: ignore[operator]
    message = document.signing_input()
    domain = fidu1.DOMAINS[document.TYPE]
    assert message.startswith(domain)
    assert len(message) == len(domain) + 64 <= 96
    body = {**document.body(), "sig": document.signature.header()}
    assert message[len(domain) :] == hashlib.sha512(fidu1.canonical(body)).digest()


def test_a_signing_input_does_not_grow_with_the_document() -> None:
    big = e.mandate_json(subject={"scope": "s", "funded_from": "f", "label": "x" * 512})
    mandate = Mandate.from_json(big, signed=False).signed(e.signer("cfo"))
    assert len(mandate.canonical()) > 3000
    assert len(mandate.signing_input()) == 81


def test_an_unknown_type_has_no_signing_input() -> None:
    with pytest.raises(MalformedError) as caught:
        fidu1.signing_input("receipt", {}, {"alg": "ed25519", "key_id": "0" * 16})
    assert caught.value.code == "version"


# -- signing (F3-2 to F3-4, F3-8) ---------------------------------------------


def test_hmac_is_refused_to_sign() -> None:
    with pytest.raises(SignatureError) as caught:
        Charter.from_json(e.charter_json(), signed=False).signed(HmacKey.generate())
    assert caught.value.code == "alg"


def test_hmac_verifies_no_signature() -> None:
    with pytest.raises(SignatureError) as caught:
        e.charter().verify(HmacKey.generate())
    assert caught.value.code == "alg"


def test_a_signature_claiming_hmac_is_malformed() -> None:
    doc = e.charter().to_json()
    doc["sig"]["alg"] = "hmac-sha256"
    with pytest.raises(MalformedError) as caught:
        Charter.from_json(doc)
    assert caught.value.code == "alg"


def test_a_document_verifies_under_its_signer_only() -> None:
    charter = e.charter()
    charter.verify(e.signer("cfo").public_key())
    with pytest.raises(SignatureError) as caught:
        charter.verify(e.signer("head-ops").public_key())
    assert caught.value.code == "key"


def test_an_unsigned_document_does_not_verify() -> None:
    with pytest.raises(SignatureError) as caught:
        e.charter().unsigned().verify(e.signer("cfo").public_key())
    assert caught.value.code == "unsigned"
    with pytest.raises(ValueError, match="not signed"):
        e.charter().unsigned().signing_input()


def test_a_document_altered_after_signing_does_not_verify() -> None:
    doc = e.charter().to_json()
    doc["deployment"]["label"] = "acme-test"
    with pytest.raises(SignatureError) as caught:
        Charter.from_json(doc).verify(e.signer("cfo").public_key())
    assert caught.value.code == "signature"


def test_a_signature_naming_the_right_key_but_forged_does_not_verify() -> None:
    charter = e.charter()
    assert charter.signature is not None
    forged = Signature(charter.signature.key_id, bytes(64))
    with pytest.raises(SignatureError) as caught:
        fidu1.verify("charter", charter.body(), forged, e.signer("cfo").public_key())
    assert caught.value.code == "signature"


class _Lying:
    """A signer that returns something other than its own signature."""

    def __init__(self) -> None:
        self._key = Ed25519Signer.generate()

    alg = "ed25519"

    @property
    def key_id(self) -> str:
        return self._key.key_id

    def public_key(self) -> object:
        return self._key.public_key()

    def sign(self, message: bytes) -> bytes:
        return bytes(64)

    def verify(self, message: bytes, signature: bytes) -> bool:  # pragma: no cover
        return False


def test_a_signature_that_does_not_verify_is_never_used() -> None:
    with pytest.raises(SignatureError) as caught:
        Charter.from_json(e.charter_json(), signed=False).signed(_Lying())  # type: ignore[arg-type]
    assert caught.value.code == "signature"


def test_ed25519_signing_is_deterministic() -> None:
    assert e.charter().digest == e.charter().digest
    assert e.charter().to_json() == Charter.from_json(e.charter().to_json()).to_json()


# -- the value domain (§3.1) -------------------------------------------------------


def test_canonical_refuses_a_float_and_parse_refuses_duplicates_and_floats() -> None:
    with pytest.raises(MalformedError) as caught:
        fidu1.canonical({"x": 1.5})
    assert caught.value.code == "json"
    for text in ('{"a": 1, "a": 2}', '{"a": 1.5}', '{"a": NaN}', "not json", b"\xff"):
        with pytest.raises(MalformedError) as caught:
            fidu1.parse(text)
        assert caught.value.code == "json"


def test_digest_is_sha256_of_the_canonical_bytes() -> None:
    assert (
        fidu1.digest({"b": 1, "a": [True, None]})
        == hashlib.sha256(b'{"a":[true,null],"b":1}').hexdigest()
    )


def test_sets_sort_by_utf16_code_units() -> None:
    # U+FF5E sorts before U+1F600 by code point, after it by UTF-16 code unit:
    # U+1F600 is the surrogate pair D83D DE00, and FF5E > D83D.
    tilde, smile = chr(0xFF5E), chr(0x1F600)
    assert fidu1.utf16_sorted([smile, tilde, "a"]) == ["a", smile, tilde]


def test_format_instant() -> None:
    moment = datetime(2026, 10, 18, 12, 0, 0, 5, tzinfo=timezone(timedelta(hours=2)))
    assert fidu1.format_instant(moment) == "2026-10-18T10:00:00.000005Z"
    with pytest.raises(ValueError, match="timezone"):
        fidu1.format_instant(datetime(2026, 1, 1))


def test_key_of_reads_ed25519_keys_only() -> None:
    spec = e.key("cfo")
    assert fidu1.key_of(spec).key_id == e.signer("cfo").key_id
    for bad in (
        "hmac-sha256:" + "00" * 32,
        "ed25519:" + "AB" * 32,
        "ed25519:00",
        spec + "00",  # a key and more: the whole spelling, or nothing
    ):
        with pytest.raises(MalformedError):
            fidu1.key_of(bad)


def test_an_integer_past_the_safe_range_is_refused() -> None:
    """FIDU1 integers are safe integers (§3.1): one past 2**53 - 1 is out of
    range, as is one past a field's own maximum."""
    assert fidu1.check_integer(fidu1.MAX_SAFE_INTEGER, "$.n") == fidu1.MAX_SAFE_INTEGER
    with pytest.raises(MalformedError, match="between"):
        fidu1.check_integer(fidu1.MAX_SAFE_INTEGER + 1, "$.n")
    with pytest.raises(MalformedError, match="between 1 and 9"):
        fidu1.check_integer(10, "$.n", 1, 9)


def _fields(value: object, names: tuple[str, ...] = ("x",)) -> Fields:
    return Fields(value, "$", names)


@pytest.mark.parametrize(
    ("value", "names", "code"),
    [
        ([], ("x",), "type"),
        ({}, ("x",), "fields"),
        ({"x": 1, "y": 2}, ("x",), "fields"),
    ],
)
def test_fields_hold_exactly_what_they_name(
    value: object, names: tuple[str, ...], code: str
) -> None:
    with pytest.raises(MalformedError) as caught:
        _fields(value, names)
    assert caught.value.code == code


@pytest.mark.parametrize(
    ("reader", "value", "code"),
    [
        ("hash", "AB" * 32, "format"),
        ("hash", 7, "type"),
        ("uuid", "6F1C0C8E-3C1F-4F0E-9A51-2B8A7F0D9E11", "format"),
        ("name", "Upper", "format"),
        ("instant", "2026-10-18T09:00:00Z", "format"),
        ("instant", "2026-13-18T09:00:00.000000Z", "format"),
        ("money", "1.5", "format"),
        ("money", "01.00000000", "format"),
        ("decimal", "1.50", "format"),
        ("decimal", "-1", "format"),
        ("key", "ed25519:00", "format"),
        ("boolean", 1, "type"),
        ("integer", True, "type"),
        ("integer", "1", "type"),
        ("integer", -1, "range"),
        ("text", "", "format"),
        ("text", "a\nb", "format"),
        ("text", "x" * 513, "format"),
    ],
)
def test_each_value_is_of_its_form(reader: str, value: object, code: str) -> None:
    with pytest.raises(MalformedError) as caught:
        getattr(_fields({"x": value}), reader)("x")
    assert caught.value.code == code
    assert caught.value.path == "$.x"


def test_values_of_their_form_are_read() -> None:
    f = _fields({"x": "0.25"})
    assert f.decimal("x", fidu1.FRACTION) == Decimal("0.25")
    assert _fields({"x": "12.50000000"}).money("x") == Decimal("12.5")
    assert _fields({"x": "2026-10-18T09:00:00.000000Z"}).instant("x") == e.at("2026-10-18T09:00:00")
    assert _fields({"x": None}).is_null("x")


def test_a_fraction_has_at_most_six_places() -> None:
    assert _fields({"x": "0.123456"}).decimal("x", fidu1.FRACTION) == Decimal("0.123456")
    with pytest.raises(MalformedError):
        _fields({"x": "0.1234567"}).decimal("x", fidu1.FRACTION)


@pytest.mark.parametrize(
    ("value", "code"),
    [(["b", "a"], "set"), (["a", "a"], "set"), ("a", "type"), ([1], "type"), (["a"] * 3, "set")],
)
def test_a_set_is_sorted_and_holds_each_member_once(value: object, code: str) -> None:
    with pytest.raises(MalformedError) as caught:
        _fields({"x": value}).strings("x")
    assert caught.value.code == code


def test_arrays_and_mappings() -> None:
    with pytest.raises(MalformedError) as caught:
        _fields({"x": "no"}).array("x")
    assert caught.value.code == "type"
    with pytest.raises(MalformedError) as caught:
        _fields({"x": [1, 2, 3]}).array("x", max_items=2)
    assert caught.value.code == "range"
    with pytest.raises(MalformedError) as caught:
        list(_fields({"x": []}).mapping("x"))
    assert caught.value.code == "type"
    with pytest.raises(MalformedError) as caught:
        fidu1.check_strings([1, 2, 3], "$", max_items=2)
    assert caught.value.code == "range"
    with pytest.raises(MalformedError) as caught:
        _fields({"x": 3}).choice("x", ("a",))
    assert caught.value.code == "type"


def test_a_document_is_an_object_of_its_version_and_type() -> None:
    for raw, code in (
        ([], "type"),
        ({**e.charter_json(), "v": "FIDU2"}, "version"),
        ({**e.charter_json(), "type": "mandate"}, "version"),
    ):
        with pytest.raises(MalformedError) as caught:
            Charter.from_json(raw, signed=False)
        assert caught.value.code == code


def test_a_document_past_its_size_bound_is_refused() -> None:
    raw = e.charter_json(deployment={"ledger": e.LEDGER, "label": "x"})
    raw["padding"] = "x" * 70_000
    with pytest.raises(MalformedError) as caught:
        Fields.document(raw, "charter", (*Charter.FIELDS, "padding"), signed=False)
    assert caught.value.code == "size"

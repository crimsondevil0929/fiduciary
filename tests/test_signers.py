"""Every document signs through every kind of signer (F3-7, F3-8).

A local Ed25519 key, a key an HTTP signing service holds
(:class:`~agentgov.receipts.signing.HttpRemoteSigner`), and a key AWS KMS
holds (:class:`~agentgov.receipts.kms_aws.AwsKmsSigner`) all sign every FIDU1
document, because every message is at most 96 bytes: a mandate far larger
than KMS would sign whole is signed as its digest.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from agentgov.exceptions import SignerUnavailableError
from agentgov.receipts.kms_aws import AwsKmsSigner
from agentgov.receipts.signing import Ed25519Signer, HttpRemoteSigner, Signer

from fiduciary import fidu1
from fiduciary.documents import Approval, Charter, Document, Mandate, ToolBinding
from tests import examples as e
from tests.fakes import FakeKms, KeyService

DRAFTS: dict[str, Callable[[], Document]] = {
    "charter": lambda: Charter.from_json(e.charter_json(), signed=False),
    "mandate": lambda: Mandate.from_json(e.mandate_json(), signed=False),
    "child": lambda: Mandate.from_json(e.child_json(), signed=False),
    "tool": lambda: ToolBinding.from_json(e.tool_json(), signed=False),
    "approval": lambda: Approval.from_json(e.approval_json(), signed=False),
    "large mandate": lambda: Mandate.from_json(_large_mandate(), signed=False),
}


def _large_mandate() -> dict[str, Any]:
    tenants = [f"tenant-{i:04d}" for i in range(400)]
    return e.mandate_json(data={**e.mandate_json()["data"], "tenants": tenants})


@pytest.fixture
def service() -> Iterator[KeyService]:
    with KeyService() as running:
        yield running


def _signers(service: KeyService) -> dict[str, tuple[Signer, list[bytes]]]:
    key = Ed25519Signer.generate()
    service.add("fiduciary", key)
    kms = FakeKms(Ed25519Signer.generate())
    return {
        "local": (key, []),
        "http": (HttpRemoteSigner(service.url, "fiduciary"), service.messages),
        "kms": (AwsKmsSigner("alias/fiduciary", client=kms), kms.messages),
    }


@pytest.mark.parametrize("kind", ["local", "http", "kms"])
@pytest.mark.parametrize("name", list(DRAFTS))
def test_every_document_signs_and_verifies_through_every_signer(
    service: KeyService, kind: str, name: str
) -> None:
    signer, seen = _signers(service)[kind]
    signed = DRAFTS[name]().signed(signer)
    signed.verify(signer.public_key())
    again = type(signed).from_json(signed.to_json())
    again.verify(signer.public_key())
    message = signed.signing_input()
    assert len(message) <= fidu1.MAX_SIGNING_INPUT
    if kind != "local":
        assert seen == [message], "the service was asked to sign exactly the 96-byte input"


def test_kms_would_refuse_the_large_mandate_whole() -> None:
    kms = FakeKms(Ed25519Signer.generate())
    signer = AwsKmsSigner("alias/fiduciary", client=kms)
    mandate = Mandate.from_json(_large_mandate(), signed=False)
    whole = fidu1.DOMAINS["mandate"] + mandate.canonical()
    assert len(whole) > 4096
    with pytest.raises(SignerUnavailableError):
        signer.sign(whole)
    mandate.signed(signer).verify(signer.public_key())


def test_a_key_service_answering_garbage_signs_nothing(service: KeyService) -> None:
    service.add("fiduciary", Ed25519Signer.generate())
    signer = HttpRemoteSigner(service.url, "fiduciary")
    service.garbage = True
    with pytest.raises(SignerUnavailableError):
        DRAFTS["charter"]().signed(signer)


def test_a_remote_key_verifies_under_its_pinned_public_half_only(service: KeyService) -> None:
    key = Ed25519Signer.generate()
    service.add("fiduciary", key)
    signed = DRAFTS["approval"]().signed(HttpRemoteSigner(service.url, "fiduciary"))
    signed.verify(key.public_key())
    assert signed.signature is not None and signed.signature.key_id == key.key_id

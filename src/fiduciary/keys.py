"""Opening signers: a key file, or a key a signing service holds (§3.5, §10.5).

A key file holds an Ed25519 seed in hex, as Interlock's operator keys do
(:func:`interlock.operators.generate_key`, readable by its owner only). A
``[signers.<name>]`` table names a key held elsewhere:

    [signers.cfo-kms]
    type = "aws-kms"                  # agentgov.receipts.kms_aws.AwsKmsSigner
    key_id = "alias/fiduciary-cfo"
    region = "us-east-1"

    [signers.service]
    type = "http"                     # agentgov's HttpRemoteSigner, as Interlock's
    url = "https://kms.internal:8443"
    key = "fiduciary-service"
    token_env = "FIDUCIARY_KMS_TOKEN"

Every FIDU1 message is at most 96 bytes, so AWS KMS signs any document (F3-7).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from agentgov.receipts.signing import Signer

__all__ = ["SignerSpec", "generate_key", "load_key", "open_signer", "parse_signers"]

_NAME: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_KEYS: Final[Mapping[str, frozenset[str]]] = {
    "http": frozenset({"type", "url", "key", "token_env", "timeout_seconds", "version"}),
    "aws-kms": frozenset({"type", "key_id", "region"}),
}


def generate_key(path: str | Path) -> Signer:
    """A fresh Ed25519 key, its seed written to ``path``, readable by its owner
    only. Never overwrites a file."""
    from interlock.operators import generate_key as generate

    return generate(path)


def load_key(path: str | Path) -> Signer:
    """The Ed25519 key in a file :func:`generate_key` wrote."""
    from interlock.operators import load_key as load

    return load(path)


@dataclass(frozen=True, slots=True)
class SignerSpec:
    """One ``[signers.<name>]`` table."""

    name: str
    type: str
    settings: Mapping[str, Any]

    def open(self) -> Signer:
        """The signer, its key pinned now.

        :raises SignerUnavailableError: If the service cannot be reached.
        """
        if self.type == "aws-kms":
            from agentgov.receipts.kms_aws import AwsKmsSigner

            return AwsKmsSigner(str(self.settings["key_id"]), region=self.settings.get("region"))
        from interlock.config import SignerConfig

        version = self.settings.get("version")
        return SignerConfig(
            name=self.name,
            type="http",
            url=str(self.settings["url"]),
            key=str(self.settings["key"]),
            token_env=self.settings.get("token_env"),
            timeout=float(self.settings.get("timeout_seconds", 5.0)),
            version=None if version is None else int(version),
        ).open()


def parse_signers(raw: object) -> dict[str, SignerSpec]:
    """``[signers.<name>]`` tables, checked.

    :raises ValueError: On a table that is not one.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("'signers' is tables: [signers.<name>]")
    found: dict[str, SignerSpec] = {}
    for name, entry in raw.items():
        where = f"[signers.{name}]"
        if not _NAME.fullmatch(str(name)) or not isinstance(entry, dict):
            raise ValueError(
                f"{where}: a signer is a table named by letters, digits, '.', '_' and '-'"
            )
        kind = str(entry.get("type"))
        if kind not in _KEYS:
            raise ValueError(f"{where}: type is 'aws-kms' or 'http', not {kind!r}")
        unknown = sorted(set(entry) - _KEYS[kind])
        if unknown:
            raise ValueError(f"{where}: unknown key(s) {', '.join(unknown)}")
        required = ("key_id",) if kind == "aws-kms" else ("url", "key")
        for key in required:
            if not isinstance(entry.get(key), str) or not entry[key]:
                raise ValueError(f"{where}: {key} is required")
        found[name] = SignerSpec(name, kind, dict(entry))
    return found


def open_signer(
    signers: Mapping[str, SignerSpec], *, key: str | Path | None, signer: str | None
) -> Signer:
    """A key file, or a named ``[signers.<name>]``: exactly one.

    :raises ValueError: On neither, both, or a name no table has.
    """
    if (key is None) == (signer is None):
        raise ValueError("sign with a key file (--key) or a named signer (--signer): one")
    if key is not None:
        return load_key(key)
    assert signer is not None
    if signer not in signers:
        raise ValueError(f"no [signers.{signer}] is configured")
    return signers[signer].open()

"""Key services the signing tests sign through.

- :class:`KeyService`: an HTTP service on ``127.0.0.1`` speaking agentgov's
  :class:`~agentgov.receipts.signing.HttpRemoteSigner` protocol, recording
  every message it is asked to sign.
- :class:`FakeKms`: a boto3 KMS client as :class:`~agentgov.receipts.kms_aws.AwsKmsSigner`
  calls it, enforcing what AWS KMS enforces for an Ed25519 key: key spec
  ``ECC_NIST_EDWARDS25519``, ``ED25519_SHA_512`` over a ``RAW`` message of 1 to
  4096 bytes.
"""

from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from agentgov.receipts.signing import Ed25519Signer

SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self) -> None:  # no reverse lookup of the host name
        self.socket.bind(self.server_address)
        self.server_address = self.socket.getsockname()
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


class KeyService:
    """Named Ed25519 keys, one version each; ``garbage`` makes it answer every
    signing request with 64 bytes that are no signature."""

    def __init__(self) -> None:
        self.keys: dict[str, Ed25519Signer] = {}
        self.messages: list[bytes] = []
        self.garbage = False
        service = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                pass

            def _answer(self, status: int, body: dict[str, Any]) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                name = self.path.split("?")[0].removeprefix("/v1/keys/")
                key = service.keys.get(name)
                if key is None:
                    self._answer(404, {"error": "no such key"})
                    return
                raw = key.public_key().raw
                self._answer(200, {"alg": "ed25519", "version": 1, "public_key": raw.hex()})

            def do_POST(self) -> None:
                name = self.path.removeprefix("/v1/keys/").removesuffix("/sign")
                length = int(self.headers.get("Content-Length", "0"))
                request = json.loads(self.rfile.read(length))
                message = base64.b64decode(request["message"])
                service.messages.append(message)
                key = service.keys[name]
                signature = bytes(64) if service.garbage else key.sign(message)
                self._answer(200, {"version": 1, "signature": signature.hex()})

        self._server = _Server(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def add(self, name: str, key: Ed25519Signer) -> None:
        self.keys[name] = key

    def __enter__(self) -> KeyService:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()


class FakeKms:
    """``boto3.client("kms")``, for one Ed25519 key."""

    def __init__(self, key: Ed25519Signer, *, arn: str = "arn:aws:kms:us-east-1:1:key/fid") -> None:
        self._key = key
        self.arn = arn
        self.messages: list[bytes] = []

    def get_public_key(self, *, KeyId: str) -> dict[str, Any]:  # noqa: N803 - boto3's names
        return {
            "KeyId": self.arn,
            "KeySpec": "ECC_NIST_EDWARDS25519",
            "KeyUsage": "SIGN_VERIFY",
            "SigningAlgorithms": ["ED25519_SHA_512", "ED25519_PH_SHA_512"],
            "PublicKey": SPKI_PREFIX + self._key.public_key().raw,
        }

    def sign(
        self,
        *,
        KeyId: str,  # noqa: N803
        Message: bytes,  # noqa: N803
        MessageType: str,  # noqa: N803
        SigningAlgorithm: str,  # noqa: N803
    ) -> dict[str, Any]:
        if KeyId != self.arn:
            raise ValueError("NotFoundException")
        if MessageType != "RAW" or SigningAlgorithm != "ED25519_SHA_512":
            raise ValueError("UnsupportedOperationException")
        if not 0 < len(Message) <= 4096:
            raise ValueError("ValidationException: Message is 1 to 4096 bytes")
        self.messages.append(Message)
        return {
            "KeyId": self.arn,
            "Signature": self._key.sign(Message),
            "SigningAlgorithm": SigningAlgorithm,
        }

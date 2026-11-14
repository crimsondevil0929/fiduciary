"""Passkeys for principals (1.17.0): registering one, and signing with it.

A principal's key file is a hex seed that any process running as its owner can read: an
agent with a file tool can read ``keys/owner.key`` and sign its own release. A passkey
(Touch ID, Face ID, Windows Hello, a security key) signs only when a person touches it,
and its private key never leaves the authenticator.

- :func:`register` replaces a principal's key with a passkey's, in the charter's next
  version, signed and approved as charter versions are: in a solo deployment by the
  owner's current key alone, which is then moved to ``keys/retired/`` and signs nothing
  more here. With more principals, the version waits for the charter quorum's approvals.
- :class:`BrowserPasskey` signs a FIDU1 document with a passkey through a one-shot page on
  ``http://localhost``: the command that needs the signature (``fiduciary solo enforce``,
  ``fiduciary passkey add`` on a principal that already has one) serves it, opens it, and
  waits for the touch. :func:`create` registers a new passkey the same way.
- :func:`signer_for` is the key a command signs with for a principal: its key file, or its
  passkey.

The console (``fiduciary console``) approves tickets with a passkey on its own page.
"""

from __future__ import annotations

import base64
import http.server
import json
import os
import shutil
import threading
import time
import uuid
import webbrowser
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from fiduciary import fidu1, webauthn
from fiduciary.exceptions import FiduciaryError, SignatureError
from fiduciary.webauthn import Assertion, PasskeyKey, PasskeySigner, Policy

if TYPE_CHECKING:
    from agentgov.receipts.signing import Signer

    from fiduciary.bridge import Fiduciary
    from fiduciary.documents import Charter

__all__ = [
    "BrowserPasskey",
    "Created",
    "PasskeyError",
    "Registered",
    "complete",
    "create",
    "draft_charter",
    "policy_for",
    "register",
    "signer_for",
]

DEFAULT_ORIGIN: Final = "http://localhost:8740"
"""The origin a passkey is registered for unless told otherwise: the console's own."""
WAIT: Final = timedelta(minutes=5)
"""How long a one-shot page waits for the touch."""


class PasskeyError(FiduciaryError):
    """A passkey could not be registered or asked to sign."""


@dataclass(frozen=True, slots=True)
class Registered:
    """What :func:`register` did."""

    principal: str
    key: str
    key_id: str
    charter_version: int
    adopted: bool
    """Whether the version is in force: ``False`` while it waits for more approvals."""
    draft: Path
    retired: Path | None = None
    """Where the principal's old key file was moved, when it was in ``keys/``."""


@dataclass(frozen=True, slots=True)
class Created:
    """A passkey the browser just made: its point, its credential id, the origin."""

    point: bytes
    credential: str
    origin: str


def policy_for(origin: str, *, any_port: bool = False, credential: str | None = None) -> Policy:
    """The policy of a passkey registered on ``origin``: its host is the relying party;
    ``any_port`` admits every port of ``http://localhost`` (the console on a port of
    its own choosing), at the cost that any page on any local port may then ask."""
    found = webauthn.ORIGIN.fullmatch(origin)
    if found is None or found["lport"] == "*":
        raise PasskeyError(f"{origin!r} is no origin a passkey is registered on")
    rp_id = "localhost" if found["local"] else str(found["host"])
    origins = ("http://localhost:*",) if any_port and found["local"] else (origin,)
    try:
        return Policy(rp_id, origins, credential)
    except ValueError as exc:
        raise PasskeyError(str(exc)) from exc


def draft_charter(fid: Fiduciary, principal: str, point: bytes, policy: Policy) -> Charter:
    """The charter's next version, unsigned, with ``principal``'s key the passkey
    ``point``: what the passkey itself signs (:func:`register`, or the console).

    :raises PasskeyError: When the principal is unknown, or its key (or another's) is
        this passkey already.
    """
    from fiduciary.documents import Charter

    charter = fid._authority.decide.charter(fid.log.state())
    member = charter.principal(principal)
    if member is None:
        raise PasskeyError(f"{principal!r} is no principal of the charter")
    key = PasskeyKey(point, policy)
    if member.key == key.spec():
        raise PasskeyError(f"{principal}'s key is this passkey already")
    if any(p.key == key.spec() for p in charter.principals):
        raise PasskeyError("another principal's key is this passkey: one key, one principal")
    body = charter.body()
    body.update(
        {
            "version": charter.version + 1,
            "prev": charter.digest,
            "principals": [
                p.to_json()
                if p.name != principal
                else {"name": principal, "key": key.spec(), "webauthn": policy.to_json()}
                for p in charter.principals
            ],
            "issued_at": fidu1.format_instant(datetime.now(UTC)),
        }
    )
    return Charter.from_json({"v": "FIDU1", "type": "charter", **body}, signed=False)


def complete(
    fid: Fiduciary,
    root: Path,
    principal: str,
    signed: Charter,
    *,
    signer: Signer | PasskeySigner | None = None,
) -> Registered:
    """Propose ``signed`` (a :func:`draft_charter` the passkey signed), approve it with
    ``principal``'s current key (``signer``, or its key file), and adopt it when the
    charter's quorum is one, moving the old key file to ``keys/retired/``.

    :raises PasskeyError: When ``signed`` is not signed by the passkey it names for
        ``principal``, or the principal's current key does not sign here.
    """
    authority = fid._authority
    charter = authority.decide.charter(fid.log.state())
    member = charter.principal(principal)
    named = signed.principal(principal)
    if member is None or named is None or named.webauthn is None:
        raise PasskeyError(f"the charter version makes no passkey {principal!r}'s key")
    try:
        signed.verify(named.public_key)
    except FiduciaryError as exc:
        raise PasskeyError(f"the passkey's signature of the charter: {exc}") from exc
    current = signer if signer is not None else signer_for(fid, root, principal)
    authority.propose(signed)
    approve(fid, charter, principal, current, "charter", signed.digest)
    path = root / f"charter-v{signed.version}.json"
    path.write_text(signed.pretty())
    adopted = charter.charter_quorum <= 1
    retired = None
    if adopted:
        authority.adopt_charter(signed.digest)
        retired = _retire(root, principal, member.key)
    return Registered(
        principal, named.key, named.public_key.key_id, signed.version, adopted, path, retired
    )


def register(
    fid: Fiduciary,
    root: Path,
    principal: str,
    point: bytes,
    policy: Policy,
    *,
    signer: Signer | PasskeySigner | None = None,
    passkey: PasskeySigner | None = None,
) -> Registered:
    """Make ``principal``'s key the passkey ``point``, in the charter's next version.

    The version is signed by the passkey itself (``passkey``, or a page in the browser):
    a charter is signed by one of its own principals, and so the new key proves it is
    held. It is approved by ``signer``, the principal's current key (its key file in
    ``keys/`` by default). When the charter's quorum is one, it is adopted at once and
    the old key file is moved to ``keys/retired/``.

    :raises PasskeyError: When the principal is unknown, its key is already this
        passkey, or no key of its signs here.
    """
    draft = draft_charter(fid, principal, point, policy)
    key = PasskeyKey(point, policy)
    holder = (
        passkey
        if passkey is not None
        else BrowserPasskey(
            principal, key, what=f"the charter version that makes this passkey {principal}'s key"
        )
    )
    if holder.key_id != key.key_id:
        raise PasskeyError("the passkey that signs the charter is not the one it names")
    if signer is None:
        signer = signer_for(fid, root, principal)
    return complete(fid, root, principal, draft.signed(holder), signer=signer)


def approve(
    fid: Fiduciary,
    charter: Charter,
    principal: str,
    signer: Signer | PasskeySigner,
    subject: str,
    digest: str,
) -> None:
    """``principal``'s approval of the document ``digest``, into the intake."""
    from fiduciary.documents import Approval

    now = datetime.now(UTC)
    approval = Approval.from_json(
        {
            "v": "FIDU1",
            "type": "approval",
            "approval_id": str(uuid.uuid4()),
            "charter": {"charter_id": charter.charter_id, "version": charter.version},
            "principal": principal,
            "subject": {"type": subject, "digest": digest},
            "resolution": None,
            "decision": "approve",
            "issued_at": fidu1.format_instant(now),
            "not_after": fidu1.format_instant(now + timedelta(hours=1)),
        },
        signed=False,
    ).signed(signer)
    fid.log.store.submit(approval.canonical())


def _retire(root: Path, principal: str, old_key: str) -> Path | None:
    """Move ``principal``'s key file out of ``keys/``, if it holds the key the charter
    no longer does. Never deleted: its owner decides what becomes of it."""
    from fiduciary.bundle.export import spec as key_spec
    from fiduciary.keys import load_key

    path = root / "keys" / f"{principal}.key"
    if not path.is_file():
        return None
    try:
        if key_spec(load_key(path).public_key()) != old_key:
            return None
    except Exception:  # not a key file this deployment wrote
        return None
    retired = root / "keys" / "retired"
    retired.mkdir(mode=0o700, exist_ok=True)
    target = retired / path.name
    if target.exists():
        target = retired / f"{principal}-{int(time.time())}.key"
    shutil.move(str(path), str(target))
    return target


def signer_for(
    fid: Fiduciary, root: Path, principal: str, *, passkey: PasskeySigner | None = None
) -> Signer | PasskeySigner:
    """The key ``principal`` signs with here: its passkey (``passkey``, or a page in the
    browser) when the charter holds one for it, else its key file in ``keys/``.

    :raises PasskeyError: When the principal is unknown, or its key file is missing or
        not the key the charter holds.
    """
    from fiduciary.bundle.export import spec as key_spec
    from fiduciary.keys import load_key

    charter = fid._authority.decide.charter(fid.log.state())
    member = charter.principal(principal)
    if member is None:
        raise PasskeyError(f"{principal!r} is no principal of the charter")
    if member.webauthn is not None:
        if passkey is not None:
            if passkey.key_id != member.public_key.key_id:
                raise PasskeyError(f"that passkey is not the one the charter holds for {principal}")
            return passkey
        return BrowserPasskey(principal, PasskeyKey.parse(member.key, member.webauthn))
    path = root / "keys" / f"{principal}.key"
    if not path.is_file():
        raise PasskeyError(f"{principal}'s key is not at {path}")
    signer = load_key(path)
    if key_spec(signer.public_key()) != member.key:
        raise PasskeyError(f"{path} is not the key the charter holds for {principal}")
    return signer


# --------------------------------------------------------------------------
# the one-shot page
# --------------------------------------------------------------------------


class BrowserPasskey:
    """A principal's passkey, asked through a one-shot page on ``http://localhost``
    (:class:`~fiduciary.webauthn.PasskeySigner`). Each :meth:`assertion` serves the
    page on the policy's localhost origin, opens it, and waits for the touch.

    :param opener: How the page is opened: the default browser, or a test's driver.
    """

    alg: str = webauthn.ALG

    def __init__(
        self,
        principal: str,
        key: PasskeyKey,
        *,
        what: str = "",
        opener: Callable[[str], object] | None = None,
        wait: timedelta = WAIT,
    ) -> None:
        if key.policy is None:
            raise PasskeyError("a passkey signs only with its policy")
        self.principal = principal
        self.key_id = key.key_id
        self._key = key
        self.what = what
        self._opener = opener or webbrowser.open
        self._wait = wait

    def public_key(self) -> PasskeyKey:
        return self._key

    def assertion(self, message: bytes) -> Assertion:
        policy = self._key.policy
        assert policy is not None
        port = _local_port(policy)
        answer = _one_shot(
            port,
            {
                "mode": "get",
                "principal": self.principal,
                "what": self.what or "a document for this deployment",
                "challenge": _b64(message),
                "rp_id": policy.rp_id,
                "credential": policy.credential,
            },
            self._opener,
            self._wait,
        )
        try:
            return Assertion(
                bytes.fromhex(str(answer["authenticator_data"])),
                bytes.fromhex(str(answer["client_data_json"])),
                bytes.fromhex(str(answer["signature"])),
            )
        except (KeyError, ValueError) as exc:
            raise SignatureError("signature", f"the page answered no assertion: {exc}") from exc


def create(
    principal: str,
    origin: str = DEFAULT_ORIGIN,
    *,
    opener: Callable[[str], object] | None = None,
    wait: timedelta = WAIT,
) -> Created:
    """Make a new passkey for ``principal`` in the browser, on ``origin`` (a
    ``http://localhost`` origin, whose page this serves).

    :raises PasskeyError: When ``origin`` is not a localhost one, or the browser made none.
    """
    found = webauthn.ORIGIN.fullmatch(origin)
    if found is None or not found["local"] or not found["lport"] or found["lport"] == "*":
        raise PasskeyError("a passkey is made here on http://localhost:<port>")
    answer = _one_shot(
        int(found["lport"]),
        {
            "mode": "create",
            "principal": principal,
            "what": f"a passkey for {principal}",
            "challenge": _b64(os.urandom(32)),
            "rp_id": "localhost",
            "user": _b64(principal.encode("utf-8")),
        },
        opener or webbrowser.open,
        wait,
    )
    try:
        point = bytes.fromhex(str(answer["point"]))
        credential = str(answer["credential"])
        made_on = str(answer["origin"])
    except (KeyError, ValueError) as exc:
        raise PasskeyError(f"the page answered no passkey: {exc}") from exc
    if len(point) != 64 or made_on != origin:
        raise PasskeyError("the page answered no P-256 passkey made on this origin")
    return Created(point, credential, made_on)


def _local_port(policy: Policy) -> int:
    for origin in policy.origins:
        if origin == "http://localhost:*":
            return 0
        if origin.startswith("http://localhost:"):
            return int(origin.rsplit(":", 1)[1])
    raise PasskeyError(
        f"this passkey signs on {', '.join(policy.origins)}: sign on that page, not here"
    )


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _one_shot(
    port: int,
    request: Mapping[str, Any],
    opener: Callable[[str], object],
    wait: timedelta,
) -> Mapping[str, Any]:
    """Serve the passkey page on ``localhost:port`` until it posts an answer."""
    done = threading.Event()
    answer: dict[str, Any] = {}
    page = _page().replace("__REQUEST__", json.dumps(dict(request)).replace("</", "<\\/"))

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            return None

        def _port(self) -> int:
            address = self.server.server_address
            return int(address[1]) if isinstance(address, tuple) else 0

        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") == f"localhost:{self._port()}"

        def do_GET(self) -> None:
            if not self._host_ok():
                self.send_error(403, "open this page at http://localhost")
                return
            data = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            origin = self.headers.get("Origin", "")
            if not self._host_ok() or origin != f"http://localhost:{self._port()}":
                self.send_error(403, "cross-origin answers are refused")
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                posted = json.loads(self.rfile.read(min(length, 65536)).decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                posted = None
            if not isinstance(posted, dict):
                self.send_error(400, "send a JSON object")
                return
            answer.update(posted)
            self.send_response(204)
            self.end_headers()
            done.set()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://localhost:{server.server_address[1]}/"
        opener(url)
        if not done.wait(wait.total_seconds()):
            raise PasskeyError(f"no passkey answered at {url} within {wait}")
    finally:
        server.shutdown()
        server.server_close()
    if "error" in answer:
        raise PasskeyError(f"the browser said: {answer['error']}")
    return answer


def _page() -> str:
    """The one-shot page (``report/passkey.html``): it asks the browser for the passkey,
    and posts what it answered back to this process."""
    from importlib.resources import files

    return (files("fiduciary.report") / "passkey.html").read_text(encoding="utf-8")

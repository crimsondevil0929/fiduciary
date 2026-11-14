"""``fiduciary protect``: one command over the MCP servers a desktop client runs (1.17.0).

Claude Desktop, Cursor and Claude Code start each local MCP server from a JSON file:
``{"command": "npx", "args": ["-y", "some-server"], "env": {...}}``. ``protect`` rewrites
each such entry to start ``fiduciary mcp proxy --protected`` instead, which runs the
server itself, behind a solo deployment of its own:

- **Pins.** Each tool's whole definition as the server lists it (name, title, description,
  input and output schemas, annotations) is hashed into its signed binding
  (:func:`pin_of`). The proxy lists the server's tools again when it starts, before every
  call, and whenever the server says its list changed. A tool whose definition moved is
  withheld: it leaves the tool list, a call to it is refused with the reason, and
  ``fiduciary protect review`` shows the change field by field. Pins hold in shadow mode
  too: a rug-pull check that only reports would stop nothing.
- **Versions.** ``npx -y pkg`` becomes ``npx -y pkg@<the version published now>``, and
  ``uvx pkg`` becomes ``uvx pkg==<version>``, so the code that runs is the code that was
  pinned. A server can still change what a tool does without changing its definition;
  the version pin is what catches that, when the package moves.
- **One process.** Reads are called directly. A write commits through the outbox and is
  delivered at once, in the proxy, by Interlock's relay (``Relay.deliver``), and the
  server's own answer goes back to the agent. A write the mandate holds waits for the
  owner's approval in ``fiduciary console``.
- **Secrets.** With ``--move-secrets`` (macOS), each value in the entry's ``env`` moves to
  the Keychain, and the proxy hands it to the server when it starts it: the JSON file no
  longer holds it for any program that reads files.
- **Our own path.** The rewritten entry runs this installation's ``fiduciary`` by its
  absolute path, never ``uvx fiduciary``, which would be one more package resolved at
  every start.

``--undo`` puts every entry back as it was. Remote (HTTP) servers are listed and left as
they are.

**One owner.** Every server under one ``--root`` shares its owner (:class:`Owner`): one key
file, ``owner/owner.key``, until a passkey replaces it, for all of them at once and with one
touch (:func:`register_owner`). A server protected after that starts with the passkey.

**Accepting a change keeps the deployment.** ``fiduciary protect --review --accept`` signs
each changed tool's binding again from what the server lists now, and issues the mandate's
next version pinning them, signed by the owner: its key file, or its passkey, two touches
for every server accepted at once (:func:`accept`). The charter, its keys and passkey, the
log and every record stay where they are.

**A running proxy steps aside.** A proxy holds its deployment open while its client runs.
A command that changes the deployment (``--accept``, a passkey) asks it to let go
(``step-aside.json``, :func:`held`); the proxy answers calls with "call again in a moment"
until the command is done, then opens the deployment again, with whatever changed.
"""

from __future__ import annotations

import contextlib
import copy
import dataclasses
import functools
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Final

from fiduciary import turns
from fiduciary.exceptions import FiduciaryError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from agentgov.receipts.signing import Signer
    from interlock.relay import Relay

    from fiduciary.bridge import CallResult, Fiduciary, Session
    from fiduciary.documents import Charter, ToolBinding
    from fiduciary.mcp import McpServer, Upstream
    from fiduciary.passkeys import Registered
    from fiduciary.webauthn import PasskeyKey, PasskeySigner, Policy

__all__ = [
    "OWNER",
    "PIN_FIELDS",
    "Accepted",
    "Keychain",
    "Owner",
    "OwnerRegistered",
    "ProtectError",
    "Server",
    "Step",
    "accept",
    "client_paths",
    "diff",
    "held",
    "owner_drafts",
    "owner_finish",
    "owner_passkey",
    "pin_command",
    "pin_of",
    "protect",
    "read_servers",
    "register_owner",
    "review",
    "serve",
    "undo",
]

PIN_FIELDS: Final = ("name", "title", "description", "inputSchema", "outputSchema", "annotations")
"""What a tool's pin covers: everything an agent is shown of it, and every hint that
decides how it is governed."""
STATE: Final = "protect.json"
FLAG: Final = "--protected"
SECTIONS: Final = ("mcpServers", "servers")
"""Where a client's file keeps its servers: ``mcpServers`` (Claude Desktop, Cursor,
Claude Code), ``servers`` (VS Code)."""
KEYCHAIN_SERVICE: Final = "fiduciary-protect"
OWNER: Final = "owner"
"""The owner's principal, and its directory under a protect root."""
ASIDE: Final = "step-aside.json"
"""What a command writes into a deployment to ask its running proxy to let go of it."""
ASIDE_WAIT: Final = 30.0
"""How long a command waits, in seconds, for a running proxy to step aside."""
ASIDE_LONGEST: Final = timedelta(minutes=15)
"""How long a proxy stays aside for one ask: a command that never finishes is not waited on."""
NOT_NOW: Final = (
    "Not run: this server's owner is changing what it may do (accepting a tool change, or a "
    "passkey), and nothing was sent to it. Call it again in a moment."
)
_NAME: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")


class ProtectError(FiduciaryError):
    """A client file, a server entry or a deployment ``protect`` cannot use."""


def pin_of(tool: Mapping[str, Any]) -> str:
    """The SHA-256 of a tool's definition, over :data:`PIN_FIELDS` as the server lists
    them (JSON with sorted keys): what a binding's ``pin`` holds."""
    kept = {k: tool[k] for k in PIN_FIELDS if k in tool}
    text = json.dumps(kept, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def diff(old: Mapping[str, Any], new: Mapping[str, Any]) -> list[str]:
    """What changed between two definitions of a tool, field by field: one line each."""
    lines: list[str] = []

    def walk(path: str, a: Any, b: Any) -> None:
        if isinstance(a, Mapping) and isinstance(b, Mapping):
            for key in sorted(set(a) | set(b), key=str):
                walk(f"{path}.{key}" if path else str(key), a.get(key, _GONE), b.get(key, _GONE))
            return
        if a == b:
            return
        if a is _GONE:
            lines.append(f"{path}: added, {_short(b)}")
        elif b is _GONE:
            lines.append(f"{path}: removed (was {_short(a)})")
        else:
            lines.append(f"{path}: {_short(a)} became {_short(b)}")

    walk(
        "", {k: old[k] for k in PIN_FIELDS if k in old}, {k: new[k] for k in PIN_FIELDS if k in new}
    )
    return lines


_GONE: Final = object()


def _short(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= 160 else text[:157] + "..."


# --------------------------------------------------------------------------
# client files
# --------------------------------------------------------------------------


def client_paths() -> dict[str, Path]:
    """Where Claude Desktop and Cursor keep their MCP servers, on this system."""
    home = Path.home()
    if sys.platform == "darwin":
        claude = home / "Library" / "Application Support" / "Claude"
    elif os.name == "nt":
        claude = Path(os.environ.get("APPDATA", str(home))) / "Claude"
    else:
        claude = home / ".config" / "Claude"
    return {
        "claude-desktop": claude / "claude_desktop_config.json",
        "cursor": home / ".cursor" / "mcp.json",
    }


@dataclass(frozen=True, slots=True)
class Server:
    """One server entry of a client's file."""

    client: str
    config: Path
    section: str
    name: str
    spec: Mapping[str, Any]

    @property
    def remote(self) -> bool:
        return "url" in self.spec or str(self.spec.get("type", "stdio")) in ("http", "sse")

    @property
    def protected(self) -> bool:
        args = self.spec.get("args")
        return isinstance(args, list) and FLAG in args

    @property
    def deployment(self) -> Path | None:
        """The deployment a protected entry runs behind."""
        args = self.spec.get("args")
        if not isinstance(args, list) or "--config" not in args:
            return None
        at = args.index("--config")
        return Path(str(args[at + 1])).parent if at + 1 < len(args) else None


def read_servers(config: Path, client: str) -> list[Server]:
    """Every server entry of ``config``.

    :raises ProtectError: When the file is not a JSON object.
    """
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProtectError(f"{config}: no such file") from exc
    except ValueError as exc:
        raise ProtectError(f"{config} is not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ProtectError(f"{config} is not a JSON object")
    found = []
    for section in SECTIONS:
        servers = data.get(section)
        if isinstance(servers, dict):
            for name, spec in servers.items():
                if isinstance(spec, dict):
                    found.append(Server(client, config, section, str(name), spec))
    return found


def _write_config(config: Path, changes: Mapping[tuple[str, str], Mapping[str, Any]]) -> Path:
    """Write ``changes`` (by section and name) into ``config``, keeping a copy of the
    file as it was before ``protect`` first touched it. The new file replaces the old
    in one rename."""
    data = json.loads(config.read_text(encoding="utf-8"))
    for (section, name), spec in changes.items():
        data[section][name] = dict(spec)
    backup = config.with_name(config.name + ".before-fiduciary")
    if not backup.exists():
        shutil.copy2(config, backup)
    temporary = config.with_name(config.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, config)
    return backup


# --------------------------------------------------------------------------
# versions
# --------------------------------------------------------------------------


Resolver = Callable[[str, str], str]
"""``(ecosystem, package) -> version``: ``npm`` or ``pypi``, the version published now."""


def resolve(ecosystem: str, package: str) -> str:
    """The version of ``package`` its registry serves now.

    :raises ProtectError: When it cannot be asked, or does not answer one.
    """
    try:
        if ecosystem == "npm":
            done = subprocess.run(  # noqa: S603 - npm, asked for one version
                ["npm", "view", package, "version", "--json"],  # noqa: S607
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            )
            version = json.loads(done.stdout)
        else:
            url = f"https://pypi.org/pypi/{package}/json"
            with urllib.request.urlopen(url, timeout=30) as answer:
                version = json.loads(answer.read())["info"]["version"]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
        raise ProtectError(
            f"cannot ask {ecosystem} which version of {package} is current: {exc}"
        ) from exc
    if not isinstance(version, str) or not re.fullmatch(r"[0-9A-Za-z.+_-]{1,64}", version):
        raise ProtectError(f"{ecosystem} answered no version for {package}")
    return version


def pin_command(command: Sequence[str], resolver: Resolver) -> tuple[list[str], str | None]:
    """``command`` with the package ``npx`` or ``uvx`` runs pinned to one version, and
    what was pinned; a command that names a version already, or runs a local program,
    is returned as it is."""
    command = list(command)
    exe = Path(command[0]).name.removesuffix(".cmd").removesuffix(".exe") if command else ""
    if exe not in ("npx", "uvx"):
        return command, None
    args = command[1:]
    at = None
    if exe == "uvx" and "--from" in args:
        at = args.index("--from") + 1
    else:
        skip_next = False
        for index, arg in enumerate(args):
            if skip_next:
                skip_next = False
                continue
            if arg in ("-p", "--package", "--python", "--with"):
                skip_next = True
                continue
            if not arg.startswith("-"):
                at = index
                break
    if at is None or at >= len(args):
        return command, None
    spec = args[at]
    if exe == "npx":
        name, _, version = spec[1:].partition("@") if spec.startswith("@") else spec.partition("@")
        name = "@" + name if spec.startswith("@") else name
        if version and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+[0-9A-Za-z.+-]*", version):
            return command, None
        resolved = resolver("npm", name)
        args[at] = f"{name}@{resolved}"
    else:
        if re.search(r"==|@[0-9]", spec):
            return command, None
        name = re.split(r"[\[<>=!~ ]", spec, maxsplit=1)[0]
        resolved = resolver("pypi", name)
        args[at] = f"{spec}=={resolved}" if "[" in spec else f"{name}=={resolved}"
    return [command[0], *args], f"{name} pinned at {resolved}"


# --------------------------------------------------------------------------
# secrets
# --------------------------------------------------------------------------


class Keychain:
    """The macOS Keychain, through ``security``: where ``--move-secrets`` puts each value.

    A value passes on ``security``'s command line for a moment, where another process
    of the same user could read it, as it could read the JSON file it came from.
    """

    def __init__(self, runner: Callable[..., Any] = subprocess.run) -> None:
        self._run = runner

    @staticmethod
    def available() -> bool:
        return sys.platform == "darwin" and shutil.which("security") is not None

    def put(self, account: str, value: str) -> None:
        self._run(
            [
                "security",
                "add-generic-password",
                "-U",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                account,
                "-w",
                value,
            ],
            check=True,
            capture_output=True,
        )

    def get(self, account: str) -> str:
        done = self._run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w"],
            check=True,
            capture_output=True,
            text=True,
        )
        return str(done.stdout).rstrip("\n")

    def delete(self, account: str) -> None:
        with contextlib.suppress(subprocess.CalledProcessError):
            self._run(
                ["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account],
                check=True,
                capture_output=True,
            )


def _account(state: Mapping[str, Any], variable: str) -> str:
    return f"{state['client']}/{state['server']}/{variable}"


# --------------------------------------------------------------------------
# the owner every protected server shares
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Owner:
    """The one owner of every server under a protect root (1.17.0).

    Its key is a key file, ``owner/owner.key``, until a passkey replaces it; then
    ``owner/owner.json`` holds the passkey's public key and policy, and the key file is in
    ``owner/retired/``. A server protected later starts with this owner, so a passkey is
    registered once, for every server.
    """

    path: Path

    @classmethod
    def under(cls, root: Path) -> Owner:
        """The owner of the servers protected under ``root``."""
        return cls((root / OWNER).resolve())

    @classmethod
    def serving(cls, deployment: Path) -> Owner | None:
        """The shared owner of a protected server's deployment; ``None`` for any other."""
        try:
            named = json.loads((deployment / STATE).read_text(encoding="utf-8")).get("owner")
        except (OSError, ValueError, AttributeError):
            return None
        return cls(Path(named)) if isinstance(named, str) and named else None

    @property
    def key_file(self) -> Path:
        return self.path / "owner.key"

    def passkey(self) -> PasskeyKey | None:
        """The owner's passkey, once one replaced its key file."""
        from fiduciary.webauthn import PasskeyKey, Policy

        found = self.path / "owner.json"
        if not found.is_file():
            return None
        data = json.loads(found.read_text(encoding="utf-8"))
        return PasskeyKey.parse(str(data["key"]), Policy.from_json(data["webauthn"]))

    def signer(self) -> Signer:
        """The owner's key file, written the first time it is asked for.

        :raises ProtectError: When the owner signs with a passkey: it has no key file.
        """
        from fiduciary.keys import generate_key, load_key

        if self.passkey() is not None:
            raise ProtectError("the owner signs with a passkey: it has no key file")
        if self.key_file.is_file():
            return load_key(self.key_file)
        self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
        return generate_key(self.key_file)

    def key(self) -> str | None:
        """The owner's key as a charter writes it, or ``None`` before it has one."""
        from fiduciary.bundle.export import spec
        from fiduciary.keys import load_key

        passkey = self.passkey()
        if passkey is not None:
            return passkey.spec()
        return spec(load_key(self.key_file).public_key()) if self.key_file.is_file() else None

    def deployments(self) -> list[Path]:
        """Every deployment this owner owns: each under the protect root whose
        ``protect.json`` names it."""
        return [
            state.parent
            for state in sorted(self.path.parent.glob(f"*/*/{STATE}"))
            if Owner.serving(state.parent) == self
        ]

    def adopt(self, key: PasskeyKey) -> None:
        """Record ``key`` as the owner's: what servers protected from now on start with."""
        assert key.policy is not None
        self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
        (self.path / "owner.json").write_text(
            json.dumps(
                {
                    "principal": OWNER,
                    "key": key.spec(),
                    "key_id": key.key_id,
                    "webauthn": key.policy.to_json(),
                    "since": datetime.now(UTC).isoformat(),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


@dataclass(frozen=True, slots=True)
class OwnerRegistered:
    """What :func:`register_owner` did, server by server."""

    registered: dict[Path, Registered]
    skipped: dict[Path, str]
    """A server whose owner is not this one any more (grown to three principals, say)."""


def register_owner(
    owner: Owner,
    point: bytes,
    policy: Policy,
    *,
    passkey: PasskeySigner | None = None,
    opened: Mapping[Path, Fiduciary] | None = None,
    wait: float = ASIDE_WAIT,
) -> OwnerRegistered:
    """Make the passkey ``point`` the owner's key on every server it owns, with one touch.

    Each server's charter gets its next version naming the passkey. The passkey signs them
    all at once (``passkey``, or a page in the browser): a batch, one assertion over their
    root. The owner's current key approves each, and each is adopted; the key file then
    moves to ``owner/retired/``. A proxy that holds a server steps aside meanwhile (:func:`held`).

    :param opened: Deployments this process holds open already, by root.
    :raises ProtectError: When the owner owns no server, or none could take the passkey.
    """
    from fiduciary.passkeys import BrowserPasskey
    from fiduciary.webauthn import PasskeyKey

    key = PasskeyKey(point, policy)
    roots = owner.deployments()
    if not roots:
        raise ProtectError(f"{owner.path.parent} holds no protected server of this owner")
    with _opened(roots, opened, wait) as fids:
        holder = passkey or BrowserPasskey(OWNER, key)
        if holder.key_id != key.key_id:
            raise ProtectError("the passkey that signs the charters is not the one they name")
        done, skipped = owner_passkey(fids, key, holder)
    if not done:
        raise ProtectError(
            "no server took the passkey: " + "; ".join(f"{r.name}: {w}" for r, w in skipped.items())
        )
    owner.adopt(key)
    return OwnerRegistered(done, skipped)


def owner_passkey(
    fids: Mapping[Path, Fiduciary], key: PasskeyKey, holder: PasskeySigner
) -> tuple[dict[Path, Registered], dict[Path, str]]:
    """The owner's key made ``key`` on each deployment of ``fids``: its charter's next
    versions signed by ``holder`` in one batch, each approved by that server's current
    owner key (grouped by key, so a passkey being replaced also signs once)."""
    from fiduciary.passkeys import sign_all

    drafts, skipped = owner_drafts(fids, key)
    if not drafts:
        return {}, skipped
    order = list(drafts)
    signed = sign_all(
        holder,
        [drafts[r] for r in order],
        what=f"the charters of {len(order)} protected servers, making this passkey their "
        "owner's key",
    )
    return owner_finish(fids, dict(zip(order, signed, strict=True))), skipped


def owner_drafts(
    fids: Mapping[Path, Fiduciary], key: PasskeyKey
) -> tuple[dict[Path, Charter], dict[Path, str]]:
    """Each server's charter's next version making ``key`` the owner's, unsigned; and each
    server that cannot take it, with the reason."""
    from fiduciary.exceptions import FiduciaryError
    from fiduciary.passkeys import draft_charter

    drafts: dict[Path, Charter] = {}
    skipped: dict[Path, str] = {}
    for root, fid in fids.items():
        charter = fid._authority.decide.charter(fid.log.state())
        if len(charter.principals) != 1 or charter.principal(OWNER) is None:
            skipped[root] = "its charter is not one owner's alone"
            continue
        try:
            drafts[root] = draft_charter(fid, OWNER, key.point, key.policy)  # type: ignore[arg-type]
        except FiduciaryError as exc:
            skipped[root] = str(exc)
    return drafts, skipped


def owner_finish(
    fids: Mapping[Path, Fiduciary], signed: Mapping[Path, Charter]
) -> dict[Path, Registered]:
    """Each charter of ``signed`` (signed by the passkey it names) approved by its server's
    current owner key, servers sharing that key in one batch, and adopted."""
    from fiduciary.passkeys import approval_draft, complete, sign_all, signer_for

    groups: dict[str, tuple[Any, list[tuple[Path, Any]]]] = {}
    for root, document in signed.items():
        fid = fids[root]
        charter = fid._authority.decide.charter(fid.log.state())
        current = signer_for(fid, root, OWNER)
        groups.setdefault(current.key_id, (current, []))[1].append(
            (root, approval_draft(charter, OWNER, "charter", document.digest))
        )
    approvals = {}
    for current, items in groups.values():
        made = sign_all(current, [d for _, d in items], what="the owner's new passkey")
        approvals.update({root: a for (root, _), a in zip(items, made, strict=True)})
    return {
        root: complete(fids[root], root, OWNER, document, approval=approvals[root])
        for root, document in signed.items()
    }


# --------------------------------------------------------------------------
# a running proxy steps aside
# --------------------------------------------------------------------------


def _claimed(exc: BaseException) -> bool:
    from fiduciary.service import _claimed as claimed

    found: BaseException | None = exc
    while found is not None:
        if claimed(found) or type(found).__name__ == "ConcurrentGovernorError":
            return True
        found = found.__cause__
    return False


def _free(root: Path) -> bool:
    """Whether no other process holds the deployment at ``root`` (its ledger)."""
    from agentgov import BudgetManager

    try:
        BudgetManager.open_sqlite(str(root / "ledger.db")).close()
    except Exception as exc:
        if _claimed(exc):
            return False
        raise
    return True


def _alive(pid: int) -> bool:
    if os.name == "nt":  # no signal 0 there: os.kill would end the process
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def asked_aside(root: Path) -> bool:
    """Whether a command asks the proxy of ``root`` to step aside now. An ask whose command
    is gone, or older than :data:`ASIDE_LONGEST`, is removed, and answered no."""
    path = root / ASIDE
    try:
        asked = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        asked = {}
    pid = asked.get("pid") if isinstance(asked, dict) else None
    at = asked.get("asked_at") if isinstance(asked, dict) else None
    try:
        fresh = datetime.now(UTC) - datetime.fromisoformat(str(at)) < ASIDE_LONGEST
    except ValueError:
        fresh = False
    if isinstance(pid, int) and _alive(pid) and fresh:
        return True
    with contextlib.suppress(OSError):
        path.unlink()
    return False


@contextlib.contextmanager
def held(roots: Sequence[Path], *, wait: float = ASIDE_WAIT) -> Iterator[None]:
    """While the block runs, no protected server's proxy holds any deployment of ``roots``:
    each one that does is asked to step aside (``step-aside.json``), and opens its
    deployment again once the block is done. A deployment ``fiduciary protect`` did not
    write is not asked.

    :raises ProtectError: When a proxy did not step aside within ``wait`` seconds.
    """
    asked: list[Path] = []
    try:
        for root in roots:
            if (root / STATE).is_file() and not _free(root):
                (root / ASIDE).write_text(
                    json.dumps({"pid": os.getpid(), "asked_at": datetime.now(UTC).isoformat()}),
                    encoding="utf-8",
                )
                asked.append(root)
        deadline = time.monotonic() + wait
        for root in asked:
            while not _free(root):
                if time.monotonic() > deadline:
                    raise ProtectError(
                        f"the proxy serving {root} did not step aside within {wait:.0f} "
                        f"seconds: quit its client, run this again, then start the client"
                    )
                time.sleep(0.05)
        yield
    finally:
        for root in asked:
            with contextlib.suppress(OSError):
                (root / ASIDE).unlink()


@contextlib.contextmanager
def _opened(
    roots: Sequence[Path], opened: Mapping[Path, Fiduciary] | None, wait: float
) -> Iterator[dict[Path, Fiduciary]]:
    """Each deployment of ``roots`` open in this process (those in ``opened`` as they
    are), its proxy aside (:func:`held`), and closed again after the block."""
    from fiduciary.bridge import Fiduciary

    given = dict(opened or {})
    mine: dict[Path, Fiduciary] = {}
    with held([r for r in roots if r not in given], wait=wait):
        try:
            for root in roots:
                if root not in given:
                    mine[root] = Fiduciary.from_config(root / "fiduciary.toml")
            yield {root: given.get(root) or mine[root] for root in roots}
        finally:
            for fid in mine.values():
                with contextlib.suppress(Exception):
                    fid.close()


# --------------------------------------------------------------------------
# protect, undo, review
# --------------------------------------------------------------------------


@dataclass
class Step:
    """What ``protect`` did, or would do, with one server entry."""

    server: Server
    action: str
    """``wrap``, ``skip`` or ``unwrap``."""
    why: str
    command: list[str] = field(default_factory=list)
    pinned: str | None = None
    secrets: list[str] = field(default_factory=list)
    deployment: Path | None = None
    tools: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)


def executable() -> list[str]:
    """This installation's ``fiduciary``, by absolute path: what a protected entry runs."""
    found = shutil.which("fiduciary")
    here = Path(sys.executable).resolve().parent / "fiduciary"
    for candidate in (here, Path(found) if found else None):
        if candidate is not None and candidate.is_file() and os.access(candidate, os.X_OK):
            return [str(candidate.resolve())]
    return [str(Path(sys.executable).resolve()), "-m", "fiduciary"]


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")
    if not slug or not slug[0].isalpha():
        slug = "server_" + slug
    return slug[:48]


def protect(
    servers: Sequence[Server],
    *,
    root: Path,
    apply: bool = False,
    shadow: bool = True,
    move_secrets: bool = False,
    resolver: Resolver = resolve,
    keychain: Keychain | None = None,
    discover: Callable[[Sequence[str], Mapping[str, str]], list[dict[str, Any]]] | None = None,
    runner: Sequence[str] | None = None,
    passkey: PasskeySigner | None = None,
) -> list[Step]:
    """Protect each stdio server of ``servers``; with ``apply`` false, only say what would
    be done. A deployment is written for each under ``root/<client>/<server>``, and the
    client's file is rewritten once, at the end.

    Every server under ``root`` has one owner (:class:`Owner`). While its key is a key
    file, a new server's charter names that key. Once it is a passkey, each new server
    starts with a key of its own, and its charter's next version names the passkey at once:
    the passkey signs them all with one touch (``passkey``, or a page in the browser), and
    the key of its own is retired.

    :param shadow: Start each mandate in shadow mode (pins hold either way).
    :param runner: The command a protected entry runs (:func:`executable`, by default).
    :raises ProtectError: On a secret move asked for where there is no Keychain.
    """
    from fiduciary.init import init

    if move_secrets and keychain is None:
        if not Keychain.available():
            raise ProtectError("--move-secrets needs the macOS Keychain (security)")
        keychain = Keychain()
    run = list(runner or executable())
    owner = Owner.under(root)
    steps: list[Step] = []
    made: list[Path] = []
    changes: dict[Path, dict[tuple[str, str], dict[str, Any]]] = {}
    for server in servers:
        if server.protected:
            steps.append(Step(server, "skip", "protected already"))
            continue
        if server.remote:
            steps.append(Step(server, "skip", "a remote server: this version wraps stdio servers"))
            continue
        command_text = server.spec.get("command")
        args = server.spec.get("args") or []
        if not isinstance(command_text, str) or not isinstance(args, list):
            steps.append(Step(server, "skip", "no command to run"))
            continue
        command, pinned = pin_command([command_text, *map(str, args)], resolver)
        env = {str(k): str(v) for k, v in (server.spec.get("env") or {}).items()}
        deployment = root / _slug(server.client) / _slug(server.name)
        step = Step(
            server,
            "wrap",
            "a stdio server",
            command,
            pinned,
            sorted(env) if move_secrets else [],
            deployment,
        )
        steps.append(step)
        if not apply:
            continue
        if deployment.exists() and any(deployment.iterdir()):
            raise ProtectError(f"{deployment} is not empty: protect writes a fresh deployment")
        listed = (discover or _discover)(command, env)
        report = init(
            deployment,
            command,
            discovered=listed,
            solo=True,
            shadow=shadow,
            name=_slug(server.name),
            owner=owner.signer() if owner.passkey() is None else None,
        )
        made.append(deployment)
        step.tools, step.skipped = list(report.tools), list(report.skipped)
        state: dict[str, Any] = {
            "owner": str(owner.path),
            "client": server.client,
            "config": str(server.config),
            "section": server.section,
            "server": server.name,
            "original": copy.deepcopy(dict(server.spec)),
            "command": command,
            "pinned": pinned,
            "secrets": [],
            "env": env,
            "protected_at": datetime.now(UTC).isoformat(),
        }
        if move_secrets and env:
            assert keychain is not None
            for variable, value in env.items():
                keychain.put(_account(state, variable), value)
            state["secrets"] = sorted(env)
            state["env"] = {}
            state["original"]["env"] = {k: "(in the Keychain)" for k in env}
        (deployment / STATE).write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        os.chmod(deployment / STATE, 0o600)
        entry: dict[str, Any] = {
            "command": run[0],
            "args": [
                *run[1:],
                "--config",
                str(deployment.resolve() / "fiduciary.toml"),
                "mcp",
                "proxy",
                FLAG,
            ],
        }
        if state["env"]:
            entry["env"] = state["env"]
        changes.setdefault(server.config, {})[(server.section, server.name)] = entry
    key = owner.passkey() if made else None
    if key is not None:
        # The owner signs with a passkey: each new server takes it now, with one touch.
        from fiduciary.passkeys import BrowserPasskey

        try:
            with _opened(made, None, ASIDE_WAIT) as fids:
                _, skipped = owner_passkey(fids, key, passkey or BrowserPasskey(OWNER, key))
            if skipped:
                raise ProtectError("; ".join(f"{r.name}: {why}" for r, why in skipped.items()))
        except BaseException:
            for deployment in made:  # written by this call alone, with nothing in them yet
                shutil.rmtree(deployment, ignore_errors=True)
            raise
    for config, changed in changes.items():
        _write_config(config, changed)
    return steps


def claude_code_turns(
    project: Path, deployments: Sequence[Path], run: Sequence[str] | None = None
) -> Path:
    """Write Claude Code's turn hooks into ``project``'s ``.claude/settings.local.json``:
    ``UserPromptSubmit`` opens a turn on each protected server's deployment, ``Stop``
    closes it. Every other setting and hook is kept; ours are added once."""
    import shlex

    run = list(run or executable())
    path = project / ".claude" / "settings.local.json"
    current: dict[str, Any] = {}
    if path.is_file():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ProtectError(f"{path} is not JSON: {exc}") from exc
    hooks = current.setdefault("hooks", {})
    for event, step in (("UserPromptSubmit", "open"), ("Stop", "close")):
        entries = hooks.setdefault(event, [])
        for deployment in deployments:
            config = str(deployment.resolve() / "fiduciary.toml")
            command = " ".join(
                shlex.quote(part) for part in [*run, "--config", config, "turn", step, "--hook"]
            )
            if not any(h.get("command") == command for e in entries for h in e.get("hooks", ())):
                entries.append({"hooks": [{"type": "command", "command": command}]})
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    return path


def undo(
    servers: Sequence[Server], *, apply: bool = True, keychain: Keychain | None = None
) -> list[Step]:
    """Put every protected entry of ``servers`` back as it was before ``protect``; its
    deployment stays where it is, with its records. Secrets come back from the Keychain."""
    steps: list[Step] = []
    changes: dict[Path, dict[tuple[str, str], dict[str, Any]]] = {}
    for server in servers:
        root = server.deployment
        if not server.protected or root is None:
            continue
        try:
            state = json.loads((root / STATE).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            steps.append(Step(server, "skip", f"its deployment's {STATE} cannot be read: {exc}"))
            continue
        original = copy.deepcopy(state["original"])
        if state.get("secrets"):
            chain = keychain or Keychain()
            original["env"] = {v: chain.get(_account(state, v)) for v in state["secrets"]}
        steps.append(Step(server, "unwrap", "put back as it was", deployment=root))
        changes.setdefault(server.config, {})[(server.section, server.name)] = original
    if apply:
        for config, changed in changes.items():
            _write_config(config, changed)
    return steps


@dataclass(frozen=True, slots=True)
class Drift:
    """A tool whose server lists another definition than its pin."""

    tool: str
    upstream: str
    lines: tuple[str, ...]
    """Field by field; ``("the server no longer lists it",)`` for a tool that is gone."""


def review(
    root: Path, *, upstream: Upstream | None = None, keychain: Keychain | None = None
) -> list[Drift]:
    """Each pinned tool of the deployment at ``root`` whose server now lists another
    definition, and how it changed, against the definition pinned (``pins/``)."""
    from fiduciary.documents import ToolBinding
    from fiduciary.mcp import Upstream

    state = json.loads((root / STATE).read_text(encoding="utf-8"))
    own = upstream is None
    server = upstream or Upstream(state["command"], env=_environment(state, keychain))
    try:
        listed = {str(t.get("name")): t for t in server.definitions()}
    finally:
        if own:
            server.close()
    drifts = []
    for path in sorted((root / "tools").glob("*.tool.json")):
        binding = ToolBinding.loads(path.read_bytes())
        if binding.pin is None:
            continue
        name = binding.upstream or binding.name
        now = listed.get(name)
        if now is None:
            drifts.append(Drift(binding.name, name, ("the server no longer lists it",)))
        elif pin_of(now) != binding.pin:
            pinned_file = root / "pins" / f"{binding.name}.json"
            old = json.loads(pinned_file.read_text()) if pinned_file.exists() else {}
            drifts.append(Drift(binding.name, name, tuple(diff(old, now)) or ("a field changed",)))
    return drifts


@dataclass(frozen=True, slots=True)
class Accepted:
    """What :func:`accept` issued for one server."""

    version: int
    """The mandate version that pins what the server lists now."""
    changed: tuple[str, ...]
    """Each binding signed again, added, or no longer pinned."""
    tools: tuple[dict[str, str], ...]
    skipped: tuple[dict[str, str], ...]


def accept(
    roots: Sequence[Path],
    *,
    discover: Callable[[Sequence[str], Mapping[str, str]], list[dict[str, Any]]] | None = None,
    keychain: Keychain | None = None,
    passkey: PasskeySigner | None = None,
    wait: float = ASIDE_WAIT,
) -> dict[Path, Accepted]:
    """Accept what each server of ``roots`` lists now, in the deployment it has (1.17.0).

    Each tool's binding is signed again from its definition now, with the deployment's
    developer key (a binding no longer pinned is kept, under ``tools/retired/``, so the
    plans that cite it still verify). The outbox's sink and ``interlock.toml`` follow the
    tools that write. Then the mandate's next version pins the new bindings, signed and
    approved by the owner: its key file, or its passkey, one touch to sign every server's
    version and one to approve them. The charter, its keys and passkey, the log and every
    record stay as they are. A running proxy steps aside meanwhile (:func:`held`).

    :raises ProtectError: When a server's mandate cannot be read, or its owner cannot sign.
    """
    from interlock.sqlite_outbox import install_sqlite_outbox

    from fiduciary.demo.packs import kit
    from fiduciary.documents import ToolBinding
    from fiduciary.init import _pack, _relay_toml, govern, write_pins
    from fiduciary.keys import load_key
    from fiduciary.passkeys import approval_draft, sign_all, signer_for
    from fiduciary.solo import draft_next

    listings = {}
    for root in roots:
        state = json.loads((root / STATE).read_text(encoding="utf-8"))
        listed = (discover or _discover)(state["command"], _environment(state, keychain))
        listings[root] = (state, listed, govern(listed))
    accepted: dict[Path, Accepted] = {}
    with held(roots, wait=wait):
        pins: dict[Path, list[dict[str, str]]] = {}
        packs: dict[Path, Any] = {}
        changed: dict[Path, tuple[str, ...]] = {}
        for root, (state, listed, governed) in listings.items():
            mandate = json.loads((root / "mandate.json").read_text(encoding="utf-8"))
            label = str(mandate["subject"]["scope"]).removesuffix("-agent")
            pack = _pack(
                label,
                (OWNER,),
                governed.bindings,
                governed.modes,
                governed.schemas,
                {},
            )
            developer = load_key(root / "keys" / "developer.key")
            bindings = [
                ToolBinding.from_json(raw, signed=False).signed(developer)
                for raw in governed.bindings
            ]
            changed[root] = _write_bindings(root, bindings)
            back_office = root / "back_office.db"
            # A write no longer listed keeps its operation: the version still in force pins
            # its binding until the next one is issued, and its plans' requests name it.
            pack = dataclasses.replace(pack, sinks=_with_retired(root, pack.sinks))
            interlock = kit.interlock_toml(root, pack, back_office)
            if any(m != "read" for m in governed.modes.values()):
                interlock += _relay_toml(root, state["command"], governed.bindings)
            (root / "interlock.toml").write_text(interlock, encoding="utf-8")
            install_sqlite_outbox(back_office, pack.sinks, pack.sources)
            write_pins(root, listed, governed.bindings)
            with contextlib.suppress(FileNotFoundError):
                (root / "drift.json").unlink()
            pins[root] = sorted(
                ({"name": b.name, "digest": b.digest} for b in bindings), key=lambda x: x["name"]
            )
            packs[root] = pack
        with _opened(list(listings), None, wait) as fids:
            drafts = {}
            for root, fid in fids.items():
                state_now = fid.log.state()
                charter = fid._authority.decide.charter(state_now)
                mandate_id = json.loads((root / "mandate.json").read_text())["mandate_id"]
                head = fid._authority.decide.head_mandate(
                    state_now, fid._lineage(state_now, mandate_id)
                )
                tools = head.body()["tools"]
                drafts[root] = draft_next(
                    head,
                    charter,
                    tools={**tools, "bindings": pins[root]},
                    outbound=packs[root].outbound(),
                    windows=packs[root].windows(),
                )
            signer = functools.partial(signer_for, passkey=passkey)
            signed = _sign_grouped(fids, drafts, "the mandate's next version", signer, sign_all)
            for root, document in signed.items():
                fids[root]._authority.propose(document)
            approvals = _sign_grouped(
                fids,
                {
                    root: approval_draft(
                        fids[root]._authority.decide.charter(fids[root].log.state()),
                        OWNER,
                        "mandate",
                        document.digest,
                    )
                    for root, document in signed.items()
                },
                "approvals of the mandate's next version",
                signer,
                sign_all,
            )
            for root, document in signed.items():
                fid = fids[root]
                fid.log.store.submit(approvals[root].canonical())
                fid.binder.issue(document.digest)
                (root / "mandate.json").write_text(document.pretty())
                _, _, governed = listings[root]
                accepted[root] = Accepted(
                    document.version,
                    changed[root],
                    tuple(governed.tools),
                    tuple(governed.skipped),
                )
    return accepted


def _sign_grouped(
    fids: Mapping[Path, Fiduciary],
    drafts: Mapping[Path, Any],
    what: str,
    signer_for: Callable[..., Any],
    sign_all: Callable[..., list[Any]],
) -> dict[Path, Any]:
    """Each draft signed by its server's owner, servers that share an owner's key in one
    batch: one touch, when that key is a passkey."""
    groups: dict[str, tuple[Any, list[Path]]] = {}
    for root in drafts:
        signer = signer_for(fids[root], root, OWNER)
        groups.setdefault(signer.key_id, (signer, []))[1].append(root)
    out: dict[Path, Any] = {}
    for signer, members in groups.values():
        made = sign_all(signer, [drafts[r] for r in members], what=f"{what}: {len(members)}")
        out.update(zip(members, made, strict=True))
    return out


def _with_retired(root: Path, sinks: Sequence[Any]) -> tuple[Any, ...]:
    """``sinks``, with each operation the deployment's sinks have now and ``sinks`` leave
    out added back as it is."""
    from fiduciary.config import load_config

    current = {s.name: s for s in load_config(root / "fiduciary.toml").interlock_config().sinks}
    merged = []
    for sink in sinks:
        old = current.pop(sink.name, None)
        names = {op.name for op in sink.operations}
        kept = [op for op in (old.operations if old is not None else ()) if op.name not in names]
        merged.append(dataclasses.replace(sink, operations=(*sink.operations, *kept)))
    return (*merged, *current.values())


def _write_bindings(root: Path, bindings: Sequence[ToolBinding]) -> tuple[str, ...]:
    """Write ``bindings`` into ``tools/``; one whose file held another binding moves that
    one to ``tools/retired/``, as does a binding no longer listed, and ``fiduciary.toml``
    lists every file, retired ones too: an earlier version's plans cite them. Each
    binding name that changed, was added or was retired."""
    from fiduciary.documents import ToolBinding

    tools = root / "tools"
    retired = tools / "retired"
    names = {b.name for b in bindings}
    changed: list[str] = []
    for binding in bindings:
        path = tools / f"{binding.name}.tool.json"
        if path.is_file():
            old = ToolBinding.loads(path.read_bytes())
            if old.digest == binding.digest:
                continue
            retired.mkdir(exist_ok=True)
            os.replace(path, retired / f"{old.name}.{old.digest[:12]}.tool.json")
        path.write_text(binding.pretty())
        changed.append(binding.name)
    for path in sorted(tools.glob("*.tool.json")):
        old = ToolBinding.loads(path.read_bytes())
        if old.name not in names:
            retired.mkdir(exist_ok=True)
            os.replace(path, retired / f"{old.name}.{old.digest[:12]}.tool.json")
            changed.append(old.name)
    listed = [str(p.resolve()) for p in sorted(tools.glob("*.tool.json"))]
    listed += [str(p.resolve()) for p in sorted(retired.glob("*.tool.json"))]
    config = root / "fiduciary.toml"
    text = config.read_text(encoding="utf-8")
    replaced, count = re.subn(
        r"(?m)^(\[binding\]\n)tools = \[.*\]$",
        lambda m: m[1] + "tools = " + json.dumps(listed),
        text,
    )
    if count != 1:
        raise ProtectError(f"{config} has no [binding] tools list to rewrite")
    config.write_text(replaced, encoding="utf-8")
    return tuple(sorted(changed))


def _environment(state: Mapping[str, Any], keychain: Keychain | None) -> dict[str, str]:
    env = {str(k): str(v) for k, v in (state.get("env") or {}).items()}
    if state.get("secrets"):
        chain = keychain or Keychain()
        env.update({v: chain.get(_account(state, v)) for v in state["secrets"]})
    return env


def _discover(command: Sequence[str], env: Mapping[str, str]) -> list[dict[str, Any]]:
    from fiduciary.mcp import Upstream

    upstream = Upstream(command, env=env)
    try:
        return upstream.definitions()
    finally:
        upstream.close()


# --------------------------------------------------------------------------
# the proxy
# --------------------------------------------------------------------------


class Guard:
    """The pins of one protected server, held against what it lists."""

    def __init__(self, fid: Fiduciary, session: Session, upstream: Upstream, root: Path) -> None:
        self._fid = fid
        self._session = session
        self._upstream = upstream
        self._root = root
        self.notes: list[str] = []

    def bindings(self) -> dict[str, ToolBinding]:
        return self._session._bindings()

    def check(self) -> bool:
        """List the server's tools and withhold each whose definition moved from its pin.
        Whether the set withheld changed."""
        listed = {str(t.get("name")): t for t in self._upstream.definitions()}
        withheld: dict[str, str] = {}
        drifted: dict[str, Any] = {}
        for name, binding in self.bindings().items():
            if binding.pin is None:
                continue
            upstream = binding.upstream or name
            now = listed.get(upstream)
            if now is None:
                withheld[name] = (
                    f"Not run: the server no longer lists {upstream}. Its owner reviews the "
                    f"change with fiduciary protect review."
                )
            elif pin_of(now) != binding.pin:
                withheld[name] = (
                    f"Not run: the server changed how it describes {upstream} since it was "
                    f"approved. Nothing was sent to it. Its owner reviews the change with "
                    f"fiduciary protect review."
                )
                drifted[name] = now
        changed = withheld != self._fid.withheld
        self._fid.withheld.clear()
        self._fid.withheld.update(withheld)
        if drifted:
            record = self._root / "drift.json"
            found = json.loads(record.read_text()) if record.exists() else {}
            for name, now in drifted.items():
                pinned_file = self._root / "pins" / f"{name}.json"
                old = json.loads(pinned_file.read_text()) if pinned_file.exists() else {}
                found[name] = {"seen_at": datetime.now(UTC).isoformat(), "changes": diff(old, now)}
            record.write_text(json.dumps(found, indent=2) + "\n")
        if changed:
            self.notes.append("notifications/tools/list_changed")
            for name in sorted(withheld):
                print(f"fiduciary protect: {name} withheld: {withheld[name]}", file=sys.stderr)
        return changed


def relay_for(root: Path, upstream: Upstream, bindings: Mapping[str, ToolBinding]) -> Relay:
    """Interlock's relay for this deployment's outbox, delivering through ``upstream``:
    a write is made in this process, under the same claim, breaker and signed outcome as
    any relay's."""
    from interlock.mcp import McpAdapter
    from interlock.relay import LedgerBreaker, Relay
    from interlock.sqlite_outbox import SqliteOutboxStore

    from fiduciary.init import SINK
    from fiduciary.keys import load_key

    writes = [b for b in bindings.values() if b.kind == "plan"]
    adapter = McpAdapter(
        upstream.call,
        tools={b.name: b.upstream for b in writes if b.upstream},
        numbers={b.name: list(b.numbers) for b in writes if b.numbers},
    )
    return Relay(
        SqliteOutboxStore(str(root / "back_office.db")),
        adapters={SINK: adapter},
        breaker=LedgerBreaker.open(str(root / "ledger.db")),
        signer=load_key(root / "keys" / "relay-a.key"),
        lease=timedelta(seconds=90),
        timeout=timedelta(seconds=30),
    )


def plan_messages(root: Path, plan_id: str) -> list[Any]:
    """The outbox messages a committed plan wrote, in order."""
    import sqlite3
    import uuid

    from interlock.sqlite_outbox import OUTBOX

    with contextlib.closing(
        sqlite3.connect(f"{(root / 'back_office.db').as_uri()}?mode=ro", uri=True)
    ) as conn:
        rows = conn.execute(
            # the outbox's own table name, a module constant; the plan id is a parameter
            f"SELECT message_id FROM {OUTBOX} WHERE plan_id = ? ORDER BY seq",  # noqa: S608
            (plan_id,),
        ).fetchall()
    return [uuid.UUID(str(r[0])) for r in rows]


class _Proxy:
    """One protected server's proxy: the deployment it holds open, and the session, pins and
    relay on it. It lets the deployment go when a command asks (:func:`held`), and opens it
    again, with whatever that command changed, once it is done."""

    def __init__(self, config: Path, upstream: Upstream) -> None:
        self.config = config
        self.root = config.resolve().parent
        self.upstream = upstream
        self.fid: Fiduciary | None = None
        self.session: Session | None = None
        self.guard: Guard | None = None
        self.relay: Relay | None = None
        self.mandate_id = ""
        self.notes: list[str] = []
        self.listed: list[dict[str, Any]] = []
        """The tools last listed: what a client is shown while the proxy is aside."""
        self.server: McpServer | None = None

    def open(self) -> None:
        from interlock.mcp import numbers_on_the_wire

        from fiduciary.bridge import Fiduciary

        fid = Fiduciary.from_config(self.config, bridged=True)
        try:
            mandate = json.loads((self.root / "mandate.json").read_text())
            session = fid.session(mandate["subject"]["scope"], bridged=True)
            guard = Guard(fid, session, self.upstream, self.root)
            guard.notes = self.notes
            bindings = guard.bindings()
            for name, binding in bindings.items():
                if binding.kind == "passthrough":
                    fid.register(
                        name,
                        _numbered(
                            self.upstream.handler(name, binding.upstream),
                            binding.numbers,
                            numbers_on_the_wire,
                        ),
                    )
            relay = (
                relay_for(self.root, self.upstream, bindings)
                if any(b.kind == "plan" for b in bindings.values())
                else None
            )
            guard.check()
            if turns.closing(self.root):  # asked to close while no proxy held it
                turns.close_file(fid, self.root, str(mandate["mandate_id"]))
        except BaseException:
            with contextlib.suppress(Exception):
                fid.close()
            raise
        self.fid, self.session, self.guard, self.relay = fid, session, guard, relay
        self.mandate_id = str(mandate["mandate_id"])
        if self.server is not None:
            self.server._session = session

    def close(self) -> None:
        if self.relay is not None:
            with contextlib.suppress(Exception):
                self.relay.close()
        if self.fid is not None:
            with contextlib.suppress(Exception):
                self.fid.close()
        self.fid = self.session = self.guard = self.relay = None

    def tick(self) -> None:
        """One turn between messages: step aside when asked, come back when the ask is gone,
        and otherwise serve the deployment (warrants, closed turns, the relay)."""
        from fiduciary.service import raise_triggers

        if self.fid is None:
            if asked_aside(self.root):
                return
            try:
                self.open()
            except Exception as exc:
                if not _claimed(exc):
                    raise
                return  # another process holds it still: asked again next turn
            self.notes.append("notifications/tools/list_changed")
            return
        if asked_aside(self.root):
            self.close()
            return
        fid = self.fid
        fid.serve_once()
        if fid.outbox is not None:
            raise_triggers(fid)
        if turns.closing(self.root):
            turns.close_file(fid, self.root, self.mandate_id)  # Claude Code stopped
        if self.relay is not None:
            self.relay.run_once()  # what a closed turn released, and any retry now due


def serve(
    config: Path,
    reader: IO[bytes],
    writer: IO[bytes],
    *,
    keychain: Keychain | None = None,
) -> None:
    """Serve one client over stdio, in front of the protected server (module docstring)."""
    from fiduciary.mcp import McpServer, Upstream, serve_stdio

    root = config.resolve().parent
    state = json.loads((root / STATE).read_text(encoding="utf-8"))
    upstream = Upstream(state["command"], env=_environment(state, keychain))
    proxy = _Proxy(config, upstream)
    gate = threading.RLock()
    try:
        if not asked_aside(root):
            try:
                proxy.open()
            except Exception as exc:
                if not _claimed(exc):
                    raise
        # With a command holding the deployment, the proxy starts aside (no session yet),
        # and opens it once that command is done.
        placeholder: Any = proxy.session

        class Protected(McpServer):
            def tool_list(self) -> list[dict[str, Any]]:
                if proxy.fid is None:
                    return list(proxy.listed)
                with proxy.fid.lock:
                    proxy.listed = super().tool_list()
                return list(proxy.listed)

            def _dispatch(
                self, method: str, params: Mapping[str, Any], *, modern: bool = False
            ) -> dict[str, Any]:
                fid, session, guard = proxy.fid, proxy.session, proxy.guard
                if fid is None or session is None or guard is None:
                    if method == "tools/call":
                        return {"content": [{"type": "text", "text": NOT_NOW}], "isError": True}
                    return super()._dispatch(method, params, modern=modern)
                if method == "tools/list" or method == "tools/call" or proxy.upstream.changed:
                    with fid.lock:
                        guard.check()  # before every call: a definition that moved is withheld
                if method != "tools/call":
                    return super()._dispatch(method, params, modern=modern)
                name = params.get("name")
                arguments = params.get("arguments", {})
                if not isinstance(name, str) or not isinstance(arguments, dict):
                    return super()._dispatch(method, params, modern=modern)
                found = turns.current(root)
                with fid.lock:
                    session.turn = None if found is None else str(found["turn"])
                    result = session.call(name, arguments)
                    session.turn = None
                if found is not None and not result.ok:
                    turns.note_refused(root, f"{name} was refused")
                return _answer(
                    result, proxy.relay, root, turn=None if found is None else str(found["turn"])
                )

            def drain(self) -> list[dict[str, Any]]:
                notes = [{"jsonrpc": "2.0", "method": n} for n in dict.fromkeys(proxy.notes)]
                proxy.notes.clear()
                return notes

        proxy.server = Protected(placeholder, lock=gate)
        serve_stdio(proxy.server, reader, writer, service=proxy.tick, lock=gate)
    finally:
        proxy.close()
        upstream.close()


def _numbered(
    handler: Callable[[Mapping[str, Any]], Any],
    paths: Sequence[str],
    convert: Callable[[Any, Sequence[str]], Any],
) -> Callable[[Mapping[str, Any]], Any]:
    if not paths:
        return handler
    return lambda arguments: handler(convert(dict(arguments), paths))


def _answer(
    result: CallResult, relay: Relay | None, root: Path, *, turn: str | None = None
) -> dict[str, Any]:
    """What the agent is told of a call: the server's own answer, for a read and for a
    write delivered now; the session's words, for anything else."""
    value = result.value
    if isinstance(value, dict) and "content" in value:
        return {"content": value["content"], "isError": bool(value.get("isError")) or not result.ok}
    if result.committed and relay is not None and isinstance(value, dict) and not value.get("held"):
        answers: list[dict[str, Any]] = []
        error = False
        for message in plan_messages(root, str(value.get("plan_id"))):
            done = relay.deliver(message)
            body = done.result.body if done.result is not None else None
            if body is not None:
                answer = json.loads(body)
                answers.extend(c for c in answer.get("content") or () if isinstance(c, dict))
                error = error or bool(answer.get("isError")) or not done.delivered
                if isinstance(answer.get("structuredContent"), dict) and len(answers) <= 1:
                    return {
                        "content": answers,
                        "structuredContent": answer["structuredContent"],
                        "isError": error,
                    }
            elif turn is not None and done.state == "held":
                answers.append(
                    {
                        "type": "text",
                        "text": "Committed. The call is made when this turn ends, if every "
                        "call in the turn commits; if one is refused, it is not made.",
                    }
                )
            else:
                error = error or not done.delivered
                answers.append(
                    {
                        "type": "text",
                        "text": "Committed; the call was not made yet: "
                        + (done.reason or done.state)
                        + ". It is made once, by the relay.",
                    }
                )
        if answers:
            return {"content": answers, "isError": error}
    return {
        "content": [{"type": "text", "text": result.render() or result.outcome}],
        "isError": not result.ok,
    }

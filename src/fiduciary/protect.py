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
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
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
    from interlock.relay import Relay

    from fiduciary.bridge import CallResult, Fiduciary, Session
    from fiduciary.documents import ToolBinding
    from fiduciary.mcp import Upstream

__all__ = [
    "PIN_FIELDS",
    "Keychain",
    "ProtectError",
    "Server",
    "Step",
    "client_paths",
    "diff",
    "pin_command",
    "pin_of",
    "protect",
    "read_servers",
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
) -> list[Step]:
    """Protect each stdio server of ``servers``; with ``apply`` false, only say what would
    be done. A deployment is written for each under ``root/<client>/<server>``, and the
    client's file is rewritten once, at the end.

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
    steps: list[Step] = []
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
        )
        step.tools, step.skipped = list(report.tools), list(report.skipped)
        state: dict[str, Any] = {
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


def reprotect(
    root: Path,
    *,
    resolver: Resolver = resolve,
    keychain: Keychain | None = None,
    discover: Callable[[Sequence[str], Mapping[str, str]], list[dict[str, Any]]] | None = None,
    shadow: bool = True,
) -> Path:
    """Accept what the server lists now: a new deployment from its current definitions,
    where the old one was; the old one is kept beside it, with its records."""
    from fiduciary.init import init

    state = json.loads((root / STATE).read_text(encoding="utf-8"))
    kept = root.with_name(f"{root.name}.{time.strftime('%Y%m%dT%H%M%S')}")
    os.replace(root, kept)
    env = _environment(state, keychain)
    listed = (discover or _discover)(state["command"], env)
    init(
        root,
        state["command"],
        discovered=listed,
        solo=True,
        shadow=shadow,
        name=_slug(state["server"]),
    )
    (root / STATE).write_text((kept / STATE).read_text(encoding="utf-8"), encoding="utf-8")
    os.chmod(root / STATE, 0o600)
    return kept


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


def serve(
    config: Path,
    reader: IO[bytes],
    writer: IO[bytes],
    *,
    keychain: Keychain | None = None,
) -> None:
    """Serve one client over stdio, in front of the protected server (module docstring)."""
    from interlock.mcp import numbers_on_the_wire

    from fiduciary.bridge import Fiduciary
    from fiduciary.mcp import McpServer, Upstream, serve_stdio
    from fiduciary.service import raise_triggers

    root = config.resolve().parent
    state = json.loads((root / STATE).read_text(encoding="utf-8"))
    upstream = Upstream(state["command"], env=_environment(state, keychain))
    fid = Fiduciary.from_config(config, bridged=True)
    try:
        scope = json.loads((root / "mandate.json").read_text())["subject"]["scope"]
        session = fid.session(scope, bridged=True)
        guard = Guard(fid, session, upstream, root)
        bindings = guard.bindings()
        for name, binding in bindings.items():
            if binding.kind == "passthrough":
                fid.register(
                    name,
                    _numbered(
                        upstream.handler(name, binding.upstream),
                        binding.numbers,
                        numbers_on_the_wire,
                    ),
                )
        relay = (
            relay_for(root, upstream, bindings)
            if any(b.kind == "plan" for b in bindings.values())
            else None
        )
        guard.check()
        if turns.closing(root):  # asked to close while no proxy ran
            turns.close_file(
                fid, root, json.loads((root / "mandate.json").read_text())["mandate_id"]
            )

        class Protected(McpServer):
            def _dispatch(
                self, method: str, params: Mapping[str, Any], *, modern: bool = False
            ) -> dict[str, Any]:
                if method == "tools/list" or method == "tools/call" or upstream.changed:
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
                    result, relay, root, turn=None if found is None else str(found["turn"])
                )

            def drain(self) -> list[dict[str, Any]]:
                notes = [{"jsonrpc": "2.0", "method": n} for n in guard.notes]
                guard.notes.clear()
                return notes

        mandate_id = json.loads((root / "mandate.json").read_text())["mandate_id"]

        def turn() -> None:
            fid.serve_once()
            if fid.outbox is not None:
                raise_triggers(fid)
            if turns.closing(root):
                turns.close_file(fid, root, mandate_id)  # Claude Code stopped: the hook asked
            if relay is not None:
                relay.run_once()  # what a closed turn released, and any retry now due

        serve_stdio(Protected(session, lock=fid.lock), reader, writer, service=turn, lock=fid.lock)
    finally:
        with contextlib.suppress(Exception):
            fid.close()
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

"""Agents in CI: ``fiduciary ci`` and the GitHub Action (1.20.0).

An agent in a GitHub Actions job reads text anyone can write (an issue, a comment, a pull
request from a fork) with tools that can push, comment and read the job's secrets. Public
write-ups since late 2025 show the pattern working: untrusted text in the prompt, then leaked
secrets or changed workflows. A prompt telling the agent not to do that is not enforcement.

``fiduciary ci start`` runs the agent's MCP servers behind protected proxies, in a process of
its own that outlives the step, and gives the agent an MCP file that reaches them over
loopback HTTP:

- **The event's untrusted text is labeled from the first call.** Issue and pull request text,
  comments, reviews and branch names written by anyone the repository does not trust
  (``--trust``: owners, members and collaborators by default), and anything from a fork, label
  every session ``untrusted`` before the agent reads a word (:func:`untrusted`). A send then
  waits for a person, under the rule of two, once the session also holds private data.
- **The job's secrets stay out of the agent's reach.** The values the servers need, and the
  secrets named with ``--secret``, are read from the start step's environment and handed to
  the proxy on its standard input; it keeps them in memory alone
  (:class:`fiduciary.custody.MemoryStore`), and on Linux marks itself so that other processes of
  the same user cannot read its memory. Each value is also seeded as ``secret``: no call carries
  one out, whatever tool the agent read it with.
- **Workflow files are refused.** A call that names a path under ``.github/workflows`` or
  ``.github/actions`` is refused before it runs (``--protected-path``).
- **Refuse and explain.** Nobody approves in CI: a call the mandate would hold stays held and is
  not made. ``fiduciary ci finish`` stops the proxy, writes what was refused and held, and why,
  to the job summary (``$GITHUB_STEP_SUMMARY``) and to a comment file, exports each server's
  bundle with its pinned keys and charter digest, and verifies it.

**What it cannot see.** The proxy governs the tools behind it. An agent with a shell of its own
reaches the network and the runner's files directly; give the agent no shell (Claude Code:
``--disallowedTools Bash``), or the guarantees above do not hold. A local server's process holds
its own token in its environment, which a process of the same user with a shell could read; a
remote server's token never leaves the proxy.
"""

from __future__ import annotations

import contextlib
import hmac
import io
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Final

from fiduciary.exceptions import FiduciaryError

__all__ = [
    "DEFAULT_TRUST",
    "PROTECTED",
    "CiError",
    "Finished",
    "ServerReport",
    "Source",
    "finish",
    "render",
    "serve",
    "start",
    "untrusted",
]

DEFAULT_TRUST: Final = ("OWNER", "MEMBER", "COLLABORATOR")
"""Who the repository trusts by default: GitHub's ``author_association`` of people with write
access."""
PROTECTED: Final = (".github/workflows", ".github/actions")
"""What no call may name, by default."""
STATE: Final = "ci.json"
"""What the proxy writes into its root once it serves: where, its process, its servers."""
AGENT_CONFIG: Final = "mcp.json"
"""The MCP file the agent is given."""
_TRUSTED_EVENTS: Final = frozenset({"push", "workflow_dispatch", "schedule", "merge_group"})
"""Events whose text comes from people with write access."""
_PLACEHOLDER: Final = re.compile(r"\$\{(?:env:)?([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class CiError(FiduciaryError):
    """A CI proxy that cannot start, or a root it cannot finish."""


# -- the event -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Source:
    """Text in the event that counts as untrusted: where it is, and who wrote it."""

    what: str
    author: str = ""
    association: str = ""
    fork: str = ""
    """The fork's repository, when the text came from one."""

    def words(self) -> str:
        who = f" by @{self.author}" if self.author else ""
        role = f" ({self.association})" if self.association else ""
        fork = f", from the fork {self.fork}" if self.fork else ""
        return f"{self.what}{fork}{who}{role}"


def _get(payload: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(payload, Mapping):
            return None
        payload = payload.get(key)
    return payload


def untrusted(
    event: str, payload: Mapping[str, Any], trust: Sequence[str] = DEFAULT_TRUST
) -> list[Source]:
    """The text in a GitHub Actions event that the repository does not trust: written by
    someone whose ``author_association`` is not in ``trust``, or coming from a fork.

    An event this does not know (anything but issues, comments, pull requests, reviews and
    discussions, and the events only writers start) is untrusted whole."""
    trusted = {t.upper() for t in trust}
    found: list[Source] = []

    def check(what: str, node: Any, texts: Sequence[str], fork: str = "") -> None:
        if not isinstance(node, Mapping):
            return
        if not any(isinstance(node.get(t), str) and node.get(t) for t in texts):
            return
        author = str(_get(node, "user", "login") or "")
        association = str(node.get("author_association") or "")
        if fork or association.upper() not in trusted:
            found.append(Source(what, author, association, fork))

    pull = _get(payload, "pull_request")
    fork = ""
    if isinstance(pull, Mapping):
        head = _get(pull, "head", "repo", "full_name")
        base = _get(pull, "base", "repo", "full_name")
        if head and base and head != base:
            fork = str(head)
    if event in _TRUSTED_EVENTS:
        return []
    if event == "workflow_run":
        run = _get(payload, "workflow_run")
        head = _get(run, "head_repository", "full_name")
        base = _get(run, "repository", "full_name")
        if head and base and head != base:
            found.append(Source("the run that started this job", fork=str(head)))
        return found
    if event in ("issues", "issue_comment"):
        check("the issue's title and body", _get(payload, "issue"), ("title", "body"))
    if event == "issue_comment":
        check("the comment", _get(payload, "comment"), ("body",))
    if event in (
        "pull_request",
        "pull_request_target",
        "pull_request_review",
        "pull_request_review_comment",
    ):
        check("the pull request's title, body and branch", pull, ("title", "body"), fork)
    if event == "pull_request_review":
        check("the review", _get(payload, "review"), ("body",), fork)
    if event == "pull_request_review_comment":
        check("the review comment", _get(payload, "comment"), ("body",), fork)
    if event in ("discussion", "discussion_comment"):
        check("the discussion's title and body", _get(payload, "discussion"), ("title", "body"))
    if event == "discussion_comment":
        check("the comment", _get(payload, "comment"), ("body",))
    known = {
        "issues",
        "issue_comment",
        "pull_request",
        "pull_request_target",
        "pull_request_review",
        "pull_request_review_comment",
        "discussion",
        "discussion_comment",
    }
    if event not in known:
        found.append(Source(f"the {event or 'unnamed'} event, which this does not know"))
    return found


# -- the servers' values ---------------------------------------------------------------------


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def referenced(entries: Sequence[Mapping[str, Any]]) -> list[str]:
    """Every environment variable the servers' entries name: a ``${VAR}`` in any value, and
    Codex's ``env_vars``, ``bearer_token_env_var`` and ``env_http_headers``."""
    names: set[str] = set()
    for entry in entries:
        for text in _strings(entry):
            names.update(m[1] for m in _PLACEHOLDER.finditer(text))
        names.update(str(v) for v in entry.get("env_vars") or () if isinstance(v, str))
        if isinstance(entry.get("bearer_token_env_var"), str):
            names.add(entry["bearer_token_env_var"])
        names.update(str(v) for v in (entry.get("env_http_headers") or {}).values())
    return sorted(names)


def _expanded(entry: Mapping[str, Any], values: Mapping[str, str]) -> dict[str, Any]:
    """``entry`` with every variable it names filled in from ``values``: what the server is
    started with, held by the proxy alone."""

    def fill(value: Any) -> Any:
        if isinstance(value, str):
            return _PLACEHOLDER.sub(
                lambda m: values.get(m[1], m[2] if m[2] is not None else m[0]), value
            )
        if isinstance(value, Mapping):
            return {k: fill(v) for k, v in value.items()}
        if isinstance(value, list):
            return [fill(v) for v in value]
        return value

    out: dict[str, Any] = fill(dict(entry))
    token = out.pop("bearer_token_env_var", None)
    headers = dict(out.pop("http_headers", None) or {})
    if isinstance(token, str) and token in values:
        headers["Authorization"] = f"Bearer {values[token]}"
    for name, variable in (out.pop("env_http_headers", None) or {}).items():
        if variable in values:
            headers[str(name)] = values[variable]
    if headers:
        out["headers"] = {**dict(out.get("headers") or {}), **headers}
    passed = [v for v in out.pop("env_vars", None) or () if isinstance(v, str) and v in values]
    if passed:
        out["env"] = {**{v: values[v] for v in passed}, **dict(out.get("env") or {})}
    return out


# -- the proxy -------------------------------------------------------------------------------


def _undumpable() -> None:
    """On Linux, keep other processes of this user from reading this one's memory and
    environment (``prctl(PR_SET_DUMPABLE, 0)``)."""
    if sys.platform.startswith("linux"):  # pragma: no cover - Linux only, in CI
        import ctypes

        with contextlib.suppress(OSError, AttributeError):
            ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)


@dataclass
class _Served:
    proxy: Any
    server: Any
    gate: threading.RLock
    upstream: Any


def serve(root: Path, given: Mapping[str, Any], *, stop: threading.Event | None = None) -> None:
    """The CI proxy (``fiduciary ci serve``): protect the servers ``given`` names, with the
    values it carries, and serve each at ``http://127.0.0.1:<port>/mcp/<server>`` to a
    bearer token written into the agent's MCP file, until ``/shutdown`` or a signal."""
    from fiduciary import protect
    from fiduciary.clients import read_entries
    from fiduciary.custody import MemoryStore
    from fiduciary.flow import CI, seed

    _undumpable()
    stop = stop or threading.Event()
    values = {str(k): str(v) for k, v in (given.get("values") or {}).items()}
    source = Path(str(given["config"]))
    names = [str(n) for n in given.get("secrets") or ()]
    copy = root / "servers" / source.name
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    memory = MemoryStore()
    servers = [
        protect.Server("ci", copy, section, name, _expanded(entry, values))
        for section, name, entry in read_entries(copy)
    ]
    steps = protect.protect(
        servers,
        root=root / "protect",
        apply=True,
        shadow=False,
        move_secrets=True,
        store=memory,
        runner=[sys.executable, "-m", "fiduciary"],
    )
    protected = [str(p) for p in given.get("protected") or PROTECTED]
    sources = [Source(**s) for s in given.get("sources") or ()]
    seed(
        root / "protect",
        CI,
        labels=["untrusted"] if sources else [],
        secrets=[v for v in values.values() if len(v) >= 8],
        notes=[f"untrusted: {s.words()}" for s in sources]
        + [f"secret: {n}" for n in names if n in values],
    )
    served: dict[str, _Served] = {}
    skipped: dict[str, str] = {}
    try:
        for step in steps:
            if step.action != "wrap" or step.deployment is None:
                skipped[step.server.name] = step.why
                continue
            path = step.deployment / protect.STATE
            state = json.loads(path.read_text(encoding="utf-8"))
            state["protected_paths"] = protected
            path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
            upstream = protect.upstream_of(state, memory)
            proxy, server, gate = protect.protected_server(
                step.deployment / "fiduciary.toml", upstream
            )
            served[step.server.name] = _Served(proxy, server, gate, upstream)
        listener = _listener(served, stop, str(given.get("listen") or "127.0.0.1:0"))
        token = listener.token
        port = int(listener.server_address[1])
        agent = {
            "mcpServers": {
                name: {
                    "type": "http",
                    "url": f"http://127.0.0.1:{port}/mcp/{name}",
                    "headers": {"Authorization": f"Bearer {token}"},
                }
                for name in served
            }
        }
        _private(root / AGENT_CONFIG, json.dumps(agent, indent=2) + "\n")
        _private(
            root / STATE,
            json.dumps(
                {
                    "pid": os.getpid(),
                    "url": f"http://127.0.0.1:{port}",
                    "token": token,
                    "servers": {n: str(s.proxy.root) for n, s in served.items()},
                    "skipped": skipped,
                    "sources": [asdict(s) for s in sources],
                    "secrets": [n for n in names if n in values],
                    "protected": protected,
                },
                indent=2,
            )
            + "\n",
        )
        threading.Thread(target=listener.serve_forever, daemon=True).start()
        for number in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(ValueError):  # not the main thread: tests stop it themselves
                signal.signal(number, lambda *_: stop.set())
        while not stop.wait(1.0):
            for each in served.values():
                with each.gate:
                    try:
                        each.proxy.tick()
                    except Exception as exc:
                        print(f"fiduciary ci: {exc}", file=sys.stderr)
        listener.shutdown()
        listener.server_close()
    finally:
        for each in served.values():
            with each.gate:
                each.proxy.close()
            each.upstream.close()
        with contextlib.suppress(OSError, ValueError):
            found = json.loads((root / STATE).read_text(encoding="utf-8"))
            found["stopped"] = True
            _private(root / STATE, json.dumps(found, indent=2) + "\n")


def _private(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as out:
        out.write(text)
    temporary.replace(path)


def _listener(served: Mapping[str, _Served], stop: threading.Event, listen: str) -> Any:
    from fiduciary.mcp import MAX_MESSAGE, McpError, parse_message

    host, _, port = listen.rpartition(":")
    if host not in ("127.0.0.1", "localhost", "::1", "[::1]"):
        raise CiError(f"--listen {listen}: the CI proxy listens on loopback only")
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def _answer(self, status: int, body: Any = None) -> None:
            data = b"" if body is None else json.dumps(body).encode("utf-8")
            self.send_response(status)
            if body is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            self._answer(405, {"error": "POST only"})

        do_DELETE = do_GET  # noqa: N815 - http.server's name

        def do_POST(self) -> None:
            origin = self.headers.get("Origin")
            if origin not in (None, "null"):
                self._answer(403, {"error": "no browser origin reaches the CI proxy"})
                return
            given = self.headers.get("Authorization") or ""
            if not hmac.compare_digest(given.encode(), f"Bearer {token}".encode()):
                self._answer(401, {"error": "the bearer token in the agent's MCP file"})
                return
            path = self.path.split("?", 1)[0].rstrip("/")
            if path == "/shutdown":
                stop.set()
                self._answer(204)
                return
            each = served.get(path.removeprefix("/mcp/")) if path.startswith("/mcp/") else None
            if each is None:
                self._answer(404, {"error": f"no server at {path}"})
                return
            size = int(self.headers.get("Content-Length") or 0)
            if size > MAX_MESSAGE:
                self._answer(413, {"error": "the message is too long"})
                return
            try:
                message = parse_message(self.rfile.read(size))
            except McpError as exc:
                self._answer(
                    400,
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}},
                )
                return
            with each.gate:
                reply = each.server.handle(message)
            if reply is None:
                self._answer(202)
            else:
                self._answer(200, reply)

    server = ThreadingHTTPServer((host.strip("[]") or "127.0.0.1", int(port or 0)), Handler)
    server.token = token  # type: ignore[attr-defined]
    return server


# -- start and finish ------------------------------------------------------------------------


def start(
    config: Path,
    root: Path,
    *,
    event: str = "",
    event_path: Path | None = None,
    secret_names: Sequence[str] = (),
    trust: Sequence[str] = DEFAULT_TRUST,
    protected: Sequence[str] = PROTECTED,
    runner: Sequence[str] | None = None,
    wait: float = 120.0,
) -> dict[str, Any]:
    """Start the CI proxy in a process of its own and wait until it serves; what it wrote
    into ``root``'s ``ci.json``.

    :raises CiError: When the MCP file names a variable this environment does not set, or the
        proxy does not start.
    """
    from fiduciary.clients import read_entries
    from fiduciary.protect import executable

    root.mkdir(parents=True, exist_ok=True)
    if (root / STATE).exists():
        raise CiError(f"{root} has a CI proxy already: finish it, or start in another directory")
    try:
        entries = [entry for _, _, entry in read_entries(config)]
    except FiduciaryError as exc:
        raise CiError(str(exc)) from exc
    if not entries:
        raise CiError(f"{config} lists no MCP server")
    named = referenced(entries)
    missing = [n for n in named if n not in os.environ]
    if missing:
        raise CiError(
            f"{config} names {', '.join(missing)}, which this step's environment does not set"
        )
    names = sorted({*named, *secret_names})
    values = {n: os.environ[n] for n in names if n in os.environ}
    payload: Any = {}
    if event_path is not None and event_path.is_file():
        try:
            payload = json.loads(event_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise CiError(f"{event_path} is not the event's JSON: {exc}") from exc
    sources = untrusted(event, payload if isinstance(payload, Mapping) else {}, trust)
    env = {k: v for k, v in os.environ.items() if k not in values}
    log = (root / "ci.log").open("ab")
    command = [*(runner or executable()), "ci", "serve", "--root", str(root.resolve())]
    process = subprocess.Popen(  # noqa: S603 - this installation's own fiduciary
        command,
        stdin=subprocess.PIPE,
        stdout=log,
        stderr=log,
        env=env,
        start_new_session=True,
    )
    assert process.stdin is not None
    process.stdin.write(
        json.dumps(
            {
                "config": str(config.resolve()),
                "values": values,
                "secrets": list(secret_names),
                "sources": [asdict(s) for s in sources],
                "protected": list(protected),
            }
        ).encode("utf-8")
    )
    process.stdin.close()
    log.close()
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if (root / STATE).is_file():
            found: dict[str, Any] = json.loads((root / STATE).read_text(encoding="utf-8"))
            return found
        if process.poll() is not None:
            tail = (root / "ci.log").read_text(encoding="utf-8", errors="replace")[-2000:]
            raise CiError(f"the CI proxy stopped before it served:\n{tail}")
        time.sleep(0.1)
    process.kill()
    raise CiError(f"the CI proxy did not start within {wait:.0f} seconds; see {root / 'ci.log'}")


@dataclass
class ServerReport:
    """What one protected server's records say of the job."""

    name: str
    committed: int = 0
    refused: list[tuple[str, str]] = field(default_factory=list)
    """``(tool, why)``, in words."""
    held: list[str] = field(default_factory=list)
    """What waits for a person: each held call's ticket, in words."""
    sends: int = 0
    bundle: str = ""
    verified: bool | None = None
    verdict: str = ""


@dataclass
class Finished:
    """What ``fiduciary ci finish`` found."""

    servers: list[ServerReport]
    sources: list[Source]
    secrets: list[str]
    markdown: str

    @property
    def refused(self) -> int:
        return sum(len(s.refused) for s in self.servers)

    @property
    def held(self) -> int:
        return sum(len(s.held) for s in self.servers)


_CODES: Final = {
    "protected": "its arguments point at a path this job keeps from every call",
    "withheld": "its server changed how it describes the tool since it was pinned",
    "unpinned": "no tool of that name is granted",
    "arguments": "its arguments are outside the tool's schema",
    "velocity": "too many calls in too short a time",
    "revoked": "the tool was revoked for the rest of the task",
    "tenant": "the tool acts for a tenant and the session has none",
    "max_open": "the mandate has as many open tickets as it allows",
    "model": "a model the mandate does not list",
}


def _stop(state: Mapping[str, Any], wait: float) -> None:
    """Ask the proxy to stop, and wait until its process is gone."""
    pid = int(state.get("pid") or 0)
    with contextlib.suppress(OSError):
        request = urllib.request.Request(  # noqa: S310 - our own loopback proxy
            f"{state['url']}/shutdown",
            data=b"{}",
            headers={"Authorization": f"Bearer {state['token']}"},
            method="POST",
        )
        urllib.request.urlopen(request, timeout=5).close()  # noqa: S310
    deadline = time.monotonic() + wait
    for sent in (None, signal.SIGTERM, signal.SIGKILL):
        if sent is not None:
            with contextlib.suppress(OSError):
                os.kill(pid, sent)
            deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            except PermissionError:
                return
            with contextlib.suppress(ChildProcessError, OSError):
                os.waitpid(pid, os.WNOHANG)
            time.sleep(0.1)


def _report(name: str, deployment: Path, bundles: Path | None) -> ServerReport:
    from fiduciary.bundle import TrustedKeys, verify_bundle
    from fiduciary.cli import main
    from fiduciary.config import load_config
    from fiduciary.documents import Record
    from fiduciary.log import LOG_ID
    from fiduciary.shadow import RULE_WORDS
    from fiduciary.shadow import report as chains
    from fiduciary.store import State

    report = ServerReport(name)
    config = deployment / "fiduciary.toml"
    loaded = load_config(config)
    store = loaded.open_store()
    try:
        state = State.of([Record.loads(r.data) for r in store.records(LOG_ID)])
    finally:
        with contextlib.suppress(Exception):
            store.close()
    for events in state.sessions.values():
        for event, body, _ in events:
            if event == "refused":
                code = str(body.get("code"))
                report.refused.append((str(body.get("tool") or "?"), _CODES.get(code, code)))
            elif event == "sent":
                report.sends += 1
                if body.get("outcome") == "refused":
                    why = "; ".join(RULE_WORDS.get(r, r) for r in body.get("rules") or ())
                    report.refused.append((str(body.get("tool")), why))
    reasons = {"provenance": "after what the session read", "held": "a write the mandate holds"}
    for row in state.open_tickets():
        reason = reasons.get(row.ticket_class, row.ticket_class)
        report.held.append(f"ticket {row.ticket_id}: {reason}")
    mandate = json.loads((deployment / "mandate.json").read_text(encoding="utf-8"))
    held_for_reading = (mandate.get("provenance") or {}).get("on") == "hold"
    if loaded.chains is not None and loaded.chains.is_dir():
        for lineage in chains(loaded.chains):
            for plan in lineage.plans:
                if plan.outcome == "committed":
                    report.committed += 1
                elif held_for_reading and set(plan.blocked_by) == {"provenance_gate"}:
                    continue  # held for the principals, with a ticket: among the held
                elif plan.outcome == "aborted" and plan.blocked_by:
                    report.refused.append(
                        (plan.tool or "?", "the mandate's checks: " + ", ".join(plan.blocked_by))
                    )
    if bundles is not None:
        target = bundles / name
        target.mkdir(parents=True, exist_ok=True)
        out = target / f"{name}.fidb"
        quiet = io.StringIO()
        with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
            exported = main(
                [
                    "--config",
                    str(config),
                    "export-bundle",
                    "--mandate",
                    str(mandate["mandate_id"]),
                    "--ledger-key",
                    str(deployment / "keys" / "ledger-checkpoints.key"),
                    "--out",
                    str(out),
                ]
            )
        if exported != 0:
            report.verdict = "the bundle could not be exported: " + quiet.getvalue().strip()[-300:]
            report.verified = False
            return report
        for kept in ("pinned-keys.json", "charter.digest"):
            (target / kept).write_bytes((deployment / kept).read_bytes())
        charter = (deployment / "charter.digest").read_text(encoding="utf-8").strip()
        keys = TrustedKeys.loads((deployment / "pinned-keys.json").read_bytes())
        verified = verify_bundle(out.read_bytes(), charter=charter, keys=keys)
        report.bundle = str(out)
        report.verified = verified.passed
        report.verdict = "every check passes, V1 to V14" if verified.passed else verified.render()
    return report


def render(found: Finished) -> str:
    """The job summary, in Markdown."""
    lines = ["## Fiduciary: what the agent's tools did", ""]
    if found.sources:
        lines.append("Untrusted from the first call, so every session was labeled `untrusted`:")
        lines += [f"- {s.words()}" for s in found.sources]
    else:
        lines.append("Nothing in the event came from someone the repository does not trust.")
    if found.secrets:
        names = ", ".join(f"`{n}`" for n in found.secrets)
        lines += ["", f"Kept from the agent, and refused in any call: {names}."]
    for server in found.servers:
        lines += ["", f"### {server.name}", ""]
        lines.append(
            f"{server.committed} {'write' if server.committed == 1 else 'writes'} made, "
            f"{server.sends} {'send' if server.sends == 1 else 'sends'} judged, "
            f"{len(server.refused)} refused, {len(server.held)} held."
        )
        if server.refused:
            lines += ["", "**Refused** (nothing was sent):", ""]
            lines += [f"- `{tool}`: {why}" for tool, why in server.refused]
        if server.held:
            lines += ["", "**Held** (nobody approves in CI, so it was not made):", ""]
            lines += [f"- {held}" for held in server.held]
        if server.bundle:
            mark = "verifies" if server.verified else "does not verify"
            lines += ["", f"Bundle `{Path(server.bundle).name}` {mark}: {server.verdict}."]
        elif server.verdict:
            lines += ["", server.verdict]
    lines += [
        "",
        "Check a bundle yourself, offline: `fiduciary verify-bundle <server>.fidb --charter "
        "$(cat charter.digest) --keys pinned-keys.json`.",
        "",
    ]
    return "\n".join(lines)


def finish(
    root: Path,
    *,
    bundles: Path | None = None,
    summary: Path | None = None,
    comment: Path | None = None,
    wait: float = 30.0,
) -> Finished:
    """Stop the CI proxy in ``root``, read what each server's records say, export and verify
    each bundle into ``bundles``, and write the summary.

    :raises CiError: When ``root`` holds no CI proxy.
    """
    try:
        state = json.loads((root / STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CiError(f"{root} holds no CI proxy: run fiduciary ci start first") from exc
    if not state.get("stopped"):
        _stop(state, wait)
    reports = [
        _report(name, Path(deployment), bundles)
        for name, deployment in sorted(state.get("servers", {}).items())
    ]
    found = Finished(
        reports,
        [Source(**s) for s in state.get("sources") or ()],
        list(state.get("secrets") or ()),
        "",
    )
    found.markdown = render(found)
    (root / "summary.md").write_text(found.markdown, encoding="utf-8")
    if summary is not None:
        with summary.open("a", encoding="utf-8") as out:
            out.write(found.markdown)
    if comment is not None and (found.refused or found.held):
        comment.write_text(found.markdown, encoding="utf-8")
    return found


def outputs(found: Finished, path: Path | None, bundles: Path | None) -> None:
    """The step's outputs, into ``$GITHUB_OUTPUT``."""
    if path is None:
        return
    with path.open("a", encoding="utf-8") as out:
        out.write(f"refused={found.refused}\nheld={found.held}\n")
        if bundles is not None:
            out.write(f"bundles={bundles}\n")

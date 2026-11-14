"""The MCP bridge: a session out of process (``docs/FIDUCIARY_SPEC.md`` §8.2, §8.4).

The agent's process holds an MCP connection and, over HTTP, its session's
token, and nothing else of fiduciary's (F8-11). Every connection is one
:class:`~fiduciary.bridge.Session`:

- ``tools/list`` returns the bindings the version in force pins, each with
  its ``description`` and ``input`` schema exactly as the binding holds them,
  and the two built-ins, ``fiduciary_ticket_status`` and
  ``fiduciary_request``: nothing else (F8-4).
- ``tools/call`` is :meth:`~fiduciary.bridge.Session.call`. A refusal is a
  result with ``isError: true`` and the sanitized feedback as its text. A
  malformed request is a JSON-RPC error, and nothing is built (F8-5).

No method, path or tool submits, relays or shows an approval (F7-4).

Transports:

- :func:`serve_stdio`: newline-delimited JSON-RPC on a pipe.
- :class:`HttpBridge`: Streamable HTTP, one endpoint (``/mcp``), ``POST``
  only, answering with ``application/json``. Each session is bound to one
  bearer token, issued when the session opens and valid for it alone, or,
  under ``fiduciary serve``, to an agent's token and the session id its
  ``initialize`` was answered with (``DELETE`` ends it); the listener speaks
  TLS, or listens on loopback only (F8-6, A8).

:class:`Upstream` fronts another MCP server (``fiduciary mcp proxy``): an
upstream tool is exposed only where the mandate pins a ``passthrough``
binding of its name, and every call to it is metered and counted (F8-7).
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import ipaddress
import json
import logging
import math
import os
import secrets
import select
import ssl
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import IO, Any, Final

from interlock.mcp import LEGACY_VERSIONS, MODERN_VERSIONS, UNSUPPORTED_VERSION

from fiduciary import __version__
from fiduciary.bridge import Fiduciary, Session
from fiduciary.exceptions import FiduciaryError

__all__ = [
    "MAX_MESSAGE",
    "PROTOCOL_VERSIONS",
    "Agent",
    "HttpBridge",
    "McpError",
    "McpServer",
    "Upstream",
    "serve_stdio",
]

logger = logging.getLogger("fiduciary.mcp")

PROTOCOL_VERSIONS: Final = MODERN_VERSIONS + LEGACY_VERSIONS
"""The MCP revisions this server speaks, newest first (1.17.0): 2026-07-28, served
statelessly to a request that carries its revision in ``_meta``, and the revisions that
open with ``initialize``."""
_META: Final = "io.modelcontextprotocol/"
MAX_MESSAGE: Final = 1_048_576
"""The largest JSON-RPC message read, in bytes."""
SERVICE_SECONDS: Final = 1.0
"""How often a bridge judges the intake and executes the warrants formed: a
warrant for a quote or a proposal this process holds is executed here (F7-14)."""
HANDSHAKE_SECONDS: Final = 5.0
"""How long a client has to finish its TLS handshake, which runs in its
connection's own thread (1.11.0): one that connects and says nothing holds only
that thread, never the loop that accepts every other client."""
READ_SECONDS: Final = 60.0
"""How long a connection may wait between bytes of a request before it is
closed (1.11.0): a client that trickles its request holds a thread no longer."""
BACKLOG: Final = 64
"""Connections the bridge's listener queues before it accepts them (1.11.0; the
standard library's default is 5)."""

PARSE_ERROR: Final = -32700
INVALID_REQUEST: Final = -32600
METHOD_NOT_FOUND: Final = -32601
INVALID_PARAMS: Final = -32602


class McpError(FiduciaryError):
    """A request the bridge answers with a JSON-RPC error."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def _strict(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for key, value in pairs:
        if key in found:
            raise ValueError(f"duplicate key {key!r}")
        found[key] = value
    return found


def parse_message(data: bytes | str) -> Any:
    """A JSON-RPC message, strictly: no duplicate key, no NaN.

    :raises McpError: ``-32700`` on anything that is not one JSON value.
    """
    if len(data) > MAX_MESSAGE:
        raise McpError(PARSE_ERROR, f"a message is at most {MAX_MESSAGE} bytes")

    def constant(name: str) -> Any:
        raise ValueError(f"{name} is no JSON")

    try:
        return json.loads(data, object_pairs_hook=_strict, parse_constant=constant)
    except (ValueError, UnicodeDecodeError) as exc:
        raise McpError(PARSE_ERROR, f"not JSON: {exc}") from exc


def _error(ident: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": ident, "error": error}


def _server_info() -> dict[str, str]:
    return {"name": "fiduciary", "version": __version__}


INSTRUCTIONS: Final = (
    "Every tool here acts under a signed mandate. A refused call says why, in terms of your "
    "own arguments; fiduciary_request asks the principals for what the mandate does not allow."
)


class McpServer:
    """One session's MCP methods (module docstring).

    :param session: The session every call of this connection acts in.
    :param lock: Held around every call: the service's turns take it too.
    """

    def __init__(self, session: Session, *, lock: threading.RLock | None = None) -> None:
        self._session = session
        self._lock = lock or threading.RLock()
        self.initialized = False

    @property
    def session(self) -> Session:
        return self._session

    def handle(self, message: Any) -> dict[str, Any] | None:
        """Answer one JSON-RPC message: a response, or ``None`` for a notification."""
        if not isinstance(message, dict):
            return _error(None, INVALID_REQUEST, "a request is one JSON-RPC object")
        ident = message.get("id")
        notification = "id" not in message
        if (
            message.get("jsonrpc") != "2.0"
            or not isinstance(message.get("method"), str)
            or not (notification or (isinstance(ident, str | int) and not isinstance(ident, bool)))
        ):
            return None if notification else _error(ident, INVALID_REQUEST, "not JSON-RPC 2.0")
        method = message["method"]
        params = message.get("params", {})
        if notification:
            if method == "notifications/initialized":
                self.initialized = True
            return None  # every other notification is ignored, as MCP allows
        if not isinstance(params, dict):
            return _error(ident, INVALID_PARAMS, "params is an object")
        # 2026-07-28 (1.17.0): a request that carries its revision in _meta is served on
        # its own, with no initialize; an older client's initialize opens the legacy era.
        meta = params.get("_meta")
        version = meta.get(_META + "protocolVersion") if isinstance(meta, dict) else None
        if version is not None:
            if version not in MODERN_VERSIONS:
                return _error(
                    ident,
                    UNSUPPORTED_VERSION,
                    "Unsupported protocol version",
                    {"supported": list(PROTOCOL_VERSIONS), "requested": version},
                )
            if not isinstance(meta.get(_META + "clientCapabilities"), dict):  # type: ignore[union-attr]
                return _error(
                    ident, INVALID_PARAMS, "a request carries its client's capabilities in _meta"
                )
        try:
            result = self._dispatch(method, params, modern=version is not None)
        except McpError as exc:
            return _error(ident, exc.code, str(exc))
        if version is not None:
            result = {
                **result,
                "resultType": "complete",
                "_meta": {**result.get("_meta", {}), _META + "serverInfo": _server_info()},
            }
        return {"jsonrpc": "2.0", "id": ident, "result": result}

    def drain(self) -> list[dict[str, Any]]:
        """Notifications this server has for its client, sent after the next answer
        (1.17.0): ``notifications/tools/list_changed`` when a protected tool is withheld."""
        return []

    def tool_list(self) -> list[dict[str, Any]]:
        """The tools this connection lists: the session's, as MCP writes them."""
        with self._lock:
            tools = self._session.tool_definitions()
        return [
            {"name": t["name"], "description": t["description"], "inputSchema": t["input_schema"]}
            for t in tools
        ]

    def _dispatch(
        self, method: str, params: Mapping[str, Any], *, modern: bool = False
    ) -> dict[str, Any]:
        if method == "initialize":
            asked = params.get("protocolVersion")
            version = asked if asked in LEGACY_VERSIONS else LEGACY_VERSIONS[0]
            return {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": _server_info(),
                "instructions": INSTRUCTIONS,
            }
        if method == "server/discover":
            return {
                "supportedVersions": list(PROTOCOL_VERSIONS),
                "capabilities": {"tools": {"listChanged": True}},
                "instructions": INSTRUCTIONS,
                "ttlMs": 0,
                "cacheScope": "private",
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            listed: dict[str, Any] = {"tools": self.tool_list()}
            if modern:
                # A tool can be withheld at any moment (a pin that moved): never cached.
                listed.update(ttlMs=0, cacheScope="private")
            return listed
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments", {})
            if not isinstance(name, str) or not isinstance(arguments, dict):
                raise McpError(INVALID_PARAMS, "tools/call names a tool, and its arguments object")
            with self._lock:
                result = self._session.call(name, arguments)
            if isinstance(result.value, dict) and "content" in result.value:
                content = result.value["content"]  # an upstream tool's own result
                error = bool(result.value.get("isError")) or not result.ok
            else:
                text = result.render()
                if result.value is not None and result.outcome == "served":
                    # The sentence a session gives, and the value it carries.
                    value = json.dumps(result.value, sort_keys=True)
                    text = f"{text}\n{value}" if text else value
                content = [{"type": "text", "text": text or result.outcome}]
                error = not result.ok
            return {"content": content, "isError": error}
        raise McpError(METHOD_NOT_FOUND, f"no method {method}")


class _Lines:
    """The lines of a pipe, read so that ``select`` tells the truth about them.

    A buffered reader reads ahead: two messages written together arrive in one
    read, and the second waits in the reader's buffer, where ``select`` on the
    pipe does not see it. A request sent right after a notification would
    then wait for whatever is written next. So the pipe is read here, by its
    descriptor, and only an empty buffer waits on ``select``. A reader with no
    descriptor (a test's ``BytesIO``) is read as it is, and never waited on.
    """

    def __init__(self, reader: IO[bytes]) -> None:
        self._reader = reader
        try:
            self._fd: int | None = reader.fileno()
        except (OSError, ValueError):
            self._fd = None
        self._held = bytearray()
        self._ended = False

    def ready(self, timeout: float) -> bool:
        """Whether a line can be read now, waiting up to ``timeout`` seconds
        for the pipe when none is held."""
        if self._fd is None or self._ended or b"\n" in self._held:
            return True
        if len(self._held) > MAX_MESSAGE:
            return True
        try:
            ready, _, _ = select.select([self._fd], [], [], timeout)
        except (OSError, ValueError):
            return True  # a pipe select cannot wait on (Windows): read, blocking
        return bool(ready)

    def readline(self) -> bytes:
        """The next line, at most :data:`MAX_MESSAGE` and one bytes, as
        ``readline(MAX_MESSAGE + 1)`` reads it; ``b""`` once the pipe closes."""
        if self._fd is None:
            return self._reader.readline(MAX_MESSAGE + 1)
        while b"\n" not in self._held and len(self._held) <= MAX_MESSAGE and not self._ended:
            chunk = os.read(self._fd, 65536)
            if chunk:
                self._held += chunk
            else:
                self._ended = True
        end = self._held.find(b"\n")
        cut = end + 1 if 0 <= end <= MAX_MESSAGE else min(len(self._held), MAX_MESSAGE + 1)
        line = bytes(self._held[:cut])
        del self._held[:cut]
        return line


def serve_stdio(
    server: McpServer,
    reader: IO[bytes],
    writer: IO[bytes],
    *,
    service: Callable[[], object] | None = None,
    lock: threading.RLock | None = None,
) -> None:
    """Serve one session on a pipe until it closes: one JSON-RPC message per
    line. ``service`` is called between messages, and when the pipe has been
    idle for :data:`SERVICE_SECONDS`, under ``lock``."""
    lock = lock or threading.RLock()
    lines = _Lines(reader)
    last = time.monotonic()
    while True:
        if service is not None:
            ready = lines.ready(SERVICE_SECONDS)
            if not ready or time.monotonic() - last >= SERVICE_SECONDS:
                with lock:
                    service()
                last = time.monotonic()
                if not ready:
                    continue
        line = lines.readline()
        if not line:
            return
        if not line.strip():
            continue
        try:
            answer = server.handle(parse_message(line))
        except McpError as exc:
            answer = _error(None, exc.code, str(exc))
        if answer is not None:
            writer.write(json.dumps(answer, separators=(",", ":")).encode() + b"\n")
            for note in server.drain():
                writer.write(json.dumps(note, separators=(",", ":")).encode() + b"\n")
            writer.flush()


# --------------------------------------------------------------------------
# Streamable HTTP (F8-6)
# --------------------------------------------------------------------------


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class Agent:
    """An agent a bridge opens sessions for (``[bridge.agents]``): whoever
    presents a bearer token whose SHA-256 is ``token_sha256``. Each
    ``initialize`` without an ``Mcp-Session-Id`` opens a session of its own
    (``open``), up to ``max_sessions`` at once. The bridge holds the digest,
    never the token (F10-3)."""

    name: str
    token_sha256: str
    open: Callable[[], Session]
    max_sessions: int = 4


@dataclass(slots=True)
class _Opened:
    agent: str
    server: McpServer
    used: float


class _Listener(ThreadingHTTPServer):
    """The bridge's listener (1.11.0). It queues :data:`BACKLOG` connections,
    and with TLS it accepts the bare connection and makes the handshake in the
    connection's own thread, bounded by :data:`HANDSHAKE_SECONDS`: a server
    socket wrapped whole would make it inside ``accept``, where one silent
    client stalls every other."""

    daemon_threads = True
    request_queue_size = BACKLOG

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        tls: ssl.SSLContext | None,
    ) -> None:
        self.tls = tls
        super().__init__(address, handler)

    def finish_request(self, request: Any, client_address: Any) -> None:
        if self.tls is None:
            super().finish_request(request, client_address)
            return
        request.settimeout(HANDSHAKE_SECONDS)
        try:
            wrapped = self.tls.wrap_socket(request, server_side=True)
        except (OSError, ValueError) as exc:  # timed out, refused, or gone
            logger.debug("bridge: no TLS handshake from %s: %s", client_address, exc)
            return
        try:
            super().finish_request(wrapped, client_address)
        finally:
            with contextlib.suppress(OSError):
                wrapped.close()

    def handle_error(self, request: Any, client_address: Any) -> None:
        """A connection that broke, or timed out, is the client's to retry:
        named at debug level. Anything else is logged with its traceback."""
        error = sys.exc_info()[1]
        if isinstance(error, OSError):
            logger.debug("bridge: connection from %s ended: %s", client_address, error)
        else:
            logger.warning("bridge: request from %s failed", client_address, exc_info=error)


class HttpBridge:
    """Sessions over Streamable HTTP (F8-6): ``POST /mcp``. Nothing else is
    served, but ``DELETE /mcp``, which ends an agent's session.

    A session is reached two ways:

    - by a bearer token :meth:`add` issued for it alone (``fiduciary mcp
      serve``);
    - by an agent's token, and the ``Mcp-Session-Id`` its ``initialize``
      answered with (``fiduciary serve``, ``agents``).

    :param host: Where to listen: a loopback address, unless ``tls`` is given.
    :param tls: The server's TLS context.
    :param service: Called every :data:`SERVICE_SECONDS`, under the lock.
    :param agents: The agents sessions are opened for, by name.
    :param idle_seconds: How long after its last request an agent's session is
        closed idle, at the latest (the service's ``idle``, and a turn of the
        writer that closes it): what ``Retry-After`` counts down to when an
        agent holds its most sessions (1.11.0).
    :raises McpError: When asked to listen beyond loopback without TLS.
    """

    def __init__(
        self,
        host: str,
        port: int,
        *,
        tls: ssl.SSLContext | None = None,
        service: Callable[[], object] | None = None,
        lock: threading.RLock | None = None,
        agents: Mapping[str, Agent] | None = None,
        idle_seconds: float | None = None,
    ) -> None:
        if tls is None and not _loopback(host):
            raise McpError(
                INVALID_REQUEST,
                f"{host} is not loopback: over the network the bridge speaks TLS (F8-6)",
            )
        self._lock = lock or threading.RLock()
        self._servers: dict[str, McpServer] = {}
        self._agents: dict[str, Agent] = dict(agents or {})
        self._opened: dict[str, _Opened] = {}
        self._service = service
        self._idle_seconds = idle_seconds
        self._stopped = threading.Event()
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "fiduciary"
            sys_version = ""
            timeout = READ_SECONDS

            def log_message(self, *args: object) -> None:
                pass

            def _send(
                self, status: int, body: Any = None, headers: Mapping[str, str] | None = None
            ) -> None:
                data = b"" if body is None else json.dumps(body, separators=(",", ":")).encode()
                self.send_response(status)
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                if body is not None:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _refuse_method(self) -> None:
                self._send(405, {"error": "POST /mcp only"}, {"Allow": "POST"})

            do_GET = do_PUT = do_PATCH = _refuse_method  # noqa: N815

            def _admitted(self) -> McpServer | Agent | None:
                """The caller, or ``None`` with the refusal sent."""
                if self.path.split("?")[0] != "/mcp":
                    self._send(404, {"error": "not found"})
                    return None
                origin = self.headers.get("Origin")
                if origin is not None and not bridge._origin_allowed(origin):
                    self._send(403, {"error": "origin not allowed"})
                    return None
                found = bridge._authorized(self.headers.get("Authorization"))
                if found is None:
                    self._send(
                        401,
                        {"error": "a bearer token for one session, or an agent's"},
                        {"WWW-Authenticate": 'Bearer realm="fiduciary"'},
                    )
                return found

            def do_DELETE(self) -> None:
                caller = self._admitted()
                if caller is None:
                    return
                if not isinstance(caller, Agent):
                    self._refuse_method()
                    return
                sent = self.headers.get("Mcp-Session-Id")
                if sent is None or not bridge._end(caller, sent):
                    self._send(404, {"error": "no such session for this agent"})
                    return
                self._send(204)

            def do_POST(self) -> None:
                caller = self._admitted()
                if caller is None:
                    return
                sent = self.headers.get("Mcp-Session-Id")
                if isinstance(caller, McpServer) and sent not in (None, caller.session.session_id):
                    self._send(404, {"error": "no such session for this token"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", ""))
                except ValueError:
                    self._send(411, {"error": "Content-Length is required"})
                    return
                if not 0 < length <= MAX_MESSAGE:
                    self._send(413, {"error": f"a message is 1 to {MAX_MESSAGE} bytes"})
                    return
                body = self.rfile.read(length)
                try:
                    message = parse_message(body)
                except McpError as exc:
                    self._send(400, _error(None, exc.code, str(exc)))
                    return
                server = caller
                if isinstance(caller, Agent):
                    found = self._agent_server(caller, sent, message)
                    if found is None:
                        return
                    server = found
                assert isinstance(server, McpServer)
                answer = server.handle(message)
                headers = {"Mcp-Session-Id": server.session.session_id}
                if answer is None:
                    self._send(202, None, headers)
                else:
                    self._send(200, answer, headers)

            def _agent_server(self, agent: Agent, sent: str | None, message: Any) -> Any:
                if sent is not None:
                    found = bridge._served(agent, sent)
                    if found is None:
                        self._send(404, {"error": "no such session for this agent"})
                    return found
                if not isinstance(message, dict) or message.get("method") != "initialize":
                    self._send(
                        400,
                        _error(
                            message.get("id") if isinstance(message, dict) else None,
                            INVALID_REQUEST,
                            "no Mcp-Session-Id: initialize first",
                        ),
                    )
                    return None
                try:
                    return bridge._open(agent)
                except _FullError as exc:
                    self._send(429, {"error": str(exc)}, {"Retry-After": str(exc.retry_after)})
                except FiduciaryError as exc:  # the lineage is not bound, or not in force
                    self._send(403, {"error": str(exc)[:512]})
                return None

        self._http = _Listener((host, port), Handler, tls)
        self._thread: threading.Thread | None = None
        self._ticker: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._http.server_address[:2]
        return str(host), int(port)

    def add(self, session: Session) -> str:
        """Serve ``session``, and return the bearer token that alone reaches it."""
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._servers[token] = McpServer(session, lock=self._lock)
        return token

    def _authorized(self, header: str | None) -> McpServer | Agent | None:
        if header is None or not header.startswith("Bearer "):
            return None
        offered = header[len("Bearer ") :].strip().encode()
        digest = hashlib.sha256(offered).hexdigest().encode()
        found: McpServer | Agent | None = None
        with self._lock:
            for token, server in self._servers.items():
                if hmac.compare_digest(token.encode(), offered):
                    found = server
            for agent in self._agents.values():
                if hmac.compare_digest(agent.token_sha256.encode(), digest):
                    found = agent
        return found

    # -- agents' sessions ----------------------------------------------------------------

    def _open(self, agent: Agent) -> McpServer:
        with self._lock:
            used = [o.used for o in self._opened.values() if o.agent == agent.name]
            if len(used) >= agent.max_sessions:
                raise _FullError(
                    f"agent {agent.name} has {len(used)} sessions open, its most: close one "
                    f"(DELETE /mcp with its Mcp-Session-Id), or retry once the least recently "
                    f"used closes idle",
                    retry_after=self._retry_after(min(used)),
                )
            session = agent.open()
            server = McpServer(session, lock=self._lock)
            self._opened[session.session_id] = _Opened(agent.name, server, time.monotonic())
            return server

    def _served(self, agent: Agent, session_id: str) -> McpServer | None:
        with self._lock:
            found = self._opened.get(session_id)
            if found is None or found.agent != agent.name:
                return None
            found.used = time.monotonic()
            return found.server

    def _end(self, agent: Agent, session_id: str) -> bool:
        with self._lock:
            found = self._opened.get(session_id)
            if found is None or found.agent != agent.name:
                return False
            del self._opened[session_id]
            _close(found.server.session)
            return True

    def set_agents(self, agents: Mapping[str, Agent]) -> None:
        """Serve ``agents`` from now on. The sessions of an agent no longer
        named, or named with another token, are closed."""
        with self._lock:
            kept = {
                name
                for name, agent in agents.items()
                if name in self._agents and self._agents[name].token_sha256 == agent.token_sha256
            }
            self._agents = dict(agents)
            for session_id, opened in list(self._opened.items()):
                if opened.agent not in kept:
                    del self._opened[session_id]
                    _close(opened.server.session)

    def _retry_after(self, used: float) -> int:
        """Seconds until a session last used at ``used`` is closed idle
        (``idle_seconds`` after it, 1.11.0); without that bound, one second."""
        if self._idle_seconds is None:
            return 1
        return max(1, math.ceil(used + self._idle_seconds - time.monotonic()))

    def close_idle(self, seconds: float) -> int:
        """Close every agent's session no request reached for ``seconds``.
        Returns how many."""
        cutoff = time.monotonic() - seconds
        with self._lock:
            idle = [sid for sid, opened in self._opened.items() if opened.used < cutoff]
            for session_id in idle:
                _close(self._opened.pop(session_id).server.session)
        return len(idle)

    def close_sessions(self) -> None:
        """Close every agent's session: the bridge is going."""
        with self._lock:
            opened, self._opened = self._opened, {}
            for each in opened.values():
                _close(each.server.session)

    def open_sessions(self) -> int:
        """The agents' sessions open now."""
        with self._lock:
            return len(self._opened)

    # -- serving ---------------------------------------------------------------------------

    @staticmethod
    def _origin_allowed(origin: str) -> bool:
        """A browser's request from a page served elsewhere is refused: a
        loopback listener must not be reachable by DNS rebinding."""
        host = origin.split("://", 1)[-1].split("/", 1)[0]
        host = host.rsplit(":", 1)[0] if not host.endswith("]") else host
        return _loopback(host.strip("[]"))

    def start(self) -> None:
        self._thread = threading.Thread(target=self._http.serve_forever, daemon=True)
        self._thread.start()
        if self._service is not None:
            self._ticker = threading.Thread(target=self._tick, daemon=True)
            self._ticker.start()

    def _tick(self) -> None:
        while not self._stopped.wait(SERVICE_SECONDS):
            assert self._service is not None
            with self._lock:
                try:
                    self._service()
                except FiduciaryError:
                    pass  # the next turn tries again

    def serve_forever(self) -> None:
        self.start()
        self._stopped.wait()

    def stop(self) -> None:
        self._stopped.set()
        if self._thread is not None:  # shutdown() waits for a loop that never ran
            self._http.shutdown()
            self._thread.join(5)
        self._http.server_close()


class _FullError(FiduciaryError):
    """An agent holds as many sessions as it may. ``retry_after``: the seconds
    until its least recently used session closes idle, as ``Retry-After`` says."""

    def __init__(self, message: str, *, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _close(session: Session) -> None:
    """Close a session the bridge drops; one whose store is gone stays open
    in the log, as a crash leaves it."""
    try:
        session.close()
    except Exception as exc:  # the store or the ledger is unreachable
        logger.warning("session %s could not be closed: %s", session.session_id, exc)


# --------------------------------------------------------------------------
# the upstream of a proxy (F8-7)
# --------------------------------------------------------------------------


class Upstream:
    """An upstream MCP server, run as a child process speaking stdio.

    The revision is agreed as 2026-07-28 asks (1.17.0): a ``server/discover`` probe, and
    ``initialize`` for a server that needs it (:func:`interlock.mcp.handshake`).

    :param env: Its environment, beside this process's own: where a protected server's
        secrets go (1.17.0).
    :raises McpError: When it does not start, or does not answer.
    """

    def __init__(
        self,
        command: Sequence[str],
        *,
        timeout: float = 30.0,
        env: Mapping[str, str] | None = None,
    ) -> None:
        from interlock.mcp import McpCallError, handshake

        if not command:
            raise McpError(INVALID_REQUEST, "the proxy needs an upstream command")
        self._timeout = timeout
        self._next = 0
        self._lock = threading.Lock()
        self.changed = False
        """Whether the server said its tools changed (``notifications/tools/list_changed``)
        since :meth:`definitions` last listed them."""
        for probe in (2.0, 0.0):
            try:
                self._process = subprocess.Popen(  # noqa: S603 - the operator's own command
                    list(command),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    env=None if env is None else {**os.environ, **env},
                )
            except OSError as exc:
                raise McpError(INVALID_REQUEST, f"cannot run the upstream: {exc}") from exc
            assert self._process.stdout is not None
            self._lines = _Lines(self._process.stdout)
            try:
                self.era = handshake(
                    self._request_for_handshake,
                    lambda method: self._send({"jsonrpc": "2.0", "method": method}),
                    {"name": "fiduciary-proxy", "version": __version__},
                    timeout=timeout,
                    probe=probe,
                )
                return
            except (McpCallError, McpError) as exc:
                died = self._process.poll() is not None
                self.close()
                if probe and died:
                    continue  # the probe stopped it: start it again, and initialize
                raise McpError(INVALID_REQUEST, f"the upstream did not start: {exc}") from exc

    def _request_for_handshake(
        self, method: str, params: Mapping[str, Any], seconds: float
    ) -> Mapping[str, Any]:
        from interlock.mcp import McpCallError, McpRefusedError

        try:
            return self._exchange(method, params, seconds, raw=True)
        except _Refused as exc:
            raise McpRefusedError(exc.text, code=exc.code, data=exc.data) from exc
        except McpError as exc:
            raise McpCallError(str(exc), sent=self._process.poll() is None) from exc
        except OSError as exc:
            raise McpCallError(str(exc), sent=False) from exc

    def _send(self, message: Mapping[str, Any]) -> None:
        assert self._process.stdin is not None
        self._process.stdin.write(json.dumps(message).encode() + b"\n")
        self._process.stdin.flush()

    def request(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        """Send one request and read its response, in the agreed revision.

        :raises McpError: On an error response, or silence past the timeout.
        """
        try:
            return self._exchange(
                method,
                self.era.params(params, {"name": "fiduciary-proxy", "version": __version__}),
                self._timeout,
            )
        except _Refused as exc:
            raise McpError(INVALID_REQUEST, f"the upstream refused {method}") from exc

    def _exchange(
        self, method: str, params: Mapping[str, Any], seconds: float, *, raw: bool = False
    ) -> dict[str, Any]:
        with self._lock:
            self._next += 1
            ident = self._next
            self._send({"jsonrpc": "2.0", "id": ident, "method": method, "params": dict(params)})
            deadline = time.monotonic() + seconds
            while True:
                left = deadline - time.monotonic()
                if not self._lines.ready(max(0.0, left)) or left <= 0:
                    raise McpError(INVALID_REQUEST, f"the upstream did not answer {method}")
                line = self._lines.readline()
                if not line:
                    raise McpError(INVALID_REQUEST, "the upstream closed its pipe")
                try:
                    message = parse_message(line)
                except McpError:
                    continue  # a server's stray output
                if isinstance(message, dict) and message.get("method") == (
                    "notifications/tools/list_changed"
                ):
                    self.changed = True
                if not isinstance(message, dict) or message.get("id") != ident:
                    continue  # a notification, or a request of its own: not answered
                if "error" in message:
                    error = message["error"] if isinstance(message["error"], dict) else {}
                    code = error.get("code")
                    raise _Refused(
                        str(error.get("message") or "an error")[:200],
                        code if isinstance(code, int) else None,
                        error.get("data"),
                    )
                result = message.get("result")
                return result if isinstance(result, dict) else {}

    def definitions(self) -> list[dict[str, Any]]:
        """Every tool the server lists, as it lists it (every page)."""
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            found = self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(t for t in found.get("tools") or () if isinstance(t, dict))
            cursor = found.get("nextCursor")
            if not cursor:
                self.changed = False
                return tools

    def tools(self) -> list[str]:
        """The upstream's tool names."""
        return [str(t["name"]) for t in self.definitions() if "name" in t]

    def call(self, tool: str, arguments: Mapping[str, Any], timeout: float) -> dict[str, Any]:
        """One ``tools/call``, as :class:`interlock.mcp.McpAdapter` makes it (1.17.0).

        :raises interlock.mcp.McpCallError: When no result came back.
        """
        from interlock.mcp import McpCallError, McpRefusedError

        try:
            return self._exchange(
                "tools/call",
                self.era.params(
                    {"name": tool, "arguments": dict(arguments)},
                    {"name": "fiduciary-proxy", "version": __version__},
                ),
                timeout,
            )
        except _Refused as exc:
            raise McpRefusedError(exc.text, code=exc.code, data=exc.data) from exc
        except McpError as exc:
            raise McpCallError(str(exc), sent=True) from exc

    def handler(self, name: str, upstream: str | None = None) -> Callable[[Mapping[str, Any]], Any]:
        """A passthrough implementation that calls upstream tool ``upstream`` (``name``
        by default)."""

        def call(arguments: Mapping[str, Any]) -> dict[str, Any]:
            result = self.request(
                "tools/call", {"name": upstream or name, "arguments": dict(arguments)}
            )
            content = result.get("content", [])
            return {
                "content": [c for c in content if isinstance(c, dict)],
                "isError": bool(result.get("isError")),
            }

        return call

    def close(self) -> None:
        if self._process.stdin is not None and not self._process.stdin.closed:
            with contextlib.suppress(OSError):
                self._process.stdin.close()
        try:
            self._process.wait(5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
        if self._process.stdout is not None:
            self._process.stdout.close()


class _Refused(Exception):
    """An upstream's JSON-RPC error, with its code and data."""

    def __init__(self, text: str, code: int | None, data: Any) -> None:
        super().__init__(text)
        self.text, self.code, self.data = text, code, data


def proxy_handlers(fid: Fiduciary, upstream: Upstream) -> dict[str, Callable[..., Any]]:
    """The handlers a proxy registers: one per upstream tool that a
    ``passthrough`` binding the deployment holds names. An upstream tool no
    binding names is never reachable (F8-7)."""
    passthrough = {t.name for t in fid.binder.deployment.tools.values() if t.kind == "passthrough"}
    return {name: upstream.handler(name) for name in upstream.tools() if name in passthrough}

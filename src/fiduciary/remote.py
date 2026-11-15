"""A remote MCP server, spoken to over Streamable HTTP (1.20.0).

``fiduciary protect`` wraps a remote server's entry (``{"type": "http", "url": ...,
"headers": {...}}``) the way it wraps a local one: the client starts the proxy over stdio, and
the proxy speaks HTTP to the server. The server's headers, its ``Authorization`` token among
them, are the proxy's: they leave the client's file, and the agent's process never holds them.

:class:`HttpUpstream` is the client side of the transport, in the shape
:class:`fiduciary.mcp.Upstream` has, so the proxy, its pins and Interlock's relay use either:

- **2026-07-28.** Every request is a POST carrying ``MCP-Protocol-Version``, ``Mcp-Method`` and,
  on ``tools/call``, ``Mcp-Name``, with each argument the tool marks ``x-mcp-header`` mirrored
  into ``Mcp-Param-<name>`` (a value that is not plain ASCII goes as ``=?base64?...?=``). A tool
  whose ``x-mcp-header`` marks break the rules is left out of the list, as the transport
  requires. The answer is one JSON object or a stream of server-sent events, read to the
  response that carries the request's id.
- **Older servers.** A server that does not answer ``server/discover`` is spoken to as
  2025-11-25 and before: ``initialize``, then every request with the ``Mcp-Session-Id`` it
  gave; a session the server forgot (404) is opened again once.
- **What it does not do.** Sign in with OAuth: a token the client got by OAuth stays with the
  client, and a server that answers 401 is left as it is. The old HTTP+SSE transport (2024-11-05)
  is not spoken. Redirects are not followed, and plain ``http`` is used only on loopback.
"""

from __future__ import annotations

import base64
import contextlib
import ipaddress
import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import IO, Any, Final

from fiduciary import __version__
from fiduciary.exceptions import FiduciaryError

__all__ = ["MAX_RESPONSE", "HttpUpstream", "RemoteError", "header_params", "header_value"]

MAX_RESPONSE: Final = 8 * 1024 * 1024
"""The most of one answer read, in bytes."""
_CLIENT: Final = {"name": "fiduciary-proxy", "version": __version__}
_TOKEN: Final = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
"""An HTTP field name's characters (RFC 9110 ``tchar``)."""
_PRIMITIVE: Final = frozenset({"string", "integer", "boolean"})
_SENTINEL: Final = re.compile(r"=\?base64\?.*\?=", re.DOTALL)


class RemoteError(FiduciaryError):
    """A remote server that cannot be spoken to: its URL, its answer, or its sign-in."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None  # a redirect is answered as the error it came with


class _StatusError(Exception):
    """An HTTP answer that is not a JSON-RPC message: its status and a little of its body."""

    def __init__(self, status: int, text: str) -> None:
        super().__init__(f"HTTP {status}: {text[:160]}")
        self.status = status


class _RefusedError(Exception):
    """A JSON-RPC error answer."""

    def __init__(self, text: str, code: int | None, data: Any) -> None:
        super().__init__(text)
        self.text, self.code, self.data = text, code, data


def _loopback(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    with contextlib.suppress(ValueError):
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    return False


def check_url(url: str) -> str:
    """``url``, when the proxy may speak to it: ``https``, or ``http`` on loopback.

    :raises RemoteError: For any other.
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return url
    if parts.scheme == "http" and parts.hostname and _loopback(parts.hostname):
        return url
    raise RemoteError(
        f"{url!r} is not a URL the proxy speaks to: https, or http on this machine only"
    )


def header_params(schema: object) -> dict[str, tuple[str, ...]] | None:
    """The arguments a tool's ``inputSchema`` marks ``x-mcp-header``, by header name, each with
    its path of ``properties`` keys; ``None`` when a mark breaks the transport's rules (an
    empty name or one that is no field name, a name twice, a mark on a number, an object or
    an array, or a mark anywhere a chain of ``properties`` does not reach)."""
    found: dict[str, tuple[str, ...]] = {}
    placed: set[int] = set()

    def walk(node: Mapping[str, Any], path: tuple[str, ...]) -> bool:
        properties = node.get("properties")
        if not isinstance(properties, Mapping):
            return True
        for key, child in properties.items():
            if not isinstance(child, Mapping):
                continue
            if "x-mcp-header" in child:
                name = child["x-mcp-header"]
                kind = child.get("type")
                if (
                    not isinstance(name, str)
                    or not name
                    or not _TOKEN.fullmatch(name)
                    or name.lower() in {n.lower() for n in found}
                    or not (isinstance(kind, str) and kind in _PRIMITIVE)
                ):
                    return False
                found[name] = (*path, str(key))
                placed.add(id(child))
            if not walk(child, (*path, str(key))):
                return False
        return True

    def marks(node: object) -> Iterator[int]:
        if isinstance(node, Mapping):
            if "x-mcp-header" in node:
                yield id(node)
            for value in node.values():
                yield from marks(value)
        elif isinstance(node, list):
            for value in node:
                yield from marks(value)

    if not isinstance(schema, Mapping):
        return {}
    if not walk(schema, ()):
        return None
    if any(mark not in placed for mark in marks(schema)):
        return None  # a mark under items, oneOf, $ref and the like: no chain reaches it
    return found


def header_value(value: object) -> str | None:
    """``value`` as a header carries it: text as it is when it is plain ASCII, else
    ``=?base64?...?=``; ``None`` for no value."""
    if value is None:
        return None
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, int):
        text = str(value)
    else:
        text = str(value)
    plain = (
        all(c == "\t" or " " <= c <= "~" for c in text)
        and text == text.strip()
        and not _SENTINEL.fullmatch(text)
    )
    if plain:
        return text
    return "=?base64?" + base64.b64encode(text.encode("utf-8")).decode("ascii") + "?="


def _at(arguments: Mapping[str, Any], path: Sequence[str]) -> object:
    node: object = arguments
    for key in path:
        if not isinstance(node, Mapping) or key not in node:
            return None
        node = node[key]
    return node


def _events(stream: IO[bytes], limit: int) -> Iterator[Any]:
    """The JSON-RPC messages of a server-sent event stream, as each event ends."""
    data: list[str] = []
    read = 0
    for raw in stream:
        read += len(raw)
        if read > limit:
            raise _StatusError(413, "the event stream is longer than the proxy reads")
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            if data:
                with contextlib.suppress(ValueError):
                    yield json.loads("\n".join(data))
                data = []
            continue
        if line.startswith(":"):
            continue  # a comment, a keep-alive
        field, _, value = line.partition(":")
        if field == "data":
            data.append(value.removeprefix(" "))
    if data:
        with contextlib.suppress(ValueError):
            yield json.loads("\n".join(data))


class HttpUpstream:
    """A remote MCP server over Streamable HTTP, in :class:`fiduciary.mcp.Upstream`'s shape
    (module docstring).

    :param url: The server's MCP endpoint.
    :param headers: Sent on every request: its sign-in among them. The proxy's, never the
        agent's.
    :param opener: How requests are made (tests).
    :raises fiduciary.mcp.McpError: When it does not answer, or no revision is shared.
    """

    def __init__(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        from interlock.mcp import McpCallError, handshake

        from fiduciary.mcp import INVALID_REQUEST, McpError

        self.url = check_url(url)
        self._headers = {str(k): str(v) for k, v in (headers or {}).items()}
        self._timeout = timeout
        self._open = opener or urllib.request.build_opener(_NoRedirect).open
        self._lock = threading.RLock()
        self._next = 0
        self._session: str | None = None
        self._legacy: str | None = None
        """The revision an older server answered ``initialize`` with."""
        self._paths: dict[str, dict[str, tuple[str, ...]]] = {}
        """Each listed tool's ``x-mcp-header`` arguments: header name and path."""
        self.era: Any = None
        self.changed = False
        """Whether the server said its tools changed since :meth:`definitions` last listed."""
        self.server_info: dict[str, Any] = {}
        """The name and version the server gave, when it gave them."""
        try:
            self.era = handshake(
                self._request_for_handshake,
                self._notify,
                _CLIENT,
                timeout=timeout,
                probe=timeout,
            )
        except (McpCallError, RemoteError) as exc:
            raise McpError(INVALID_REQUEST, f"the remote server did not start: {exc}") from exc

    # -- the wire -------------------------------------------------------------------------

    def _version(self, method: str) -> str | None:
        from interlock.mcp import MODERN_VERSIONS

        if self.era is not None:
            return str(self.era.version)
        if method == "initialize":
            return None  # 2025-11-25 sends no revision header on initialize
        return self._legacy or MODERN_VERSIONS[0]

    def _post(
        self, message: Mapping[str, Any], seconds: float, *, tool: str | None = None
    ) -> dict[str, Any] | None:
        """Send one message; the response that carries its id, or ``None`` for a
        notification the server accepted.

        :raises _RefusedError: On a JSON-RPC error answer.
        :raises _StatusError: On an HTTP answer that is no JSON-RPC message.
        :raises OSError: When the server cannot be reached.
        """
        method = str(message.get("method", ""))
        headers = {
            **self._headers,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Mcp-Method": method,
            "User-Agent": f"fiduciary-proxy/{__version__}",
        }
        version = self._version(method)
        if version is not None:
            headers["MCP-Protocol-Version"] = version
        if self._session is not None:
            headers["Mcp-Session-Id"] = self._session
        params = message.get("params")
        if isinstance(params, Mapping) and method in ("tools/call", "prompts/get"):
            name = header_value(params.get("name"))
            if name is not None:
                headers["Mcp-Name"] = name
        if tool is not None and isinstance(params, Mapping):
            arguments = params.get("arguments")
            for header, path in self._paths.get(tool, {}).items():
                value = header_value(_at(arguments if isinstance(arguments, Mapping) else {}, path))
                if value is not None:
                    headers[f"Mcp-Param-{header}"] = value
        request = urllib.request.Request(  # noqa: S310 - check_url admits https, or loopback http
            self.url, data=json.dumps(message).encode("utf-8"), headers=headers, method="POST"
        )
        try:
            answer = self._open(request, timeout=seconds)
        except urllib.error.HTTPError as exc:
            body = exc.read(MAX_RESPONSE)
            with contextlib.suppress(ValueError):
                found = json.loads(body)
                if isinstance(found, dict) and isinstance(found.get("error"), dict):
                    error = found["error"]
                    code = error.get("code")
                    raise _RefusedError(
                        str(error.get("message") or "an error")[:200],
                        code if isinstance(code, int) else None,
                        error.get("data"),
                    ) from exc
            raise _StatusError(exc.code, body.decode("utf-8", "replace")) from exc
        with contextlib.closing(answer):
            session = answer.headers.get("Mcp-Session-Id")
            if method == "initialize" and session:
                self._session = str(session)
            if "id" not in message:
                return None
            kind = str(answer.headers.get("Content-Type", "")).split(";")[0].strip().lower()
            if kind == "text/event-stream":
                for found in _events(answer, MAX_RESPONSE):
                    reply = self._reply(found, message["id"])
                    if reply is not None:
                        return reply
                raise _StatusError(502, "the event stream ended with no response to the request")
            body = answer.read(MAX_RESPONSE + 1)
            if len(body) > MAX_RESPONSE:
                raise _StatusError(413, "the answer is longer than the proxy reads")
            try:
                found = json.loads(body)
            except ValueError as exc:
                raise _StatusError(answer.status, "the answer is not JSON") from exc
            reply = self._reply(found, message["id"])
            if reply is None:
                raise _StatusError(502, "the answer is not the response to the request")
            return reply

    def _reply(self, found: object, ident: object) -> dict[str, Any] | None:
        if not isinstance(found, dict):
            return None
        if found.get("method") == "notifications/tools/list_changed":
            self.changed = True
        if "id" not in found or found.get("id") != ident:
            return None  # a notification, or a request of the server's own: not answered
        if isinstance(found.get("error"), dict):
            error = found["error"]
            code = error.get("code")
            raise _RefusedError(
                str(error.get("message") or "an error")[:200],
                code if isinstance(code, int) else None,
                error.get("data"),
            )
        result = found.get("result")
        result = result if isinstance(result, dict) else {}
        meta = result.get("_meta")
        info = meta.get("io.modelcontextprotocol/serverInfo") if isinstance(meta, dict) else None
        if isinstance(info, dict):
            self.server_info = dict(info)
        return result

    def _exchange(
        self,
        method: str,
        params: Mapping[str, Any],
        seconds: float,
        *,
        tool: str | None = None,
        again: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            self._next += 1
            message = {"jsonrpc": "2.0", "id": self._next, "method": method, "params": dict(params)}
            try:
                reply = self._post(message, seconds, tool=tool)
            except _StatusError as exc:
                if exc.status == 404 and self._session is not None and again:
                    # an older server forgot the session: open another, and ask once more
                    self._session = None
                    self._legacy_open(seconds)
                    return self._exchange(method, params, seconds, tool=tool, again=False)
                if exc.status == 401:
                    raise RemoteError(
                        "the server asked for a sign-in (401): protect holds a token the "
                        "entry's headers give it; one a client gets by OAuth stays with the client"
                    ) from exc
                raise
            assert reply is not None
            return reply

    def _legacy_open(self, seconds: float) -> None:
        from interlock.mcp import PROTOCOL_VERSION

        self._next += 1
        reply = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": dict(_CLIENT),
                },
            },
            seconds,
        )
        assert reply is not None
        self._notify("notifications/initialized")

    def _request_for_handshake(
        self, method: str, params: Mapping[str, Any], seconds: float
    ) -> Mapping[str, Any]:
        from interlock.mcp import McpCallError, McpRefusedError

        try:
            if method == "initialize":
                self.era = None
            found = self._exchange(method, params, seconds, again=False)
        except _RefusedError as exc:
            raise McpRefusedError(exc.text, code=exc.code, data=exc.data) from exc
        except _StatusError as exc:
            raise McpCallError(str(exc), sent=True) from exc
        except OSError as exc:
            raise McpCallError(f"cannot reach {self.url}: {exc}", sent=False) from exc
        if method == "initialize":
            info = found.get("serverInfo")
            if isinstance(info, dict):
                self.server_info = dict(info)
            version = found.get("protocolVersion")
            self._legacy = str(version) if isinstance(version, str) else None
        return found

    def _notify(self, method: str) -> None:
        with contextlib.suppress(_StatusError, _RefusedError, OSError):
            self._post({"jsonrpc": "2.0", "method": method}, self._timeout)

    # -- what the proxy asks --------------------------------------------------------------

    def request(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        """Send one request and read its response, in the agreed revision.

        :raises fiduciary.mcp.McpError: On an error response, or no answer.
        """
        from fiduciary.mcp import INVALID_REQUEST, McpError

        try:
            return self._exchange(method, self.era.params(params, _CLIENT), self._timeout)
        except RemoteError as exc:
            raise McpError(INVALID_REQUEST, str(exc)) from exc
        except _RefusedError as exc:
            raise McpError(INVALID_REQUEST, f"the remote server refused {method}") from exc
        except (_StatusError, OSError) as exc:
            raise McpError(
                INVALID_REQUEST, f"the remote server did not answer {method}: {exc}"
            ) from exc

    def definitions(self) -> list[dict[str, Any]]:
        """Every tool the server lists, as it lists it (every page), less any whose
        ``x-mcp-header`` marks break the transport's rules."""
        tools: list[dict[str, Any]] = []
        paths: dict[str, dict[str, tuple[str, ...]]] = {}
        cursor: str | None = None
        while True:
            found = self.request("tools/list", {"cursor": cursor} if cursor else {})
            for tool in found.get("tools") or ():
                if not isinstance(tool, dict):
                    continue
                params = header_params(tool.get("inputSchema"))
                if params is None:
                    continue  # the transport requires a client to leave it out
                paths[str(tool.get("name"))] = params
                tools.append(tool)
            cursor = found.get("nextCursor")
            if not cursor:
                self.changed = False
                self._paths = paths
                return tools

    def tools(self) -> list[str]:
        """The server's tool names."""
        return [str(t["name"]) for t in self.definitions() if "name" in t]

    def call(self, tool: str, arguments: Mapping[str, Any], timeout: float) -> dict[str, Any]:
        """One ``tools/call``, as :class:`interlock.mcp.McpAdapter` makes it.

        :raises interlock.mcp.McpCallError: When no result came back.
        """
        from interlock.mcp import McpCallError, McpRefusedError

        try:
            return self._exchange(
                "tools/call",
                self.era.params({"name": tool, "arguments": dict(arguments)}, _CLIENT),
                timeout,
                tool=tool,
            )
        except _RefusedError as exc:
            raise McpRefusedError(exc.text, code=exc.code, data=exc.data) from exc
        except RemoteError as exc:
            raise McpRefusedError(str(exc)) from exc  # a 401: the server did nothing
        except _StatusError as exc:
            raise McpCallError(str(exc), sent=True) from exc
        except OSError as exc:
            raise McpCallError(str(exc), sent=False) from exc

    def handler(self, name: str, upstream: str | None = None) -> Callable[[Mapping[str, Any]], Any]:
        """A passthrough implementation that calls remote tool ``upstream`` (``name`` by
        default)."""
        target = upstream or name

        def call(arguments: Mapping[str, Any]) -> dict[str, Any]:
            from fiduciary.mcp import INVALID_REQUEST, McpError

            try:
                result = self._exchange(
                    "tools/call",
                    self.era.params({"name": target, "arguments": dict(arguments)}, _CLIENT),
                    self._timeout,
                    tool=target,
                )
            except RemoteError as exc:
                raise McpError(INVALID_REQUEST, str(exc)) from exc
            except _RefusedError as exc:
                raise McpError(INVALID_REQUEST, f"the remote server refused {target}") from exc
            except (_StatusError, OSError) as exc:
                raise McpError(INVALID_REQUEST, f"the remote server did not answer: {exc}") from exc
            content = result.get("content", [])
            return {
                "content": [c for c in content if isinstance(c, dict)],
                "isError": bool(result.get("isError")),
            }

        return call

    def close(self) -> None:
        """End an older server's session (``DELETE``), when it gave one."""
        if self._session is None:
            return
        request = urllib.request.Request(  # noqa: S310 - the URL check_url admitted
            self.url,
            headers={**self._headers, "Mcp-Session-Id": self._session},
            method="DELETE",
        )
        with contextlib.suppress(urllib.error.URLError, OSError):
            self._open(request, timeout=5).close()
        self._session = None

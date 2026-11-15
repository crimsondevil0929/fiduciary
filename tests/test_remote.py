"""Remote MCP servers (1.20.0): ``fiduciary.remote.HttpUpstream`` against a stand-in that
speaks 2026-07-28 or 2025-11-25 over Streamable HTTP, and ``fiduciary protect`` on a remote
entry, end to end: the token leaves the client's file, the proxy holds it, the tools are
pinned, a held write waits, a changed definition is withheld, and ``unprotect`` puts the
entry back with its token."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from fiduciary import custody, protect
from fiduciary.mcp import McpError
from fiduciary.protect import read_servers
from fiduciary.remote import HttpUpstream, RemoteError, check_url, header_params, header_value
from tests.http_kit import StandIn
from tests.test_protect import Client

TOKEN = "tok_" + "Rk2v9QpL7xW3nB8cT5yZ1aD4"


def _sent(stand_in: StandIn, method: str) -> list[dict[str, str]]:
    return [
        {k.lower(): v for k, v in headers.items()}
        for headers, body in stand_in.seen
        if body is not None and body.get("method") == method
    ]


@pytest.mark.parametrize("sse", [False, True])
def test_a_modern_server_is_spoken_to_with_its_headers(sse: bool) -> None:
    with StandIn(sse=sse) as stand_in:
        upstream = HttpUpstream(stand_in.url)
        assert upstream.era.version == "2026-07-28" and upstream.era.modern
        listed = upstream.definitions()
        assert [t["name"] for t in listed] == ["search_docs", "create_doc", "delete_doc"], (
            "a tool whose x-mcp-header mark breaks the rules is left out"
        )
        answer = upstream.handler("search_docs")({"query": "q", "region": "eu-west"})
        assert json.loads(answer["content"][0]["text"])["query"] == "q"
        made = upstream.call("create_doc", {"title": "Plan"}, 10)
        assert made["structuredContent"] == {"id": "doc-1"}
        assert upstream.server_info == {"name": "stand-in"}
        (call, _) = _sent(stand_in, "tools/call")
        assert call["mcp-name"] == "search_docs" and call["mcp-param-region"] == "eu-west"
        assert call["mcp-method"] == "tools/call" and call["mcp-protocol-version"] == "2026-07-28"
        # a value that is not plain ASCII goes as base64, and the server reads it back
        upstream.handler("search_docs")({"query": "q", "region": "Zürich"})
        assert _sent(stand_in, "tools/call")[-1]["mcp-param-region"].startswith("=?base64?")
        upstream.close()  # a modern server has no session to end


def test_an_older_server_is_spoken_to_in_its_session() -> None:
    with StandIn(mode="legacy", sse=True) as stand_in:
        upstream = HttpUpstream(stand_in.url)
        assert upstream.era.version == "2025-11-25" and not upstream.era.modern
        assert upstream.server_info["version"] == "2.0.0"
        assert len(upstream.tools()) == 3
        stand_in.forget = True  # the server restarts and forgets the session: opened again
        assert upstream.call("create_doc", {"title": "x"}, 10)["structuredContent"]["id"]
        assert len(_sent(stand_in, "initialize")) == 2
        initialized = [
            b for _, b in stand_in.seen if b and b.get("method") == "notifications/initialized"
        ]
        assert len(initialized) == 2
        assert all(h["mcp-protocol-version"] == "2025-11-25" for h in _sent(stand_in, "tools/list"))
        upstream.close()
        assert stand_in.seen[-1][1] is None, "the session ends with a DELETE"


def test_what_the_remote_client_refuses() -> None:
    stand_in = StandIn(token=TOKEN)
    if True:
        with pytest.raises(McpError, match="sign-in"):
            HttpUpstream(stand_in.url)
        upstream = HttpUpstream(stand_in.url, headers={"Authorization": f"Bearer {TOKEN}"})
        assert len(upstream.definitions()) == 3
        with pytest.raises(McpError, match="refused"):
            upstream.request("no/such", {})
        stand_in.token = "another"
        with pytest.raises(McpError, match="sign-in"):
            upstream.request("tools/list", {})
        from interlock.mcp import McpRefusedError

        with pytest.raises(McpRefusedError, match="sign-in"):
            upstream.call("create_doc", {"title": "x"}, 5)
        with pytest.raises(McpError, match="sign-in"):
            upstream.handler("search_docs")({"query": "q"})
        stand_in.token = TOKEN
        stand_in.close()
        with pytest.raises(McpError, match="did not answer"):
            upstream.request("tools/list", {})
    with pytest.raises(McpError, match="did not start"):
        HttpUpstream("http://127.0.0.1:9/mcp", timeout=2)
    for bad in ("http://example.com/mcp", "ftp://x/y", "https:///nohost"):
        with pytest.raises(RemoteError, match="not a URL"):
            check_url(bad)
    assert check_url("http://[::1]:8000/mcp") and check_url("http://app.localhost/mcp")


def test_header_marks_and_values() -> None:
    string = {"type": "string"}
    assert header_params({"properties": {"a": {**string, "x-mcp-header": "A"}}}) == {"A": ("a",)}
    nested = {
        "properties": {
            "o": {"type": "object", "properties": {"b": {**string, "x-mcp-header": "B"}}}
        }
    }
    assert header_params(nested) == {"B": ("o", "b")}
    for broken in (
        {"properties": {"a": {"type": "number", "x-mcp-header": "A"}}},
        {"properties": {"a": {**string, "x-mcp-header": ""}}},
        {"properties": {"a": {**string, "x-mcp-header": "a b"}}},
        {
            "properties": {
                "a": {**string, "x-mcp-header": "A"},
                "b": {**string, "x-mcp-header": "a"},
            }
        },
        {"oneOf": [{"properties": {"a": {**string, "x-mcp-header": "A"}}}]},
    ):
        assert header_params(broken) is None
    assert header_params("no schema") == {}
    assert header_value("plain") == "plain" and header_value(None) is None
    assert header_value(True) == "true" and header_value(42) == "42"
    for odd in (" padded ", "line\nbreak", "=?base64?literal?="):
        assert header_value(odd) is not None and header_value(odd).startswith("=?base64?")  # type: ignore[union-attr]


def _client_file(tmp_path: Path, url: str, headers: dict[str, str]) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    config = tmp_path / ".mcp.json"
    config.write_text(
        json.dumps({"mcpServers": {"docs": {"type": "http", "url": url, "headers": headers}}})
    )
    return config


def test_protect_wraps_a_remote_server_and_keeps_its_token(tmp_path: Path) -> None:
    with StandIn(token=TOKEN, sse=True) as stand_in:
        config = _client_file(
            tmp_path, stand_in.url, {"Authorization": f"Bearer {TOKEN}", "X-Team": "blue"}
        )
        servers = read_servers(config, "claude-code")
        (planned,) = protect.protect(servers, root=tmp_path / "protect")
        assert planned.action == "wrap" and planned.remote and planned.command == [stand_in.url]
        (step,) = protect.protect(
            servers,
            root=tmp_path / "protect",
            apply=True,
            runner=[sys.executable, "-m", "fiduciary"],
        )
        assert step.secrets == ["Authorization"] and step.store is not None
        assert "only you can read" in step.store
        assert {t["name"]: t["mode"] for t in step.tools} == {
            "search_docs": "read",
            "create_doc": "write",
            "delete_doc": "held",
        }
        text = config.read_text()
        assert TOKEN not in text and stand_in.url not in text, "the token and URL left the file"
        entry = json.loads(text)["mcpServers"]["docs"]
        assert entry["type"] == "stdio" and entry["args"][-1] == "--protected"
        assert step.deployment is not None
        state = json.loads((step.deployment / protect.STATE).read_text())
        assert state["remote"]["headers"] == {
            "Authorization": {"secret": True},
            "X-Team": {"value": "blue"},
        }
        assert state["original"]["headers"]["Authorization"].startswith("(in a file")
        kept = custody.FileStore(tmp_path / "protect").get("claude-code/docs/header:Authorization")
        assert kept == f"Bearer {TOKEN}"

        client = Client(entry)
        try:
            listed = client.call("tools/list")["result"]["tools"]
            assert {t["name"] for t in listed} >= {"search_docs", "create_doc", "delete_doc"}
            read = client.tool("search_docs", {"query": "plans", "region": "eu"})
            assert not read["isError"] and "plans" in read["content"][0]["text"]
            made = client.tool("create_doc", {"title": "Q3"})
            assert not made["isError"] and made["structuredContent"] == {"id": "doc-1"}
            held = client.tool("delete_doc", {"id": "doc-1"})
            assert "approv" in held["content"][0]["text"].lower()
            stand_in.phase = 1  # the server changes how it describes search_docs
            withheld = client.tool("search_docs", {"query": "plans"})
            assert (
                withheld["isError"] and "changed how it describes" in withheld["content"][0]["text"]
            )
        finally:
            client.close()
        sent = _sent(stand_in, "tools/call")
        assert all(h["authorization"] == f"Bearer {TOKEN}" for h in sent)
        assert stand_in.docs == ["Q3"], "the held delete was not sent"
        (drift,) = protect.review(step.deployment)
        assert drift.tool == "search_docs"

        protect.undo(read_servers(config, "claude-code"))
        assert json.loads(config.read_text())["mcpServers"]["docs"] == {
            "type": "http",
            "url": stand_in.url,
            "headers": {"Authorization": f"Bearer {TOKEN}", "X-Team": "blue"},
        }


def test_a_remote_secret_moves_into_the_store_asked_for(tmp_path: Path) -> None:
    kept: dict[str, str] = {}

    class Vault:
        name, where = "keychain", "the stand-in vault"

        def put(self, account: str, value: str) -> None:
            kept[account] = value

        def get(self, account: str) -> str:
            return kept[account]

        def delete(self, account: str) -> None:
            kept.pop(account, None)

    with StandIn(token=TOKEN) as stand_in:
        url = stand_in.url + "?api_key=" + TOKEN
        config = _client_file(tmp_path, url, {"Authorization": f"Bearer {TOKEN}"})
        (step,) = protect.protect(
            read_servers(config, "cursor"),
            root=tmp_path / "protect",
            apply=True,
            move_secrets=True,
            store=Vault(),
            runner=[sys.executable, "-m", "fiduciary"],
        )
        assert step.store == "the stand-in vault" and step.secrets == ["Authorization", "url"]
        assert set(kept) == {"cursor/docs/header:Authorization", "cursor/docs/url"}
        assert step.command == [stand_in.url], "the URL shown has no query"
        assert step.deployment is not None
        state = json.loads((step.deployment / protect.STATE).read_text())
        assert state["remote"]["url"] is None and state["remote"]["url_secret"]
        assert state["store"] == "keychain"
        url_now, headers = protect._remote_headers(state, Vault())
        assert url_now == url and headers["Authorization"] == f"Bearer {TOKEN}"
        protect.undo(read_servers(config, "cursor"), keychain=Vault())
        restored = json.loads(config.read_text())["mcpServers"]["docs"]
        assert restored["url"] == url and restored["headers"]["Authorization"] == f"Bearer {TOKEN}"


def test_a_header_the_client_fills_in_is_passed_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with StandIn(token=TOKEN) as stand_in:
        config = _client_file(tmp_path, stand_in.url, {"Authorization": "Bearer ${DOCS_TOKEN}"})
        servers = read_servers(config, "claude-code")
        monkeypatch.delenv("DOCS_TOKEN", raising=False)
        (skipped,) = protect.protect(servers, root=tmp_path / "p", apply=True)
        assert skipped.action == "skip" and "${DOCS_TOKEN}, which is not set" in skipped.why
        monkeypatch.setenv("DOCS_TOKEN", TOKEN)
        (step,) = protect.protect(
            servers, root=tmp_path / "p", apply=True, runner=[sys.executable, "-m", "fiduciary"]
        )
        assert step.action == "wrap" and step.secrets == []
        entry = json.loads(config.read_text())["mcpServers"]["docs"]
        # the client fills the variable in when it starts the proxy, as it did for the server
        assert entry["env"] == {"FIDUCIARY_HEADER_AUTHORIZATION": "Bearer ${DOCS_TOKEN}"}
        assert step.deployment is not None
        state = json.loads((step.deployment / protect.STATE).read_text())
        _, headers = protect._remote_headers(
            state, None, {"FIDUCIARY_HEADER_AUTHORIZATION": f"Bearer {TOKEN}"}
        )
        assert headers == {"Authorization": f"Bearer {TOKEN}"}
        with pytest.raises(protect.ProtectError, match="passed no FIDUCIARY_HEADER"):
            protect._remote_headers(state, None, {})
    inputs = _client_file(
        tmp_path / "vs", "https://mcp.example.com/mcp", {"Authorization": "${input:t}"}
    )
    (asked,) = protect.protect(read_servers(inputs, "vscode"), root=tmp_path / "q", apply=True)
    assert asked.action == "skip" and "asks the client for ${input:t}" in asked.why
    (plain,) = protect.protect(
        read_servers(_client_file(tmp_path / "h", "http://example.com/mcp", {}), "x"),
        root=tmp_path / "r",
    )
    assert plain.action == "skip" and "https, or http on this machine only" in plain.why


def test_a_remote_server_that_will_not_answer_is_left_as_it_is(tmp_path: Path) -> None:
    with StandIn(token=TOKEN) as stand_in:
        config = _client_file(tmp_path, stand_in.url, {})
        (step,) = protect.protect(read_servers(config, "x"), root=tmp_path / "p", apply=True)
        assert step.action == "skip" and "sign-in (401)" in step.why
        assert json.loads(config.read_text())["mcpServers"]["docs"]["type"] == "http"


def _remote_state(tmp_path: Path, url: str) -> dict[str, Any]:
    return {
        "owner": str(tmp_path / "owner"),
        "client": "c",
        "server": "s",
        "remote": {"url": url, "url_secret": False, "headers": {"X": {"template": "${X_KEY}"}}},
    }


def test_a_codex_template_header_reads_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _remote_state(tmp_path, "https://mcp.example.com/mcp")
    monkeypatch.setenv("X_KEY", "k-1")
    assert protect._remote_headers(state, None)[1] == {"X": "k-1"}
    assert protect._signed(state)
    assert not protect._signed({"remote": {"headers": {}, "url_secret": False}})


SDK_SERVER = r'''
import sys
from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

app = MCPServer("sdk-stand-in")

@app.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def echo(text: str) -> str:
    """Say it back."""
    return "echo: " + text

@app.tool(annotations=ToolAnnotations(destructiveHint=False))
def note(title: str) -> str:
    """Write a note."""
    return "noted " + title

app.run("streamable-http", host="127.0.0.1", port=int(sys.argv[1]),
        stateless_http=sys.argv[2] == "stateless", json_response=sys.argv[2] == "stateless")
'''


@pytest.mark.parametrize("mode", ["stateless", "stateful"])
def test_against_the_official_mcp_sdk_server(tmp_path: Path, mode: str) -> None:
    """The reference implementation's Streamable HTTP server (``mcp`` 2.x, in the dev group),
    as the proxy meets it: listed, pinned, read and written through ``protect``."""
    import socket
    import subprocess
    import time

    pytest.importorskip("mcp.server.mcpserver")
    pytest.importorskip("uvicorn")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    script = tmp_path / "sdk_server.py"
    script.write_text(SDK_SERVER)
    server = subprocess.Popen(  # noqa: S603 - the test's own server
        [sys.executable, str(script), str(port), mode],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}/mcp"
        for _ in range(100):
            with socket.socket() as attempt:
                if attempt.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.1)
        upstream = HttpUpstream(url)
        assert upstream.era.version == "2026-07-28"
        assert {t["name"] for t in upstream.definitions()} == {"echo", "note"}
        upstream.close()
        config = _client_file(tmp_path, url, {})
        (step,) = protect.protect(
            read_servers(config, "claude-code"),
            root=tmp_path / "protect",
            apply=True,
            runner=[sys.executable, "-m", "fiduciary"],
        )
        assert {t["name"]: t["mode"] for t in step.tools} == {"echo": "read", "note": "write"}
        client = Client(json.loads(config.read_text())["mcpServers"]["docs"])
        try:
            read = client.tool("echo", {"text": "hi"})
            assert read["content"][0]["text"] == "echo: hi"
            wrote = client.tool("note", {"title": "Q4"})
            assert not wrote["isError"] and wrote["content"][0]["text"] == "noted Q4"
        finally:
            client.close()
    finally:
        server.terminate()
        server.wait(10)


class _Answer:
    """What a fake opener returns: a response with headers and a body, read once."""

    def __init__(self, body: bytes, kind: str = "application/json", status: int = 200) -> None:
        import io

        self._stream = io.BytesIO(body)
        self.headers = {"Content-Type": kind}
        self.status = status

    def read(self, limit: int = -1) -> bytes:
        return self._stream.read(limit)

    def __iter__(self) -> Any:
        return iter(self._stream.readlines())

    def close(self) -> None:
        pass


def _fake(script: list[Any]) -> Any:
    """An opener that answers each request with the next of ``script``: an ``_Answer``, a
    callable of the request, or an exception to raise."""

    def opener(request: Any, timeout: float = 0) -> Any:
        step = script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step(request) if callable(step) else step

    return opener


def _discovered() -> _Answer:
    return _Answer(
        json.dumps(
            {"jsonrpc": "2.0", "id": 1, "result": {"supportedVersions": ["2026-07-28"]}}
        ).encode()
    )


def test_what_a_remote_answer_can_get_wrong() -> None:
    import io
    import urllib.error

    from interlock.mcp import McpCallError, McpRefusedError

    url = "https://mcp.example.com/mcp"

    def http_error(status: int, body: bytes) -> urllib.error.HTTPError:
        return urllib.error.HTTPError(url, status, "x", {}, io.BytesIO(body))  # type: ignore[arg-type]

    # an older server answers the probe with plain text: it is spoken to with initialize
    legacy = [
        http_error(405, b"method not allowed"),
        _Answer(
            json.dumps(
                {"jsonrpc": "2.0", "id": 2, "result": {"protocolVersion": "2025-11-25"}}
            ).encode()
        ),
        _Answer(b"", status=202),
    ]
    upstream = HttpUpstream(url, opener=_fake(legacy))
    assert upstream.era.version == "2025-11-25"
    # the answers that are not the response to the request
    sse_end = _Answer(
        b'data: {"jsonrpc":"2.0","method":"notifications/tools/list_changed"}\n\n',
        "text/event-stream",
    )
    upstream._open = _fake([sse_end])
    with pytest.raises(McpError, match="ended with no response"):
        upstream.request("tools/list", {})
    assert upstream.changed, "a list change on the stream is kept"
    upstream._open = _fake([_Answer(b"not json")])
    with pytest.raises(McpError, match="not JSON"):
        upstream.request("tools/list", {})
    upstream._open = _fake(
        [_Answer(json.dumps({"jsonrpc": "2.0", "id": 999, "result": {}}).encode())]
    )
    with pytest.raises(McpError, match="not the response"):
        upstream.request("tools/list", {})
    trailing = _Answer(b'data: {"jsonrpc":"2.0","id":7,"result":{"tools":[]}}', "text/event-stream")
    upstream._next = 6
    upstream._open = _fake([trailing])
    assert upstream.request("tools/list", {}) == {"tools": []}, "a last event with no blank line"
    upstream._open = _fake(
        [http_error(500, json.dumps({"error": {"code": -32603, "message": "boom"}}).encode())]
    )
    with pytest.raises(McpRefusedError, match="boom"):
        upstream.call("create_doc", {}, 5)
    upstream._open = _fake([http_error(502, b"bad gateway")])
    with pytest.raises(McpCallError, match="HTTP 502"):
        upstream.call("create_doc", {}, 5)
    upstream._open = _fake([OSError("reset")])
    with pytest.raises(McpCallError, match="reset"):
        upstream.call("create_doc", {}, 5)
    upstream._open = _fake(
        [http_error(500, json.dumps({"error": {"code": -32603, "message": "boom"}}).encode())]
    )
    with pytest.raises(McpError, match="refused create_doc"):
        upstream.handler("create_doc")({})
    upstream._open = _fake([http_error(503, b"busy")])
    with pytest.raises(McpError, match="did not answer"):
        upstream.handler("create_doc")({})
    upstream._open = _fake([http_error(400, b"")])
    with pytest.raises(McpError, match="did not answer"):
        upstream.request("tools/list", {})
    big = _Answer(b"x" * (8 * 1024 * 1024 + 2))
    upstream._open = _fake([big])
    with pytest.raises(McpError, match="longer than the proxy reads"):
        upstream.request("tools/list", {})
    long_stream = _Answer(b"data: " + b"x" * (8 * 1024 * 1024 + 2) + b"\n\n", "text/event-stream")
    upstream._open = _fake([long_stream])
    with pytest.raises(McpError, match="longer than the proxy reads"):
        upstream.request("tools/list", {})
    listed = {"tools": ["not a tool", {"name": "ok", "inputSchema": {"type": "object"}}]}
    upstream._open = _fake(
        [
            _Answer(
                json.dumps({"jsonrpc": "2.0", "id": upstream._next + 1, "result": listed}).encode()
            )
        ]
    )
    assert [t["name"] for t in upstream.definitions()] == ["ok"]
    # a redirect is never followed: it is answered as the error it came with
    from fiduciary.remote import _NoRedirect

    _NoRedirect().redirect_request()  # answers nothing: the redirect stays the error
    assert header_params({"properties": {"a": "not a schema", "b": {"type": "string"}}}) == {}

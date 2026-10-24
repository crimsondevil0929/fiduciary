"""MCP conformance (§8.2, F8-4 to F8-7, F7-4), on SQLite and PostgreSQL:
the methods, stdio, Streamable HTTP with a token per session, and the proxy."""

from __future__ import annotations

import datetime as dt
import http.client
import io
import json
import os
import select
import ssl
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from fiduciary import mcp as mcp_module
from fiduciary.bridge import Fiduciary, Session
from fiduciary.mcp import (
    HttpBridge,
    McpError,
    McpServer,
    Upstream,
    parse_message,
    proxy_handlers,
    serve_stdio,
)
from fiduciary.store import Store
from tests import bound
from tests import bridge_kit as kit
from tests import examples as e
from tests.conftest import Clock
from tests.test_executor import _verify

UPSTREAM = str(Path(__file__).with_name("upstream_server.py"))


@pytest.fixture
def world(store: Store, tmp_path: Path, clock: Clock) -> Iterator[bound.World]:
    yield from kit.world(store, tmp_path, clock)


@pytest.fixture
def fid(world: bound.World) -> Fiduciary:
    world.issue_and_bind(kit.mandate(world))
    return kit.fiduciary(world, in_process="no")


def _session(fid: Fiduciary, tenant: str = "acme") -> Session:
    return fid.session(bound.SCOPE, tenant=tenant, bridged=True)


def _call(server: McpServer, ident: int, method: str, params: Any = None) -> Any:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": ident, "method": method}
    if params is not None:
        message["params"] = params
    return server.handle(message)


def _chain(world: bound.World) -> int:
    mandate_id = next(iter(world.log.state().bound))
    return len(world.binder.engine(mandate_id)._chain.records())


REFUND = {"order_id": 7, "amount": "12.50", "payment_intent": "pi_0000000007"}


# -- the methods (F8-4, F8-5, F7-4) -------------------------------------------------------


def test_tools_list_is_the_pinned_bindings_and_the_built_ins(fid: Fiduciary) -> None:
    server = McpServer(_session(fid))
    init = _call(server, 1, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}})
    assert init["result"]["protocolVersion"] == "2025-03-26"
    assert init["result"]["capabilities"] == {"tools": {"listChanged": False}}
    later = _call(server, 2, "initialize", {"protocolVersion": "2099-01-01"})
    assert later["result"]["protocolVersion"] == "2025-06-18"
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server.initialized
    assert _call(server, 3, "ping")["result"] == {}
    tools = {t["name"]: t for t in _call(server, 4, "tools/list")["result"]["tools"]}
    assert set(tools) == {
        "adjust_orders",
        "lookup_order",
        "refund_order",
        "fiduciary_request",
        "fiduciary_ticket_status",
    }
    refund = kit.refund(fid.binder.deployment.ledger and "sqlite")  # the schema is the same
    assert tools["refund_order"]["inputSchema"] == dict(refund.input)
    assert tools["refund_order"]["description"] == refund.description
    assert tools["lookup_order"]["inputSchema"] == dict(e.lookup().input)


def test_a_call_and_a_refusal(world: bound.World, fid: Fiduciary) -> None:
    server = McpServer(_session(fid))
    done = _call(server, 1, "tools/call", {"name": "refund_order", "arguments": REFUND})
    assert done["result"]["isError"] is False
    assert done["result"]["content"][0]["text"].startswith(
        "Committed: refund_order ran as plan plan-"
    )
    before = _chain(world)
    # Outside the schema: a float. Refused as a result, before any plan (F8-5).
    floated = {**REFUND, "order_id": 7.0}
    refused = _call(server, 2, "tools/call", {"name": "refund_order", "arguments": floated})
    assert refused["result"]["isError"] is True
    assert "arguments were refused" in refused["result"]["content"][0]["text"]
    served = _call(server, 3, "tools/call", {"name": "lookup_order", "arguments": {"order_id": 3}})
    assert json.loads(served["result"]["content"][0]["text"]) == {"order_id": 3, "status": "open"}
    assert _chain(world) == before
    _verify(world)


def test_a_malformed_request_is_an_error_and_builds_nothing(
    world: bound.World, fid: Fiduciary
) -> None:
    server = McpServer(_session(fid))
    before = _chain(world)
    for params, code in (
        ({"name": 7, "arguments": {}}, -32602),
        ({"name": "refund_order", "arguments": [1, 2]}, -32602),
        ([1], -32602),
    ):
        found = _call(server, 1, "tools/call", params)
        assert found["error"]["code"] == code, found
    for invalid in (
        [{"jsonrpc": "2.0"}],
        {"jsonrpc": "1.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "id": True, "method": "ping"},
    ):
        answer = server.handle(invalid)
        assert answer is not None and answer["error"]["code"] == -32600
    assert server.handle({"jsonrpc": "2.0", "method": "tools/call"}) is None
    assert server.handle({"jsonrpc": "1.0", "method": "ping"}) is None
    with pytest.raises(McpError) as bad:
        parse_message(b'{"jsonrpc": "2.0", "id": 1, "id": 2}')
    assert bad.value.code == -32700
    for text in (b"NaN", b"{", b"\xff", b"x" * (1_048_577)):
        with pytest.raises(McpError):
            parse_message(text)
    assert _chain(world) == before
    assert [r.kind for r in world.log.records()][-1] == "session.opened"  # nothing recorded


def test_no_method_and_no_tool_takes_an_approval(world: bound.World, fid: Fiduciary) -> None:
    """F7-4: nothing an agent reaches submits, relays or shows an approval."""
    server = McpServer(_session(fid))
    intake = list(world.store.intake())
    for method in (
        "approvals/submit",
        "approve",
        "intake/submit",
        "tickets/approve",
        "resources/list",
        "prompts/list",
        "sampling/createMessage",
    ):
        assert _call(server, 1, method, {})["error"]["code"] == -32601
    names = {t["name"] for t in _call(server, 2, "tools/list")["result"]["tools"]}
    assert not {n for n in names if "approv" in n or "warrant" in n or "intake" in n}
    approval = e.approval("cfo", subject="ticket", digest="a" * 64, resolution="b" * 64).to_json()
    for name in ("approve", "submit_approval"):
        found = _call(server, 3, "tools/call", {"name": name, "arguments": approval})
        assert found["result"]["isError"] is True
    assert list(world.store.intake()) == intake


def test_stdio_serves_a_session_until_its_pipe_closes(fid: Fiduciary) -> None:
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "nope"}},
    ]
    raw = b"".join(json.dumps(m).encode() + b"\n" for m in lines) + b"\n" + b'{"a": 1, "a": 2}\n'
    out = io.BytesIO()
    serve_stdio(McpServer(_session(fid)), io.BytesIO(raw), out)
    answers = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [a.get("id") for a in answers] == [1, 2, None]
    assert answers[1]["result"]["isError"] is True
    assert answers[2]["error"]["code"] == -32700


def _answers(fd: int, count: int, *, within: float) -> list[Any]:
    """The first ``count`` lines written to ``fd``, or those written within
    ``within`` seconds."""
    held, deadline = b"", time.monotonic() + within
    while held.count(b"\n") < count:
        left = deadline - time.monotonic()
        if left <= 0 or not select.select([fd], [], [], left)[0]:
            break
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        held += chunk
    return [json.loads(line) for line in held.splitlines()]


def test_stdio_answers_a_request_written_with_the_message_before_it(
    fid: Fiduciary, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Messages written together arrive in one read. Each is answered at
    once, not when the next is written or the service's turn comes: a client
    that sends ``notifications/initialized`` and then ``tools/list`` gets its
    list (the stand-in MCP loop of ``scripts/live_acceptance.py`` waited on
    it for good)."""
    monkeypatch.setattr(mcp_module, "SERVICE_SECONDS", 30.0)
    into, feed = os.pipe()
    out, write = os.pipe()
    reader, writer = os.fdopen(into, "rb"), os.fdopen(write, "wb")
    turns: list[float] = []
    serving = threading.Thread(
        target=serve_stdio,
        args=(McpServer(_session(fid)), reader, writer),
        kwargs={"service": lambda: turns.append(time.monotonic())},
    )
    serving.start()
    try:
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}},
        ]
        os.write(feed, b"".join(json.dumps(m).encode() + b"\n" for m in messages))
        answers = _answers(out, 3, within=10)
        assert [a["id"] for a in answers] == [1, 2, 3]
        assert answers[1]["result"]["tools"]
        assert turns == []  # answered with no turn of the service between
    finally:
        os.close(feed)
        serving.join(10)
        reader.close()
        writer.close()
        os.close(out)
    assert not serving.is_alive()


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"one\n",
        b"one\ntwo\n\nthree",
        b"short\n" + b"x" * 20 + b"\nafter\n",
        b"y" * 9 + b"\n" + b"z" * 8 + b"\n",
    ],
)
def test_a_pipe_is_read_line_by_line_as_a_buffered_reader_reads_it(
    data: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pipe's lines are the lines ``readline(MAX_MESSAGE + 1)`` gives,
    an over-long one cut where it cuts it, then ``b""`` at the end."""
    monkeypatch.setattr(mcp_module, "MAX_MESSAGE", 8)
    expected, buffered = [], io.BytesIO(data)
    while line := buffered.readline(9):
        expected.append(line)
    into, feed = os.pipe()
    with os.fdopen(into, "rb") as reader:
        lines = mcp_module._Lines(reader)
        assert lines.ready(0) is False  # nothing written yet
        os.write(feed, data)
        os.close(feed)
        read = []
        while lines.ready(5) and (line := lines.readline()):
            read.append(line)
        assert lines.readline() == b""
    assert read == expected


# -- Streamable HTTP (F8-6) ----------------------------------------------------------------


def _post(
    address: tuple[str, int],
    body: Any,
    *,
    token: str | None = None,
    headers: dict[str, str] | None = None,
    path: str = "/mcp",
    method: str = "POST",
    context: ssl.SSLContext | None = None,
) -> tuple[int, dict[str, str], Any]:
    host, port = address
    conn = (
        http.client.HTTPSConnection(host, port, context=context, timeout=10)
        if context is not None
        else http.client.HTTPConnection(host, port, timeout=10)
    )
    sent = {"Content-Type": "application/json", **(headers or {})}
    if token is not None:
        sent["Authorization"] = f"Bearer {token}"
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    conn.request(method, path, body=data if method == "POST" else None, headers=sent)
    response = conn.getresponse()
    payload = response.read()
    conn.close()
    return (
        response.status,
        {k.lower(): v for k, v in response.getheaders()},
        json.loads(payload) if payload else None,
    )


def test_a_token_opens_its_session_and_nothing_else(world: bound.World, fid: Fiduciary) -> None:
    with pytest.raises(McpError, match="not loopback"):
        HttpBridge("0.0.0.0", 0)  # noqa: S104 - refused: the network needs TLS
    bridge = HttpBridge("127.0.0.1", 0, lock=fid.lock)
    acme, beta = _session(fid, "acme"), _session(fid, "beta")
    token_a, token_b = bridge.add(acme), bridge.add(beta)
    assert token_a != token_b and len(token_a) >= 40
    bridge.start()
    try:
        where = bridge.address
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        status, headers, body = _post(where, init, token=token_a)
        assert status == 200 and headers["mcp-session-id"] == acme.session_id
        assert body["result"]["serverInfo"]["name"] == "fiduciary"
        call = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "refund_order", "arguments": REFUND},
        }
        # Order 7 is acme's: acme's token commits it; beta's session is refused it.
        assert _post(where, call, token=token_a)[2]["result"]["isError"] is False
        assert _post(where, call, token=token_b)[2]["result"]["isError"] is True
        assert _post(where, init)[0] == 401
        assert _post(where, init, token="x" * 43)[0] == 401
        assert _post(where, init, headers={"Authorization": f"Basic {token_a}"})[0] == 401
        crossed = {"Mcp-Session-Id": beta.session_id}
        assert _post(where, init, token=token_a, headers=crossed)[0] == 404
        assert _post(where, init, token=token_a, path="/approve")[0] == 404
        assert _post(where, init, token=token_a, path="/intake")[0] == 404
        for method in ("GET", "DELETE", "PUT"):
            assert _post(where, init, token=token_a, method=method)[0] == 405
        evil = {"Origin": "http://evil.example"}
        assert _post(where, init, token=token_a, headers=evil)[0] == 403
        local = {"Origin": "http://localhost:3000"}
        assert _post(where, init, token=token_a, headers=local)[0] == 200
        note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        assert _post(where, note, token=token_a)[0] == 202
        assert _post(where, b"{", token=token_a)[2]["error"]["code"] == -32700
        oversized = {"Content-Length": str(1_048_577)}  # refused before a byte is read
        assert _post(where, b"", token=token_a, headers=oversized)[0] == 413
    finally:
        bridge.stop()
    _verify(world)


def _certificate(tmp: Path) -> tuple[Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fiduciary-test")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(__import__("ipaddress").ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp / "bridge.pem", tmp / "bridge.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def test_over_tls_the_bridge_may_listen_beyond_loopback(
    world: bound.World, fid: Fiduciary, tmp_path: Path
) -> None:
    cert, key = _certificate(tmp_path)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    bridge = HttpBridge("127.0.0.1", 0, tls=tls, lock=fid.lock)
    token = bridge.add(_session(fid))
    bridge.start()
    try:
        client = ssl.create_default_context(cafile=str(cert))
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        status, _, body = _post(bridge.address, ping, token=token, context=client)
        assert status == 200 and body["result"] == {}
    finally:
        bridge.stop()
    wide = HttpBridge("0.0.0.0", 0, tls=tls)  # noqa: S104 - TLS: permitted
    wide.stop()


# -- the proxy (F8-7) ---------------------------------------------------------------------


def test_the_proxy_exposes_only_pinned_upstream_tools(
    world: bound.World, fid: Fiduciary, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "upstream.log"
    monkeypatch.setenv("UPSTREAM_LOG", str(log))
    upstream = Upstream([sys.executable, UPSTREAM])
    try:
        assert set(upstream.tools()) == {"lookup_order", "purge_orders"}
        handlers = proxy_handlers(fid, upstream)
        assert set(handlers) == {"lookup_order"}
        for name, handler in handlers.items():
            fid.register(name, handler)
        server = McpServer(_session(fid))
        listed = {t["name"]: t for t in _call(server, 1, "tools/list")["result"]["tools"]}
        assert "purge_orders" not in listed
        assert listed["lookup_order"]["description"] == e.lookup().description  # the binding's
        found = _call(
            server, 2, "tools/call", {"name": "lookup_order", "arguments": {"order_id": 4}}
        )
        assert found["result"] == {
            "content": [{"type": "text", "text": "order 4: open"}],
            "isError": False,
        }
        purge = _call(server, 3, "tools/call", {"name": "purge_orders", "arguments": {}})
        assert purge["result"]["isError"] is True
        with pytest.raises(McpError, match=r"did not answer|refused"):
            upstream.request("no/such/method", {})
    finally:
        upstream.close()
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls == [{"arguments": {"order_id": 4}, "name": "lookup_order"}]
    world.governor.refresh()
    metered = [
        en
        for en in world.governor.audit_trail(bound.SCOPE)
        if en.memo.endswith("tool lookup_order") and en.entry_type.value == "spend"
    ]
    assert len(metered) == 1
    refusals = [r.content for r in world.log.records() if r.kind == "session.refused"]
    assert refusals[-1]["tool"] is None and refusals[-1]["code"] == "unpinned"
    _verify(world)


UPSTREAM_IN_ONE_WRITE = """
import json, os, sys
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    result = {"tools": [{"name": "lookup_order"}]} if message["method"] == "tools/list" else {}
    note = {"jsonrpc": "2.0", "method": "notifications/message", "params": {}}
    answer = {"jsonrpc": "2.0", "id": message["id"], "result": result}
    os.write(1, (json.dumps(note) + "\\n" + json.dumps(answer) + "\\n").encode())
"""


def test_an_upstream_answer_written_with_a_notification_is_read() -> None:
    """An upstream that writes a notification and its answer together: the
    answer, in the reader's buffer behind the notification, is read at once,
    not taken for silence until the timeout."""
    began = time.monotonic()
    upstream = Upstream([sys.executable, "-c", UPSTREAM_IN_ONE_WRITE], timeout=20)
    try:
        assert upstream.tools() == ["lookup_order"]
    finally:
        upstream.close()
    assert time.monotonic() - began < 15


def test_an_upstream_that_will_not_run_is_refused(tmp_path: Path) -> None:
    with pytest.raises(McpError, match="cannot run"):
        Upstream([str(tmp_path / "missing")])
    with pytest.raises(McpError, match="upstream command"):
        Upstream([])
    with pytest.raises(McpError, match=r"closed its pipe|did not answer"):
        Upstream([sys.executable, "-c", "pass"], timeout=5)


def test_a_built_in_answers_in_words_and_in_value(fid: Fiduciary) -> None:
    """Over MCP as in a session: the sentence, then the value it carries."""
    session = _session(fid)
    server = McpServer(session)
    asked = session.call("adjust_orders", {"first": 1, "second": 2, "amount": "1.00"})
    assert asked.ticket is not None
    found = _call(
        server,
        1,
        "tools/call",
        {"name": "fiduciary_ticket_status", "arguments": {"ticket_id": asked.ticket.ticket_id}},
    )
    words, value = found["result"]["content"][0]["text"].split("\n")
    assert words.startswith(f"Ticket {asked.ticket.ticket_id} (refused) is ")
    assert json.loads(value)["ticket_id"] == asked.ticket.ticket_id


def test_an_agents_token_opens_sessions_of_its_own(world: bound.World, fid: Fiduciary) -> None:
    """``fiduciary serve``'s agents (``[bridge.agents]``): the bridge holds the
    SHA-256 of each agent's token; every ``initialize`` opens a session, up
    to the agent's bound; ``DELETE`` ends one; an idle one is closed; an
    agent dropped at a reload loses its sessions."""
    import hashlib

    from fiduciary.mcp import Agent

    def agent(name: str, token: str, mandate: str = bound.SCOPE, most: int = 2) -> Agent:
        return Agent(
            name,
            hashlib.sha256(token.encode()).hexdigest(),
            lambda: fid.session(mandate, tenant="acme", bridged=True),
            most,
        )

    desk = agent("desk", "desk-token")
    bridge = HttpBridge(
        "127.0.0.1", 0, lock=fid.lock, agents={"desk": desk, "lost": agent("lost", "x", "nope")}
    )
    static = bridge.add(_session(fid))
    bridge.start()
    try:
        where = bridge.address
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        listing = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        assert _post(where, listing, token="desk-token")[0] == 400  # initialize first
        assert _post(where, b"[]", token="desk-token")[0] == 400
        status, headers, _ = _post(where, init, token="desk-token")
        first = headers["mcp-session-id"]
        assert status == 200
        second = _post(where, init, token="desk-token")[1]["mcp-session-id"]
        assert first != second and bridge.open_sessions() == 2
        assert _post(where, init, token="desk-token")[0] == 429  # its bound
        mine = {"Mcp-Session-Id": first}
        assert _post(where, listing, token="desk-token", headers=mine)[0] == 200
        assert _post(where, listing, token="desk-token", headers={"Mcp-Session-Id": "x"})[0] == 404
        # A session of another lineage that is not in force: refused, nothing opened.
        assert _post(where, init, token="x")[0] == 403
        assert _post(where, init, token=static, method="DELETE")[0] == 405
        assert _post(where, init, token="desk-token", method="DELETE")[0] == 404
        assert _post(where, init, token="desk-token", method="DELETE", headers=mine)[0] == 204
        assert _post(where, listing, token="desk-token", headers=mine)[0] == 404
        assert bridge.close_idle(3600) == 0
        assert bridge.close_idle(0) == 1 and bridge.open_sessions() == 0
        third = _post(where, init, token="desk-token")[1]["mcp-session-id"]
        bridge.set_agents({"desk": desk})  # kept: the same token
        assert bridge.open_sessions() == 1
        bridge.set_agents({"desk": agent("desk", "new-token")})  # another token: closed
        assert bridge.open_sessions() == 0
        assert (
            _post(where, listing, token="desk-token", headers={"Mcp-Session-Id": third})[0] == 401
        )
        assert _post(where, init, token="new-token")[0] == 200
        bridge.close_sessions()
        assert bridge.open_sessions() == 0
    finally:
        bridge.stop()
    state = world.log.state()
    events = [event for events in state.sessions.values() for event, _, _ in events]
    assert events.count("opened") == 5 and events.count("closed") == 4  # the static stays
    _verify(world)

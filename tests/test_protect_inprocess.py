"""``fiduciary protect`` and its proxy in this process (1.17.0), and the commands around it.

``test_protect.py`` runs the rewritten entry as a client would, in a child process. Here
the same proxy is served over pipes in a thread, so what it does is measured where the
tests run; the commands (``protect``, ``unprotect``, ``turn``, ``passkey add``) are called
through ``cli.main``; and the bridge's 2026-07-28 paths are held to the revision's rules.
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import threading
import urllib.request
import webbrowser
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from fiduciary import cli, passkeys, protect, turns
from fiduciary.mcp import McpError, McpServer, Upstream
from fiduciary.protect import Keychain, ProtectError, Server, read_servers
from tests.test_protect import write_client_file

FIDUCIARY = [sys.executable, "-m", "fiduciary"]


class Pipes:
    """The proxy served in a thread over two pipes; this side speaks JSON-RPC to it."""

    def __init__(self, config: Path) -> None:
        to_proxy_r, self._to_proxy = os.pipe()
        self._from_proxy, from_proxy_w = os.pipe()
        reader = os.fdopen(to_proxy_r, "rb", buffering=0)
        writer = os.fdopen(from_proxy_w, "wb")
        self.thread = threading.Thread(
            target=self._serve, args=(config, reader, writer), daemon=True
        )
        self.error: BaseException | None = None
        self.thread.start()
        self.ident = 0
        self.notes: list[str] = []
        self._buffer = b""

    def _serve(self, config: Path, reader: Any, writer: Any) -> None:
        try:
            protect.serve(config, reader, writer)
        except BaseException as exc:  # surfaced by close()
            self.error = exc
        finally:
            writer.close()

    def _line(self) -> bytes:
        while b"\n" not in self._buffer:
            ready, _, _ = select.select([self._from_proxy], [], [], 60)
            assert ready, "the proxy did not answer in 60 s"
            chunk = os.read(self._from_proxy, 65536)
            assert chunk, f"the proxy stopped: {self.error!r}"
            self._buffer += chunk
        line, _, self._buffer = self._buffer.partition(b"\n")
        return line

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.ident += 1
        message = {"jsonrpc": "2.0", "id": self.ident, "method": method, "params": params or {}}
        os.write(self._to_proxy, json.dumps(message).encode() + b"\n")
        while True:
            answer: dict[str, Any] = json.loads(self._line())
            if "id" not in answer:
                self.notes.append(str(answer["method"]))
                continue
            return answer

    def tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = self.call("tools/call", {"name": name, "arguments": arguments})[
            "result"
        ]
        return result

    def close(self) -> None:
        os.close(self._to_proxy)
        self.thread.join(30)
        os.close(self._from_proxy)
        assert self.error is None, self.error


@pytest.fixture
def client_file(tmp_path: Path) -> Path:
    return write_client_file(tmp_path)


@pytest.fixture
def protected(client_file: Path, tmp_path: Path) -> Iterator[tuple[Path, Path]]:
    servers = [s for s in read_servers(client_file, "claude-desktop") if s.name == "notes"]
    (step,) = protect.protect(servers, root=tmp_path / "protect", apply=True, runner=FIDUCIARY)
    assert step.deployment is not None
    yield step.deployment, client_file


def test_the_proxy_in_this_process_holds_pins_answers_writes_and_waits_for_turns(
    protected: tuple[Path, Path], tmp_path: Path
) -> None:
    root, _ = protected
    proxy = Pipes(root / "fiduciary.toml")
    try:
        meta = {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
        }
        found = proxy.call("server/discover", {"_meta": meta})["result"]
        assert found["resultType"] == "complete" and "2026-07-28" in found["supportedVersions"]
        listed = proxy.call("tools/list", {"_meta": meta})["result"]
        assert listed["ttlMs"] == 0 and listed["cacheScope"] == "private"
        made = proxy.tool("create_note", {"title": "in process", "priority": 0.5})
        assert made["structuredContent"] == {"id": "note-1"}
        assert proxy.tool("search_notes", {"query": "in"})["isError"] is False
        assert (
            "Held for the principals' approval"
            in proxy.tool("delete_note", {"id": "x"})["content"][0]["text"]
        )
        refused = proxy.tool("create_note", {"title": "no priority"})
        assert refused["isError"], "an argument the schema requires is missing"
        # a turn: the write waits, a refused call cancels it
        turns.open_turn(root)
        waiting = proxy.tool("create_note", {"title": "turned", "priority": 1})
        assert "made when this turn ends" in waiting["content"][0]["text"]
        proxy.tool("create_note", {"title": "no priority"})  # refused: the turn will cancel
        turns.request_close(root)
        proxy.call("ping")  # the proxy's idle turn closes it
        deadline = __import__("time").monotonic() + 15
        while turns.current(root) is not None and __import__("time").monotonic() < deadline:
            __import__("time").sleep(0.2)
            proxy.call("ping")
        assert turns.current(root) is None
        assert json.loads((tmp_path / "notes.json").read_text()) == [
            {"title": "in process", "priority": 0.5}
        ], "the cancelled turn's write was never made"
        # the server's description moves: withheld, and the client told
        (tmp_path / "phase").write_text("1")
        assert proxy.tool("search_notes", {"query": "x"})["isError"]
        proxy.call("ping")
        assert "notifications/tools/list_changed" in proxy.notes
        unknown = proxy.call(
            "tools/list",
            {"_meta": {**meta, "io.modelcontextprotocol/protocolVersion": "1999-01-01"}},
        )
        assert unknown["error"]["code"] == -32022
        assert "2026-07-28" in unknown["error"]["data"]["supported"]
        bare = proxy.call(
            "tools/list", {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}}
        )
        assert bare["error"]["code"] == -32602
    finally:
        proxy.close()


def test_the_commands_protect_review_accept_and_unprotect(
    client_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    monkeypatch.setattr(protect, "executable", lambda: FIDUCIARY)
    argv = ["protect", "--mcp-config", str(client_file), "--root", str(tmp_path / "protect")]
    assert cli.main(argv) == 0
    said = capsys.readouterr().out
    assert "would wrap  notes" in said and "skip        remote" in said and "--apply" in said
    assert cli.main([*argv, "--apply", "--claude-code"]) == 0
    said = capsys.readouterr().out
    assert "protected   notes" in said and "create_note: write" in said
    assert "Claude Code's hooks" in said and (tmp_path / ".claude" / "settings.local.json").exists()
    assert cli.main([*argv, "--review"]) == 0
    assert "every tool is as pinned" in capsys.readouterr().out
    (tmp_path / "phase").write_text("2")
    assert cli.main([*argv, "--review"]) == 1
    said = capsys.readouterr().out
    assert "changed     notes list_notes:" in said and "readOnlyHint" in said
    assert cli.main([*argv, "--review", "--accept"]) == 0
    said = capsys.readouterr().out
    assert "accepted    notes" in said
    assert cli.main([*argv, "--review"]) == 0
    capsys.readouterr()
    assert cli.main(["unprotect", "--mcp-config", str(client_file)]) == 0
    assert "unwrap      notes" in capsys.readouterr().out
    assert json.loads(client_file.read_text())["mcpServers"]["notes"]["command"] == sys.executable
    # nothing to name, and nothing found where the clients keep theirs
    monkeypatch.setattr(protect, "client_paths", lambda: {"cursor": tmp_path / "none.json"})
    assert cli.main(["protect"]) == 2
    assert "no client file found" in capsys.readouterr().err


def test_turn_commands_without_a_proxy(
    protected: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    root, _ = protected
    config = str(root / "fiduciary.toml")
    assert cli.main(["--config", config, "turn", "open"]) == 0
    assert "is open" in capsys.readouterr().out
    assert cli.main(["--config", config, "turn", "close"]) == 0
    assert "it held no request" in capsys.readouterr().out
    assert cli.main(["--config", config, "turn", "close"]) == 0
    assert "none is open" in capsys.readouterr().out


def test_passkey_add_on_the_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from fiduciary.init import init
    from fiduciary.webauthn import SoftPasskey
    from tests.test_passkeys import _browser

    root = tmp_path / "solo"
    init(root, None, solo=True, name="solo")
    passkey = SoftPasskey(seed=31)
    monkeypatch.setattr(webbrowser, "open", _browser(passkey, "http://localhost:8744"))
    config = str(root / "fiduciary.toml")
    assert (
        cli.main(["--config", config, "passkey", "add", "--origin", "http://localhost:8744"]) == 0
    )
    said = capsys.readouterr().out
    assert "for owner" in said and "adopted" in said and "retired" in said
    assert cli.main(["--config", config, "passkey", "add", "--principal", "mallory"]) == 1
    assert "no principal" in capsys.readouterr().err


def test_the_helpers_say_what_they_cannot_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(ProtectError, match="no such file"):
        read_servers(missing, "x")
    (tmp_path / "bad.json").write_text("{")
    with pytest.raises(ProtectError, match="not JSON"):
        read_servers(tmp_path / "bad.json", "x")
    (tmp_path / "list.json").write_text("[]")
    with pytest.raises(ProtectError, match="not a JSON object"):
        read_servers(tmp_path / "list.json", "x")
    (tmp_path / "vscode.json").write_text(
        json.dumps({"servers": {"a": {"type": "sse", "url": "x"}, "b": {"args": "no"}, "c": 1}})
    )
    found = read_servers(tmp_path / "vscode.json", "vscode")
    assert [(s.name, s.remote) for s in found] == [("a", True), ("b", False)]
    steps = protect.protect(found, root=tmp_path / "p")
    assert [(s.server.name, s.why) for s in steps] == [
        ("a", "a remote server: this version wraps stdio servers"),
        ("b", "no command to run"),
    ]
    assert Server("c", missing, "mcpServers", "s", {"args": ["--config"]}).deployment is None
    monkeypatch.setattr(sys, "platform", "linux")
    assert not Keychain.available()
    with pytest.raises(ProtectError, match="Keychain"):
        protect.protect(found, root=tmp_path / "p", move_secrets=True)
    for platform, name in (("linux", ".config"), ("win32", "Claude")):
        monkeypatch.setattr(sys, "platform", platform)
        assert name in str(protect.client_paths()["claude-desktop"])
    monkeypatch.undo()  # sys.platform is everyone's: put it back before anything else runs
    calls: list[list[str]] = []

    def security(argv: list[str], **_: Any) -> Any:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    chain = Keychain(security)
    chain.delete("a/b/C")
    assert calls[0][1] == "delete-generic-password"

    def fails(argv: list[str], **_: Any) -> Any:
        raise subprocess.CalledProcessError(44, argv)

    Keychain(fails).delete("gone")  # deleting what is not there is not an error
    assert Path(protect.executable()[0]).is_absolute(), "never a name resolved at start"


def test_a_registry_is_asked_for_the_version_published_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout='"4.2.0"'),
    )
    assert protect.resolve("npm", "some-server") == "4.2.0"

    class Answer:
        def __enter__(self) -> Answer:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"info": {"version": "0.9.1"}}).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Answer())
    assert protect.resolve("pypi", "mcp-server-fetch") == "0.9.1"
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("offline"))
    )
    with pytest.raises(ProtectError, match="cannot ask pypi"):
        protect.resolve("pypi", "x")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout='"bad version!"'),
    )
    with pytest.raises(ProtectError, match="no version"):
        protect.resolve("npm", "x")
    assert protect.pin_command(["npx", "-p", "pkg", "-y"], lambda e, p: "1") == (
        ["npx", "-p", "pkg", "-y"],
        None,
    )
    assert protect.pin_command(["uvx", "pkg[extra]"], lambda e, p: "2.0")[0] == [
        "uvx",
        "pkg[extra]==2.0",
    ]
    assert protect.pin_command([], lambda e, p: "1") == ([], None)


_MODERN = r"""
import json, sys
for line in sys.stdin:
    m = json.loads(line)
    if "id" not in m:
        continue
    meta = m.get("params", {}).get("_meta", {})
    if m["method"] == "server/discover":
        r = {"resultType": "complete", "supportedVersions": ["2026-07-28"],
             "capabilities": {"tools": {}}}
    elif m["method"] == "tools/list":
        if "io.modelcontextprotocol/protocolVersion" not in meta:
            e = {"code": -32602, "message": "no _meta"}
            print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "error": e}), flush=True)
            continue
        cursor = m["params"].get("cursor")
        r = {"resultType": "complete", "tools": [{"name": "a" if not cursor else "b"}]}
        if not cursor:
            r["nextCursor"] = "2"
    elif m["params"]["name"] == "refuse":
        e = {"code": -32000, "message": "no", "data": {"x": 1}}
        print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "error": e}), flush=True)
        continue
    else:
        r = {"resultType": "complete", "content": [{"type": "text", "text": "ok"}]}
    print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}), flush=True)
"""


def test_the_upstream_client_speaks_to_a_modern_server(tmp_path: Path) -> None:
    from interlock.mcp import McpRefusedError

    script = tmp_path / "modern.py"
    script.write_text(_MODERN)
    upstream = Upstream([sys.executable, str(script)], timeout=10)
    try:
        assert upstream.era.modern and upstream.era.version == "2026-07-28"
        assert upstream.tools() == ["a", "b"], "every page"
        assert upstream.call("hello", {}, 5)["content"][0]["text"] == "ok"
        with pytest.raises(McpRefusedError) as refused:
            upstream.call("refuse", {}, 5)
        assert refused.value.code == -32000 and refused.value.data == {"x": 1}
        with pytest.raises(McpError, match="refused"):
            upstream.request("tools/call", {"name": "refuse", "arguments": {}})
    finally:
        upstream.close()


def test_a_legacy_client_after_initialize_and_unknown_methods(protected: tuple[Path, Path]) -> None:
    from fiduciary.bridge import Fiduciary

    root, _ = protected
    fid = Fiduciary.from_config(root / "fiduciary.toml", bridged=True)
    try:
        scope = json.loads((root / "mandate.json").read_text())["subject"]["scope"]
        server = McpServer(fid.session(scope, bridged=True))
        hello = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            }
        )
        assert hello is not None and hello["result"]["protocolVersion"] == "2024-11-05"
        assert server.drain() == []
        missing = server.handle(
            {"jsonrpc": "2.0", "id": 2, "method": "resources/list", "params": {}}
        )
        assert missing is not None and missing["error"]["code"] == -32601
    finally:
        fid.close()


def test_a_one_shot_page_refuses_another_host_and_another_origin() -> None:
    import http.client

    answered: dict[str, Any] = {}

    def opener(url: str) -> None:
        port = int(url.rsplit(":", 1)[1].strip("/"))

        def poke() -> None:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/", headers={"Host": f"127.0.0.1:{port}"})
            answered["host"] = conn.getresponse().status
            conn.close()
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "POST",
                "/",
                body=b"{}",
                headers={
                    "Host": f"localhost:{port}",
                    "Origin": "http://evil.example",
                    "Content-Length": "2",
                },
            )
            answered["origin"] = conn.getresponse().status
            conn.close()
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "POST",
                "/",
                body=b"[]",
                headers={
                    "Host": f"localhost:{port}",
                    "Origin": f"http://localhost:{port}",
                    "Content-Length": "2",
                },
            )
            answered["shape"] = conn.getresponse().status
            conn.close()
            req = urllib.request.Request(
                f"http://localhost:{port}/",
                data=json.dumps({"error": "NotAllowedError: the user said no"}).encode(),
                headers={"Content-Type": "application/json", "Origin": f"http://localhost:{port}"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5).read()  # noqa: S310

        threading.Thread(target=poke, daemon=True).start()

    with pytest.raises(passkeys.PasskeyError, match="the user said no"):
        passkeys._one_shot(
            0,
            {"mode": "get", "principal": "p", "challenge": "AA", "rp_id": "localhost"},
            opener,
            timedelta(seconds=20),
        )
    assert answered == {"host": 403, "origin": 403, "shape": 400}
    with pytest.raises(passkeys.PasskeyError, match="http://localhost:<port>"):
        passkeys.create("p", "https://example.com")

"""One owner for every protected server, and accepting a tool change in place (1.17.0).

A desktop client runs several MCP servers, and their packages move. So:

- every server ``fiduciary protect`` wraps under one root shares one owner: one key file,
  until one passkey replaces it on all of them, with one touch (a batch: the passkey signs
  the root of a Merkle tree over every charter's signing input, and each charter carries
  its path); a server protected after that starts with the passkey, also with one touch;
- ``--review --accept`` keeps the deployment: the charter, its passkey, the log and every
  record stay, and the mandate's next version pins the tools as the server lists them now,
  signed by the owner's passkey: one touch to sign every server's version, one to approve;
- a proxy that is running steps aside while a command changes its deployment, answers
  "call again in a moment" meanwhile, and comes back with the change.

Every bundle exported after verifies offline, in Python and in the browser's checks alike.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from fiduciary import cli, protect
from fiduciary.bridge import Fiduciary
from fiduciary.documents import Charter, ToolBinding
from fiduciary.protect import Owner, ProtectError, read_servers
from fiduciary.webauthn import Assertion, PasskeyKey, Policy, SoftPasskey
from tests.test_passkeys import _browser
from tests.test_protect import STUB
from tests.test_protect_inprocess import Pipes

FIDUCIARY = [sys.executable, "-m", "fiduciary"]
ORIGIN = "http://localhost:8746"
POLICY = Policy("localhost", (ORIGIN,), "Y3JlZGVudGlhbC1pZC0x")


class Touches:
    """A software passkey that counts its touches: each assertion is one."""

    alg = "webauthn-es256"

    def __init__(self, passkey: SoftPasskey) -> None:
        self._signer = passkey.signer(POLICY, ORIGIN)
        self.key_id = self._signer.key_id
        self.count = 0

    def public_key(self) -> PasskeyKey:
        return self._signer.public_key()

    def assertion(self, message: bytes) -> Assertion:
        self.count += 1
        return self._signer.assertion(message)


def _client_file(tmp_path: Path, *names: str) -> Path:
    """A Claude Desktop file with one stand-in server per name. ``notes`` and ``tasks``
    turn together (one phase file); any other name turns alone."""
    stub = tmp_path / "server.py"
    stub.write_text(STUB)
    servers = {
        name: {
            "command": sys.executable,
            "args": [str(stub)],
            "env": {
                "NOTES_FILE": str(tmp_path / f"{name}.json"),
                "PHASE_FILE": str(
                    tmp_path / ("phase" if name in ("notes", "tasks") else f"{name}.phase")
                ),
            },
        }
        for name in names
    }
    config = tmp_path / "claude_desktop_config.json"
    config.write_text(json.dumps({"mcpServers": servers}))
    return config


def _protect(client: Path, root: Path, *names: str, passkey: Any = None) -> list[Path]:
    servers = [s for s in read_servers(client, "claude-desktop") if s.name in names]
    steps = protect.protect(servers, root=root, apply=True, runner=FIDUCIARY, passkey=passkey)
    return [s.deployment.resolve() for s in steps if s.deployment is not None]


def _charter(root: Path) -> Charter:
    fid = Fiduciary.from_config(root / "fiduciary.toml")
    try:
        return fid._authority.decide.charter(fid.log.state())
    finally:
        fid.close()


def _head(root: Path) -> tuple[int, int, str]:
    """The log's head, the mandate version in force, and the charter's digest."""
    fid = Fiduciary.from_config(root / "fiduciary.toml")
    try:
        state = fid.log.state()
        mandate = json.loads((root / "mandate.json").read_text())["mandate_id"]
        head = fid._authority.decide.head_mandate(state, fid._lineage(state, mandate))
        return state.head_seq, head.version, fid._authority.decide.charter(state).digest
    finally:
        fid.close()


def _bundle_verifies(root: Path, out: Path) -> None:
    """The deployment's bundle verifies offline, against its pinned charter and keys, in
    Python and (with Node) in the browser's checks, passkey signatures among them."""
    mandate = json.loads((root / "mandate.json").read_text())["mandate_id"]
    bundle = out / f"{root.name}.fidb"
    argv = ["--config", str(root / "fiduciary.toml"), "export-bundle", "--mandate", mandate]
    ledger_key = str(root / "keys" / "ledger-checkpoints.key")
    assert cli.main([*argv, "--ledger-key", ledger_key, "--out", str(bundle)]) == 0
    charter = (root / "charter.digest").read_text().strip()
    keys = str(root / "pinned-keys.json")
    assert cli.main(["verify-bundle", str(bundle), "--charter", charter, "--keys", keys]) == 0
    node = shutil.which("node")
    if node is None:
        return
    harness = Path(__file__).resolve().parent / "js" / "verify_vectors.cjs"
    listing = out / f"{root.name}.json"
    listing.write_text(json.dumps([{"name": root.name, "file": bundle.name, "keys": None}]))
    done = subprocess.run(  # noqa: S603
        [node, str(harness), str(listing)], capture_output=True, text=True, check=False, timeout=120
    )
    assert done.returncode == 0, done.stderr
    found = json.loads(done.stdout)[root.name]
    assert found["checks"].get("passkeys") == "pass", found["failing"]


def test_every_protected_server_shares_one_owner_and_one_touch_registers_its_passkey(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    client = _client_file(tmp_path, "notes", "tasks", "wiki")
    root = tmp_path / "protect"
    roots = _protect(client, root, "notes", "tasks")
    owner = Owner.under(root)
    assert owner.deployments() == sorted(roots)
    assert owner.key_file.is_file() and owner.passkey() is None
    for each in roots:
        # one owner: no key of its own in the deployment, the shared one in its charter
        assert not (each / "keys" / "owner.key").exists()
        assert _charter(each).principal("owner").key == owner.key()  # type: ignore[union-attr]
        assert Owner.serving(each) == owner

    passkey = SoftPasskey(seed=41)
    touches = Touches(passkey)
    done = protect.register_owner(owner, passkey.point, POLICY, passkey=touches)
    assert touches.count == 1, "one touch registers the passkey on every server"
    assert set(done.registered) == set(roots) and not done.skipped
    assert owner.passkey() is not None and owner.passkey().spec() == passkey.spec  # type: ignore[union-attr]
    assert not owner.key_file.exists() and (owner.path / "retired" / "owner.key").is_file()
    for each in roots:
        charter = _charter(each)
        assert charter.version == 2 and charter.principal("owner").key == passkey.spec  # type: ignore[union-attr]
        assert charter.signature is not None and len(charter.signature.batch) == 1

    # A server protected now starts with the passkey: one touch, its own key retired.
    later = Touches(passkey)
    (wiki,) = _protect(client, root, "wiki", passkey=later)
    assert later.count == 1
    assert _charter(wiki).principal("owner").key == passkey.spec  # type: ignore[union-attr]
    assert not (wiki / "keys" / "owner.key").exists()
    assert (wiki / "keys" / "retired" / "owner.key").is_file()
    assert owner.deployments() == sorted([*roots, wiki])

    # Registering it again is refused in words; the old key file signs nothing now.
    with pytest.raises(ProtectError, match="no server took the passkey"):
        protect.register_owner(owner, passkey.point, POLICY, passkey=Touches(passkey))
    with pytest.raises(ProtectError, match="has no key file"):
        owner.signer()
    for each in [*roots, wiki]:
        _bundle_verifies(each, tmp_path)


def test_passkey_add_once_on_the_command_line_for_every_protected_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = _client_file(tmp_path, "notes", "tasks")
    root = tmp_path / "protect"
    roots = _protect(client, root, "notes", "tasks")
    passkey = SoftPasskey(seed=43)
    monkeypatch.setattr(webbrowser, "open", _browser(passkey, "http://localhost:8747"))
    argv = ["passkey", "add", "--origin", "http://localhost:8747"]
    # through one server's configuration: the owner is every server's
    config = str(roots[0] / "fiduciary.toml")
    assert cli.main(["--config", config, *argv]) == 0
    said = capsys.readouterr().out
    assert said.count("adopted") == 2 and "for every server protected here" in said
    assert "moved to" in said
    for each in roots:
        assert _charter(each).principal("owner").key == passkey.spec  # type: ignore[union-attr]
    # --protected, with no server under the root, says so
    assert cli.main([*argv, "--protected", "--root", str(tmp_path / "empty")]) == 1
    assert "holds no protected server" in capsys.readouterr().err


def test_accepting_a_change_keeps_the_charter_the_passkey_and_the_log(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    client = _client_file(tmp_path, "notes", "tasks")
    root = tmp_path / "protect"
    roots = _protect(client, root, "notes", "tasks")
    passkey = SoftPasskey(seed=47)
    protect.register_owner(Owner.under(root), passkey.point, POLICY, passkey=Touches(passkey))
    before = {each: _head(each) for each in roots}
    # a read before the change, so the log cites the binding the change retires
    proxy = Pipes(roots[0] / "fiduciary.toml")
    try:
        assert not proxy.tool("list_notes", {})["isError"]
    finally:
        proxy.close()

    (tmp_path / "phase").write_text("2")  # a description grows; list_notes now writes
    for each in roots:
        changed = {d.tool for d in protect.review(each)}
        assert changed == {"search_notes", "list_notes"}

    touches = Touches(passkey)
    accepted = protect.accept(roots, passkey=touches)
    assert touches.count == 2, "one touch signs every server's version, one approves them"
    for each in roots:
        seq, version, charter = _head(each)
        assert accepted[each].version == version == before[each][1] + 1
        assert set(accepted[each].changed) == {"list_notes", "search_notes"}
        assert charter == before[each][2], "the charter, and the passkey in it, are kept"
        assert seq > before[each][0], "the log goes on: nothing was rebuilt"
        assert protect.review(each) == []
        retired = sorted(p.name.split(".")[0] for p in (each / "tools" / "retired").iterdir())
        assert retired == ["list_notes", "search_notes"]
        listed = json.loads(
            next(
                line.split(" = ", 1)[1]
                for line in (each / "fiduciary.toml").read_text().splitlines()
                if line.startswith("tools = ")
            )
        )
        assert len(listed) == 6  # the four bindings, and the two retired ones
        binding = ToolBinding.loads((each / "tools" / "list_notes.tool.json").read_bytes())
        assert binding.kind == "plan"
        assert not (each / "drift.json").exists()
    assert sorted(p.name for p in roots[0].parent.iterdir()) == sorted(r.name for r in roots)

    # The proxy serves the change: list_notes is a write now, made once by the relay.
    proxy = Pipes(roots[0] / "fiduciary.toml")
    try:
        names = {t["name"] for t in proxy.call("tools/list")["result"]["tools"]}
        assert {"list_notes", "search_notes"} <= names
        answer = proxy.tool("list_notes", {})
        assert not answer["isError"], answer
    finally:
        proxy.close()
    _bundle_verifies(roots[0], tmp_path)


def test_a_running_proxy_steps_aside_and_comes_back_with_the_change(tmp_path: Path) -> None:
    client = _client_file(tmp_path, "notes")
    root = tmp_path / "protect"
    (deployment,) = _protect(client, root, "notes")
    proxy = Pipes(deployment / "fiduciary.toml")
    try:
        assert "list_notes" in {t["name"] for t in proxy.call("tools/list")["result"]["tools"]}
        (tmp_path / "phase").write_text("2")
        listed = {t["name"] for t in proxy.call("tools/list")["result"]["tools"]}
        assert "list_notes" not in listed and "search_notes" not in listed  # withheld

        # Asked by hand: the proxy lets go on its next turn and answers in words meanwhile.
        ask = deployment / protect.ASIDE
        ask.write_text(json.dumps({"pid": os.getpid(), "asked_at": datetime.now(UTC).isoformat()}))
        _until(lambda: protect._free(deployment))
        answer = proxy.tool("create_note", {"title": "x", "priority": 1})
        assert answer["isError"] and "Call it again in a moment" in answer["content"][0]["text"]
        assert {t["name"] for t in proxy.call("tools/list")["result"]["tools"]} == listed
        ask.unlink()
        _until(lambda: not protect._free(deployment))

        # The command asks for itself: accepted while the proxy runs, without a restart.
        accepted = protect.accept([deployment])
        assert accepted[deployment].version == 2
        assert not ask.exists()
        _until(lambda: not protect._free(deployment))
        listed = {t["name"] for t in proxy.call("tools/list")["result"]["tools"]}
        assert {"list_notes", "search_notes"} <= listed
        assert "notifications/tools/list_changed" in proxy.notes
        assert not proxy.tool("list_notes", {})["isError"]
    finally:
        proxy.close()


def test_an_ask_whose_command_is_gone_is_dropped(tmp_path: Path) -> None:
    (tmp_path / protect.STATE).write_text("{}")
    ask = tmp_path / protect.ASIDE
    ask.write_text(json.dumps({"pid": 2**22 + 7, "asked_at": datetime.now(UTC).isoformat()}))
    assert not protect.asked_aside(tmp_path) and not ask.exists()
    ask.write_text(json.dumps({"pid": os.getpid(), "asked_at": "2020-01-01T00:00:00+00:00"}))
    assert not protect.asked_aside(tmp_path) and not ask.exists()
    ask.write_text("not json")
    assert not protect.asked_aside(tmp_path) and not ask.exists()
    ask.write_text(json.dumps({"pid": os.getpid(), "asked_at": datetime.now(UTC).isoformat()}))
    assert protect.asked_aside(tmp_path)


def test_a_proxy_that_will_not_step_aside_is_named(tmp_path: Path) -> None:
    client = _client_file(tmp_path, "notes")
    (deployment,) = _protect(client, tmp_path / "protect", "notes")
    fid = Fiduciary.from_config(deployment / "fiduciary.toml")  # holds it, and never lets go
    try:
        with (
            pytest.raises(ProtectError, match="did not step aside"),
            protect.held([deployment], wait=0.3),
        ):
            pass  # pragma: no cover - never reached
        assert not (deployment / protect.ASIDE).exists()
    finally:
        fid.close()


def _until(condition: Any, seconds: float = 30.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "the proxy did not answer the ask in time"
        time.sleep(0.05)


def test_the_console_registers_the_owners_passkey_on_every_server_with_one_touch(
    tmp_path: Path,
) -> None:
    from fiduciary.control import ConfiguredDeployment, ControlError

    client = _client_file(tmp_path, "notes", "tasks")
    roots = _protect(client, tmp_path / "protect", "notes", "tasks")
    desk = ConfiguredDeployment("d", roots[1] / "fiduciary.toml", scratch=tmp_path, accepts=True)
    passkey = SoftPasskey(seed=53)
    proxy = Pipes(roots[0] / "fiduciary.toml")  # one server's client is running
    try:
        draft = desk.passkey_draft(
            {
                "principal": "owner",
                "point": passkey.point.hex(),
                "credential": "Y3JlZGVudGlhbC1pZC0x",
                "origin": ORIGIN,
            }
        )
        assert len(draft["charters"]) == 2
        made = passkey.assert_(bytes.fromhex(draft["signing_input"]), ORIGIN)
        assertion = {
            "authenticator_data": made.authenticator_data.hex(),
            "client_data_json": made.client_data_json.hex(),
            "signature": made.signature.hex(),
        }
        sent = {"principal": "owner", "key_id": draft["key_id"], "assertion": assertion}
        with pytest.raises(ControlError, match="names a server of this owner"):
            desk.passkey_register(
                {**sent, "charters": [{"config": str(tmp_path / "x.toml"), "charter": {}}]}
            )
        done = desk.passkey_register(
            {**sent, "charter": draft["charter"], "charters": draft["charters"]}
        )
        assert done["servers"] == 2 and done["adopted"] and done["retired"]
        _until(lambda: not protect._free(roots[0]))  # the proxy came back
        assert not proxy.tool("search_notes", {"query": "x"})["isError"]
    finally:
        proxy.close()
        desk.close()
    for each in roots:
        assert _charter(each).principal("owner").key == passkey.spec  # type: ignore[union-attr]
    assert Owner.under(tmp_path / "protect").passkey() is not None


def test_what_the_owner_commands_refuse_and_leave_behind(tmp_path: Path) -> None:
    from fiduciary import solo

    client = _client_file(tmp_path, "notes", "tasks", "wiki")
    root = tmp_path / "protect"
    roots = _protect(client, root, "notes", "tasks")
    owner = Owner.under(root)
    passkey, stranger = SoftPasskey(seed=61), SoftPasskey(seed=62)
    with pytest.raises(ProtectError, match="not the one they name"):
        protect.register_owner(owner, passkey.point, POLICY, passkey=Touches(stranger))
    with pytest.raises(ProtectError, match="holds no protected server"):
        protect.register_owner(Owner.under(tmp_path / "none"), passkey.point, POLICY)
    # one server grows to three principals: it is no longer this owner's alone
    fid = Fiduciary.from_config(roots[1] / "fiduciary.toml")
    try:
        solo.add_principals(fid, roots[1], ["alice", "bob"])
    finally:
        fid.close()
    done = protect.register_owner(owner, passkey.point, POLICY, passkey=Touches(passkey))
    assert set(done.registered) == {roots[0]}
    assert done.skipped == {roots[1]: "its charter is not one owner's alone"}

    # a new server the passkey cannot sign for is not left half-protected
    before = client.read_text()
    with pytest.raises(Exception, match="not by the key pinned for its signer"):
        _protect(client, root, "wiki", passkey=Touches(stranger))
    assert not (root / "claude-desktop" / "wiki").exists() and client.read_text() == before


def test_a_proxy_started_while_asked_waits_and_a_dropped_tool_is_retired(tmp_path: Path) -> None:
    client = _client_file(tmp_path, "notes")
    (deployment,) = _protect(client, tmp_path / "protect", "notes")
    ask = deployment / protect.ASIDE
    ask.write_text(json.dumps({"pid": os.getpid(), "asked_at": datetime.now(UTC).isoformat()}))
    proxy = Pipes(deployment / "fiduciary.toml")
    try:
        answer = proxy.tool("search_notes", {"query": "x"})
        assert answer["isError"] and "Call it again in a moment" in answer["content"][0]["text"]
        assert proxy.call("tools/list")["result"]["tools"] == []
        ask.unlink()
        _until(lambda: not protect._free(deployment))
        assert not proxy.tool("search_notes", {"query": "x"})["isError"]
        bad = proxy.call("tools/call", {"name": 5, "arguments": {}})
        assert "error" in bad
    finally:
        proxy.close()

    def listing(command: Any, env: Any) -> list[dict[str, Any]]:
        """The stand-in's tools, less delete_note."""
        found = protect._discover(command, env)
        return [t for t in found if t["name"] != "delete_note"]

    accepted = protect.accept([deployment], discover=listing)
    assert "delete_note" in accepted[deployment].changed
    assert not (deployment / "tools" / "delete_note.tool.json").exists()
    assert any(
        p.name.startswith("delete_note.") for p in (deployment / "tools" / "retired").iterdir()
    )
    assert protect._alive(os.getpid()) and not protect._alive(2**22 + 11)
    if os.getuid() != 0:
        assert protect._alive(1)  # someone else's process: alive, though not ours to signal

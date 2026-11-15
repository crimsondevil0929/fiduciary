"""Approvals in the client's own flow (1.20.0): MCP 2026-07-28 multi round-trip requests,
tested with the official Python SDK's client (``mcp`` 2.x, in the dev group) against a
protected server.

A write the mandate holds is answered ``input_required``: a URL-mode elicitation of the
console's page for its ticket, and a request state. The SDK's client asks its elicitation
callback, which here approves the ticket as the owner would in the console, and retries; the
retry gets the server's own answer. A client that declines is told the call is still held. A
client that does not declare URL elicitation is told in words, as before.
"""

from __future__ import annotations

import asyncio
import json
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Literal

import pytest

from fiduciary import protect
from fiduciary.approvals import Approvals, url_capable
from fiduciary.protect import read_servers
from tests.test_protect import Client, write_client_file

mcp = pytest.importorskip("mcp")


def _protected(tmp_path: Path) -> tuple[dict[str, Any], Path]:
    config = write_client_file(tmp_path)
    (step, _) = protect.protect(
        read_servers(config, "claude-desktop"),
        root=tmp_path / "protect",
        apply=True,
        shadow=False,
        runner=[sys.executable, "-m", "fiduciary"],
    )
    assert step.deployment is not None
    return json.loads(config.read_text())["mcpServers"]["notes"], step.deployment


def _approve(deployment: Path, ticket_id: str, decision: str = "approve") -> None:
    """What the owner does in the console: a draft of the ticket's release, signed with the
    owner's key, into the deployment's intake."""
    from fiduciary.control import ConfiguredDeployment
    from fiduciary.documents import Approval
    from fiduciary.keys import load_key

    desk = ConfiguredDeployment(
        "notes", deployment / "fiduciary.toml", accepts=True, scratch=deployment.parent
    )
    try:
        draft = desk.draft(
            {
                "ticket_id": ticket_id,
                "principal": "owner",
                "option": "release",
                "decision": decision,
            }
        )
        owner = protect.Owner.serving(deployment)
        assert owner is not None
        signed = Approval.from_json(draft["approval"], signed=False).signed(
            load_key(owner.key_file)
        )
        desk.accept({"approval": signed.to_json()})
    finally:
        desk.close()


def _run(
    entry: dict[str, Any], deployment: Path, answer: Literal["accept", "decline"]
) -> tuple[Any, list[str]]:
    from mcp import Client as SdkClient
    from mcp import types
    from mcp.client.stdio import StdioServerParameters

    seen: list[str] = []

    async def elicit(context: Any, params: Any) -> Any:
        assert params.mode == "url"
        seen.append(params.url)
        query = urllib.parse.parse_qs(params.url.split("#/desk?", 1)[1])
        if answer == "accept":
            _approve(deployment, query["t"][0])
        return types.ElicitResult(action=answer)

    async def main() -> Any:
        server = StdioServerParameters(command=entry["command"], args=entry["args"])
        async with SdkClient(server, elicitation_callback=elicit) as client:
            made = await client.call_tool("create_note", {"title": "kept", "priority": 2})
            assert not made.is_error
            return await client.call_tool("delete_note", {"id": "note-1"})

    return asyncio.run(main()), seen


def test_the_owner_approves_in_the_clients_own_flow(tmp_path: Path) -> None:
    entry, deployment = _protected(tmp_path)
    result, seen = _run(entry, deployment, "accept")
    assert len(seen) == 1 and seen[0].startswith("http://localhost:8740/#/desk?d=notes&t=")
    assert not result.is_error
    assert [c.text for c in result.content] == ["deleted"], "the server's own answer comes back"


def test_a_declined_elicitation_leaves_the_call_held(tmp_path: Path) -> None:
    entry, deployment = _protected(tmp_path)
    result, seen = _run(entry, deployment, "decline")
    assert len(seen) == 1
    assert "Still held for the owner's approval: nothing was sent" in result.content[0].text


def test_a_client_that_does_not_ask_is_told_in_words(tmp_path: Path) -> None:
    entry, _ = _protected(tmp_path)
    client = Client(entry)
    try:
        held = client.tool("delete_note", {"id": "note-1"})
        assert "Held for the principals' approval" in held["content"][0]["text"]
    finally:
        client.close()


def test_a_request_state_is_this_deployments_and_this_calls(tmp_path: Path) -> None:
    approvals = Approvals(tmp_path)
    assert Approvals(tmp_path)._key == approvals._key, "the key outlives the proxy"
    state = approvals._state("plan-1", "delete_note", {"id": "n"})
    assert approvals._plan_of(state, "delete_note", {"id": "n"}) == "plan-1"
    assert approvals._plan_of(state, "delete_note", {"id": "other"}) is None
    assert approvals._plan_of(state + "0", "delete_note", {"id": "n"}) is None
    assert approvals._plan_of("not.a.state", "delete_note", {"id": "n"}) is None
    assert approvals._plan_of(None, "delete_note", {"id": "n"}) is None
    other = tmp_path / "other"
    other.mkdir()
    assert Approvals(other)._plan_of(state, "delete_note", {"id": "n"}) is None, (
        "another deployment's key does not open it"
    )
    capable: dict[str, Any] = {
        "_meta": {"io.modelcontextprotocol/clientCapabilities": {"elicitation": {"url": {}}}}
    }
    assert url_capable(capable)
    assert not url_capable(
        {"_meta": {"io.modelcontextprotocol/clientCapabilities": {"elicitation": {}}}}
    )
    assert not url_capable({})
    words = approvals.resume(
        None,  # type: ignore[arg-type]
        None,
        {"requestState": "forged.0"},
        "delete_note",
        {"id": "n"},
        serve=lambda: None,
    )
    assert words["isError"] and "did not give for this call" in words["content"][0]["text"]


def _modern(name: str, arguments: dict[str, Any], **extra: Any) -> dict[str, Any]:
    meta = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {"elicitation": {"url": {}}},
    }
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments, "_meta": meta, **extra},
    }


def test_the_round_trips_in_this_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The proxy's server answered in this process: asked, approved, waited on, cancelled."""
    import fiduciary.approvals as approvals

    monkeypatch.setattr(approvals, "WAIT", 0.5)
    _, deployment = _protected(tmp_path)
    state = json.loads((deployment / protect.STATE).read_text())
    upstream = protect.upstream_of(state)
    proxy, server, gate = protect.protected_server(
        deployment / "fiduciary.toml", upstream, console="http://localhost:9999/"
    )
    try:

        def ask(message: dict[str, Any]) -> dict[str, Any]:
            with gate:
                answer = server.handle(message)
                proxy.tick()
            assert answer is not None
            result: dict[str, Any] = answer["result"]
            return result

        first = ask(_modern("delete_note", {"id": "note-1"}))
        assert first["resultType"] == "input_required"
        url = first["inputRequests"]["approval"]["params"]["url"]
        assert url.startswith("http://localhost:9999/#/desk?d=notes&t=")
        ticket = urllib.parse.parse_qs(url.split("?", 1)[1])["t"][0]
        state_token = first["requestState"]
        again = ask(_modern("delete_note", {"id": "note-1"}, requestState=state_token))
        assert again["resultType"] == "input_required", "still held: asked again"
        _approve(deployment, ticket)
        done = ask(
            _modern(
                "delete_note",
                {"id": "note-1"},
                requestState=state_token,
                inputResponses={"approval": {"action": "accept"}},
            )
        )
        assert done["resultType"] == "complete" and done["content"][0]["text"] == "deleted"
        after = ask(_modern("delete_note", {"id": "note-1"}, requestState=state_token))
        assert "Made, after its owner approved it" in after["content"][0]["text"]

        second = ask(_modern("delete_note", {"id": "note-2"}))
        cancel = urllib.parse.parse_qs(
            second["inputRequests"]["approval"]["params"]["url"].split("?", 1)[1]
        )["t"][0]
        _cancel(deployment, cancel)
        refused = ask(_modern("delete_note", {"id": "note-2"}, requestState=second["requestState"]))
        assert refused["isError"] and "Nothing was sent" in refused["content"][0]["text"]
        plain = ask(_modern("list_notes", {}))
        assert plain["resultType"] == "complete"
    finally:
        proxy.close()
        upstream.close()


def _cancel(deployment: Path, ticket_id: str) -> None:
    from fiduciary.control import ConfiguredDeployment
    from fiduciary.documents import Approval
    from fiduciary.keys import load_key

    desk = ConfiguredDeployment(
        "notes", deployment / "fiduciary.toml", accepts=True, scratch=deployment.parent
    )
    try:
        draft = desk.draft({"ticket_id": ticket_id, "principal": "owner", "option": "cancel"})
        owner = protect.Owner.serving(deployment)
        assert owner is not None
        signed = Approval.from_json(draft["approval"], signed=False).signed(
            load_key(owner.key_file)
        )
        desk.accept({"approval": signed.to_json()})
    finally:
        desk.close()


def test_a_proxy_with_no_relay_says_so() -> None:
    from fiduciary.approvals import _made

    found = _made(None, [])
    assert found is not None and "no relay" in found["content"][0]["text"]

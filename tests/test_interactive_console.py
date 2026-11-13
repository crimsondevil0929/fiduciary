"""The control plane (1.7.0): ``fiduciary console``, its boundary, the approvals
desk, votes signed by the principal, the bundle verifier and the sandbox."""

from __future__ import annotations

import dataclasses
import hashlib
import http.client
import json
import threading
import urllib.parse
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from fiduciary import cli, demo, fidu1, report
from fiduciary.control import (
    ConfiguredDeployment,
    Console,
    ControlError,
    _payloads,
    _sqlite_copy,
    discover,
    receipt_view,
    serve,
)
from fiduciary.demo.sandbox import PRESETS, Sandbox, SandboxDeployment
from fiduciary.keys import load_key
from fiduciary.log import LOG_ID

VECTORS = Path(__file__).resolve().parent.parent / "vectors" / "fidu1" / "bundles"


class _Client:
    """Raw HTTP to a console on loopback: every header is the test's to set."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.host = f"127.0.0.1:{port}"

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        try:
            sent = {"Host": self.host, **(headers or {})}
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for name, value in sent.items():
                conn.putheader(name, value)
            if body is not None and "Content-Length" not in sent:
                conn.putheader("Content-Length", str(len(body)))
            conn.endheaders(body)
            resp = conn.getresponse()
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
        finally:
            conn.close()

    def get(self, path: str, **headers: str) -> tuple[int, Any]:
        status, _, data = self.request("GET", path, headers=headers)
        return status, json.loads(data)

    def post(self, path: str, payload: Any, **headers: str) -> tuple[int, Any]:
        base = {"Content-Type": "application/json", "X-Fiduciary-Console": "1"}
        status, _, data = self.request(
            "POST", path, json.dumps(payload).encode(), {**base, **headers}
        )
        return status, json.loads(data)


def _hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("recorded")
    assert demo.run(root, domain="fintech").held
    return root


@pytest.fixture(scope="module")
def console(run_dir: Path) -> Iterator[tuple[_Client, Console]]:
    """The console over a recorded run, with the sandbox, as ``demo --serve``."""
    before = _hashes(run_dir)
    server = serve(Console.over(run_dir, recorded=True, sandbox=Sandbox()), "127.0.0.1:0")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield _Client(server.server_address[1]), server.console
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    # Nothing the console did, the sandbox and its votes included, touched the run.
    assert _hashes(run_dir) == before


@pytest.fixture
def client(console: tuple[_Client, Console]) -> _Client:
    return console[0]


def _held(scratch: Path) -> tuple[Path, str]:
    built = SandboxDeployment("fintech", scratch)
    ticket = built.call({"preset": "held"})["ticket"]
    root = built.built().world.root
    built.close()
    assert ticket
    return root, ticket


@pytest.fixture(scope="module")
def deployment(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    """A deployment with an open held ticket, left on disk with its config and keys."""
    return _held(tmp_path_factory.mktemp("deployment"))


def _vote(root: Path, draft: dict[str, Any], principal: str) -> str:
    """What a browser does: sign the console's signing input with the key file."""
    signer = load_key(root / "keys" / f"{principal}.key")
    return signer.sign(bytes.fromhex(draft["signing_input"])).hex()


# -- the boundary -------------------------------------------------------------------------


def test_listens_on_loopback_only(run_dir: Path) -> None:
    with pytest.raises(ValueError, match="loopback only"):
        serve(Console(run_dir), "0.0.0.0:0")
    with pytest.raises(ValueError, match="HOST:PORT"):
        serve(Console(run_dir), "127.0.0.1:http")


def test_the_boundary_refuses_what_another_site_could_send(client: _Client) -> None:
    write = {"Content-Type": "application/json", "X-Fiduciary-Console": "1"}
    body = b"{}"
    cases = [
        ("GET", "/api/status", None, {"Host": "evil.example:80"}, 403),  # DNS rebinding
        ("GET", "/api/status", None, {"Sec-Fetch-Site": "cross-site"}, 403),
        ("GET", "/", None, {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"}, 200),
        (
            "GET",
            "/after.fidb",
            None,
            {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors"},
            403,
        ),
        (
            "POST",
            "/api/sandbox/fintech/open",
            body,
            {**write, "Origin": "http://evil.example"},
            403,
        ),
        ("POST", "/api/sandbox/fintech/open", body, {"Content-Type": "application/json"}, 403),
        ("POST", "/api/sandbox/fintech/open", body, {**write, "Content-Type": "text/plain"}, 415),
        ("POST", "/api/sandbox/fintech/open", body, {**write, "Content-Length": "999999999"}, 413),
        ("POST", "/api/sandbox/fintech/open", body, {**write, "Transfer-Encoding": "chunked"}, 411),
        ("POST", "/api/sandbox/fintech/open", b"[1]", write, 400),
        ("POST", "/api/sandbox/fintech/open", b"{", write, 400),
        ("POST", "/api/verify", body, write, 415),
        ("POST", "/api/nothing", body, write, 404),
        ("POST", "/after.fidb", body, write, 404),
        ("GET", "/api/nothing", None, {}, 404),
        ("OPTIONS", "/api/status", None, {}, 405),
        ("DELETE", "/after.fidb", None, {}, 405),
    ]
    for method, path, data, headers, expected in cases:
        status, _, _ = client.request(method, path, data, headers)
        assert status == expected, (method, path, headers)
    status, _, _ = client.request(
        "POST", "/api/sandbox/fintech/open", body, {**write, "Content-Length": "x"}
    )
    assert status == 400
    status, _, _ = client.request("POST", "/api/sandbox/fintech/open", None, write)
    assert status == 411


def test_only_the_console_bundles_and_public_pins_are_served(client: _Client) -> None:
    for path in (
        "/keys/cfo.key",
        "/fiduciary.toml",
        "/interlock.toml",
        "/fiduciary.db",
        "/receipts.jsonl",
        "/../pyproject.toml",
        "/%2e%2e/pyproject.toml",
        "/.hidden.fidb",
        "/missing.fidb",
        "/keys",
    ):
        status, _, _ = client.request("GET", path)
        assert status == 404, path
    status, headers, data = client.request("GET", "/after.fidb")
    assert status == 200 and headers["content-type"] == "application/x-tar" and data[:5]
    status, headers, data = client.request("GET", "/pinned-keys.json")
    assert status == 200 and json.loads(data)
    status, headers, _ = client.request("HEAD", "/report.md")
    assert status == 200 and headers["content-type"].startswith("text/markdown")
    status, headers, page = client.request("GET", "/")
    assert status == 200
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert b'"served":true' in page  # the page may ask the console; a file never does
    status, _, _ = client.request("GET", "/dashboard.html")
    assert status == 200


def test_a_failing_request_answers_without_its_cause(
    client: _Client, console: tuple[_Client, Console], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> dict[str, Any]:
        raise RuntimeError("secret detail")

    monkeypatch.setattr(console[1], "status", broken)
    status, body = client.get("/api/status")
    assert status == 500 and "secret" not in body["error"]


# -- the desk, over a recorded run --------------------------------------------------------


def test_the_desk_reads_a_recorded_run_and_takes_no_vote(client: _Client) -> None:
    status, found = client.get("/api/status")
    assert status == 200 and found["console"] == "fiduciary" and found["title"] == "Stack demo"
    (recorded,) = [d for d in found["deployments"] if d["kind"] == "recorded"]
    assert recorded["id"] == "fintech" and recorded["name"] == "Fintech"
    assert not recorded["accepts"] and not recorded["signs"]
    assert [p["name"] for p in recorded["principals"]] == ["cfo", "head-ops", "security"]
    assert {b["path"] for b in found["bundles"]} >= {"before.fidb", "after.fidb"}
    status, tickets = client.get("/api/deployments/fintech/tickets")
    assert status == 200 and tickets
    held = next(t for t in tickets if t["class"] == "held")
    status, view = client.get(f"/api/deployments/fintech/tickets/{held['ticket_id']}")
    assert view["status"] == "warranted"
    assert view["warrant"]["execution"] == "applied"
    assert len(view["warrant"]["basis"]) == 2
    (message,) = view["held"]
    assert message["payload_matches"] is True and message["payload"]["currency"] == "usd"
    assert view["document"]["ticket_id"] == held["ticket_id"]
    release = next(o for o in view["options"] if o["action"] == "release")
    assert release["quorum"] == 2 and len(release["approved_by"]) == 2
    assert release["heads"] and "@" in release["heads"][0]
    status, body = client.get("/api/deployments/fintech/tickets/no-such-ticket")
    assert status == 404
    status, body = client.get("/api/deployments/nowhere/tickets")
    assert status == 404
    status, body = client.post(
        "/api/deployments/fintech/approvals/draft",
        {"ticket_id": held["ticket_id"], "principal": "security"},
    )
    assert status == 409 and "warranted" in body["error"]


# -- the sandbox: calls, a quorum, a warrant ---------------------------------------------


def test_the_sandbox_holds_a_call_and_two_votes_release_it(client: _Client) -> None:
    status, opened = client.post("/api/sandbox/fintech/open", {})
    assert status == 200 and opened["active"] and opened["rung"] == "T"
    assert {t["name"] for t in opened["tools"]} >= {"refund_order", "flag_dispute"}
    status, called = client.post("/api/sandbox/fintech/call", {"preset": "held"})
    assert status == 200 and called["outcome"] == "committed" and called["ticket"]
    assert called["receipts"][0]["rung"] == "T" and called["ledger"]["halted"]
    ident, ticket = "sandbox-fintech", called["ticket"]
    status, view = client.get(f"/api/deployments/{ident}/tickets/{ticket}")
    assert view["status"] == "open" and view["held"][0]["payload_matches"]
    status, found = client.get("/api/status")
    sandbox = next(d for d in found["deployments"] if d["id"] == ident)
    assert sandbox["signs"] and sandbox["accepts"] and sandbox["open"] == 1
    recorded: list[str] = []
    for principal in ("cfo", "head-ops"):
        status, draft = client.post(
            f"/api/deployments/{ident}/approvals/draft",
            {"ticket_id": ticket, "principal": principal, "option": "release"},
        )
        assert status == 200 and len(draft["signing_input"]) <= 2 * fidu1.MAX_SIGNING_INPUT
        assert "fiduciary approve" in draft["command"]
        status, voted = client.post(
            f"/api/deployments/{ident}/approvals/sign", {"approval": draft["approval"]}
        )
        assert status == 200, voted
        recorded.extend(voted["recorded"])
    assert recorded == [
        "approval.recorded",
        "approval.recorded",
        "warrant.formed",
        "execution.intent",
        "execution.applied",
    ]
    assert set(voted["outbox"].values()) == {"delivered"}
    status, view = client.get(f"/api/deployments/{ident}/tickets/{ticket}")
    assert view["status"] == "warranted" and view["warrant"]["execution"] == "applied"
    assert [v["principal"] for v in view["votes"]] == ["cfo", "head-ops"]
    status, _ = client.post(
        f"/api/deployments/{ident}/approvals/draft", {"ticket_id": ticket, "principal": "security"}
    )
    assert status == 409


def test_sandbox_calls_and_drafts_refuse_what_is_not_theirs(client: _Client) -> None:
    client.post("/api/sandbox/fintech/open", {})
    for payload, expected in (
        ({"preset": "nope"}, "no preset"),
        ({"tool": "drop_tables"}, "no tool"),
        ({"tool": "refund_order", "arguments": [1]}, "JSON object"),
        ({"tool": "refund_order", "arguments": {}, "tenant": "nobody"}, "is no tenant"),
    ):
        status, body = client.post("/api/sandbox/fintech/call", payload)
        assert status == 400 and expected in body["error"], payload
    status, body = client.post("/api/sandbox/atlantis/call", {"preset": "held"})
    assert status == 404
    status, called = client.post(
        "/api/sandbox/fintech/call",
        {
            "tool": "refund_order",
            "arguments": {"order_id": 5, "amount": "1.00", "payment_intent": "pi_0000000005"},
            "hold": True,
        },
    )
    assert status == 200 and called["outcome"] == "committed" and called["ticket"]
    ident = "sandbox-fintech"
    draft_url = f"/api/deployments/{ident}/approvals/draft"
    for payload, code in (
        ({"ticket_id": called["ticket"], "principal": "mallory"}, 400),
        ({"ticket_id": called["ticket"], "principal": "cfo", "option": "nothing"}, 400),
        ({"ticket_id": called["ticket"], "principal": "cfo", "decision": "maybe"}, 400),
        ({"principal": "cfo"}, 400),
    ):
        status, _ = client.post(draft_url, payload)
        assert status == code, payload
    status, body = client.post(f"/api/deployments/{ident}/approvals/sign", {"approval": {"v": 1}})
    assert status == 400
    status, draft = client.post(
        draft_url, {"ticket_id": called["ticket"], "principal": "security", "decision": "decline"}
    )
    status, voted = client.post(
        f"/api/deployments/{ident}/approvals/sign", {"approval": draft["approval"]}
    )
    assert status == 200 and voted["recorded"] == ["approval.recorded"]
    status, view = client.get(f"/api/deployments/{ident}/tickets/{called['ticket']}")
    assert view["votes"][0]["decision"] == "decline"


@pytest.mark.parametrize(
    ("key", "rung", "cascade"),
    [
        ("healthcare", "V", "compensated"),
        ("sre", "P", "refused"),
        ("coding-agent", "T", "refused"),
        ("workspace", "T", "refused"),
        ("service-desk", "V", "compensated"),
    ],
)
def test_each_substrate_measures_its_presets(
    client: _Client, key: str, rung: str, cascade: str
) -> None:
    status, opened = client.post(f"/api/sandbox/{key}/open", {})
    assert status == 200 and opened["rung"] == rung
    outcomes = {}
    for preset in PRESETS[key]:
        status, called = client.post(f"/api/sandbox/{key}/call", {"preset": preset.id})
        assert status == 200, called
        assert called["gateway"]["decision"] == "PERMIT"
        outcomes[preset.expect] = called
    assert outcomes["commits"]["outcome"] == "committed"
    assert outcomes["refused"]["outcome"] == cascade
    assert outcomes["held"]["ticket"]
    actions = [r for r in outcomes["commits"]["receipts"] if r["kind"] == "action"]
    assert actions[-1]["rung"] == rung
    # A delivery receipt reads as the request it delivered, on its sink, bound
    # to the action receipt that made the request.
    delivered = [r for r in outcomes["commits"]["receipts"] if r["kind"] == "delivery"]
    assert delivered or key != "healthcare"
    for r in delivered:
        assert r["rung"] == "R" and r["measurement"] == "request-only"
        assert r["substrate"] and r["action"] and r["rows"] == 0
    status, view = client.get(
        f"/api/deployments/sandbox-{key}/tickets/{outcomes['held']['ticket']}"
    )
    assert view["status"] == "open"
    if key == "coding-agent":
        # A refused plan's ticket shows what was measured: the receipt it cites.
        assert outcomes["held"]["ci"]["accepted"] >= 1
        assert view["class"] == "refused" and view["measured"]["substrate"] == "git"
        assert view["measured"]["rung"] == "T" and view["evidence"]["blocked_by"]
    else:
        assert view["held"] and view["held"][0]["payload_matches"]
    status, reset = client.post(f"/api/sandbox/{key}/reset", {})
    assert status == 200 and reset["active"]
    status, tickets = client.get(f"/api/deployments/sandbox-{key}/tickets")
    assert tickets == []


def test_the_sandbox_describes_every_industry(console: tuple[_Client, Console]) -> None:
    described = console[1].sandbox.describe()["domains"]
    assert [d["key"] for d in described] == [
        "workspace",
        "coding-agent",
        "sre",
        "healthcare",
        "service-desk",
        "fintech",
        "saas",
        "supply-chain",
    ]
    assert {d["rung"] for d in described} == {"T", "P", "V"}
    assert all(len(d["presets"]) == 3 for d in described)
    assert console[1].sandbox.deployment("fintech") is None  # ids carry the prefix


# -- votes a principal signs --------------------------------------------------------------


def test_a_vote_signed_outside_the_console_is_checked_and_queued(
    deployment: tuple[Path, str], tmp_path: Path
) -> None:
    root, ticket = deployment
    read_only = ConfiguredDeployment("d", root / "fiduciary.toml", scratch=tmp_path)
    draft = read_only.draft({"ticket_id": ticket, "principal": "cfo", "option": "release"})
    signature = _vote(root, draft, "cfo")
    with pytest.raises(ControlError, match="read-only") as refused:
        read_only.accept(
            {"approval": draft["approval"], "key_id": draft["key_id"], "signature": signature}
        )
    assert refused.value.status == 403
    desk = ConfiguredDeployment("d", root / "fiduciary.toml", scratch=tmp_path, accepts=True)
    forged = _vote(root, draft, "head-ops")  # another principal's key
    with pytest.raises(ControlError, match="does not verify") as bad:
        desk.accept({"approval": draft["approval"], "key_id": draft["key_id"], "signature": forged})
    assert bad.value.status == 422
    altered = {**draft["approval"], "decision": "decline"}
    with pytest.raises(ControlError, match="does not verify"):
        desk.accept({"approval": altered, "key_id": draft["key_id"], "signature": signature})
    with pytest.raises(ControlError, match="draft the signature covers"):
        desk.accept({"approval": "x", "key_id": draft["key_id"], "signature": signature})
    with pytest.raises(ControlError, match="no principal"):
        desk.accept(
            {
                "approval": {**draft["approval"], "principal": "mallory"},
                "key_id": draft["key_id"],
                "signature": signature,
            }
        )
    with pytest.raises(ControlError, match="holds no key"):
        desk.sign_draft({"approval": draft["approval"]})
    queued = desk.accept(
        {"approval": draft["approval"], "key_id": draft["key_id"], "signature": signature}
    )
    assert queued["intake"] > 0 and queued["recorded"] == []
    view = desk.ticket(ticket)
    assert [(p["principal"], p["option"]) for p in view["pending"]] == [("cfo", "release")]
    assert view["status"] == "open" and view["votes"] == []
    # The same approval, signed by fiduciary approve and submitted whole.
    signed = load_key(root / "keys" / "head-ops.key")
    other = desk.draft({"ticket_id": ticket, "principal": "head-ops", "option": "release"})
    from fiduciary.documents import Approval

    whole = Approval.from_json(other["approval"], signed=False).signed(signed).to_json()
    assert desk.accept({"approval": whole})["intake"] > queued["intake"]
    summary = desk.summary()
    assert summary["accepts"] and summary["store"] == "sqlite" and summary["open"] == 1
    desk.close()
    read_only.close()


def test_the_writer_records_what_the_console_queued(tmp_path: Path) -> None:
    """The deployment's own writer judges the intake: a vote the console put
    there is recorded as any other submission is."""
    from fiduciary.authority import Authority
    from fiduciary.config import load_config
    from fiduciary.log import Log

    root, ticket = _held(tmp_path / "held")
    desk = ConfiguredDeployment("d", root / "fiduciary.toml", scratch=tmp_path, accepts=True)
    draft = desk.draft({"ticket_id": ticket, "principal": "security", "option": "cancel"})
    desk.accept(
        {
            "approval": draft["approval"],
            "key_id": draft["key_id"],
            "signature": _vote(root, draft, "security"),
        }
    )
    config = load_config(root / "fiduciary.toml")
    with config.open_store() as store:
        log = Log(store, config.service(), sealer=config.sealer())
        kinds = [r.kind for r in Authority(log, pinned=config.pinned()).process_intake()]
    assert "approval.recorded" in kinds
    view = desk.ticket(ticket)
    assert [v["principal"] for v in view["votes"]] == ["security"] and view["pending"] == []
    assert view["status"] == "warranted" and view["warrant"]["action"] == "cancel"
    desk.close()


def test_a_postgres_store_is_read_where_it_stands(
    deployment: tuple[Path, str], tmp_path: Path, pg_schema: tuple[str, str]
) -> None:
    from fiduciary.postgres import PostgresStore
    from fiduciary.sqlite import SqliteStore

    root, ticket = deployment
    dsn, schema = pg_schema
    source = SqliteStore(root / "fiduciary.db")
    target = PostgresStore(dsn, schema=schema)
    with target.transaction():
        for digest, kind, data in source.documents():
            target.put_document(digest, kind, data)
        for record in source.records(LOG_ID):
            target.insert_record(record)
    source.close()
    target.close()
    desk = ConfiguredDeployment("pg", root / "fiduciary.toml", scratch=tmp_path)
    desk.config = dataclasses.replace(desk.config, database=dsn, schema=schema)
    assert desk.describe()["store"] == "postgresql"
    assert [t["ticket_id"] for t in desk.tickets()] == [ticket]
    assert desk.summary()["open"] == 1
    desk.close()


# -- the verifier -------------------------------------------------------------------------


def _vector(name: str) -> tuple[bytes, dict[str, Any]]:
    entry = next(e for e in json.loads((VECTORS / "index.json").read_text()) if e["name"] == name)
    return (VECTORS / entry["file"]).read_bytes(), entry


def test_every_golden_bundle_reaches_its_recorded_verdict(run_dir: Path) -> None:
    console = Console(run_dir)
    for entry in json.loads((VECTORS / "index.json").read_text()):
        data = (VECTORS / entry["file"]).read_bytes()
        given = {"charter": entry["charter"], "keys": json.dumps(entry["keys"])}
        found = console.verify(data, {**given, "witness": entry["witness"] or ""})
        assert found["exit_code"] == entry["exit_code"], entry["name"]
        assert found["pinned_by"] == "given"
        if found["readable"]:
            assert found["attestation"]["bundle"]["sha256"] == entry["sha256"]
            assert sorted(found["pins"]["keys"]) == sorted(entry["keys"])
        else:
            assert found["error"]


def test_verify_over_http_pins_by_deployment_or_digest(client: _Client, run_dir: Path) -> None:
    after = (run_dir / "after.fidb").read_bytes()
    post = {"Content-Type": "application/octet-stream", "X-Fiduciary-Console": "1"}
    status, _, raw = client.request("POST", "/api/verify?name=after.fidb", after, post)
    found = json.loads(raw)
    assert status == 200 and found["pinned_by"] == "fintech" and found["exit_code"] == 7
    assert found["attestation"]["verdict"]["status"] == "BREAK-GLASS DETECTED"
    assert len(found["attestation"]["frameworks"]) == 9
    status, _, raw = client.request("POST", "/api/verify?deployment=fintech", after, post)
    assert status == 200 and json.loads(raw)["exit_code"] == 7
    sealed, entry = _vector("valid-sealed-child")
    query = urllib.parse.urlencode({"charter": entry["charter"], "keys": json.dumps(entry["keys"])})
    status, _, raw = client.request("POST", f"/api/verify?{query}", sealed, post)
    found = json.loads(raw)
    assert status == 200 and found["exit_code"] == 0 and found["withheld"] == 1
    assert found["attestation"]["bundle"]["format"] == "fidb/2"
    status, _, raw = client.request("POST", "/api/verify", sealed, post)
    assert status == 422 and "no charter digest" in json.loads(raw)["error"]
    status, _, raw = client.request("POST", "/api/verify?charter=x&keys=[1]", sealed, post)
    assert status == 400


# -- the parts on their own ---------------------------------------------------------------


def test_the_console_page_for_deployments_with_no_recorded_run(tmp_path: Path) -> None:
    page = report.console_html("Desk", served=True)
    assert "<title>Desk</title>" in page and '"served":true' in page
    assert '"attestations":{}' in page
    assert (
        Console(tmp_path).page() == report.console_html("Fiduciary console", served=True).encode()
    )
    assert Console(tmp_path, page=lambda: b"x").page() == b"x"


def test_discovery_and_an_unreadable_deployment(
    tmp_path: Path, deployment: tuple[Path, str]
) -> None:
    root, _ = deployment
    assert discover(root) == [root / "fiduciary.toml"]
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "fiduciary.toml").write_text("database = 1\n")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "fiduciary.toml").write_text("")
    console = Console.over(tmp_path)
    (only,) = console.deployments()
    assert only.ident == "broken" and "error" in only.summary()
    with pytest.raises(ControlError):
        only.tickets()
    with pytest.raises(ControlError) as missing:
        console.deployment("nope")
    assert missing.value.status == 404
    console.close()
    twice = Console.over(tmp_path, [root / "fiduciary.toml", root / "fiduciary.toml"])
    assert [d.ident for d in twice.deployments()] == [root.name.lower(), f"{root.name.lower()}-2"]
    twice.close()


def test_a_sqlite_snapshot_and_the_outbox_are_read_without_writing(
    deployment: tuple[Path, str], tmp_path: Path
) -> None:
    root, _ = deployment
    with pytest.raises(ControlError):
        _sqlite_copy(tmp_path / "missing.db", tmp_path / "copy.db")
    before = (root / "fiduciary.db").read_bytes()
    _sqlite_copy(root / "fiduciary.db", tmp_path / "copy.db")
    assert (root / "fiduciary.db").read_bytes() == before and (tmp_path / "copy.db").stat().st_size
    assert _payloads(str(root / "back_office.db"), []) == {}
    assert _payloads(str(tmp_path / "nothing.db"), ["x"]) == {}
    assert _payloads(None, ["x"]) == {}
    assert _payloads("postgresql://nobody@127.0.0.1:1/none", ["x"]) == {}


def test_a_receipt_reads_as_the_desk_shows_it() -> None:
    view = receipt_view(
        {
            "receipt_id": "r",
            "coverage": {"measurement": "predicted", "known_gaps": ["g"]},
            "effect": {
                "substrate_id": "fhir",
                "row_count": 3,
                "summary": {"ins": 1, "upd": 2, "tables": ["t"], "tenants": ["a"]},
            },
            "outcome": {"status": "compensated", "substrate_txid": "fhir:s"},
            "decision": {"admitted": True, "checkers": [{"name": "blast_radius"}, "junk"]},
        }
    )
    assert view["rung"] == "V" and view["inserted"] == 1 and view["deleted"] == 0
    assert view["checkers"] == ["blast_radius"] and view["gaps"] == ["g"]
    assert receipt_view({})["rung"] == "T"
    delivered = receipt_view(
        {
            "receipt_id": "d",
            "request": {"sink": "stripe", "operation": "refund"},
            "delivery": {"remote_ref": "re_1", "status_code": 200},
            "action": {"receipt_id": "r"},
        }
    )
    assert delivered["kind"] == "delivery" and delivered["rung"] == "R"
    assert delivered["substrate"] == "stripe" and delivered["action"] == "r"
    assert delivered["remote_ref"] == "re_1" and delivered["status_code"] == 200


def _keep(served: list[Any]) -> Any:
    def run(server: Any) -> None:
        served.append(server)
        server.server_close()

    return run


def test_the_cli_serves_a_console(
    tmp_path: Path,
    deployment: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import fiduciary.control as control

    served: list[Any] = []
    monkeypatch.setattr(control, "run", _keep(served))
    root, _ = deployment
    assert (
        cli.main(["console", "--dir", str(root), "--listen", "127.0.0.1:0", "--accept-approvals"])
        == 0
    )
    assert served[0].console.deployments()[0].accepts
    err = capsys.readouterr()
    assert "takes approvals" in err.err and "serving" in err.out
    assert (
        cli.main(["console", "--dir", str(tmp_path), "--listen", "127.0.0.1:0", "--sandbox"]) == 0
    )
    assert served[1].console.sandbox is not None
    assert cli.main(["console", "--dir", str(tmp_path), "--listen", "127.0.0.1:0"]) == 0
    assert "the desk is empty" in capsys.readouterr().err
    for argv in (
        ["console", "--dir", str(tmp_path / "nowhere")],
        ["console", "--deployment", str(tmp_path / "none.toml")],
        ["console", "--dir", str(tmp_path), "--listen", "10.0.0.1:0"],
    ):
        assert cli.main(argv) == 2
    assert "loopback only" in capsys.readouterr().err


def test_demo_serve_opens_a_recorded_run(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import fiduciary.control as control

    served: list[Any] = []
    monkeypatch.setattr(control, "run", _keep(served))
    assert cli.main(["demo", "--out", str(run_dir), "--serve", "127.0.0.1:0"]) == 0
    (deployment,) = served[0].console.deployments()
    assert deployment.kind == "recorded" and not deployment.accepts
    assert "the run recorded in" in capsys.readouterr().out
    assert cli.main(["demo", "--out", str(run_dir), "--serve", "8.8.8.8:0"]) == 2

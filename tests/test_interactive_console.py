"""Tests for Wave 4 (1.7.0): the interactive loopback control plane and console."""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from fiduciary import __version__, demo
from fiduciary.bundle.format import pack, unpack
from fiduciary.demo.control import ControlPlaneServer, serve_control_plane
from fiduciary.report import serve_console


class _Client:
    """Tiny loopback HTTP helper over an ephemeral :class:`ControlPlaneServer`."""

    def __init__(self, server: ControlPlaneServer, dashboard_url: str) -> None:
        self.server = server
        self.dashboard_url = dashboard_url
        self.base_url = dashboard_url.rsplit("/", 1)[0]

    def get_json(self, path: str) -> tuple[int, dict[str, Any]]:
        req = urllib.request.Request(f"{self.base_url}{path}", method="GET")  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                return int(resp.status), json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return int(exc.code), json.loads(exc.read().decode("utf-8"))

    def get_bytes(self, path: str) -> tuple[int, bytes]:
        req = urllib.request.Request(f"{self.base_url}{path}", method="GET")  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                return int(resp.status), resp.read()
        except urllib.error.HTTPError as exc:
            return int(exc.code), exc.read()

    def post_json(self, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(  # noqa: S310
            f"{self.base_url}{path}",
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                return int(resp.status), json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return int(exc.code), json.loads(exc.read().decode("utf-8"))

    def post_raw(
        self, path: str, data: bytes, content_type: str = "application/x-tar"
    ) -> tuple[int, dict[str, Any]]:
        req = urllib.request.Request(  # noqa: S310
            f"{self.base_url}{path}",
            data=data,
            method="POST",
            headers={"Content-Type": content_type},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                return int(resp.status), json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return int(exc.code), json.loads(exc.read().decode("utf-8"))


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("demo-fintech")
    outcome = demo.run(root, domain="fintech")
    assert outcome.held
    return root


@pytest.fixture
def client(demo_dir: Path) -> Iterator[_Client]:
    server, url = serve_console(demo_dir, "127.0.0.1:0")
    assert isinstance(server, ControlPlaneServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield _Client(server, url)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_loopback_guard(demo_dir: Path) -> None:
    with pytest.raises(ValueError, match="loopback only"):
        serve_control_plane(demo_dir, "0.0.0.0:8740")
    with pytest.raises(ValueError, match="HOST:PORT"):
        serve_control_plane(demo_dir, "127.0.0.1:notaport")


def test_status_and_dashboard_assets(client: _Client) -> None:
    code, status = client.get_json("/api/status")
    assert code == 200
    assert status["mode"] == "live-server"
    assert status["version"] == __version__ == "1.7.0"
    assert status["open_tickets"] == []
    assert status["held_tickets"] == []

    by_key = {d["key"]: d for d in status["domains"]}
    assert set(by_key) == {
        "fintech",
        "healthcare",
        "sre",
        "supply-chain",
        "saas",
        "coding-agent",
    }
    assert by_key["fintech"]["on_disk"] is True
    assert {b["name"] for b in by_key["fintech"]["bundles"]} == {"before.fidb", "after.fidb"}
    assert by_key["healthcare"]["substrate"] == "fhir"
    assert by_key["healthcare"]["measurement"] == "predicted"
    assert by_key["sre"]["substrate"] == "kubernetes"
    assert by_key["sre"]["measurement"] == "plan-bound"
    assert by_key["coding-agent"]["substrate"] == "git"

    for dom in by_key.values():
        assert len(dom["principals"]) == 3
        assert [p["id"] for p in dom["presets"]] == ["normal", "trap", "held"]
        assert dom["ledger"]["usd"]["halted"] is False
        assert len(dom["windows"]) >= 1

    html_code, html_bytes = client.get_bytes("/dashboard.html")
    assert html_code == 200
    html_text = html_bytes.decode("utf-8")
    assert "api/verify-bundle" in html_text
    assert "api/warrant/vote" in html_text
    assert "api/sandbox/call" in html_text
    assert "crypto.subtle" in html_text
    assert "unpackTar" in html_text
    assert "Try a call & sign a 2-of-3 quorum warrant" in html_text
    assert "This page makes no network requests." in html_text

    trav_code, _ = client.get_bytes("/../pyproject.toml")
    assert trav_code == 404


def test_verify_bundle_fidb1_fidb2_and_tampered(client: _Client, demo_dir: Path) -> None:
    # 1. Clean fidb/1 bundle on disk (before.fidb)
    code, clean = client.post_json(
        "/api/verify-bundle", {"domain": "fintech", "bundle": "before.fidb"}
    )
    assert code == 200
    assert clean["passed"] is True
    assert clean["exit_code"] == 0
    assert clean["seal"]["format"] == "fidb/1"
    assert clean["seal"]["sealed"] is False
    assert clean["seal"]["verified"] is True
    assert clean["seal"]["records_whole"] > 0
    assert clean["seal"]["records_two_tag"] > 0
    assert len(clean["checks"]) == 12
    assert all(c["status"] == "pass" for c in clean["checks"])
    assert len(clean["frameworks"]) == 9
    assert len(clean["mappings"]["packs"]) == 9

    # 2. Post-break-glass fidb/1 bundle on disk (after.fidb) -> exit 7 with V7 break-glass label
    code, after = client.post_json(
        "/api/verify-bundle", {"domain": "fintech", "bundle": "after.fidb"}
    )
    assert code == 200
    assert after["passed"] is False
    assert after["exit_code"] == 7
    assert any(f["check"] == "V7" and f["label"] == "break-glass" for f in after["findings"])

    # 3. Sealed fidb/2 bundle exported from SaaS multi-workspace sandbox + re-verified via upload
    call_code, saas_call = client.post_json(
        "/api/sandbox/call", {"domain": "saas", "preset": "normal"}
    )
    assert call_code == 200
    assert saas_call["outcome"] == "committed"

    code, sealed = client.post_json(
        "/api/verify-bundle", {"domain": "saas", "bundle": "@sandbox-sealed"}
    )
    assert code == 200
    assert sealed["passed"] is True
    assert sealed["exit_code"] == 0
    assert sealed["seal"]["format"] == "fidb/2"
    assert sealed["seal"]["sealed"] is True
    assert sealed["seal"]["status"] == "verified"
    assert sealed["seal"]["records_whole"] > 0
    assert sealed["seal"]["records_withheld"] > 0
    assert all(c["status"] == "pass" for c in sealed["checks"])

    # Also upload an actual sealed fidb/2 byte stream directly to POST /api/verify-bundle
    sb_bytes, sb_charter, sb_keys = client.server.control._export_sandbox_bundle(
        "saas", sealed=True
    )
    raw_code, raw_res = client.post_raw("/api/verify-bundle?domain=saas", sb_bytes)
    assert raw_code == 200
    assert raw_res["passed"] is True
    assert raw_res["seal"]["format"] == "fidb/2"
    assert raw_res["seal"]["records_withheld"] > 0
    assert len(raw_res["mappings"]["packs"]) == 9
    assert sb_charter and sb_keys

    # 4. Tampered fidb/1 bundle (mutate log.jsonl inside the tar without updating manifest.json)
    before_bytes = (demo_dir / "before.fidb").read_bytes()
    members = dict(unpack(before_bytes))
    log_lines = [
        json.loads(ln) for ln in members["log.jsonl"].decode("utf-8").splitlines() if ln.strip()
    ]
    log_lines[0]["issued_at"] = "2099-01-01T00:00:00Z"
    members["log.jsonl"] = b"".join(
        (json.dumps(item, sort_keys=True) + "\n").encode("utf-8") for item in log_lines
    )
    tampered_bytes = pack(members)
    code, tampered = client.post_json(
        "/api/verify-bundle",
        {
            "domain": "fintech",
            "filename": "tampered.fidb",
            "data_base64": base64.b64encode(tampered_bytes).decode("ascii"),
        },
    )
    assert code == 200
    assert tampered["passed"] is False
    assert tampered["exit_code"] in (3, 4)
    assert tampered["seal"]["verified"] is False
    assert any(f["check"] == "V1" for f in tampered["findings"])

    # 5. Unreadable non-tar bytes -> clean exit_code 2 JSON report
    code, unreadable = client.post_json(
        "/api/verify-bundle",
        {
            "domain": "fintech",
            "filename": "garbage.fidb",
            "data_base64": base64.b64encode(b"not-a-posix-tar").decode("ascii"),
        },
    )
    assert code == 200
    assert unreadable["passed"] is False
    assert unreadable["exit_code"] == 2
    assert unreadable["verdict"]["status"] == "UNREADABLE"


def test_sandbox_calls_across_substrates_preserve_disk_files(
    client: _Client, demo_dir: Path
) -> None:
    disk_hashes_before = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in demo_dir.iterdir()
        if p.is_file()
    }

    # SQLite (fintech): normal commit + 50-row cross-tenant trigger rollback
    code, normal = client.post_json("/api/sandbox/call", {"domain": "fintech", "preset": "normal"})
    assert code == 200
    assert normal["gateway"]["decision"] == "PERMIT"
    assert normal["outcome"] == "committed"
    assert normal["measured"]["committed"] is True
    assert normal["measured"]["rows"] == 2
    assert normal["receipt"]["outcome"] == "committed"
    assert normal["receipt"]["measurement"] == "transactional"
    assert list(normal["outbox"].values()) == ["delivered"]

    code, trap = client.post_json("/api/sandbox/call", {"domain": "fintech", "preset": "trap"})
    assert code == 200
    assert trap["gateway"]["decision"] == "PERMIT"
    assert trap["outcome"] == "refused"
    assert trap["measured"]["committed"] is False
    assert trap["measured"]["rows"] == 50
    assert trap["unchanged"] is True
    assert trap["receipt"]["outcome"] == "refused"
    assert any(f["severity"] == "blocking" for f in trap["findings"])

    # FHIR R4 (healthcare): normal predicted commit + read-back put-back COMPENSATED receipt
    code, hc_ok = client.post_json(
        "/api/sandbox/call", {"domain": "healthcare", "preset": "normal"}
    )
    assert code == 200
    assert hc_ok["outcome"] == "committed"
    assert hc_ok["receipt"]["measurement"] == "predicted"
    assert hc_ok["receipt"]["substrate"] == "fhir"

    code, hc_comp = client.post_json(
        "/api/sandbox/call", {"domain": "healthcare", "preset": "trap"}
    )
    assert code == 200
    assert hc_comp["gateway"]["decision"] == "PERMIT"
    assert hc_comp["outcome"] == "compensated"
    assert hc_comp["measured"]["compensated"] is True
    assert hc_comp["unchanged"] is True
    assert hc_comp["receipt"]["outcome"] == "compensated"

    # Kubernetes (sre): dryRun plan-bound commit + mutating-webhook x10 refusal
    code, sre_ok = client.post_json("/api/sandbox/call", {"domain": "sre", "preset": "normal"})
    assert code == 200
    assert sre_ok["outcome"] == "committed"
    assert sre_ok["receipt"]["measurement"] == "plan-bound"
    assert sre_ok["ledger"]["units"]["vcpu"]["spent"] == "2"

    code, sre_trap = client.post_json("/api/sandbox/call", {"domain": "sre", "preset": "trap"})
    assert code == 200
    assert sre_trap["outcome"] == "refused"
    assert any(f["severity"] == "blocking" for f in sre_trap["findings"])

    # Git (coding-agent): worktree stage + CAS commit on main
    code, git_ok = client.post_json(
        "/api/sandbox/call", {"domain": "coding-agent", "preset": "normal"}
    )
    assert code == 200
    assert git_ok["outcome"] == "committed"
    assert git_ok["measured"]["rows"] == 1
    assert git_ok["receipt"]["substrate"] == "git"

    disk_hashes_after = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in demo_dir.iterdir()
        if p.is_file()
    }
    assert disk_hashes_after == disk_hashes_before


def test_warrant_vote_quorum_release_duplicate_and_decline(client: _Client) -> None:
    # 1. Stage a held Schedule II controlled-substance dispense in healthcare
    code, held_call = client.post_json(
        "/api/sandbox/call", {"domain": "healthcare", "preset": "held"}
    )
    assert code == 200
    assert held_call["outcome"] == "committed"
    ticket = held_call["ticket"]
    assert ticket is not None
    assert ticket["class"] == "held"
    assert ticket["open"] is True
    assert ticket["quorum"]["needed"] == 2
    assert len(ticket["options"][0]["expected_heads"]) >= 1
    held_msgs = [m for m, s in held_call["outbox"].items() if s == "held"]
    assert len(held_msgs) == 1
    held_msg = held_msgs[0]

    # Status endpoint exposes the open held ticket
    _, status = client.get_json("/api/status")
    assert any(t["ticket_id"] == ticket["ticket_id"] for t in status["held_tickets"])

    p1, p2, _p3 = ticket["principals"]

    # 2. First principal votes approve -> 1 of 2, no warrant yet, message remains held
    code, v1 = client.post_json(
        "/api/warrant/vote",
        {
            "domain": "healthcare",
            "ticket_id": ticket["ticket_id"],
            "principal": p1,
            "decision": "approve",
        },
    )
    assert code == 200
    assert v1["vote"]["counted"] is True
    assert v1["vote"]["duplicate"] is False
    assert v1["quorum"]["approved_by"] == [p1]
    assert v1["warrant"] is None
    assert v1["outbox"][held_msg] == "held"

    # 3. Duplicate vote by p1 -> principal counts once, still 1 of 2, no warrant
    code, v1_dup = client.post_json(
        "/api/warrant/vote",
        {
            "domain": "healthcare",
            "ticket_id": ticket["ticket_id"],
            "principal": p1,
            "decision": "approve",
        },
    )
    assert code == 200
    assert v1_dup["vote"]["duplicate"] is True
    assert v1_dup["vote"]["counted"] is False
    assert v1_dup["quorum"]["approved_by"] == [p1]
    assert v1_dup["warrant"] is None
    assert v1_dup["outbox"][held_msg] == "held"

    # 4. Second distinct principal votes approve -> 2-of-3 Warrant forms, executes at
    # expected_heads, and releases/delivers the held message
    code, v2 = client.post_json(
        "/api/warrant/vote",
        {
            "domain": "healthcare",
            "ticket_id": ticket["ticket_id"],
            "principal": p2,
            "decision": "approve",
        },
    )
    assert code == 200
    assert v2["quorum"]["reached"] is True
    assert v2["quorum"]["approved_by"] == [p1, p2]
    assert v2["newly_executed"] is True
    warrant = v2["warrant"]
    assert warrant is not None
    assert warrant["action"] == "release"
    assert len(warrant["basis"]) == 2
    assert len(warrant["expected_heads"]) >= 1
    assert warrant["released"] is True
    assert "execution.applied" in warrant["executed"]
    assert v2["outbox"][held_msg] == "delivered"

    # 5. Rejection / decline path on SRE held security-group change: 2 rejects close the ticket
    code, sre_held = client.post_json("/api/sandbox/call", {"domain": "sre", "preset": "held"})
    assert code == 200
    sre_ticket = sre_held["ticket"]
    assert sre_ticket is not None
    sre_msg = next(m for m, s in sre_held["outbox"].items() if s == "held")
    sp1, sp2, _sp3 = sre_ticket["principals"]

    code, r1 = client.post_json(
        "/api/warrant/vote",
        {
            "domain": "sre",
            "ticket_id": sre_ticket["ticket_id"],
            "principal": sp1,
            "decision": "reject",
        },
    )
    assert code == 200
    assert r1["ticket"]["open"] is True
    assert r1["warrant"] is None

    code, r2 = client.post_json(
        "/api/warrant/vote",
        {
            "domain": "sre",
            "ticket_id": sre_ticket["ticket_id"],
            "principal": sp2,
            "decision": "reject",
        },
    )
    assert code == 200
    assert r2["ticket"]["open"] is False
    assert r2["ticket"]["status"] == "declined"
    assert r2["warrant"] is None
    assert r2["outbox"][sre_msg] == "held"

"""Notices (1.8.0): principals told when a ticket waits for them and when it is
decided, in log order, at least once, each notice signed or keyed so a retry is
the same notice."""

from __future__ import annotations

import http.server
import json
import threading
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest

from fiduciary import cli
from fiduciary.config import ConfigError, load_config
from fiduciary.demo.sandbox import SandboxDeployment
from fiduciary.keys import generate_key
from fiduciary.notify import (
    EVENTS,
    Notice,
    NotifyConfig,
    Webhook,
    for_store,
    request_for,
    verify_standard,
)


class _Sent:
    """What a webhook was sent, and what it answers."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes, dict[str, str]]] = []
        self.status = 200

    def __call__(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> int:
        self.calls.append((url, body, dict(headers)))
        return self.status


@pytest.fixture
def sandbox(tmp_path: Path) -> Iterator[SandboxDeployment]:
    built = SandboxDeployment("fintech", tmp_path / "sb")
    built.built()
    yield built
    built.close()


def _config(tmp_path: Path, *hooks: Webhook) -> NotifyConfig:
    return NotifyConfig(
        webhooks=hooks,
        state=tmp_path / "cursor.json",
        key=tmp_path / "notify.key",
        console="http://127.0.0.1:8740/",
        deployment="fintech",
    )


def _hold_and_release(sandbox: SandboxDeployment) -> str:
    """A held refund, then two of three principals sign its release."""
    ticket = str(sandbox.call({"preset": "held"})["ticket"])
    for principal in ("cfo", "head-ops"):
        draft = sandbox.draft({"ticket_id": ticket, "principal": principal, "option": "release"})
        sandbox.sign_draft({"approval": draft["approval"]})
    return ticket


def test_a_ticket_and_its_warrant_are_told_in_order_signed(
    sandbox: SandboxDeployment, tmp_path: Path
) -> None:
    signer = generate_key(tmp_path / "notify.key")
    sent = _Sent()
    hook = Webhook("on-call", "https://hooks.example/fiduciary")
    notifier = for_store(_config(tmp_path, hook), sandbox.built().world.store, send=sent)
    assert notifier.turn() == {"on-call": 0}  # the past is not announced
    ticket = _hold_and_release(sandbox)
    assert notifier.turn() == {"on-call": 3}
    events = [json.loads(body)["event"] for _, body, _ in sent.calls]
    assert events == ["ticket.raised", "warrant.formed", "execution.applied"]
    raised = json.loads(sent.calls[0][1])
    assert raised["ticket_id"] == ticket and raised["ticket"]["class"] == "held"
    assert {"action": "release", "quorum": 2} in raised["ticket"]["options"]
    assert raised["link"] == f"http://127.0.0.1:8740/#/desk?d=fintech&t={ticket}"
    assert "payload" not in json.dumps(raised) and "amount" not in raised
    public = f"ed25519:{cast(Any, signer.public_key()).raw.hex()}"
    for _, body, headers in sent.calls:
        assert verify_standard(body, headers, public)["id"] == headers["webhook-id"]
    tampered = sent.calls[0][1].replace(b"held", b"dead")
    with pytest.raises(ValueError, match="no v1a signature"):
        verify_standard(tampered, sent.calls[0][2], public)
    assert notifier.turn() == {"on-call": 0}  # each told once


def test_a_failed_delivery_is_retried_as_the_same_notice(
    sandbox: SandboxDeployment, tmp_path: Path
) -> None:
    generate_key(tmp_path / "notify.key")
    sent = _Sent()
    clock = {"t": 1_000_000.0}
    hook = Webhook("on-call", "https://hooks.example/fiduciary", events=("ticket.raised",))
    config = _config(tmp_path, hook)
    notifier = for_store(config, sandbox.built().world.store, send=sent)
    notifier._now = lambda: clock["t"]
    notifier.turn()
    sandbox.call({"preset": "held"})
    sent.status = 503
    assert notifier.turn() == {"on-call": 0}
    first = sent.calls[-1][2]["webhook-id"]
    assert notifier.turn() == {"on-call": 0} and len(sent.calls) == 1  # backing off
    clock["t"] += 10
    sent.status = 200
    assert notifier.turn() == {"on-call": 1}
    assert sent.calls[-1][2]["webhook-id"] == first
    # A new notifier reads the cursor where the last one left it.
    again = for_store(config, sandbox.built().world.store, send=sent)
    assert again.turn() == {"on-call": 0}


def test_slack_and_pagerduty_shapes(sandbox: SandboxDeployment, tmp_path: Path) -> None:
    sent = _Sent()
    slack = Webhook("slack", "https://hooks.slack.example/T/B/x", format="slack")
    pager = Webhook(
        "pager",
        "https://events.pagerduty.example/v2/enqueue",
        format="pagerduty",
        routing_key_env="TEST_PD_KEY",
    )
    config = NotifyConfig(webhooks=(slack, pager), state=tmp_path / "c.json", deployment="fintech")
    notifier = for_store(config, sandbox.built().world.store, send=sent)
    notifier.turn()
    import os

    os.environ["TEST_PD_KEY"] = "routing-key"
    try:
        ticket = _hold_and_release(sandbox)
        notifier.turn()
    finally:
        del os.environ["TEST_PD_KEY"]
    by_url: dict[str, list[dict[str, Any]]] = {}
    for url, body, _ in sent.calls:
        by_url.setdefault(url, []).append(json.loads(body))
    first = by_url[slack.url][0]
    assert first["text"].startswith("A held ticket waits for its quorum")
    assert first["blocks"][0]["type"] == "section"
    events = by_url[pager.url]
    actions = [e["event_action"] for e in events]
    assert actions[:3] == ["trigger", "acknowledge", "resolve"]
    assert {e["dedup_key"] for e in events} == {f"fiduciary:fintech:{ticket}"}
    assert events[0]["routing_key"] == "routing-key"


def test_requests_refuse_what_they_cannot_send(tmp_path: Path) -> None:
    notice = Notice(
        "fn_x", "ticket.raised", {"event": "ticket.raised", "deployment": "d", "log": {"seq": 1}}
    )
    with pytest.raises(ValueError, match="signed"):
        request_for(notice, Webhook("s", "https://x.example/"))
    with pytest.raises(ValueError, match="is not set"):
        request_for(
            notice,
            Webhook("p", "https://x.example/", format="pagerduty", routing_key_env="NOPE_X"),
            environ={},
        )
    for kwargs, message in (
        ({"url": "http://hooks.example/"}, "https URL"),
        ({"url": "https://x.example/", "format": "email"}, "format is one of"),
        ({"url": "https://x.example/", "events": ("ticket.raised", "x")}, "no such event"),
        ({"url": "https://x.example/", "format": "pagerduty"}, "routing_key_env"),
    ):
        with pytest.raises(ValueError, match=message):
            Webhook("w", **kwargs)
    assert Webhook("loop", "http://127.0.0.1:9/").events == EVENTS
    with pytest.raises(ValueError, match="timestamp"):
        verify_standard(b"{}", {"webhook-id": "a", "webhook-timestamp": "1"}, "ed25519:" + "0" * 64)
    with pytest.raises(ValueError, match="no webhook-id"):
        verify_standard(b"{}", {}, "ed25519:" + "0" * 64)


def _deployment(sandbox: SandboxDeployment) -> Path:
    return sandbox.built().world.root / "fiduciary.toml"


def _with_notify(path: Path, table: str) -> None:
    path.write_text(path.read_text() + "\n" + table)


def test_the_configuration_reads_notify(sandbox: SandboxDeployment, tmp_path: Path) -> None:
    path = _deployment(sandbox)
    original = path.read_text()
    _with_notify(
        path,
        '[notify]\nkey = "keys/notify.key"\nconsole = "http://127.0.0.1:8740/"\n'
        '[[notify.webhooks]]\nname = "on-call"\nurl = "https://hooks.example/f"\n'
        'classes = ["held"]\n',
    )
    found = load_config(path).notify
    assert found is not None and found.webhooks[0].classes == ("held",)
    assert found.deployment == path.parent.name and found.state.name == "notify-cursor.json"
    for table, message in (
        ('[notify]\nnope = 1\n[[notify.webhooks]]\nurl = "https://x.example/"\n', "has no nope"),
        ("[notify]\n", "names no"),
        ('[notify]\n[[notify.webhooks]]\nurl = "https://x.example/"\n', "key is required"),
        (
            '[notify]\nkey = "k"\n[[notify.webhooks]]\nurl = "https://a.example/"\nextra = 1\n',
            "has no extra",
        ),
        (
            '[notify]\nkey = "k"\n[[notify.webhooks]]\nname = "a"\nurl = "https://a.example/"\n'
            '[[notify.webhooks]]\nname = "a"\nurl = "https://b.example/"\n',
            "named twice",
        ),
        ("notify = 3\n", "is a table"),
    ):
        path.write_text(
            table + original if table.startswith("notify =") else original + "\n" + table
        )
        with pytest.raises(ConfigError, match=message):
            load_config(path)
    path.write_text(original)


class _Receiver(http.server.BaseHTTPRequestHandler):
    got: ClassVar[list[tuple[dict[str, str], bytes]]] = []

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _Receiver.got.append((dict(self.headers), body))
        self.send_response(204)
        self.end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        return


def test_the_cli_delivers_and_tests_a_webhook(
    sandbox: SandboxDeployment, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Receiver)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        root = sandbox.built().world.root
        generate_key(root / "keys" / "notify.key")
        path = _deployment(sandbox)
        _with_notify(
            path,
            f'[notify]\nkey = "keys/notify.key"\nstate = "{tmp_path / "cur.json"}"\n'
            f'[[notify.webhooks]]\nname = "local"\nurl = "http://127.0.0.1:{server.server_port}/"\n',
        )
        assert cli.main(["--config", str(path), "notify", "--test", "local"]) == 0
        assert "answered 204" in capsys.readouterr().out
        assert cli.main(["--config", str(path), "notify", "--once"]) == 0  # sets the cursor
        sandbox.call({"preset": "held"})
        assert cli.main(["--config", str(path), "notify", "--once"]) == 0
        assert "1 notice(s) delivered" in capsys.readouterr().out
        events = [json.loads(b)["event"] for _, b in _Receiver.got]
        assert events == ["notice.test", "ticket.raised"]
        assert cli.main(["--config", str(path), "notify", "--test", "nope"]) == 2
    finally:
        server.shutdown()
        server.server_close()


class _Redirecting(http.server.BaseHTTPRequestHandler):
    """Answers every request with the status in its path, pointing elsewhere."""

    target: ClassVar[str] = ""
    hits: ClassVar[list[tuple[str, str]]] = []

    def _answer(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        _Redirecting.hits.append((self.command, self.path))
        if self.path.startswith("/elsewhere"):
            self.send_response(204)
        else:
            self.send_response(int(self.path.strip("/")))
            self.send_header("Location", _Redirecting.target)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        self._answer()

    def do_GET(self) -> None:
        self._answer()

    def log_message(self, fmt: str, *args: object) -> None:
        return


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_a_redirect_is_a_failed_delivery_and_is_not_followed(status: int) -> None:
    """B9 (1.11.0): before, urllib followed a 301/302/303 as a GET without the
    notice, to wherever the webhook pointed, and its 2xx counted as delivered."""
    from fiduciary.notify import _send

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Redirecting)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        _Redirecting.target, _Redirecting.hits = f"{base}/elsewhere", []
        answer = _send(f"{base}/{status}", b"{}", {"Content-Type": "application/json"}, 5.0)
        assert answer == status
        assert _Redirecting.hits == [("POST", f"/{status}")]  # nothing went to /elsewhere
    finally:
        server.shutdown()
        server.server_close()


def test_a_redirected_notice_stays_due(sandbox: SandboxDeployment, tmp_path: Path) -> None:
    """End to end, on the notifier's own sender: a webhook that answers 302
    delivers nothing, and the notice is still due."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Redirecting)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        _Redirecting.target, _Redirecting.hits = f"{base}/elsewhere", []
        generate_key(tmp_path / "notify.key")
        config = _config(tmp_path, Webhook("moved", f"{base}/302", events=("ticket.raised",)))
        notifier = for_store(config, sandbox.built().world.store)
        notifier.turn()
        sandbox.call({"preset": "held"})
        assert notifier.turn() == {"moved": 0}
        assert _Redirecting.hits == [("POST", "/302")]
    finally:
        server.shutdown()
        server.server_close()

"""Notices: tell the principals when a ticket waits for them, and when it is decided.

A notice is a pure function of one log record: a ticket raised (its class, its options
and their quorums, when it expires), a warrant formed, its execution applied, refused or
abandoned, a ticket declined or lapsed. It carries ids, digests and a link to the ticket
in ``fiduciary console``, never a payload, a row or a statement: whoever reads it opens
the console, where their own access decides what they see.

Each ``[[notify.webhooks]]`` entry names a URL and a format:

- ``standard``: the notice as canonical JSON, signed under the Standard Webhooks
  convention with an asymmetric key (``webhook-signature: v1a,<base64 Ed25519>`` over
  ``<webhook-id>.<webhook-timestamp>.<body>``), by the deployment's notification key
  (``[notify] key``, a ``fiduciary keygen`` file). A receiver verifies it with the key's
  public half, which the deployment publishes; no secret is shared.
- ``slack``: a Slack incoming webhook's message, its blocks and a button to the ticket.
- ``pagerduty``: a PagerDuty Events API v2 event. A raised ticket triggers an incident
  keyed by the ticket (``dedup_key``); its warrant executed, or the ticket declined or
  lapsed, resolves it. The routing key is read from the environment (``routing_key_env``).

Delivery is at least once, in log order, per webhook. A notice's id is the record's place
and hash and the webhook's name, so a retry is the same notice and a receiver keeps it
once (``webhook-id``, PagerDuty's ``dedup_key``). The webhook's cursor (``[notify]
state``, a JSON file) moves past a record only once its notice was answered ``2xx``; a
failure is retried, with backoff, before anything after it.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from fiduciary import fidu1
from fiduciary.documents import Record, Ticket, loads

__all__ = [
    "EVENTS",
    "FORMATS",
    "Notice",
    "Notifier",
    "NotifyConfig",
    "Webhook",
    "for_store",
    "notice_of",
    "request_for",
]

_log = logging.getLogger("fiduciary.notify")

EVENTS: Final = (
    "ticket.raised",
    "warrant.formed",
    "execution.applied",
    "execution.refused",
    "execution.abandoned",
    "ticket.declined",
    "ticket.lapsed",
)
FORMATS: Final = ("standard", "slack", "pagerduty")
SCHEMA: Final = "fiduciary-notice/1"
_RESOLVING: Final = frozenset(
    {
        "execution.applied",
        "execution.refused",
        "execution.abandoned",
        "ticket.declined",
        "ticket.lapsed",
    }
)
_LOOPBACK: Final = ("127.0.0.1", "localhost", "::1")


@dataclass(frozen=True, slots=True)
class Webhook:
    """One ``[[notify.webhooks]]`` entry."""

    name: str
    url: str
    format: str = "standard"
    events: tuple[str, ...] = EVENTS
    classes: tuple[str, ...] = ()
    """The ticket classes notified; empty: every class."""
    routing_key_env: str | None = None
    timeout: float = 10.0

    def __post_init__(self) -> None:
        if self.format not in FORMATS:
            raise ValueError(f"webhook {self.name}: format is one of {', '.join(FORMATS)}")
        unknown = sorted(set(self.events) - set(EVENTS))
        if unknown:
            raise ValueError(f"webhook {self.name}: no such event {', '.join(unknown)}")
        parsed = urllib.parse.urlsplit(self.url)
        loopback = parsed.hostname in _LOOPBACK
        if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
            raise ValueError(f"webhook {self.name}: an https URL (http only on loopback)")
        if self.format == "pagerduty" and not self.routing_key_env:
            raise ValueError(f"webhook {self.name}: pagerduty reads its routing_key_env")


@dataclass(frozen=True, slots=True)
class NotifyConfig:
    """``[notify]``: where notices go, and how they are signed and linked."""

    webhooks: tuple[Webhook, ...]
    state: Path
    key: Path | None = None
    console: str | None = None
    """The console's address, for each notice's link to its ticket."""
    deployment: str = "deployment"
    """This deployment's id in the console (its directory's name, by default)."""


@dataclass(frozen=True, slots=True)
class Notice:
    """One notice, before it is shaped for a webhook."""

    id: str
    event: str
    body: dict[str, Any]

    def canonical(self) -> bytes:
        return fidu1.canonical(self.body)


def notice_of(
    record: Record,
    *,
    deployment: str,
    console: str | None,
    ticket: Ticket | None = None,
) -> dict[str, Any] | None:
    """The notice ``record`` makes, or ``None`` for a record no one is told of.

    :param ticket: The ticket a ``ticket.raised`` names, for its options and expiry.
    """
    kind = record.kind
    if kind not in EVENTS:
        return None
    body = record.content
    ticket_id = str(body.get("ticket_id") or "")
    found: dict[str, Any] = {
        "v": SCHEMA,
        "event": kind,
        "deployment": deployment,
        "log": {"seq": record.seq, "hash": record.hash},
        "issued_at": fidu1.format_instant(record.issued_at),
        "mandate_id": body.get("mandate_id"),
        "ticket_id": ticket_id or None,
    }
    if kind == "ticket.raised":
        found["ticket"] = {
            "class": body.get("class"),
            "digest": body.get("ticket"),
            "options": (
                [{"action": o.resolution.action, "quorum": o.quorum} for o in ticket.options]
                if ticket is not None
                else []
            ),
            "not_after": fidu1.format_instant(ticket.not_after) if ticket is not None else None,
        }
    if "warrant" in body or "warrant_id" in body:
        found["warrant"] = {
            "id": body.get("warrant_id"),
            "digest": body.get("warrant"),
            "action": body.get("action"),
        }
    if console and ticket_id:
        query = urllib.parse.urlencode({"d": deployment, "t": ticket_id})
        found["link"] = f"{console.rstrip('/')}/#/desk?{query}"
    return {k: v for k, v in found.items() if v is not None}


def _identity(record: Record, webhook: Webhook) -> str:
    digest = hashlib.sha256(f"{record.seq}:{record.hash}:{webhook.name}".encode()).hexdigest()
    return f"fn_{digest[:32]}"


def _headline(notice: Mapping[str, Any]) -> str:
    event, ticket = notice["event"], notice.get("ticket") or {}
    short = str(notice.get("ticket_id") or "")[:8]
    if event == "ticket.raised":
        options = ", ".join(
            f"{o['action']} ({o['quorum']} to sign)" for o in ticket.get("options", [])
        )
        return f"A {ticket.get('class', '')} ticket waits for its quorum: {short}" + (
            f" · {options}" if options else ""
        )
    if event == "warrant.formed":
        return f"Warrant formed for ticket {short}: {notice['warrant'].get('action')}"
    if event.startswith("execution."):
        return f"Warrant {event.split('.')[1]} for ticket {short}"
    return f"Ticket {short} {event.split('.')[1]}"


def request_for(
    notice: Notice,
    webhook: Webhook,
    *,
    signer: Any = None,
    now: Callable[[], float] = time.time,
    environ: Mapping[str, str] = os.environ,
) -> tuple[bytes, dict[str, str]]:
    """The body and headers ``webhook`` is sent for ``notice``.

    :raises ValueError: For a ``standard`` webhook with no signer, or a
        ``pagerduty`` one whose routing key is not in the environment.
    """
    headers = {"Content-Type": "application/json", "User-Agent": "fiduciary-notify"}
    body = notice.body
    if webhook.format == "standard":
        if signer is None:
            raise ValueError(
                f"webhook {webhook.name}: a standard notice is signed: set [notify] key"
            )
        data = notice.canonical()
        stamp = str(int(now()))
        message = f"{notice.id}.{stamp}.".encode() + data
        signature = base64.b64encode(signer.sign(message)).decode()
        headers.update(
            {
                "webhook-id": notice.id,
                "webhook-timestamp": stamp,
                "webhook-signature": f"v1a,{signature}",
            }
        )
        return data, headers
    headline = _headline(body)
    if webhook.format == "slack":
        fields = [
            {"type": "mrkdwn", "text": f"*Event*\n`{body['event']}`"},
            {"type": "mrkdwn", "text": f"*Deployment*\n{body['deployment']}"},
        ]
        if body.get("ticket_id"):
            fields.append({"type": "mrkdwn", "text": f"*Ticket*\n`{body['ticket_id']}`"})
        expiry = (body.get("ticket") or {}).get("not_after")
        if expiry:
            fields.append({"type": "mrkdwn", "text": f"*Expires*\n{expiry}"})
        blocks: list[dict[str, Any]] = [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*{headline}*"}},
            {"type": "section", "fields": fields},
        ]
        if body.get("link"):
            blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Open in the console"},
                            "url": body["link"],
                        }
                    ],
                }
            )
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"notice `{notice.id}` · log #{body['log']['seq']}"}
                ],
            }
        )
        return json.dumps({"text": headline, "blocks": blocks}, sort_keys=True).encode(), headers
    routing = environ.get(webhook.routing_key_env or "", "")
    if not routing:
        raise ValueError(f"webhook {webhook.name}: {webhook.routing_key_env} is not set")
    event: dict[str, Any] = {
        "routing_key": routing,
        "event_action": "resolve" if body["event"] in _RESOLVING else "trigger",
        "dedup_key": f"fiduciary:{body['deployment']}:{body.get('ticket_id') or notice.id}",
        "payload": {
            "summary": headline[:1024],
            "source": f"fiduciary:{body['deployment']}",
            "severity": "warning",
            "component": "approvals",
            "custom_details": body,
        },
    }
    if body.get("link"):
        event["links"] = [{"href": body["link"], "text": "Open in the console"}]
    if body["event"] == "warrant.formed":
        event["event_action"] = "acknowledge"
    return json.dumps(event, sort_keys=True).encode(), headers


@dataclass
class _Cursor:
    path: Path
    seen: dict[str, int] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> _Cursor:
        try:
            found = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls(path)
        except (OSError, ValueError) as exc:
            raise ValueError(f"{path}: the notify cursor is unreadable: {exc}") from exc
        return cls(path, {str(k): int(v) for k, v in dict(found).items()})

    def save(self) -> None:
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(self.seen, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)


Send = Callable[[str, bytes, Mapping[str, str], float], int]
"""``(url, body, headers, timeout) -> HTTP status``."""


def _send(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> int:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")  # noqa: S310 - https, or loopback http, checked when configured
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:  # noqa: S310
            return int(reply.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


class Notifier:
    """Delivers each webhook's notices, in log order, from its cursor.

    :param records: The log's records after a sequence number: ``records(after=seq)``.
    :param document: A document by digest, for the tickets the notices describe.
    :param first: Where a webhook with no cursor starts: the log's head when the
        notifier first runs (default), so a deployment's past is not announced.
    """

    def __init__(
        self,
        config: NotifyConfig,
        records: Callable[[int], Iterable[Record]],
        document: Callable[[str], bytes | None],
        *,
        head: Callable[[], int],
        signer: Any = None,
        send: Send = _send,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self._records = records
        self._document = document
        self._head = head
        self._signer = signer
        self._send = send
        self._now = now
        self._cursor = _Cursor.load(config.state)
        self._waiting: dict[str, tuple[int, float]] = {}

    def _ticket(self, digest: object) -> Ticket | None:
        if not isinstance(digest, str):
            return None
        data = self._document(digest)
        if data is None:
            return None
        with contextlib.suppress(Exception):
            found = loads(data, signed=True)
            if isinstance(found, Ticket):
                return found
        return None

    def _wanted(self, webhook: Webhook, record: Record) -> bool:
        if record.kind not in webhook.events:
            return False
        if webhook.classes and record.kind == "ticket.raised":
            return str(record.content.get("class")) in webhook.classes
        return True

    def notices(self, webhook: Webhook, after: int) -> list[tuple[Record, Notice]]:
        out = []
        for record in self._records(after):
            if not self._wanted(webhook, record):
                continue
            ticket = (
                self._ticket(record.content.get("ticket"))
                if record.kind == "ticket.raised"
                else None
            )
            body = notice_of(
                record,
                deployment=self.config.deployment,
                console=self.config.console,
                ticket=ticket,
            )
            if body is not None:
                body["id"] = _identity(record, webhook)
                out.append((record, Notice(body["id"], record.kind, body)))
        return out

    def turn(self) -> dict[str, int]:
        """Deliver what is due on each webhook; how many each delivered."""
        delivered: dict[str, int] = {}
        for webhook in self.config.webhooks:
            delivered[webhook.name] = self._deliver(webhook)
        return delivered

    def _deliver(self, webhook: Webhook) -> int:
        if webhook.name not in self._cursor.seen:
            self._cursor.seen[webhook.name] = self._head()
            self._cursor.save()
        waiting = self._waiting.get(webhook.name)
        if waiting is not None and self._now() < waiting[1]:
            return 0
        count = 0
        last_seq = self._cursor.seen[webhook.name]
        for record, notice in self.notices(webhook, last_seq):
            try:
                body, headers = request_for(notice, webhook, signer=self._signer, now=self._now)
                status = self._send(webhook.url, body, headers, webhook.timeout)
            except (OSError, ValueError) as exc:
                _log.warning("notify %s: %s not delivered: %s", webhook.name, notice.id, exc)
                status = 0
            if not 200 <= status < 300:
                tries = (waiting[0] if waiting else 0) + 1
                self._waiting[webhook.name] = (tries, self._now() + min(300.0, 2.0**tries))
                _log.warning("notify %s: %s answered %s; retried", webhook.name, notice.id, status)
                return count
            self._waiting.pop(webhook.name, None)
            last_seq = record.seq
            self._cursor.seen[webhook.name] = last_seq
            self._cursor.save()
            count += 1
        # Nothing due past the records read: the cursor follows the log.
        head = self._head()
        if head > self._cursor.seen[webhook.name] and not self._waiting.get(webhook.name):
            self._cursor.seen[webhook.name] = head
            self._cursor.save()
        return count


def verify_standard(
    body: bytes, headers: Mapping[str, str], public_key: str, *, tolerance: float = 300.0
) -> dict[str, Any]:
    """What a receiver does with a ``standard`` notice: check its signature under
    the deployment's notification key (``ed25519:<hex>``) and its timestamp, and
    read it.

    :raises ValueError: On a notice that is not signed by that key, or is stale.
    """
    lowered = {k.lower(): v for k, v in headers.items()}
    identity, stamp = lowered.get("webhook-id", ""), lowered.get("webhook-timestamp", "")
    if not identity or not stamp.isdigit():
        raise ValueError("no webhook-id or webhook-timestamp")
    if abs(time.time() - int(stamp)) > tolerance:
        raise ValueError("the notice's timestamp is outside the tolerance")
    verifier = fidu1.key_of(public_key)
    message = f"{identity}.{stamp}.".encode() + body
    for part in lowered.get("webhook-signature", "").split():
        version, _, value = part.partition(",")
        if version == "v1a":
            with contextlib.suppress(ValueError):
                if verifier.verify(message, base64.b64decode(value)):
                    found: dict[str, Any] = json.loads(body)
                    return found
    raise ValueError("no v1a signature verifies under the notification key")


def parse(
    table: Mapping[str, Any], *, base: Path, place: Callable[[object, str], Path]
) -> NotifyConfig:
    """``[notify]`` read from ``fiduciary.toml``.

    :raises ValueError: On anything it may not say.
    """
    allowed = {"webhooks", "state", "key", "console", "deployment"}
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ValueError(f"[notify] has no {', '.join(unknown)}")
    hooks: list[Webhook] = []
    names: set[str] = set()
    for i, raw in enumerate(table.get("webhooks") or ()):
        if not isinstance(raw, Mapping):
            raise ValueError(f"[[notify.webhooks]] {i} is a table")
        keys = {"name", "url", "format", "events", "classes", "routing_key_env", "timeout_seconds"}
        extra = sorted(set(raw) - keys)
        if extra:
            raise ValueError(f"[[notify.webhooks]] {i} has no {', '.join(extra)}")
        name = str(raw.get("name") or f"webhook-{i + 1}")
        if name in names:
            raise ValueError(f"[[notify.webhooks]] {name!r} is named twice")
        names.add(name)
        hooks.append(
            Webhook(
                name=name,
                url=str(raw.get("url") or ""),
                format=str(raw.get("format") or "standard"),
                events=tuple(raw.get("events") or EVENTS),
                classes=tuple(raw.get("classes") or ()),
                routing_key_env=raw.get("routing_key_env"),
                timeout=float(raw.get("timeout_seconds", 10.0)),
            )
        )
    if not hooks:
        raise ValueError("[notify] names no [[notify.webhooks]]")
    key = place(table["key"], "[notify] key") if "key" in table else None
    if key is None and any(h.format == "standard" for h in hooks):
        raise ValueError("[notify] key is required: a standard notice is signed")
    state = place(table.get("state", "notify-cursor.json"), "[notify] state")
    return NotifyConfig(
        webhooks=tuple(hooks),
        state=state,
        key=key,
        console=str(table["console"]) if "console" in table else None,
        deployment=str(table.get("deployment") or base.name),
    )


def for_store(config: NotifyConfig, store: Any, *, send: Send = _send) -> Notifier:
    """A notifier over a store's log, signing with ``config.key`` (``fiduciary
    notify``, and the service's ``notifier`` part)."""
    from fiduciary.keys import load_key
    from fiduciary.log import LOG_ID

    def records(after: int) -> list[Record]:
        return [Record.loads(r.data) for r in store.records(LOG_ID, after=after)]

    def document(digest: str) -> bytes | None:
        found = store.document(digest)
        return None if found is None else bytes(found[1])

    return Notifier(
        config,
        records,
        document,
        head=lambda: int(store.head(LOG_ID)[0]),
        signer=load_key(config.key) if config.key is not None else None,
        send=send,
    )

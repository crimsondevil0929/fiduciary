"""Outbound data control (1.19.0): what leaves a session, judged before it leaves.

A read tool is not only a read. A fetch carries its URL out, a search carries its query, an
email tool carries its recipients and its body. This module holds what the bridge and the
offline verifier share, with no I/O of its own:

- **where a call sends**, from the arguments a binding's ``egress`` declares
  (:func:`destinations`): a URL's host, an email's domain, a repository's ``owner/name``;
- **credentials**, found by their published formats and by entropy (:func:`secrets_in`);
- **values**: the distinctive tokens of what a session read (:func:`tracked`), and every
  token an outbound argument carries, as plain text, URL-decoded, base64 or hex
  (:func:`carried`), each kept only as a keyed digest (:class:`Tracker`);
- **the rules** (:class:`Outbound`, from a mandate's ``provenance``) and the judgment of one
  send or one read against them (:func:`judge_send`, :func:`judge_read`).

What this catches is an exact value leaving, whole or encoded. It does not catch a
paraphrase, a summary, a value split across calls, or a value shorter than the minimum
length: a model that rewrites a secret in its own words is not seen.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import math
import re
import secrets
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from email.utils import getaddresses
from typing import Any, Final
from urllib.parse import unquote, unquote_plus, urlsplit

__all__ = [
    "DESTINATION_KINDS",
    "MIN_LENGTH",
    "Outbound",
    "Tracker",
    "carried",
    "destinations",
    "egress_of",
    "is_sensitive",
    "judge_read",
    "judge_send",
    "matches",
    "records_in",
    "secrets_in",
    "size_of",
    "strings_in",
    "tracked",
]

DESTINATION_KINDS: Final = ("email", "repository", "url")
"""What an argument may name a destination as."""

MIN_LENGTH: Final = 8
"""The shortest value tracked: shorter ones (a price, a short id) are too common to tell
apart from what an agent would send anyway."""

MAX_LENGTH: Final = 256
"""The longest token tracked or looked for."""

PER_READ: Final = 512
"""At most this many values are tracked from one read."""

PER_SESSION: Final = 16384
"""At most this many values are tracked in one session; past it, nothing more is."""

UNREADABLE: Final = "?"
"""A destination that could not be read: never one a rule allows."""

URL_FIELDS: Final = frozenset(
    {
        "url",
        "uri",
        "href",
        "link",
        "endpoint",
        "webhook",
        "webhook_url",
        "callback",
        "callback_url",
        "target_url",
        "image_url",
        "redirect_uri",
        "redirect_url",
    }
)
EMAIL_FIELDS: Final = frozenset(
    {
        "to",
        "cc",
        "bcc",
        "recipient",
        "recipients",
        "email",
        "emails",
        "to_email",
        "to_address",
        "reply_to",
    }
)
REPOSITORY_FIELDS: Final = frozenset(
    {"repo", "repository", "full_name", "repo_name", "repository_name"}
)

_HOST: Final = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*"
)
_REPO_PART: Final = re.compile(r"[a-z0-9_.-]{1,100}")
_LABEL_SENSITIVE: Final = ("secret", "private")


def is_sensitive(label: str) -> bool:
    """Whether ``label`` marks sensitive data: ``secret``, ``private`` or a partition's.
    The same as interlock's :func:`interlock.invariants.is_sensitive` (1.11.0)."""
    return label in _LABEL_SENSITIVE or label.startswith("partition:")


# -- where a call sends -----------------------------------------------------------------


def egress_of(schema: Mapping[str, Any]) -> dict[str, str]:
    """The arguments of an input ``schema`` that name a destination, by their names: the
    top-level properties called ``url``, ``to``, ``repo`` and the like."""
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return {}
    found: dict[str, str] = {}
    for name in sorted(properties):
        key = str(name).lower()
        if key in URL_FIELDS:
            found[str(name)] = "url"
        elif key in EMAIL_FIELDS:
            found[str(name)] = "email"
        elif key in REPOSITORY_FIELDS:
            found[str(name)] = "repository"
    return found


def destinations(egress: Mapping[str, str], arguments: Mapping[str, Any]) -> dict[str, list[str]]:
    """Each destination ``arguments`` name through the arguments ``egress`` declares, by
    kind, normalized and sorted: a host, an email's domain, a repository's ``owner/name``
    (lower case). A value that cannot be read is :data:`UNREADABLE`."""
    found: dict[str, set[str]] = {}
    for name, kind in egress.items():
        if name not in arguments:
            continue
        for value in _values(arguments[name]):
            if kind == "url":
                found.setdefault(kind, set()).add(_host(value))
            elif kind == "email":
                found.setdefault(kind, set()).update(_domains(value))
            elif kind == "repository":
                found.setdefault(kind, set()).add(_repository(value, arguments.get("owner")))
    return {kind: sorted(values) for kind, values in sorted(found.items()) if values}


def _values(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list | tuple):
        for item in value:
            if isinstance(item, str):
                yield item


def _host(value: str) -> str:
    text = value.strip()
    if "://" not in text:
        text = "http://" + text
    try:
        host = urlsplit(text).hostname or ""
    except ValueError:
        return UNREADABLE
    host = host.rstrip(".").lower()
    return host if _HOST.fullmatch(host) else UNREADABLE


def _domains(value: str) -> set[str]:
    found = set()
    for _, address in getaddresses([value]):
        _, at, domain = address.rpartition("@")
        domain = domain.strip().rstrip(".").lower()
        found.add(domain if at and _HOST.fullmatch(domain) else UNREADABLE)
    return found or {UNREADABLE}


def _repository(value: str, owner: object) -> str:
    text = value.strip().lower().removesuffix(".git")
    if "github.com/" in text:
        text = text.split("github.com/", 1)[1]
    if "/" not in text and isinstance(owner, str):
        text = f"{owner.strip().lower()}/{text}"
    parts = text.strip("/").split("/")
    if len(parts) == 2 and all(_REPO_PART.fullmatch(p) for p in parts):
        return "/".join(parts)
    return UNREADABLE


def matches(patterns: Sequence[str], value: str) -> bool:
    """Whether ``value`` is one ``patterns`` admit: ``*`` admits any; ``*.example.com``
    admits every name under ``example.com`` (not itself); ``acme/*`` every repository of
    ``acme``; anything else only itself. :data:`UNREADABLE` is admitted by none."""
    if value == UNREADABLE:
        return False
    for pattern in patterns:
        if pattern == "*" or pattern == value:
            return True
        if pattern.startswith("*.") and value.endswith(pattern[1:]):
            return True
        if pattern.endswith("/*") and value.startswith(pattern[:-1]) and "/" in value:
            return True
    return False


# -- credentials --------------------------------------------------------------------------

_PATTERNS: Final = tuple(
    re.compile(p)
    for p in (
        r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA)[0-9A-Z]{16}\b",  # AWS access key id
        r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b",  # GitHub token
        r"\bgithub_pat_[A-Za-z0-9_]{50,255}\b",  # GitHub fine-grained token
        r"\bglpat-[A-Za-z0-9_-]{20,}\b",  # GitLab token
        r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b",  # Slack token
        r"\b(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{16,}\b",  # Stripe key
        r"\bsk-ant-[A-Za-z0-9_-]{20,}\b",  # Anthropic key
        r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b",  # OpenAI key
        r"\bAIza[0-9A-Za-z_-]{35}\b",  # Google API key
        r"\bSG\.[A-Za-z0-9_-]{16,32}\.[A-Za-z0-9_-]{16,64}\b",  # SendGrid key
        r"\bnpm_[A-Za-z0-9]{36}\b",  # npm token
        r"\bpypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{50,}",  # PyPI token
        r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",  # JWT
        r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----",  # a private key block
    )
)
_PASSWORD_URL: Final = re.compile(r"\b[a-z][a-z0-9+.-]{1,20}://[^/\s:@]{1,128}:([^@\s/]{6,256})@")
_ASSIGNED: Final = re.compile(
    r"(?i)\b(?:api[_-]?key|secret|token|password|passwd|private[_-]?key|access[_-]?key)"
    r"[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9_\-+/=.]{16,256})"
)
_BARE: Final = re.compile(r"[A-Za-z0-9+/_-]{32,256}")


def _entropy(text: str) -> float:
    counts: dict[str, int] = {}
    for char in text:
        counts[char] = counts.get(char, 0) + 1
    return -sum((n / len(text)) * math.log2(n / len(text)) for n in counts.values())


def _random_looking(token: str, threshold: float) -> bool:
    return (
        any(c.isupper() for c in token)
        and any(c.islower() for c in token)
        and any(c.isdigit() for c in token)
        and _entropy(token) >= threshold
    )


def secrets_in(text: str) -> list[str]:
    """Every credential in ``text``: by a provider's published format, a password in a
    connection string, a value assigned to a name like ``api_key``, or a long random-looking
    token. Sorted, each once. A hash in hex (four bits a character at most) and a UUID are
    not random-looking enough to count."""
    found: set[str] = set()
    for pattern in _PATTERNS:
        found.update(m.group(0) for m in pattern.finditer(text))
    found.update(m.group(1) for m in _PASSWORD_URL.finditer(text))
    found.update(m.group(1) for m in _ASSIGNED.finditer(text) if _entropy(m.group(1)) >= 3.0)
    found.update(m.group(0) for m in _BARE.finditer(text) if _random_looking(m.group(0), 4.5))
    return sorted(found)


# -- values ---------------------------------------------------------------------------------

_SPLIT: Final = re.compile(r"[\s\"'<>&?#,;|()\[\]{}\\]+")
_FINE: Final = re.compile(r"[A-Za-z0-9_.@+-]+")
_BASE64: Final = re.compile(r"[A-Za-z0-9+/_-]{12,}={0,2}")
_HEX: Final = re.compile(r"(?:[0-9a-fA-F]{2}){8,}")


def strings_in(value: Any, limit: int = 4096) -> Iterator[str]:
    """The strings ``value`` holds (a JSON value), keys left out, at most ``limit``."""
    stack = [value]
    seen = 0
    while stack and seen < limit:
        item = stack.pop()
        if isinstance(item, str):
            seen += 1
            yield item
        elif isinstance(item, Mapping):
            stack.extend(item[k] for k in sorted(item, key=str, reverse=True))
        elif isinstance(item, list | tuple):
            stack.extend(reversed(item))


def _tokens(text: str) -> set[str]:
    found: set[str] = set()
    for part in _SPLIT.split(text):
        if not part:
            continue
        found.add(part)
        if "=" in part:
            found.add(part.split("=", 1)[1])
        if ":" in part and "://" not in part:
            found.add(part.split(":", 1)[1])
        found.update(_FINE.findall(part))
    return {t for t in found if MIN_LENGTH <= len(t) <= MAX_LENGTH}


def _distinctive(token: str) -> bool:
    """A token unlikely to be an ordinary word: it has a digit or an ``@``, an upper-case
    letter after its first, or twenty characters or more."""
    return (
        any(c.isdigit() for c in token)
        or "@" in token
        or any(c.isupper() for c in token[1:])
        or len(token) >= 20
    )


def tracked(value: Any) -> list[str]:
    """The values worth tracking in what a read returned: each distinctive token of its
    strings, and each whole string short enough. Sorted, at most :data:`PER_READ`."""
    found: set[str] = set()
    for text in strings_in(value):
        if MIN_LENGTH <= len(text) <= MAX_LENGTH and _distinctive(text):
            found.add(text)
        found.update(t for t in _tokens(text) if _distinctive(t))
    return sorted(found)[:PER_READ]


def _decoded(token: str) -> Iterator[str]:
    """What ``token`` says as base64 or hex, when it reads as text."""
    if _HEX.fullmatch(token):
        try:
            yield bytes.fromhex(token).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            pass
    if _BASE64.fullmatch(token):
        padded = token + "=" * (-len(token) % 4)
        for decode in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                raw = decode(padded.encode("ascii"))
                text = raw.decode("utf-8")
            except (binascii.Error, ValueError, UnicodeDecodeError):
                continue
            if text and sum(c.isprintable() or c.isspace() for c in text) >= 0.9 * len(text):
                yield text


def carried(arguments: Any) -> set[str]:
    """Every token ``arguments`` carry out: in plain text, URL-decoded, and decoded once
    more from base64 or hex."""
    found: set[str] = set()
    for text in strings_in(arguments):
        for variant in {text, unquote(text), unquote_plus(text)}:
            tokens = _tokens(variant)
            found.update(tokens)
            for token in [*tokens, variant]:
                for decoded in _decoded(token.strip()):
                    found.update(_tokens(decoded))
                    found.update(_tokens(unquote(decoded)))
    return found


@dataclass
class Tracker:
    """The values one session read, as keyed digests, by label. The key is the session's
    own, kept in memory and never recorded: a digest in a record can be matched against
    another record of the same session, and against nothing else."""

    key: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    by_digest: dict[str, set[str]] = field(default_factory=dict)

    def digest(self, value: str) -> str:
        return hmac.new(self.key, value.encode("utf-8"), hashlib.sha256).hexdigest()[:32]

    def track(self, label: str, values: Iterable[str]) -> list[str]:
        """Track ``values`` under ``label``; the digests newly tracked, sorted."""
        added: set[str] = set()
        for value in values:
            if len(self.by_digest) >= PER_SESSION:
                break
            digest = self.digest(value)
            labels = self.by_digest.setdefault(digest, set())
            if label not in labels:
                labels.add(label)
                added.add(digest)
        return sorted(added)

    def adopt(self, label: str, digests: Iterable[str]) -> list[str]:
        """Track digests another session made under the same key (:mod:`fiduciary.flow`);
        the ones newly tracked, sorted."""
        added: set[str] = set()
        for digest in digests:
            if len(self.by_digest) >= PER_SESSION:
                break
            labels = self.by_digest.setdefault(digest, set())
            if label not in labels:
                labels.add(label)
                added.add(digest)
        return sorted(added)

    def found(self, tokens: Iterable[str]) -> dict[str, list[str]]:
        """Each tracked digest among ``tokens``, by the label it was tracked under."""
        hits: dict[str, set[str]] = {}
        for token in tokens:
            digest = self.digest(token)
            for label in self.by_digest.get(digest, ()):
                hits.setdefault(label, set()).add(digest)
        return {label: sorted(d) for label, d in sorted(hits.items())}


def records_in(value: Any) -> int:
    """How many records a read returned: the length of the longest list in its structured
    content (an MCP result's ``structuredContent``, or the value itself), else one."""
    structured = value.get("structuredContent", value) if isinstance(value, Mapping) else value
    longest = 0
    stack: list[tuple[Any, int]] = [(structured, 0)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, list | tuple):
            longest = max(longest, len(item))
            if depth < 2:
                stack.extend((x, depth + 1) for x in item[:64])
        elif isinstance(item, Mapping) and depth < 2:
            stack.extend((v, depth + 1) for v in item.values())
    return max(longest, 1)


def size_of(arguments: Mapping[str, Any]) -> int:
    """The bytes a call's arguments carry out: their strings' UTF-8 lengths."""
    return sum(len(text.encode("utf-8")) for text in strings_in(arguments, limit=100000))


# -- the rules --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Outbound:
    """A mandate's rules for what leaves (``provenance``, 1.19.0), with the rule of two and
    the egress rules they sit beside.

    - ``destinations``: after sensitive data, a send may go only to these: ``hosts`` for a
      URL, ``emails`` (domains) for a recipient, and ``repositories`` (the private ones;
      any other counts as public). A kind with no list is not limited.
    - ``values``: an exact value the session read, labeled ``secret`` or ``private``, may not
      leave: a secret by no send but a plan to a sink ``egress`` clears for it; a private
      value only to a destination the lists above admit, or by a plan to a sink cleared for
      it.
    - ``records_read``, ``bytes_sent``: at most this many records read and bytes sent in a
      ``per`` (``turn``, or ``session``).
    """

    hosts: tuple[str, ...] | None = None
    emails: tuple[str, ...] | None = None
    repositories: tuple[str, ...] | None = None
    values: bool = False
    records_read: int | None = None
    bytes_sent: int | None = None
    per: str = "turn"
    rule_of_two: bool = False
    egress: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def allowed(self, kind: str) -> tuple[str, ...] | None:
        return {"url": self.hosts, "email": self.emails, "repository": self.repositories}.get(kind)

    @property
    def counts(self) -> bool:
        """Whether reads are counted (a ``records_read`` budget)."""
        return self.records_read is not None


def judge_send(
    rules: Outbound,
    *,
    plan: bool,
    labels: Sequence[str],
    destinations: Mapping[str, Sequence[str]],
    matched: Mapping[str, Sequence[str]],
    sinks: Sequence[str] = (),
    sent_before: int = 0,
    size: int = 0,
) -> tuple[str, ...]:
    """The rules one send breaks, sorted: ``rule_of_two`` (a passthrough send only: a
    plan's is interlock's gate), ``destination:<kind>``, ``value:<label>``,
    ``budget:bytes_sent``. Empty when it breaks none."""
    held = set(labels)
    sensitive = any(is_sensitive(label) for label in held)
    egress = dict(rules.egress)
    broken: set[str] = set()
    if not plan and rules.rule_of_two and "untrusted" in held and sensitive and destinations:
        broken.add("rule_of_two")
    if sensitive:
        for kind, values in destinations.items():
            allowed = rules.allowed(kind)
            if allowed is not None and not all(matches(allowed, v) for v in values):
                broken.add(f"destination:{kind}")
    if rules.values:
        for label in matched:
            cleared = egress.get(label)
            if plan:
                if label == "secret" and (cleared is None or not set(sinks) <= set(cleared)):
                    broken.add("value:secret")
                elif label != "secret" and cleared is not None and not set(sinks) <= set(cleared):
                    broken.add(f"value:{label}")
            elif label == "secret":
                broken.add("value:secret")
            else:
                listed = [
                    rules.allowed(kind) is not None
                    and all(matches(rules.allowed(kind) or (), v) for v in values)
                    for kind, values in destinations.items()
                ]
                if not listed or not all(listed):
                    broken.add(f"value:{label}")
    if rules.bytes_sent is not None and sent_before + size > rules.bytes_sent:
        broken.add("budget:bytes_sent")
    return tuple(sorted(broken))


def judge_read(rules: Outbound, *, read_before: int, records: int) -> tuple[str, ...]:
    """The rules one read breaks: ``budget:records_read``, or none."""
    if rules.records_read is not None and read_before + records > rules.records_read:
        return ("budget:records_read",)
    return ()

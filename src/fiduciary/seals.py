"""Seals: whose a record is, said so that only its own bundles can tell (§9.2).

A sealed bundle (``fidb/2``) carries one customer's records whole and the rest
of the log as places and hashes. A place and a hash say nothing of whose a
record was, so they cannot show that a withheld record was not the
customer's own. A *sealed log* says so at write time. Each record carries a
seal, two tags, and its ledger anchor repeats them; a bundle carries the keys
its own tags are made with, so its verifier can tell every record that is its
own, withheld or not, and nothing about any other.

**Whose a record is** (:func:`audience`) follows what it concerns:

- ``all``: the charter's records, and approvals of a charter or a suspension;
- ``line`` of lineage *X*: *X*'s versions and their events, its ``amend``
  tickets with what decided and executed them, and approvals of its versions
  and revocations. Every bundle of *X*, of a lineage below it, or of one
  above it, shows them;
- ``lineage`` *X*: *X*'s sessions, and its other tickets with what decided
  and executed them. Bundles of *X* and of the lineages above it show them;
- ``none``: a refused submission that names no subject.

**Keys.** From the deployment's seal secret *S* (``[service] seal_key``):
``key(all) = HMAC(S, "fidb/2 all")``, ``key(X) = HMAC(S, "fidb/2 lineage\\n" X)``
and ``line(X) = HMAC(key(X), "fidb/2 line")``. A bundle of lineage *R* holds
``key(all)``, ``key(X)`` of *R* and each lineage below it, and ``line(A)`` of
each lineage above it: never the key of a lineage beside it, nor the
``lineage`` key of one above it.

**Tags.** ``tag(k) = HMAC(k, "fidb/2 seal\\n" || canonical([log, seq]))``, so a
lineage's tags differ at every place and never repeat. A record's seal is two
of them, sorted:

====================  ========================================================
``all``               ``tag(key(all))`` and a pad
``lineage`` *X*       ``tag(key(X))`` and a pad
``line`` *X*          ``tag(line(X))`` and ``tag(key(P))``, *P* the lineage
                      *X* was issued under (or *X*, for a root)
``none``              two pads
====================  ========================================================

A pad is a tag under a key only *S* derives, so every seal is two tags that
look alike, and a withheld record's says nothing of its kind or its depth.
``tag(key(P))`` on a child's version is how a parent's bundle finds every
lineage below it: a child's first version is a record its parent's key opens.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from fiduciary import fidu1

__all__ = [
    "Audience",
    "BundleKeys",
    "Sealer",
    "audience",
    "line_key",
    "tag",
]

_TAG: Final = b"fidb/2 seal\n"
_ALL: Final = b"fidb/2 all"
_LINEAGE: Final = b"fidb/2 lineage\n"
_LINE: Final = b"fidb/2 line"
_PAD: Final = b"fidb/2 pad\n"
MIN_SECRET: Final = 32
"""The fewest bytes a seal secret holds."""


def _mac(key: bytes, message: bytes) -> bytes:
    return hmac.new(key, message, hashlib.sha256).digest()


def tag(key: bytes, log: str, seq: int) -> str:
    """The tag of ``key`` at record ``seq`` of ``log``, 64 hex."""
    return _mac(key, _TAG + fidu1.canonical([log, seq])).hex()


def line_key(lineage_key: bytes) -> bytes:
    """The ``line`` key of a lineage, from its own key."""
    return _mac(lineage_key, _LINE)


@dataclass(frozen=True, slots=True)
class Audience:
    """Whose a record is: ``scope`` (``all``, ``line``, ``lineage`` or
    ``none``), the lineage it concerns, and, for ``line``, the lineage that
    lineage was issued under (``None`` for a root)."""

    scope: str
    lineage: str | None = None
    parent: str | None = None


class Sealer:
    """Seals records, and gives each bundle its keys, from a seal secret."""

    __slots__ = ("_all", "_pads", "_secret")

    def __init__(self, secret: bytes) -> None:
        if len(secret) < MIN_SECRET:
            raise ValueError(f"a seal secret holds at least {MIN_SECRET} bytes")
        self._secret = secret
        self._all = _mac(secret, _ALL)
        self._pads = (_mac(secret, _PAD + b"0"), _mac(secret, _PAD + b"1"))

    def lineage_key(self, lineage: str) -> bytes:
        return _mac(self._secret, _LINEAGE + lineage.encode("utf-8"))

    def seal(self, log: str, seq: int, whose: Audience) -> tuple[str, str]:
        """The seal of record ``seq`` of ``log``, which is ``whose``."""
        pads = [tag(p, log, seq) for p in self._pads]
        if whose.scope == "all":
            tags = [tag(self._all, log, seq), pads[0]]
        elif whose.scope == "lineage":
            assert whose.lineage is not None
            tags = [tag(self.lineage_key(whose.lineage), log, seq), pads[0]]
        elif whose.scope == "line":
            assert whose.lineage is not None
            own = self.lineage_key(whose.lineage)
            above = self.lineage_key(whose.parent or whose.lineage)
            tags = [tag(line_key(own), log, seq), tag(above, log, seq)]
        else:
            tags = pads
        first, second = sorted(tags)
        return first, second

    def keys(self, lineages: Iterable[str], above: Iterable[str]) -> BundleKeys:
        """The keys of a bundle of ``lineages`` (one lineage and every one
        below it), whose lineage is below each of ``above``."""
        return BundleKeys(
            self._all,
            {x: self.lineage_key(x) for x in lineages},
            {a: line_key(self.lineage_key(a)) for a in above},
        )


@dataclass(frozen=True, slots=True)
class BundleKeys:
    """The keys a sealed bundle carries (``withheld.json``): ``all``, each of
    its lineages' own, and each lineage above them's ``line`` key."""

    all: bytes
    lineages: Mapping[str, bytes]
    lines: Mapping[str, bytes]

    def tags(self, log: str, seq: int) -> dict[str, str]:
        """Each tag these keys make at record ``seq``, to whose it says the
        record is: ``"all"`` or a lineage."""
        found = {tag(self.all, log, seq): "all"}
        for lineage, key in self.lineages.items():
            found[tag(key, log, seq)] = lineage
            found[tag(line_key(key), log, seq)] = lineage
        for lineage, key in self.lines.items():
            found[tag(key, log, seq)] = lineage
        return found

    def opens(self, log: str, seq: int, seal: Sequence[str]) -> str | None:
        """Whose these keys say record ``seq`` is, by its ``seal``; ``None``
        when it is none of theirs."""
        mine = self.tags(log, seq)
        for found in seal:
            if found in mine:
                return mine[found]
        return None

    def to_json(self) -> dict[str, Any]:
        return {
            "all": self.all.hex(),
            "lineages": {k: v.hex() for k, v in sorted(self.lineages.items())},
            "lines": {k: v.hex() for k, v in sorted(self.lines.items())},
        }

    @classmethod
    def from_json(cls, raw: object) -> BundleKeys:
        """:raises ValueError: On anything but ``{all, lineages, lines}`` of hex keys."""
        if not isinstance(raw, dict) or sorted(raw) != ["all", "lineages", "lines"]:
            raise ValueError("the keys are {all, lineages, lines}")

        def key(value: object) -> bytes:
            if not isinstance(value, str) or not fidu1.HASH.fullmatch(value):
                raise ValueError("a key is 64 lowercase hex characters")
            return bytes.fromhex(value)

        def table(value: object) -> dict[str, bytes]:
            if not isinstance(value, dict):
                raise ValueError("lineages and lines map a lineage to its key")
            return {str(k): key(v) for k, v in value.items()}

        return cls(key(raw["all"]), table(raw["lineages"]), table(raw["lines"]))


# -- whose a record is --------------------------------------------------------------


Mandates = Callable[[str], "tuple[str, str | None] | None"]
"""A stored mandate's ``(mandate_id, its parent's mandate_id)`` by its digest,
or ``None``."""


def audience(
    kind: str,
    body: Mapping[str, Any],
    state: Any,
    mandates: Mandates,
    submitted: Callable[[int], bytes | None],
) -> Audience:
    """Whose a record of ``kind`` with ``body`` is, written after ``state``
    (a :class:`fiduciary.store.State`): what the log said before it, the
    mandates the store holds, and the intake submissions."""

    def line(lineage: str, parent: str | None = None) -> Audience:
        if lineage in state.lineages:
            parent = state.parent_lineage(lineage)
        return Audience("line", lineage, parent)

    def ticket(lineage: str, ticket_class: str) -> Audience:
        return line(lineage) if ticket_class == "amend" else Audience("lineage", lineage)

    def subject(subject_type: str, digest: str) -> Audience:
        if subject_type in ("charter", "suspension"):
            return Audience("all")
        if subject_type in ("mandate", "revocation"):
            issued = state.issued.get(digest)
            if issued is not None:
                return line(issued)
            found = mandates(digest) if subject_type == "mandate" else None
            return Audience("none") if found is None else line(*found)
        row = state.tickets.get(state.tickets_by_digest.get(digest, ""))
        return Audience("none") if row is None else ticket(row.mandate_id, row.ticket_class)

    if kind in ("charter.adopted", "principal.suspended"):
        return Audience("all")
    if kind.startswith("mandate."):
        parent = body.get("parent")
        return line(str(body["mandate_id"]), state.issued.get(parent) if parent else None)
    if kind.startswith("session."):
        return Audience("lineage", str(body["mandate_id"]))
    if kind.startswith(("ticket.", "warrant.")):
        return ticket(str(body["mandate_id"]), str(body["class"]))
    if kind.startswith("execution."):
        warrant = state.warrants.get(str(body["warrant"]))
        if warrant is None:
            return Audience("none")
        return ticket(warrant.mandate_id, warrant.ticket_class)
    if kind == "approval.recorded":
        return subject(str(body["subject"]["type"]), str(body["subject"]["digest"]))
    # approval.refused: whose it is, if what was submitted says
    try:
        raw = json.loads(submitted(int(body["intake"])) or b"")
        named = raw["subject"]
        return subject(str(named["type"]), str(named["digest"]))
    except (ValueError, TypeError, KeyError):
        return Audience("none")

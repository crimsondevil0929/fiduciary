"""Sealed maps: a mandate that commits to every tenant's entry and names none.

A vendor's root mandate maps each customer to what it may do: the email
domains invitations to it may go to (``outbound.recipients[].tenant_domains``).
Written out, every customer's bundle would carry every other customer's key
and domains, since a child mandate refines its parent and the bundle holds
both. Sealed, the root holds one hash, the root of a Merkle tree over every
entry, and grants nothing from it; each customer's child mandate *opens* its
own entry, with the salt and the proof that put it under the root, and is
held to it (§5.2, ``refines``). A bundle of one customer's lineage then names
that customer's entry and nothing of the others'.

- **The leaf** of entry ``(key, values)``: SHA-256 of ``fidu1/sealed-leaf\\n``,
  a 32-byte salt, and the canonical JSON of ``[key, values]``. The salt is
  the vendor's, one per entry, so a leaf cannot be confirmed by guessing a
  customer's key and domains.
- **A node**: SHA-256 of ``fidu1/sealed-node\\n`` and its two children, the
  smaller first, so a proof is a list of siblings and names no position.
- **The root** of no entry is the leaf hash of nothing: 64 zeros, refused.

Salts come from a secret the vendor keeps (:func:`salts`), so sealing the
same map twice gives the same root.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from fiduciary import fidu1

__all__ = ["Opening", "Sealed", "opens", "salts", "seal"]

_LEAF: Final = b"fidu1/sealed-leaf\n"
_NODE: Final = b"fidu1/sealed-node\n"


@dataclass(frozen=True, slots=True)
class Opening:
    """What opens one entry under a sealed root: its salt, and the sibling
    hashes from its leaf to the root."""

    salt: str
    proof: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {"salt": self.salt, "proof": list(self.proof)}


@dataclass(frozen=True, slots=True)
class Sealed:
    """A map sealed: its root, and each entry's opening, for its child alone."""

    root: str
    openings: Mapping[str, Opening]


def _leaf(key: str, values: Sequence[str], salt: str) -> bytes:
    body = fidu1.canonical([key, sorted(values)])
    return hashlib.sha256(_LEAF + bytes.fromhex(salt) + body).digest()


def _node(left: bytes, right: bytes) -> bytes:
    low, high = sorted((left, right))
    return hashlib.sha256(_NODE + low + high).digest()


def salts(secret: bytes, keys: Sequence[str]) -> dict[str, str]:
    """Each key's salt, derived from the vendor's ``secret``: HMAC-SHA-256 of
    the key, so one map seals to one root, and only the secret's holder can
    reproduce a salt."""
    return {k: hmac.new(secret, k.encode("utf-8"), hashlib.sha256).hexdigest() for k in keys}


def seal(entries: Mapping[str, Sequence[str]], salt: Mapping[str, str]) -> Sealed:
    """Seal ``entries``, each under its ``salt`` (64 hex).

    :raises ValueError: On no entry, or an entry without its salt.
    """
    if not entries:
        raise ValueError("a sealed map seals at least one entry")
    missing = sorted(set(entries) - set(salt))
    if missing:
        raise ValueError(f"no salt for {', '.join(missing)}")
    keys = sorted(entries)
    level = [_leaf(k, entries[k], salt[k]) for k in keys]
    paths: dict[str, list[str]] = {k: [] for k in keys}
    owners = [[k] for k in keys]
    while len(level) > 1:
        above: list[bytes] = []
        owned: list[list[str]] = []
        for i in range(0, len(level), 2):
            if i + 1 == len(level):  # an odd node is carried up as it is
                above.append(level[i])
                owned.append(owners[i])
                continue
            for k in owners[i]:
                paths[k].append(level[i + 1].hex())
            for k in owners[i + 1]:
                paths[k].append(level[i].hex())
            above.append(_node(level[i], level[i + 1]))
            owned.append(owners[i] + owners[i + 1])
        level, owners = above, owned
    return Sealed(
        root=level[0].hex(),
        openings={k: Opening(salt[k], tuple(paths[k])) for k in keys},
    )


def opens(root: str, key: str, values: Sequence[str], opening: Opening) -> bool:
    """Whether ``opening`` puts the entry ``(key, values)`` under ``root``."""
    try:
        node = _leaf(key, values, opening.salt)
        for sibling in opening.proof:
            node = _node(node, bytes.fromhex(sibling))
    except ValueError:
        return False
    return hmac.compare_digest(node.hex(), root)

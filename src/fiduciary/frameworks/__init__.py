"""Control-framework packs: which controls each verifier check's evidence bears on.

Each pack is a JSON file beside this module, read once:

- ``framework``: the pack's key (``hipaa``), and the file's name;
- ``title`` and ``source``: what the controls are, and where their text comes from;
- ``controls``: every control the pack names, by its own identifier, in order;
- ``evidence``: for each check (``V1`` to ``V12``), the controls its evidence
  supports, a check with none left out;
- ``limits``: for some controls, what the bundle does not show.

A pack's digest is the SHA-256 of its canonical JSON (keys sorted, no
whitespace, UTF-8), so a reader of an attestation can tell which mapping was
applied. A pack decides nothing: the verdict, the exit code and every finding
are the verifier's, whatever the packs say (F9-4).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib.resources import files
from typing import Any, Final

__all__ = ["DISCLAIMER", "ORDER", "Pack", "load", "packs"]

ORDER: Final = (
    "owasp-asi-2026",
    "soc2",
    "eu-ai-act",
    "hipaa",
    "part11",
    "iso27001",
    "nist800_53",
    "sox_itgc",
    "gdpr",
)
"""Every pack this release ships, in the order reports list them."""

DISCLAIMER: Final = (
    "These mappings show which cryptographic evidence in the bundle supports an assessment "
    "of each control. They are not a certification, an audit opinion or a statement of "
    "compliance: an assessor decides whether a control is met."
)

CHECKS: Final = tuple(f"V{n}" for n in range(1, 13))


@dataclass(frozen=True, slots=True)
class Pack:
    framework: str
    title: str
    source: str
    controls: Mapping[str, str]
    evidence: Mapping[str, Sequence[str]]
    limits: Mapping[str, str]
    digest: str

    def summary(self) -> dict[str, Any]:
        covered = {c for ids in self.evidence.values() for c in ids}
        return {
            "framework": self.framework,
            "title": self.title,
            "source": self.source,
            "digest": self.digest,
            "controls": len(self.controls),
            "covered": sum(1 for c in self.controls if c in covered),
            "limits": dict(self.limits),
        }


def _canonical(raw: Any) -> bytes:
    return json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _read(name: str) -> Pack:
    raw = json.loads((files(__name__) / f"{name}.json").read_text(encoding="utf-8"))
    if raw.get("framework") != name:
        raise ValueError(f"{name}.json names framework {raw.get('framework')!r}")
    controls: dict[str, str] = raw["controls"]
    evidence: dict[str, list[str]] = raw["evidence"]
    for check, ids in evidence.items():
        if check not in CHECKS:
            raise ValueError(f"{name}.json maps {check!r}, which is not a check")
        unknown = [c for c in ids if c not in controls]
        if unknown:
            raise ValueError(f"{name}.json maps {check} to unknown controls {unknown}")
    limits: dict[str, str] = raw.get("limits", {})
    if set(limits) - set(controls):
        raise ValueError(f"{name}.json limits name unknown controls")
    return Pack(
        framework=name,
        title=raw["title"],
        source=raw["source"],
        controls=controls,
        evidence={c: tuple(evidence[c]) for c in CHECKS if evidence.get(c)},
        limits=limits,
        digest="sha256:" + hashlib.sha256(_canonical(raw)).hexdigest(),
    )


_PACKS: dict[str, Pack] = {}


def load(name: str) -> Pack:
    """The pack ``name``, read once."""
    if name not in _PACKS:
        if name not in ORDER:
            raise KeyError(f"no control-framework pack {name!r}; there are {', '.join(ORDER)}")
        _PACKS[name] = _read(name)
    return _PACKS[name]


def packs() -> tuple[Pack, ...]:
    """Every pack, in :data:`ORDER`."""
    return tuple(load(name) for name in ORDER)

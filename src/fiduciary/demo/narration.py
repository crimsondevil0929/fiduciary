"""Runtime narration and scenario copy for recorded console runs (1.9.0).

A recorded benchmark stores the raw execution facts: tool calls, measured
footprints, model transcripts, and signed bundles. Around those facts, the
console displays human-readable titles, scenario ledes, and domain metadata.
:func:`retell` overlays the latest copy onto recorded runs, so dashboards
re-rendered via ``fiduciary demo --replay`` reflect current descriptions
without re-running live models. No recorded facts change: tool arguments,
measured database rows, and model outputs remain strictly untouched.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

__all__ = ["retell"]

_DOMAIN_WORDS = ("industry", "title", "nouns")


def _words(key: str) -> tuple[Mapping[str, str], Mapping[str, str], Mapping[str, Any] | None]:
    if key == "fintech":
        from fiduciary.demo import acts

        return acts.TITLES, acts.LEDES, None
    from fiduciary.demo.packs import PACKS, profile

    module = __import__(PACKS[key].acts.__module__, fromlist=["TITLES", "LEDES"])
    return module.TITLES, module.LEDES, profile(PACKS[key])


def retell(key: str, demo: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of the ``demo`` member of a recorded console payload for industry
    ``key``, with today's titles, ledes and industry words. A scenario the code
    no longer has keeps the words it was recorded with."""
    titles, ledes, profile = _words(key)
    told = copy.deepcopy(dict(demo))
    for act in told.get("acts") or ():
        name = act.get("key")
        if name in titles:
            act["title"] = titles[name]
        if name in ledes:
            act["lede"] = ledes[name]
    if profile is not None and isinstance(told.get("domain"), dict):
        told["domain"] = {**told["domain"], **{k: profile[k] for k in _DOMAIN_WORDS}}
    return told

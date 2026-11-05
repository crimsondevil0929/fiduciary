"""Retelling a recorded run (1.9.0): today's titles, ledes and industry words,
and nothing else changed."""

from __future__ import annotations

import copy
import json
from typing import Any

from fiduciary.demo import acts
from fiduciary.demo.narration import retell
from fiduciary.demo.packs import healthcare

RECORDED: dict[str, Any] = {
    "acts": [
        {
            "n": 2,
            "key": "cascade",
            "title": "an old title",
            "lede": "an old lede",
            "calls": [{"tool": "discontinue_order", "feedback": "what the model saw"}],
        },
        {"n": 9, "key": "retired", "title": "kept", "lede": "kept"},
    ],
    "domain": {"key": "healthcare", "title": "old", "tenants": 99, "nouns": {}},
    "model": {"transcript": ["a prompt", "an answer"]},
}


def test_a_recorded_run_gets_todays_words_and_keeps_its_facts() -> None:
    before = copy.deepcopy(RECORDED)
    told = retell("healthcare", RECORDED)
    assert RECORDED == before, "the record passed in is not changed"
    cascade, retired = told["acts"]
    assert cascade["title"] == healthcare.TITLES["cascade"]
    assert cascade["lede"] == healthcare.LEDES["cascade"]
    assert cascade["calls"] == RECORDED["acts"][0]["calls"]
    assert (retired["title"], retired["lede"]) == ("kept", "kept")
    assert told["domain"]["title"] == healthcare.PACK.title and told["domain"]["tenants"] == 99
    assert json.dumps(told["model"]) == json.dumps(RECORDED["model"])


def test_the_fintech_run_is_retold_from_its_own_tables() -> None:
    told = retell("fintech", {"acts": [{"key": "audit", "title": "x", "lede": "y"}]})
    assert told["acts"][0]["title"] == acts.TITLES["audit"]
    assert told["acts"][0]["lede"] == acts.LEDES["audit"]


def test_every_scenario_has_a_title_and_a_lede() -> None:
    from fiduciary.demo.packs import PACKS

    assert set(acts.TITLES) == set(acts.LEDES) == set(acts.ACTS)
    for key, pack in PACKS.items():
        module = __import__(pack.acts.__module__, fromlist=["TITLES", "LEDES"])
        assert set(module.TITLES) == set(module.LEDES), key

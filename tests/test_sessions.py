"""The session records' rules (§4.7, §8.1, F8-8), and verify holding the log
to them, on SQLite and PostgreSQL."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from fiduciary.authority import Decide
from fiduciary.documents import Mandate
from fiduciary.exceptions import AuthorityError
from fiduciary.sqlite import SqliteStore
from fiduciary.store import Store
from tests import bound
from tests import bridge_kit as kit
from tests import examples as e
from tests.conftest import Clock, approve, need_postgres
from tests.test_executor import _verify
from tests.test_tamper import _names
from tests.test_tamper import _verify as _verify_report


@pytest.fixture
def world(store: Store, tmp_path: Path, clock: Clock) -> Iterator[bound.World]:
    yield from kit.world(store, tmp_path, clock)


def _refuses(match: str, decision: Any, *args: Any) -> None:
    with pytest.raises(AuthorityError, match=match):
        decision(*args)


def test_a_session_opens_only_under_a_bound_version_in_force(
    world: bound.World, clock: Clock
) -> None:
    decide: Decide = world.authority.decide
    mandate = kit.mandate(world, data={**kit.mandate_json(world)["data"], "tenants": ["acme"]})
    sid = str(uuid.uuid4())
    _refuses(
        "not issued",
        decide.open_session,
        world.log.state(),
        sid,
        mandate.mandate_id,
        "acme",
        "t",
        clock(),
    )
    world.issue(mandate)
    state = world.log.state()
    _refuses(
        "is issued: a session runs only under a bound",
        decide.open_session,
        state,
        sid,
        mandate.mandate_id,
        "acme",
        "t",
        clock(),
    )
    world.binder.bind(mandate.digest)
    state = world.log.state()
    _refuses("no uuid", decide.open_session, state, "s-1", mandate.mandate_id, "acme", "t", clock())
    _refuses(
        "tenant 'beta' is not one",
        decide.open_session,
        state,
        sid,
        mandate.mandate_id,
        "beta",
        "t",
        clock(),
    )
    _refuses("a tenant", decide.open_session, state, sid, mandate.mandate_id, "", "t", clock())
    late = mandate.not_after + timedelta(seconds=1)
    _refuses(
        "outside its term", decide.open_session, state, sid, mandate.mandate_id, "acme", "t", late
    )
    body = decide.open_session(state, sid, mandate.mandate_id, "acme", "t", clock())
    assert body == {
        "session": sid,
        "mandate": mandate.digest,
        "mandate_id": mandate.mandate_id,
        "version": 1,
        "tenant": "acme",
        "trajectory": "t",
        "scope": bound.SCOPE,
    }
    world.log.append("session.opened", body)
    _refuses(
        "opened already",
        decide.open_session,
        world.log.state(),
        sid,
        mandate.mandate_id,
        "acme",
        "t",
        clock(),
    )

    # Issued and not yet rebound: no session runs under the version before it.
    raw = kit.mandate_json(world)
    raw.update(version=2, prev=mandate.digest)
    v2 = Mandate.from_json(raw, signed=False).signed(e.signer("cfo"))
    world.authority.propose(v2)
    approve(world.store, ("cfo", "head-ops"), "mandate", v2.digest)
    world.authority.process_intake()
    state = world.log.state()
    state_issue = decide.issue(state, v2.digest, clock())
    world.log.append("mandate.issued", state_issue)
    _refuses(
        "version 2 is issued and not yet bound",
        decide.open_session,
        world.log.state(),
        str(uuid.uuid4()),
        mandate.mandate_id,
        "acme",
        "t",
        clock(),
    )


def test_refusals_and_closes_name_only_what_the_session_holds(world: bound.World) -> None:
    world.issue_and_bind(kit.mandate(world))
    fid = kit.fiduciary(world)
    session = fid.session(bound.SCOPE, tenant="acme")
    decide, sid = world.authority.decide, session.session_id
    state = world.log.state()
    _refuses("not opened", decide.refuse_call, state, str(uuid.uuid4()), None, "unpinned")
    _refuses("no refusal code", decide.refuse_call, state, sid, None, "bored")
    _refuses("pinned by no version", decide.refuse_call, state, sid, "drop_tables", "unpinned")
    assert decide.refuse_call(state, sid, "fiduciary_request", "velocity")["tool"] == (
        "fiduciary_request"
    )
    _refuses("billed 'support-agent' first", decide.close_session, state, sid, ["elsewhere"])
    _refuses("outside its subject", decide.close_session, state, sid, [bound.SCOPE, "support"])
    _refuses("twice", decide.close_session, state, sid, [bound.SCOPE, bound.SCOPE])
    session.close()
    session.close()  # once
    _refuses("is closed", decide.close_session, world.log.state(), sid, [bound.SCOPE])
    _verify(world)


def test_verify_names_a_session_record_its_rules_refuse(world: bound.World) -> None:
    """A session record written around the rules (here, a refusal naming a
    tool no version pins: agent text in the log) is named on replay; and a
    sessions projection edited around the service is named too (F10-1)."""
    world.issue_and_bind(kit.mandate(world))
    fid = kit.fiduciary(world)
    session = fid.session(bound.SCOPE, tenant="acme")
    forged = world.log.append(
        "session.refused",
        {
            "session": session.session_id,
            "mandate_id": session.mandate_id,
            "tool": "rm -rf",
            "code": "unpinned",
        },
    )
    session.close()
    world.governor.refresh()
    report = _verify_report(world.store, None)
    _names(report, f"record {forged.seq} (session.refused): its rules refuse it now")
    store = world.store
    if isinstance(store, SqliteStore):
        with sqlite3.connect(store.path) as raw:
            raw.execute("UPDATE sessions SET detail = 'arguments' WHERE event = 'refused'")
    else:
        import psycopg

        with psycopg.connect(need_postgres(), autocommit=True) as admin:
            admin.execute(
                f"UPDATE {store.schema}.sessions SET detail = 'arguments'"  # type: ignore[attr-defined]
                " WHERE event = 'refused'"
            )
    _names(_verify_report(world.store, None), "projection sessions: differs")

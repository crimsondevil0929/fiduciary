"""Exactly once under contention (§11 Phase 3, F7-6, F7-12, F10-5 to F10-8).

Two service processes share one deployment. That means the FIDU1 store
(SQLite or PostgreSQL), interlock's back office and operator log, and an
AgentGov ledger shared through PostgreSQL, the kind of ledger more than one
governor may open. Each process records the intake and executes every
warrant it finds. Meanwhile the principals submit their approvals: each
one twice (a replay), with one principal past the quorum besides, so an
approval arrives as the warrant forms.

Each ticket must get one warrant, from distinct principals. Each warrant
must get one intent and one outcome, and each outbox step one ILOK1 intent.
Every submission must be answered, and the log must verify with its anchors.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import time
import uuid
from collections import Counter
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from agentgov import BudgetManager
from interlock.records import Keyring, RecordLog

from fiduciary import fidu1
from fiduciary.documents import Approval
from fiduciary.postgres import PostgresStore
from fiduciary.sqlite import SqliteStore
from fiduciary.store import Store
from fiduciary.verify import verify_store
from tests import bound
from tests import examples as e
from tests.conftest import Clock, need_postgres, reopen
from tests.crash_env import Env, Manifest, crash_mandate, prepare, raise_ticket

ROOT = Path(__file__).resolve().parent.parent
ACTIONS = ("compensate", "requeue", "amend", "reject", "release")
"""In the order their back office is prepared: ``release`` halts the scope last."""


@pytest.fixture
def shared(store: Store, tmp_path: Path, clock: Clock) -> Iterator[Manifest]:
    dsn = need_postgres()
    ledger_schema = f"agl_{uuid.uuid4().hex[:12]}"
    backend = "sqlite" if isinstance(store, SqliteStore) else "postgres"
    built = bound.build(
        backend,
        tmp_path,
        store,
        clock,
        ledger=lambda: BudgetManager.open_postgres(dsn, schema=ledger_schema),
    )
    world = next(built)
    mandate = crash_mandate(world)
    world.issue_and_bind(mandate)
    manifest = Manifest(
        backend=backend,
        tmp=str(tmp_path),
        store=str(store.path) if isinstance(store, SqliteStore) else dsn,
        schema=store.schema if isinstance(store, PostgresStore) else "",
        ledger=dsn,
        ledger_schema=ledger_schema,
        back_office=world.back_office,
        stage_dsn=world.stage_dsn,
        relay_dsn=world.relay_dsn,
        charter=world.charter.digest,
        genesis=world.governor.entry(1).entry_hash,
        mandate_id=mandate.mandate_id,
        now="2026-10-18T10:30:00.000000Z",
    )
    cleanups = list(world.cleanups)
    world.cleanups.clear()
    built.close()
    try:
        yield manifest
    finally:
        for cleanup in reversed(cleanups):
            cleanup()
        import psycopg

        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f"DROP SCHEMA IF EXISTS {ledger_schema} CASCADE")


def test_two_service_processes_execute_each_warrant_once(shared: Manifest, store: Store) -> None:
    m, tmp = shared, Path(shared.tmp)
    env = Env(m)
    try:
        tickets = []
        for n, action in enumerate(ACTIONS):
            tickets.append((action, raise_ticket(env, action, n, prepare(env, action, n))))
    finally:
        env.close()

    # Each needed principal's approval, a replay of each, and one approval past the quorum.
    now = m.at()
    submissions: list[bytes] = []
    for action, ticket in tickets:
        option = next(o for o in ticket.options if o.resolution.action == action)
        for principal in e.PRINCIPALS[: option.quorum + 1]:
            raw = e.approval_json(
                principal,
                subject="ticket",
                digest=ticket.digest,
                resolution=option.digest,
                approval_id=str(uuid.uuid4()),
                issued_at=fidu1.format_instant(now),
                not_after=fidu1.format_instant(now + timedelta(hours=1)),
            )
            signed = Approval.from_json(raw, signed=False).signed(e.signer(principal)).canonical()
            submissions += [signed, signed]
    random.Random(7).shuffle(submissions)

    m.advance(1)
    m.dump(tmp / "manifest.json")
    children = []
    for i in (1, 2):
        job = {
            "timeout": 240,
            "reverse": i == 2,  # the two take the warrants in opposite orders, and meet
            "tickets": len(tickets),
            "submitted": str(tmp / "submitted"),
            "out": str(tmp / f"executed-{i}.json"),
        }
        (tmp / f"job-{i}.json").write_text(json.dumps(job))
        children.append(
            subprocess.Popen(  # noqa: S603 - this repository's own module
                [
                    sys.executable,
                    "-m",
                    "tests.contention_child",
                    tmp / "manifest.json",
                    tmp / f"job-{i}.json",
                ],
                cwd=ROOT,
                env={**os.environ, "PYTHONPATH": str(ROOT)},
                stderr=subprocess.PIPE,
                text=True,
            )
        )
    intake = reopen(store)  # the principal API: the intake, and nothing else
    before = len(list(intake.intake()))
    try:
        for data in submissions:
            intake.submit(data)
            time.sleep(0.03)
    finally:
        intake.close()
    (tmp / "submitted").touch()
    for child in children:
        _, err = child.communicate(timeout=300)
        assert child.returncode == 0, err[-3000:]
    executed = [json.loads((tmp / f"executed-{i}.json").read_text()) for i in (1, 2)]

    env = Env(m, engines=False)
    try:
        state = env.log.state()
        # Every submission answered, replays included (F10-11).
        answered = [i for i, _ in env.store.intake()]
        assert set(answered) == state.processed
        assert len(answered) == before + len(submissions)
        # One warrant per ticket, from distinct principals, as many as the quorum (F7-5, F7-6).
        assert len(state.warrants) == len(tickets)
        for action, ticket in tickets:
            assert state.ticket_status(ticket.ticket_id) == "warranted"
            warrant = env.escalation.warrant_for_ticket(ticket.ticket_id, state)
            assert warrant is not None and warrant.resolution.action == action
            option = next(o for o in ticket.options if o.resolution.action == action)
            principals = {_approval(env, d).principal for d in warrant.basis.approvals}
            assert len(principals) == len(warrant.basis.approvals) == option.quorum
        # One execution of each, by one of the two processes (F7-12, F10-8).
        assert sorted(executed[0] + executed[1]) == sorted(state.warrants)
        for digest in state.warrants:
            events = state.executions[digest]
            assert [event for event, _, _ in events] == ["intent", "applied"], events[-1][1].get(
                "why"
            )
        # One ILOK1 intent per outbox step.
        public = e.SERVICE.public_key()
        operators = RecordLog.load(tmp / "operators.jsonl", Keyring({public.key_id: public}))
        intents = Counter(
            (r.body["reason"], frozenset(t["message"] for t in r.body["targets"]))
            for r in operators
            if r.kind == "operator.intent" and str(r.body["reason"]).startswith("FIDU1 warrant ")
        )
        assert set(intents.values()) == {1}
        steps = sum(
            1
            for digest in state.warrants
            for step in state.executions[digest][0][1]["steps"]
            if step["kind"] in ("release", "cancel", "requeue", "compensate", "compensate_late")
        )
        assert len(intents) == steps
        env.governor.refresh()
        report = verify_store(
            env.store,
            service_key=e.SERVICE.public_key(),
            pinned=m.charter,
            ledger=env.governor.audit_trail(),
        )
        assert report.ok, report.problems
    finally:
        env.close()


def _approval(env: Env, digest: str) -> Approval:
    found = env.store.document(digest)
    assert found is not None
    return Approval.loads(found[1])

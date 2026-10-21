"""The crash tests (§10.4, F10-9 to F10-11), on SQLite and PostgreSQL.

For every resolution class, the service runs as a child process
(``tests.crash_child``). It is killed with ``SIGKILL`` at each checkpoint:
``raised``, ``recorded``, ``formed``, ``intent``, ``acted:<n>`` and
``applied``. The test then restarts the service from what the disk holds,
resolves, finishes what the ticket still allows, and requires:

- **F10-9.** The one terminal state the checkpoint implies:
  - killed before the intent: one fresh execution. A quote or a repair
    proposal lives only in the killed process's memory, so that execution is
    refused and the ticket lapses (F7-14);
  - killed at ``intent``: abandoned, since no step acted;
  - killed at ``acted:<n>``: applied after the last step, else abandoned,
    naming the steps that acted;
  - killed at ``applied``: applied.
- **F10-10.** Exactly once: one intent and one outcome per warrant. No second
  ILOK1 intent names it for the same targets, no second reset anchor, no
  second extension grant or decline, no second admission or commit of its
  proposal, no second issuance of its mandate. A second execution is refused.
- **F10-11.** Nothing lost: every submission in the intake is recorded or
  refused, and the ticket raised before the kill is open, warranted or lapsed.

The log then verifies, its anchors included.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from interlock.chain import EscrowChain, RecordType
from interlock.records import Keyring, RecordLog

from fiduciary.exceptions import AuthorityError
from fiduciary.postgres import PostgresStore
from fiduciary.sqlite import SqliteStore
from fiduciary.store import Store
from fiduciary.verify import verify_store
from tests import bound
from tests import examples as e
from tests.conftest import Clock, need_postgres
from tests.crash_env import (
    CLASSES,
    SCOPE,
    Env,
    Manifest,
    crash_mandate,
    prepare,
    submit_approvals,
    warrant_of,
)

ROOT = Path(__file__).resolve().parent.parent

BEFORE_INTENT = ("raised", "recorded", "formed")
KILLS: dict[str, tuple[str, ...]] = {
    action: (*BEFORE_INTENT, "intent", "acted:0", "applied") for action in CLASSES
}
KILLS["accept_repair"] = ("raised", "formed", "intent", "acted:0", "applied")  # by rule
KILLS["release"] = (*BEFORE_INTENT, "intent", "acted:0", "acted:1", "applied")  # reset, release
LOST_IN_MEMORY = ("accept_repair", "grant", "decline")
"""What a restart loses: the guard's quotes (U-3), and the repair proposals."""


@pytest.fixture
def deployment(
    store: Store, tmp_path: Path, clock: Clock, request: pytest.FixtureRequest
) -> Iterator[Manifest]:
    """A bound lineage under :func:`crash_mandate`, closed again: what is left
    is what the disk holds."""
    backend = "sqlite" if isinstance(store, SqliteStore) else "postgres"
    built = bound.build(backend, tmp_path, store, clock)
    world = next(built)
    mandate = crash_mandate(world)
    world.issue_and_bind(mandate)
    action = request.node.callspec.params["action"]
    if CLASSES[action] == "extension":  # leave the quotes something to ask for
        hold = world.governor.authorize(SCOPE, Decimal("246.00"))
        world.governor.capture(hold, Decimal("246.00"))
    manifest = Manifest(
        backend=backend,
        tmp=str(tmp_path),
        store=str(store.path) if isinstance(store, SqliteStore) else need_postgres(),
        schema=store.schema if isinstance(store, PostgresStore) else "",
        ledger=str(tmp_path / "ledger.db"),
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
    built.close()  # closes the world: the store, the ledger, the engines
    try:
        yield manifest
    finally:
        for cleanup in reversed(cleanups):
            cleanup()


def _child(m: Manifest, job: dict[str, Any]) -> None:
    tmp = Path(m.tmp)
    m.dump(tmp / "manifest.json")
    (tmp / "job.json").write_text(json.dumps(job))
    done = subprocess.run(  # noqa: S603 - this repository's own module
        [sys.executable, "-m", "tests.crash_child", tmp / "manifest.json", tmp / "job.json"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert done.returncode == -signal.SIGKILL, (job, done.returncode, done.stderr[-3000:])


@pytest.mark.parametrize("action", list(CLASSES))
def test_a_kill_at_every_checkpoint_resolves_exactly_once(
    deployment: Manifest, action: str
) -> None:
    m = deployment
    for n, kill in enumerate(KILLS[action]):
        env = Env(m)
        try:
            prepared = prepare(env, action, n)
            raised_after = env.log.state().head_seq
        finally:
            env.close()
        if "now" in prepared:
            m.now = prepared["now"]
        _child(m, {"action": action, "n": n, "kill": kill, "prepared": prepared})
        m.advance(1)
        env = Env(m)  # the restart
        try:
            _recover_and_check(env, action, kill, raised_after)
        finally:
            env.close()
        m.advance(60)


def _recover_and_check(env: Env, action: str, kill: str, raised_after: int) -> None:
    env.executor.resolve()
    env.escalation.process_intake()
    state = env.log.state()

    # F10-11: nothing lost.
    assert {i for i, _ in env.store.intake()} <= state.processed
    (row,) = [r for r in state.tickets.values() if r.seq > raised_after]
    assert state.ticket_status(row.ticket_id) in ("open", "warranted"), kill
    ticket = env.escalation.ticket(row.ticket_id, state)

    # Finish what the ticket still allows: the principals approve it, and the
    # restarted service executes the warrant once.
    if state.ticket_status(row.ticket_id) == "open":
        assert kill == "raised"
        submit_approvals(env, ticket, action)
        env.escalation.process_intake()
    warrant = warrant_of(env, ticket)
    assert warrant is not None and warrant.resolution.action == action
    if env.log.state().execution_status(warrant.digest) is None:
        assert kill in BEFORE_INTENT
        env.executor.execute(warrant)
    state = env.log.state()
    (intent,) = [b for event, b, _ in state.executions[warrant.digest] if event == "intent"]
    steps = len(intent["steps"])
    events = [event for event, _, _ in state.executions[warrant.digest]]
    (outcome,) = events[1:]
    body = state.executions[warrant.digest][1][1]

    # F10-9: the one terminal state.
    if kill in BEFORE_INTENT:
        if action in LOST_IN_MEMORY:
            assert outcome == "refused" and "_lost" in body["why"], body
            assert state.ticket_status(ticket.ticket_id) == "lapsed"
        else:
            assert outcome == "applied", body
    elif kill == "intent":
        assert outcome == ("applied" if action == "reject" else "abandoned"), body
        assert len(body["results"]) == (1 if action == "reject" else 0)
    elif kill.startswith("acted:"):
        acted = int(kill.split(":")[1]) + 1
        assert outcome == ("applied" if acted == steps else "abandoned"), body
        assert len(body["results"]) == acted
    else:
        assert outcome == "applied", body
    if outcome != "refused":
        assert state.ticket_status(ticket.ticket_id) == "warranted"

    # F10-10: exactly once.
    with pytest.raises(AuthorityError, match=r"already has an execution\.intent"):
        env.executor.execute(warrant)
    assert env.executor.resolve() == []
    _once(env, warrant.digest, warrant.resolution.proposal, warrant.resolution.mandate)

    env.governor.refresh()
    report = verify_store(
        env.store,
        service_key=e.SERVICE.public_key(),
        pinned=env.m.charter,
        ledger=env.governor.audit_trail(),
    )
    assert report.ok, report.problems


def _once(env: Env, digest: str, proposal: str | None, mandate: str | None) -> None:
    """No act names the warrant twice (F10-10)."""
    reason = f"FIDU1 warrant {digest}"
    tmp = Path(env.m.tmp)
    public = e.SERVICE.public_key()
    keyring = Keyring({public.key_id: public})
    operators = (
        RecordLog.load(tmp / "operators.jsonl", keyring)
        if (tmp / "operators.jsonl").exists()
        else ()
    )
    intents = [r for r in operators if r.kind == "operator.intent" and r.body["reason"] == reason]
    targets = Counter(frozenset(t["message"] for t in r.body["targets"]) for r in intents)
    assert all(count == 1 for count in targets.values()), targets
    answered = Counter(
        r.body["intent"]["hash"]
        for r in operators
        if r.kind.startswith("operator.") and "intent" in r.body
    )
    assert all(answered[r.record_hash] <= 1 for r in intents)

    env.governor.refresh()
    memos = [entry.memo for entry in env.governor.audit_trail()]
    assert sum(1 for memo in memos if memo.startswith(f"{reason} reset ")) <= 1
    grants = {memo.split(" ", 2)[1] for memo in memos if memo.endswith(f"approved by {reason}")}
    assert len(grants) <= 1

    runtime = (
        RecordLog.load(tmp / "runtime.jsonl", e.SERVICE) if (tmp / "runtime.jsonl").exists() else ()
    )
    assert sum(1 for r in runtime if r.body.get("approved_by") == reason) <= 1
    assert sum(1 for r in runtime if r.body.get("declined_by") == reason) <= 1

    if proposal is not None:
        chain = EscrowChain.load(tmp / "chains" / f"{env.m.mandate_id}.chain").records()
        admitted = [
            r
            for r in chain
            if r.record_type is RecordType.PLAN_ADMITTED and r.payload_hash == proposal
        ]
        assert len(admitted) <= 1
        plans = {r.plan_id for r in admitted}
        assert (
            sum(1 for r in chain if r.plan_id in plans and r.record_type is RecordType.COMMITTED)
            <= 1
        )
    if mandate is not None:
        issued = [
            r
            for r in env.log.records()
            if r.kind == "mandate.issued" and r.content["mandate"] == mandate
        ]
        assert len(issued) <= 1

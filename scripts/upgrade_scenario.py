# mypy: ignore-errors
"""One scenario, run by an earlier release's own code: what
``scripts/make_upgrade_stores.py`` runs inside each archived 0.x release, with
that release's ``src`` and ``tests`` first on the path.

    python upgrade_scenario.py <version> <out>

It writes, on the wall clock (so the ledger's instants and the log's agree, as
V12 holds them), what that release could write: a charter adopted and a
mandate issued (0.1); bound, with a derived child bound, a plan committed and
the child revoked (0.2); a dead message ticketed, approved and requeued
(0.3); a session's calls (0.4, 0.5). The releases' test helpers stamp
approvals at fixed instants, so :func:`approve` stamps them now.
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

version, out = sys.argv[1], Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
minor = int(version.split(".")[1])

import fiduciary  # noqa: E402

assert fiduciary.__version__ == version, (fiduciary.__file__, fiduciary.__version__)
from tests import conftest  # noqa: E402
from tests import examples as e  # noqa: E402

from fiduciary import fidu1  # noqa: E402


class LiveClock:
    def __init__(self) -> None:
        self.offset = timedelta(0)

    def __call__(self) -> datetime:
        return datetime.now(UTC) + self.offset

    def advance(self, **delta: float) -> None:
        self.offset += timedelta(**delta)


clock = LiveClock()
_approve = conftest.approve


def approve(store, principals, subject, digest, **changes):  # type: ignore[no-untyped-def]
    now = clock()
    changes.setdefault("issued_at", fidu1.format_instant(now))
    changes.setdefault("not_after", fidu1.format_instant(now + timedelta(hours=1)))
    return _approve(store, principals, subject, digest, **changes)


conftest.approve = approve
manifest: dict[str, object] = {"version": version}

if minor == 1:
    from agentgov import BudgetManager

    from fiduciary.authority import Authority
    from fiduciary.documents import Charter, Mandate
    from fiduciary.log import Log, ensure_anchor_scope
    from fiduciary.sqlite import SqliteStore

    store = SqliteStore(out / "fiduciary.db")
    governor = BudgetManager.open_sqlite(str(out / "ledger.db"))
    ensure_anchor_scope(governor)
    governor.open_root("support", "1000")
    genesis = governor.entry(1).entry_hash
    charter = Charter.from_json(
        e.charter_json(deployment={"ledger": genesis, "label": "test"}), signed=False
    ).signed(e.signer("cfo"))
    log = Log(store, e.SERVICE, ledger=governor, clock=clock)
    authority = Authority(log, pinned=charter.digest)
    authority.propose(charter)
    approve(store, e.PRINCIPALS, "charter", charter.digest)
    authority.adopt_charter(charter.digest)
    ref = {"charter_id": e.CHARTER_ID, "version": 1, "digest": charter.digest}
    mandate = Mandate.from_json(e.mandate_json(charter=ref), signed=False).signed(e.signer("cfo"))
    authority.propose(mandate)
    approve(store, ("cfo", "head-ops"), "mandate", mandate.digest)
    authority.issue_mandate(mandate.digest)
    manifest.update(charter=charter.digest, mandate_id=mandate.mandate_id)
    store.close()
    governor.close()
else:
    from tests import bound

    from fiduciary.sqlite import SqliteStore

    bound.approve = approve
    store = SqliteStore(out / "fiduciary.db")
    kw = {}
    if minor >= 4:
        from tests import bridge_kit as kit

        kw["tools"] = kit.tools("sqlite")
    built = bound.build("sqlite", out, store, clock, **kw)
    world = next(built)
    mandate = kit.mandate(world) if minor >= 4 else world.mandate()
    world.issue_and_bind(mandate)
    child = world.child(mandate)
    world.binder.derive(child)
    child_head = world.binder.decide.head_mandate(world.log.state(), child.mandate_id)
    world.binder.bind(child_head.digest)
    if minor >= 4:
        fid = kit.fiduciary(world, in_process="tool-calls-only")
        session = fid.session(bound.SCOPE, tenant="acme")
        call = session.call(
            "refund_order", {"order_id": 1, "amount": "10.00", "payment_intent": "pi_0000000001"}
        )
        assert call.committed, call
    else:
        assert world.execute(mandate, world.refund_plan(1, "10.00")).committed
    if minor >= 3:
        from interlock.relay import PERMANENT, DeliveryResult
        from tests.test_executor import _op_log, _outbox_ops, _run_relay

        from fiduciary.escalation import Escalation
        from fiduciary.executor import Executor

        esc = Escalation(world.authority, world.governor)
        outbox, conn = _outbox_ops(world)
        op_log = _op_log(world)
        executor = Executor(world.binder, escalation=esc, outbox=outbox, operator_log=op_log)
        _run_relay(world, DeliveryResult(outcome=PERMANENT, detail="declined"))
        dead = uuid.UUID(str(world.fetch("SELECT message_id FROM _interlock_outbox_state")[0][0]))
        ticket, _ = esc.raise_dead(mandate.mandate_id, outbox, dead)
        assert ticket is not None
        option = next(o for o in ticket.options if o.resolution.action == "requeue")
        approve(world.store, ("cfo", "head-ops"), "ticket", ticket.digest, resolution=option.digest)
        esc.process_intake()
        warrant = esc.warrant_for_ticket(ticket.ticket_id)
        assert warrant is not None
        records = executor.execute(warrant)
        assert [r.kind for r in records][-1] == "execution.applied", records
        op_log.close()
        conn.close()
    if minor >= 4:
        call = session.call(
            "refund_order", {"order_id": 3, "amount": "2.00", "payment_intent": "pi_0000000003"}
        )
        assert call.committed, call
        assert session.call("lookup_order", {"order_id": 3}).ok
        session.close()
    approve(world.store, ("cfo", "head-ops"), "revocation", child_head.digest)
    world.binder.revoke(child_head.digest)
    manifest.update(
        charter=world.charter.digest,
        mandate_id=mandate.mandate_id,
        child_id=child.mandate_id,
    )
    built.close()

    tools = out / "tools"
    tools.mkdir()
    for binding in world.binder._deployment.tools.values():
        (tools / f"{binding.digest}.json").write_text(binding.pretty())

from fiduciary.postgres import _ddl  # noqa: E402

(out / "postgres.json").write_text(json.dumps(_ddl("fid_upgrade"), indent=1) + "\n")
(out / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
print("wrote", out)

"""Binding and the lineage after it (``docs/FIDUCIARY_SPEC.md`` §1.2, §5.3 to §5.5,
§6.4), on SQLite and PostgreSQL: every store, every back office.

A bound mandate refuses every out-of-mandate plan class, and every receipt
shows the compiled policy it ran under (F6-7). Children share an ancestor's
window, and together cannot pass it, under concurrency (F5-5). Window history
survives amendment; amendment takes effect at the next plan boundary, while the
plan in flight keeps its epoch (F1-3). A parent's amendment rebases or
suspends each child (F5-7). Revocation during a stage stops the commit (E3-8),
holds the lineage's messages at the relay, and trips every satellite (G7);
it cascades (F5-8); expiry ends a term.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from interlock import PlanBuilder
from interlock.anchor import LedgerAnchor
from interlock.exceptions import InterlockError, StageConflictError
from interlock.relay import DeliveryResult, LedgerBreaker, Relay

from fiduciary.binding import BindingError
from fiduciary.compiler import receipt_problems, window_name
from fiduciary.documents import Mandate
from fiduciary.exceptions import AuthorityError
from fiduciary.sqlite import SqliteStore
from fiduciary.store import Store
from fiduciary.verify import verify_store
from tests import examples as e
from tests.bound import SCOPE, World, build
from tests.conftest import Clock, approve


@pytest.fixture
def world(store: Store, tmp_path: Path, clock: Clock) -> Iterator[World]:
    backend = "sqlite" if isinstance(store, SqliteStore) else "postgres"
    yield from build(backend, tmp_path, store, clock)


def _verified(world: World) -> None:
    world.governor.refresh()
    report = verify_store(
        world.store,
        service_key=e.SERVICE.public_key(),
        pinned=world.charter.digest,
        ledger=world.governor.audit_trail(),
    )
    assert report.ok, report.problems


def _receipted(world: World, mandate: Mandate, result: Any) -> None:
    assert result.receipt is not None
    assert receipt_problems(result.receipt, world.binder.engine(mandate.mandate_id).compiled) == []


# -- binding (§6.4) -----------------------------------------------------------------


def test_binding_delegates_records_and_builds_the_engine(world: World) -> None:
    mandate = world.mandate()
    world.issue_and_bind(mandate)
    assert world.governor.available(SCOPE) == 250
    (bound,) = [r for r in world.log.records() if r.kind == "mandate.bound"]
    assert bound.content["compiled"] == world.binder.engine(mandate.mandate_id).compiled.digest
    assert bound.content["delegated"] == "250.00000000"
    assert world.log.state().status(mandate.mandate_id) == "bound"
    entry = world.governor.find_entries(scope_id=SCOPE, limit=1)[0]
    assert entry.memo == f"FIDU1 bind {mandate.digest}"
    world.binder.bind(mandate.digest)  # again: the delegation is not made twice
    assert world.governor.available(SCOPE) == 250
    _verified(world)


def test_a_committed_plan_is_receipted_under_its_compiled_policy(world: World) -> None:
    mandate = world.mandate()
    world.issue_and_bind(mandate)
    result = world.execute(mandate, world.refund_plan(1, "19.99"))
    assert result.committed, result.feedback
    _receipted(world, mandate, result)
    assert result.receipt is not None and result.receipt.issuer == f"fiduciary:{mandate.mandate_id}"
    assert world.outbox_states() == ["pending"]


def test_binding_refuses_what_it_cannot_bind(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(BindingError, match="no issued mandate"):
        world.binder.bind("a" * 64)
    orphan_root = world.mandate(
        subject={"scope": "x", "funded_from": "nowhere", "label": "x"},
        mandate_id="0f0f0f0f-0000-4000-8000-000000000001",
    )
    world.issue(orphan_root)
    with pytest.raises(BindingError, match="no scope 'nowhere'"):
        world.binder.bind(orphan_root.digest)
    world.governor.trip("support", "halted for the test")
    mandate = world.mandate()
    world.issue(mandate)
    with pytest.raises(BindingError, match="is halted"):
        world.binder.bind(mandate.digest)
    world.governor.reset("support")
    monkeypatch.setattr(type(world.governor), "policy", property(lambda self: _Loose()))
    with pytest.raises(BindingError, match="looser than any mandate"):
        world.binder.bind(mandate.digest)


class _Loose:
    trip_on_overdraft = False
    trip_on_exhaustion = True
    max_depth = 8


def test_binding_refuses_another_ledger(world: World) -> None:
    import dataclasses

    mandate = world.mandate()
    world.issue(mandate)
    world.binder._deployment = dataclasses.replace(world.binder._deployment, ledger="0" * 64)
    with pytest.raises(BindingError, match="different ledgers"):
        world.binder.bind(mandate.digest)


def test_a_child_binds_only_under_a_bound_parent(world: World) -> None:
    parent = world.mandate()
    world.issue(parent)
    child = world.child(parent)
    world.binder.derive(child)
    digest = world.log.state().head(child.mandate_id)[1]  # type: ignore[index]
    with pytest.raises(BindingError, match="not bound"):
        world.binder.bind(digest)
    world.binder.bind(parent.digest)
    world.binder.bind(digest)
    assert world.governor.available(f"{SCOPE}/child") == 50
    assert world.governor.available(SCOPE) == 200


def test_an_engine_runs_nothing_outside_its_term(world: World) -> None:
    mandate = world.mandate()
    world.issue_and_bind(mandate)
    world.clock.now = e.at("2027-02-01T00:00:00")
    with pytest.raises(BindingError, match="outside its term"):
        world.execute(mandate, world.refund_plan(1, "1.00"))


# -- refusals -----------------------------------------------------------------------


def _variant(world: World, edit: Any) -> Mandate:
    raw = world.mandate_json()
    edit(raw)
    return Mandate.from_json(raw, signed=False).signed(e.signer("cfo"))


def _items(world: World) -> Any:
    builder = PlanBuilder(SCOPE)
    builder.insert(
        table="items",
        statement=world.sql("INSERT INTO items (tenant, sku, qty) VALUES (:tenant, :sku, :qty)"),
        parameters={"tenant": "acme", "sku": "x", "qty": 1},
        tenant_id="acme",
        stated_rows=1,
    )
    return builder.build()


def _delete(world: World) -> Any:
    builder = PlanBuilder(SCOPE)
    builder.delete(
        table="orders",
        statement=world.sql("DELETE FROM orders WHERE id = :id AND tenant = :tenant"),
        parameters={"id": 3, "tenant": "acme"},
        tenant_id="acme",
        stated_rows=1,
    )
    return builder.build()


def _mail(world: World, to: str) -> Any:
    builder = PlanBuilder(SCOPE)
    builder.enqueue(
        sink="mail", operation="send", payload={"to": to, "subject": "hi"}, tenant_id="acme"
    )
    return builder.build()


def _any(raw: dict[str, Any]) -> None:
    raw["tools"]["statements"] = "any"


def _any_updating_orders(raw: dict[str, Any]) -> None:
    _any(raw)
    raw["data"]["tables"].update(orders=["update"])


def _with_mail(raw: dict[str, Any]) -> None:
    raw["outbound"]["sinks"]["mail"] = {
        "operations": ["send"],
        "max_attempts": 5,
        "not_after_seconds": 600,
        "max_payload_bytes": 4096,
    }
    raw["outbound"]["recipients"] = [
        {
            "sink": "mail",
            "operation": "send",
            "kind": "email",
            "fields": ["to"],
            "domains": ["acme.com"],
            "addresses": [],
            "tenant_domains": {},
            "subdomains": False,
        }
    ]
    raw["tools"]["statements"] = "any"


REFUSALS: list[tuple[str, Any, Any, str]] = [
    ("table", _any, _items, "table_allowlist"),
    (
        "operation",
        _any_updating_orders,
        _delete,
        "no_delete",
    ),
    (
        "tenant",
        lambda r: r["data"].update(tenants=["acme"]),
        lambda w: w.refund_plan(2, "19.99"),
        "tenant_allowlist",
    ),
    (
        "row count",
        lambda r: r["data"].update(max_rows=1),
        lambda w: w.refund_plan(1, "19.99"),
        "blast_radius",
    ),
    (
        "drawdown",
        lambda r: r["data"]["drawdown"][0].update(max_drop="0.1"),
        lambda w: w.refund_plan(1, "150.00"),
        "tenant_drawdown_guard:orders.total",
    ),
    (
        "cap",
        lambda r: None,
        lambda w: w.refund_plan(1, "250.00"),
        "payload_amount_cap:stripe.refunds.create.amount",
    ),
    (
        "recipient",
        _with_mail,
        lambda w: _mail(w, "someone@evil.io"),
        "recipient_allowlist:mail.send",
    ),
    (
        "unbound statement",
        lambda r: None,
        lambda w: w.refund_plan(
            1,
            "19.99",
            statement=(
                "UPDATE orders SET total = total - CAST(:amount AS NUMERIC)"
                " WHERE id = :order_id AND tenant = :tenant AND 1 = 1"
            ),
        ),
        "bound_statements",
    ),
    (
        "scope",
        lambda r: None,
        lambda w: w.refund_plan(1, "19.99", scope="support"),
        "fiduciary_mandate",
    ),
    (
        "window",
        lambda r: r["windows"][0].update(limit="30"),
        lambda w: w.refund_plan(1, "19.99"),
        "rate_window:",
    ),
    (
        "currency",
        lambda r: None,
        lambda w: w.refund_plan(1, "19.99", currency="eur"),
        "payload_amount_cap:stripe.refunds.create.amount",
    ),
]


@pytest.mark.parametrize(
    ("label", "edit", "make", "refused_by"), REFUSALS, ids=[r[0] for r in REFUSALS]
)
def test_a_bound_mandate_refuses_every_out_of_mandate_plan(
    world: World, label: str, edit: Any, make: Any, refused_by: str
) -> None:
    mandate = _variant(world, edit)
    world.issue_and_bind(mandate)
    if label == "window":
        assert world.execute(mandate, world.refund_plan(3, "19.99")).committed
    result = world.execute(mandate, make(world))
    assert not result.committed
    assert any(name.startswith(refused_by) for name in result.blocked_by), result.blocked_by
    _receipted(world, mandate, result)
    assert result.feedback is not None and result.feedback.blocking


@pytest.mark.parametrize(
    ("make", "fragment"),
    [
        (lambda w: _mail(w, "ops@acme.com"), "mail"),
        (lambda w: _charge(w), "charges.create"),
    ],
    ids=["sink", "sink operation"],
)
def test_a_sink_or_operation_outside_the_mandate_is_refused_at_admission(
    world: World, make: Any, fragment: str
) -> None:
    mandate = world.mandate()
    world.issue_and_bind(mandate)
    try:
        result = world.execute(mandate, make(world))
    except InterlockError as exc:
        assert fragment in str(exc)
    else:
        assert not result.committed
    assert world.outbox_states() == []


def _charge(world: World) -> Any:
    builder = PlanBuilder(SCOPE)
    builder.enqueue(
        sink="stripe",
        operation="charges.create",
        payload={"amount": "5", "currency": "usd"},
        tenant_id="acme",
    )
    return builder.build()


# -- aggregates (F5-4 to F5-6) ------------------------------------------------------------


def test_children_together_cannot_pass_their_parents_window(world: World) -> None:
    parent = _variant(world, lambda r: r["windows"][0].update(limit="100"))
    world.issue_and_bind(parent)
    children = []
    for name in ("a", "b"):
        child = world.child(
            parent,
            subject={"scope": f"{SCOPE}/{name}", "funded_from": SCOPE, "label": name},
            mandate_id=f"00000000-0000-4000-8000-0000000000a{ord(name) - 96}",
        )
        world.binder.derive(child)
        digest = world.log.state().head(child.mandate_id)[1]  # type: ignore[index]
        world.binder.bind(digest)
        children.append((child, f"{SCOPE}/{name}"))
    for child, _ in children:
        names = [w.name for w in world.binder.engine(child.mandate_id).compiled.windows]
        assert names == [window_name(parent.mandate_id, "refund_dollars")]
    outcomes: list[str] = []
    lock = threading.Lock()
    start = threading.Barrier(12)

    def run(child: Mandate, scope: str, order: int) -> None:
        plan = world.refund_plan(order, "10", scope=scope)
        start.wait(60)
        ended = "conflicted"
        for _ in range(100):
            try:
                result = world.binder.engine(child.mandate_id).execute(plan)
            except StageConflictError:
                continue
            ended = "committed" if result.committed else ",".join(result.blocked_by)
            break
        with lock:
            outcomes.append(ended)

    threads = [
        threading.Thread(target=run, args=(child, scope, 2 * n + i + 1))
        for i, (child, scope) in enumerate(children)
        for n in range(6)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(120)
    window = f"rate_window:{window_name(parent.mandate_id, 'refund_dollars')}"
    assert sorted(outcomes) == ["committed"] * 10 + [window] * 2, outcomes


def test_window_history_survives_amendment_and_the_amendment_waits_for_the_boundary(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    mandate = _variant(world, lambda r: r["windows"][0].update(limit="50"))
    world.issue_and_bind(mandate)
    assert world.execute(mandate, world.refund_plan(1, "19.99")).committed
    amended = Mandate.from_json(
        {
            **mandate.body(),
            "version": 2,
            "prev": mandate.digest,
            "escalation": {**mandate.escalation.to_json(), "max_open": 4},
        },
        signed=False,
    ).signed(e.signer("cfo"))
    world.authority.propose(amended)
    approve(world.store, ("cfo", "head-ops"), "mandate", amended.digest)
    original = LedgerAnchor.guard_commit
    started = threading.Event()
    amending: list[threading.Thread] = []

    @contextlib.contextmanager
    def guard(self: LedgerAnchor, scope_id: str) -> Iterator[None]:
        if not amending:  # the plan in flight: the amendment lands now, beside it

            def amend() -> None:
                started.set()
                world.binder.issue(amended.digest)

            thread = threading.Thread(target=amend)
            amending.append(thread)
            thread.start()
            started.wait(10)
        with original(self, scope_id):
            yield

    monkeypatch.setattr(LedgerAnchor, "guard_commit", guard)
    in_flight = world.execute(mandate, world.refund_plan(3, "19.99"))
    amending[0].join(60)
    assert in_flight.committed and in_flight.receipt is not None
    assert in_flight.receipt.decision.policy_epoch == 1
    monkeypatch.setattr(LedgerAnchor, "guard_commit", original)
    assert world.binder.engine(mandate.mandate_id).compiled.policy_epoch == 2
    after = world.execute(mandate, world.refund_plan(5, "19.99"))
    assert not after.committed, "39.98 of 50 is held from version 1: history survives the amendment"
    assert after.receipt is not None and after.receipt.decision.policy_epoch == 2
    assert any(b.startswith("rate_window:") for b in after.blocked_by)
    _receipted(world, mandate, after)
    _verified(world)


def test_an_amended_envelope_is_delegated_or_released(world: World) -> None:
    mandate = world.mandate()
    world.issue_and_bind(mandate)
    for version, envelope in ((2, "300.00000000"), (3, "260.00000000")):
        head = world.log.state().head(mandate.mandate_id)
        assert head is not None
        raw = {
            **mandate.body(),
            "version": version,
            "prev": head[1],
            "budget": {**mandate.budget.to_json(), "envelope": envelope},
        }
        amended = Mandate.from_json(raw, signed=False).signed(e.signer("cfo"))
        world.issue(amended)
    assert world.governor.available(f"{SCOPE}/ext-1") == 50
    assert world.governor.available(SCOPE) == 210  # 40 of 250 released: 210 + 50 = 260
    bound = world.log.state().bound[mandate.mandate_id]
    assert bound["delegated"] == "260.00000000" and bound["version"] == 3
    _verified(world)


# -- rebasing and suspension (F1-4, F5-7) -------------------------------------------------


def _amend(world: World, mandate: Mandate, **changes: Any) -> Mandate:
    head = world.log.state().head(mandate.mandate_id)
    assert head is not None
    raw = {**mandate.body(), "version": head[0] + 1, "prev": head[1], **changes}
    amended = Mandate.from_json(raw, signed=False).signed(e.signer("cfo"))
    world.authority.propose(amended)
    approve(world.store, ("cfo", "head-ops"), "mandate", amended.digest)
    return amended


def _bound_child(world: World, parent: Mandate) -> Mandate:
    child = world.child(parent)
    world.binder.derive(child)
    digest = world.log.state().head(child.mandate_id)[1]  # type: ignore[index]
    world.binder.bind(digest)
    return world.binder.engine(child.mandate_id).mandate


def test_a_child_that_still_refines_the_amended_parent_is_rebased_and_rebound(world: World) -> None:
    parent = world.mandate()
    world.issue_and_bind(parent)
    child = _bound_child(world, parent)
    amended = _amend(world, parent, escalation={**parent.escalation.to_json(), "max_open": 6})
    records = world.binder.issue(amended.digest)
    kinds = [r.kind for r in records]
    assert kinds[:2] == ["mandate.issued", "mandate.rebased"]
    assert kinds.count("mandate.bound") == 2  # the parent's, then the child's
    assert records[0].seq + 1 == records[1].seq, "the rebasing is written with the amendment"
    state = world.log.state()
    assert (
        state.parents[child.mandate_id] == amended.digest
        and state.status(child.mandate_id) == "bound"
    )
    assert world.binder.engine(child.mandate_id).compiled.lineage[0].digest == amended.digest
    _verified(world)


def test_a_child_wider_than_the_amended_parent_is_suspended_and_tripped(world: World) -> None:
    parent = world.mandate()
    world.issue_and_bind(parent)
    child = _bound_child(world, parent)
    narrower = _amend(world, parent, budget={**parent.budget.to_json(), "per_call": "0.10000000"})
    records = world.binder.issue(narrower.digest)
    suspended = [r for r in records if r.kind == "mandate.suspended"]
    assert len(suspended) == 1 and any(
        "budget.per_call" in why for why in suspended[0].content["why"]
    )
    assert suspended[0].content["trips"]
    assert world.governor.halted_by(f"{SCOPE}/child") is not None
    assert world.governor.halted_by(SCOPE) is None
    with pytest.raises(BindingError, match="no longer bound"):
        world.binder.engine(child.mandate_id).execute(
            world.refund_plan(1, "1.00", scope=f"{SCOPE}/child")
        )
    with pytest.raises(BindingError, match="suspended"):
        world.binder.bind(world.log.state().head(child.mandate_id)[1])  # type: ignore[index]
    _verified(world)


# -- revocation (F5-8, F5-9, G7) -------------------------------------------------------------


class _Recording:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    def send(
        self, delivery: Any
    ) -> DeliveryResult:  # pragma: no cover - never reached: the scope is halted
        self.sent.append(delivery)
        raise AssertionError("a halted scope's message was sent")


def test_revocation_during_a_stage_stops_the_commit_holds_the_messages_and_trips_every_satellite(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = world.mandate()
    world.issue_and_bind(parent)
    child = _bound_child(world, parent)
    assert world.execute(
        parent, world.refund_plan(1, "19.99")
    ).committed  # its message waits for the relay
    for satellite in (f"{SCOPE}/recovery", f"{SCOPE}/ext-1"):
        world.governor.delegate("support", satellite, "1")
    approve(world.store, ("security",), "revocation", parent.digest)
    original = LedgerAnchor.guard_commit
    revoked: list[Any] = []

    @contextlib.contextmanager
    def guard(self: LedgerAnchor, scope_id: str) -> Iterator[None]:
        if not revoked:  # between the verdict and the commit: revoked now
            revoked.extend(world.binder.revoke(parent.digest))
        with original(self, scope_id):
            yield

    monkeypatch.setattr(LedgerAnchor, "guard_commit", guard)
    result = world.execute(parent, world.refund_plan(3, "19.99"))
    monkeypatch.setattr(LedgerAnchor, "guard_commit", original)
    assert not result.committed, "a halted scope does not commit (E3-8)"
    assert [r.kind for r in revoked] == ["mandate.revoked", "mandate.revoked"]
    assert revoked[0].content["by"] == "roster" and revoked[1].content["cascade"] == parent.digest
    for scope in (SCOPE, f"{SCOPE}/recovery", f"{SCOPE}/ext-1", f"{SCOPE}/child"):
        assert world.governor.halted_by(scope) is not None, scope
    state = world.log.state()
    assert (
        state.status(parent.mandate_id) == "revoked" and state.status(child.mandate_id) == "revoked"
    )
    with pytest.raises(BindingError, match="no longer bound"):
        world.execute(parent, world.refund_plan(5, "1.00"))
    adapter = _Recording()
    from agentgov.receipts.signing import Ed25519Signer

    relay = Relay(
        world.relay_store(),
        adapters={"stripe": adapter},
        breaker=LedgerBreaker(world.governor),
        signer=Ed25519Signer.generate(),
    )
    relay.run_once(limit=10)
    assert world.outbox_states() == ["held"] and adapter.sent == []
    _verified(world)


def test_revocation_needs_a_revocation_quorum(world: World) -> None:
    parent = world.mandate()
    world.issue_and_bind(parent)
    with pytest.raises(AuthorityError, match="revocation quorum"):
        world.binder.revoke(parent.digest)
    with pytest.raises(AuthorityError, match="version in force"):
        world.binder.revoke("b" * 64)


def test_an_ancestors_roster_revokes_a_child(world: World) -> None:
    parent = _variant(world, lambda r: r["roster"]["quorums"].update(reset_safety=2))
    world.issue_and_bind(parent)
    raw = world.child_json(parent)
    raw["roster"] = {
        **raw["roster"],
        "principals": ["cfo", "head-ops"],
        "quorums": {**raw["roster"]["quorums"], "reset_safety": 2},
    }
    world.binder.derive(Mandate.from_json(raw, signed=False))
    child = world.binder.decide.head_mandate(world.log.state(), raw["mandate_id"])
    world.binder.bind(child.digest)
    approve(
        world.store, ("security",), "revocation", child.digest
    )  # security sits on the parent's roster only
    (record,) = world.binder.revoke(child.digest)
    assert record.content["by"] == parent.mandate_id
    assert world.log.state().status(parent.mandate_id) == "bound"
    _verified(world)


# -- expiry ---------------------------------------------------------------------------------


def test_expiry_ends_a_term_and_its_descendants(world: World) -> None:
    parent = world.mandate()
    world.issue_and_bind(parent)
    child = _bound_child(world, parent)
    assert world.binder.expire() == []
    world.clock.now = e.at("2027-01-01T00:00:00")
    records = world.binder.expire()
    assert [(r.kind, r.content.get("cascade")) for r in records] == [
        ("mandate.expired", None),
        ("mandate.expired", parent.digest),
    ]
    assert world.governor.halted_by(f"{SCOPE}/child") is not None
    state = world.log.state()
    assert state.status(parent.mandate_id) == state.status(child.mandate_id) == "expired"
    assert world.binder.expire() == []
    _verified(world)


def test_a_binder_decides_under_the_writers_lock(world: World, monkeypatch: Any) -> None:
    """Two binders over one store, the second expiring the lineage just before
    the first takes the writer's lock: the first decides from what the lock
    shows it, and writes nothing (F10-5). Read before the lock, it would
    expire the lineage again."""
    from fiduciary.authority import Authority
    from fiduciary.binding import Binder
    from fiduciary.log import Log
    from tests.conftest import reopen

    parent = world.mandate()
    world.issue_and_bind(parent)
    other_store = reopen(world.store)
    other = Binder(
        Authority(
            Log(other_store, e.SERVICE, ledger=world.governor, clock=world.clock),
            pinned=world.charter.digest,
        ),
        world.governor,
        deployment=world.binder.deployment,
    )
    world.clock.now = e.at("2027-01-01T00:00:00")
    taken = world.store.transaction

    def first_the_other() -> Any:
        monkeypatch.setattr(world.store, "transaction", taken)
        assert [r.kind for r in other.expire()] == ["mandate.expired"]
        return taken()

    monkeypatch.setattr(world.store, "transaction", first_the_other)
    try:
        assert world.binder.expire() == []
    finally:
        other_store.close()
    assert [r.kind for r in world.log.records()].count("mandate.expired") == 1
    _verified(world)


def test_an_idle_turn_takes_no_writers_lock(world: World) -> None:
    """With every submission answered and nothing due, judging the intake and
    lapsing tickets read and return: another writer holding the lock does not
    hold them up (the contention test's stall under coverage, Phase 5)."""
    import time

    from fiduciary.escalation import Escalation
    from fiduciary.postgres import PostgresStore

    world.issue_and_bind(world.mandate())
    escalation = Escalation(world.authority, world.governor)
    assert escalation.process_intake() == []
    held = threading.Event()
    done = threading.Event()

    def hold() -> None:
        if isinstance(world.store, PostgresStore):
            import psycopg

            from tests.conftest import need_postgres

            with psycopg.connect(need_postgres()) as conn, conn.transaction():
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"{world.store.schema}.transaction",),
                )
                held.set()
                done.wait(30)
            return
        import sqlite3

        assert isinstance(world.store, SqliteStore)
        lite = sqlite3.connect(world.store.path, isolation_level=None)
        try:
            lite.execute("BEGIN IMMEDIATE")
            held.set()
            done.wait(30)
            lite.execute("ROLLBACK")
        finally:
            lite.close()

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert held.wait(10)
        began = time.monotonic()
        assert escalation.process_intake() == []
        assert time.monotonic() - began < 2.0
    finally:
        done.set()
        holder.join(30)

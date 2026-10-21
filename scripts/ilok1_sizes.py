"""Measure the ILOK1 records fiduciary's executor signs, against KMS's limit (F3-9).

Run from the repository root: ``python scripts/ilok1_sizes.py`` prints the
table of ``docs/FIDUCIARY_SPEC.md`` §3.5; ``--check`` requires the spec's
table to say the same, and exits 1 when it does not.

Interlock signs an ILOK1 record whole: ``ILOK1/record/v1\\n`` and its
canonical bytes (U-2). AWS KMS signs Ed25519 over at most 4,096 bytes. This
script deploys the test back office (SQLite), lets interlock's own
:class:`~interlock.operators.Operator` write releases and compensations of
one and of two targets under a reason of fiduciary's form
(``FIDU1 warrant <digest>``), and measures each record's signing input. The
growth per target is the difference. :data:`~fiduciary.documents.MAX_OUTBOX_TARGETS`
is the largest number of targets the costliest intent fits.
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Final

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentgov.receipts.signing import Ed25519Signer  # noqa: E402
from interlock import OutboundRequest, PlanBuilder  # noqa: E402
from interlock.deliveries import operations  # noqa: E402
from interlock.operators import Operator, OperatorLog  # noqa: E402
from interlock.records import Keyring  # noqa: E402
from interlock.relay import DELIVERED, DeliveryResult, LedgerBreaker, Relay  # noqa: E402
from interlock.sqlite_outbox import OPERATOR, SqliteOutboxStore  # noqa: E402
from interlock.types import EffectId  # noqa: E402
from tests import bound  # noqa: E402
from tests import examples as e  # noqa: E402
from tests.conftest import Clock  # noqa: E402
from tests.crash_env import crash_mandate  # noqa: E402

from fiduciary.sqlite import SqliteStore  # noqa: E402

LIMIT: Final = 4096
"""AWS KMS's RAW message limit for ``ED25519_SHA_512``."""
ROWS: Final = (
    ("operator.intent", "`operator.intent`, `release` or `cancel`", "target"),
    ("compensate", "`operator.intent`, `compensate`", "target"),
    ("operator.applied", "`operator.applied`", "row"),
)
SPEC: Final = ROOT / "docs" / "FIDUCIARY_SPEC.md"


class _Delivered:
    def send(self, delivery: Any) -> DeliveryResult:
        return DeliveryResult(outcome=DELIVERED, detail="ok", remote_ref="ch_measure")


def _relay(world: bound.World) -> None:
    store = world.relay_store()
    try:
        relay = Relay(
            store,
            adapters={"stripe": _Delivered()},
            breaker=LedgerBreaker(world.governor),
            signer=Ed25519Signer(bytes(32)),
        )
        for _ in range(5):
            if not relay.run_once(limit=50):
                break
    finally:
        store.close()


def _plan(world: bound.World, mandate_id: str, orders: range, *, capture: bool) -> Any:
    builder = PlanBuilder(bound.SCOPE)
    for n in orders:
        payload = {"payment_intent": f"pi_{n:010d}", "amount": "2.00", "currency": "usd"}
        builder.enqueue(
            sink="stripe",
            operation="charges.capture" if capture else "refunds.create",
            payload=payload,
            compensation=(
                OutboundRequest(sink="stripe", operation="refunds.create", payload=payload)
                if capture
                else None
            ),
            tenant_id="acme",
            effect_id=EffectId(f"e-{n}"),
            independent=True,
        )
    plan = builder.build()
    assert world.binder.engine(mandate_id).execute(plan).committed
    return plan


def measure() -> dict[str, tuple[int, int]]:
    """``{row: (bytes signed with one item, bytes each further item adds)}``."""
    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        store = SqliteStore(tmp / "fiduciary.db")
        built = bound.build("sqlite", tmp, store, Clock(e.at("2026-10-18T10:10:00")))
        world = next(built)
        try:
            mandate = crash_mandate(world)
            world.issue_and_bind(mandate)
            outbox_store = SqliteOutboxStore(world.back_office, writes=OPERATOR)
            public = e.SERVICE.public_key()
            op_log = OperatorLog(
                tmp / "operators.jsonl", e.SERVICE, Keyring({public.key_id: public})
            )
            try:
                operator = Operator(op_log, operations(outbox_store))
                reason = f"FIDU1 warrant {'0' * 64}"

                _plan(world, mandate.mandate_id, range(1, 4), capture=False)
                world.governor.trip(bound.SCOPE, "spend envelope exhausted")
                _relay(world)
                held = operations(outbox_store).held(bound.SCOPE)
                one = operator.release(held[:1], reason=reason)
                two = operator.release(held[1:3], reason=reason)

                world.governor.reset(bound.SCOPE)
                plan = _plan(world, mandate.mandate_id, range(11, 14), capture=True)
                _relay(world)
                ids = [
                    c.message_id for c in operations(outbox_store).compensables(str(plan.plan_id))
                ]
                # Within the deadline by the wall clock, which stamps deliveries.
                c_one = operator.compensate(ids[:1], plan_id=str(plan.plan_id), reason=reason)
                c_two = operator.compensate(ids[1:3], plan_id=str(plan.plan_id), reason=reason)
            finally:
                op_log.close()
                outbox_store.close()
        finally:
            built.close()

    def size(record: Any) -> int:
        return len(record.signing_input())

    measured = {
        "operator.intent": (size(one.intent), size(two.intent) - size(one.intent)),
        "compensate": (size(c_one.intent), size(c_two.intent) - size(c_one.intent)),
        "operator.applied": (size(one.record), size(two.record) - size(one.record)),
    }
    assert len(one.rows) == 1 and len(two.rows) == 2, (one.rows, two.rows)
    return measured


def fits(first: int, each: int) -> int:
    """How many items fit in :data:`LIMIT` bytes."""
    return 1 + (LIMIT - first) // each


def table(measured: dict[str, tuple[int, int]]) -> list[str]:
    lines = [
        f"| ILOK1 record | Bytes signed, first item | Each further item | Fits in {LIMIT} |",
        "|---|---|---|---|",
    ]
    for key, label, unit in ROWS:
        first, each = measured[key]
        lines.append(f"| {label} | {first} | {each} per {unit} | {fits(first, each)} {unit}s |")
    return lines


def spec_table() -> list[str]:
    text = SPEC.read_text(encoding="utf-8")
    found = re.findall(r"^ *(\| (?:ILOK1 record|`operator\.)[^\n]*\|)$", text, re.M)
    return [line.strip() for line in found]


def main(argv: list[str]) -> int:
    lines = table(measure())
    print("\n".join(lines))
    if "--check" in argv:
        wanted = [line for line in lines if not line.startswith("|---")]
        if spec_table() != wanted:
            print("docs/FIDUCIARY_SPEC.md §3.5 says otherwise:", *spec_table(), sep="\n")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""A system of record's log, reconciled against a bundle's receipts (1.10.0).

An auditor who holds a lineage's bundle and the log a system keeps of what was
done to it (Stripe's events, SendGrid's event webhook, an EDI gateway's
messages, a Kubernetes audit log, FHIR AuditEvents, GitHub's audit log) can
match the two without the deployment's ``receipts.jsonl``. The bundle carries
every action receipt of the lineage and, since 1.10.0, every delivery receipt
the relay's settler issued for one of them.

The bundle is verified first, against the pinned charter and keys. Its
receipts are used only when the container, the pinned charter and the receipts
verify (V1, V2 and V5): a log matched against receipts that do not, or that
are not shown to be the pinned deployment's, would prove nothing.
Another check that fails, such as V7 naming a break-glass act, is reported
beside the reconciliation, which still runs, since those receipts are sound.
The matching is AgentGov's ``reconcile-actions``, unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fiduciary.bundle.format import BundleError, BundleReport, unpack
from fiduciary.bundle.verify import TrustedKeys, verify_bundle

if TYPE_CHECKING:
    from agentgov.receipts import ActionReceipt, DeliveryReceipt
    from agentgov.reconciliation import ActionReport

__all__ = ["Reconciled", "bundle_receipts", "reconcile_bundle"]

TRUSTED: tuple[str, ...] = ("V1", "V2", "V5")
"""The checks a bundle's receipts must pass to be matched against a log."""


@dataclass(frozen=True, slots=True)
class Reconciled:
    """A log matched against a bundle: the bundle's verdict, and the match."""

    bundle: BundleReport
    actions: ActionReport
    receipts: int
    deliveries: int
    scope: str


def bundle_receipts(data: bytes) -> list[ActionReceipt | DeliveryReceipt]:
    """The receipts a bundle carries, action and delivery, in name order. Not
    verified here: :func:`reconcile_bundle` verifies the bundle first."""
    from agentgov.receipts import ReceiptBundle

    found = []
    for path, body in unpack(data):
        if path.startswith("receipts/") and path != "receipts/transitions.json":
            found.append(ReceiptBundle.loads(body).receipt)
    return found


def reconcile_bundle(
    data: bytes,
    export: str,
    *,
    charter: str,
    keys: TrustedKeys | None = None,
    fmt: str = "records",
    fields: Mapping[str, str] | None = None,
    actors: Sequence[str] = (),
    substrates: Sequence[str] = (),
    measurements: Sequence[str] = (),
    all_receipts: bool = False,
) -> Reconciled:
    """Verify the bundle ``data``, then match the log at ``export`` (in
    ``fmt``, as ``agentgov reconcile-actions --format`` reads it) against
    its receipts.

    ``substrates`` defaults, as AgentGov's does, to the system the format
    reads, unless ``all_receipts``.

    :raises BundleError: When the bundle cannot be read, or its container or
        receipts do not verify.
    """
    from agentgov.reconciliation import load_actions, parsers, reconcile_actions

    report = verify_bundle(data, charter=charter, keys=keys)
    failed = [f for f in report.findings if f.check in TRUSTED]
    if failed:
        raise BundleError(
            f"the bundle's receipts cannot be relied on: {failed[0].check} {failed[0].message}"
        )
    receipts = bundle_receipts(data)
    scoped = list(substrates)
    if not scoped and not all_receipts:
        system = parsers("actions")[fmt].system
        scoped = [system] if system else []
    try:
        records = load_actions(export, fmt=fmt, fields=dict(fields or {}))
    except Exception as exc:  # AgentGov raises its own errors on a bad export
        raise BundleError(f"{export}: {exc}") from exc
    actions = reconcile_actions(
        receipts,
        records,
        actors=actors,
        substrates=scoped,
        measurements=list(measurements),
    )
    scope = " and ".join(
        part
        for part in (
            f"writes to {', '.join(scoped)}" if scoped else "",
            f"measured {', '.join(measurements)}" if measurements else "",
        )
        if part
    )
    from agentgov.receipts import DeliveryReceipt

    deliveries = sum(isinstance(r, DeliveryReceipt) for r in receipts)
    return Reconciled(report, actions, len(receipts), deliveries, scope)


def summary(found: Reconciled) -> dict[str, Any]:
    """What the console and ``demo.json`` keep of a reconciliation."""
    actions = found.actions
    return {
        "matched": len(actions.matched),
        "receipts": len({m.receipt_id for m in actions.matched}),
        "phantom": len(actions.phantom),
        "phantoms": [f"{r.action} {r.resource}" for r in actions.phantom],
        "unobserved": len(actions.unobserved),
        "out_of_scope": actions.out_of_scope,
        "bundle_receipts": found.receipts,
        "bundle_deliveries": found.deliveries,
        "bundle_exit_code": found.bundle.exit_code,
    }

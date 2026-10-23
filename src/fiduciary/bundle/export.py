"""Exporting a lineage's audit bundle (``docs/FIDUCIARY_SPEC.md`` §9.1, §9.2).

:func:`export_bundle` writes, for a lineage and its descendants over a range
of the FIDU1 log, every member ``docs/BUNDLE_FORMAT.md`` lists:

- the log's whole prefix to the range's end, every document a record names
  (charters, mandates, tickets, approvals, warrants, narrowed resolutions),
  the intake submissions the approval records answer, and the tool bindings
  the mandates pin. A decision is taken from the state before it, so a
  verifier needs the authority history that led to the range;
- the lineage's footprint in the AgentGov ledger up to the anchor of the
  range's last record, each entry with its RFC 9162 inclusion proof against a
  ledger checkpoint the exporter signs with the ledger's checkpoint key, and
  any witness cosignatures;
- the lineage's ARC1 receipts, as bundles with their inclusion proofs; the
  ILOK1 records of its warrants, its messages, its recoveries and its
  extensions; and its escrow chain;
- the deployment snapshot compilation reads, and the public keys.

Everything is cut at the ledger sequence of the anchor of the range's last
record, and every instant in the bundle comes from the logs it copies, so
one lineage over one range exports to identical bytes (§9.2). Nothing in it
is row data, a payload or a transcript (F9-1): the logs it copies hold only
digests, ids, amounts and the developer's own statements.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from agentgov.core import EntryType
from agentgov.receipts import ActionReceipt, Checkpoint, DeliveryReceipt, canonical_bytes
from agentgov.receipts.merkle import MerkleTree, leaf_hash
from agentgov.receipts.signing import Signer, Verifier, verifier_of
from interlock.chain import EscrowChain, EscrowRecord
from interlock.records import SignedRecord

from fiduciary import __version__, fidu1
from fiduciary.bundle.format import FORMAT, BundleError, pack
from fiduciary.checkers import admits_scope
from fiduciary.compiler import Deployment, canonical_digests
from fiduciary.documents import Record, anchor_memo
from fiduciary.log import ANCHOR_SCOPE, LOG_ID
from fiduciary.store import State, Store

if TYPE_CHECKING:
    from agentgov import BudgetManager
    from agentgov.core import LedgerEntry
    from agentgov.receipts import Cosignature, ReceiptLog
    from interlock.deliveries import OutboxOperations
    from interlock.outbound import SinkSpec

__all__ = ["Evidence", "export_bundle", "sink_json", "spec"]

_HEX: Final = re.compile(r"[0-9a-f]{64}")
_WARRANT: Final = re.compile(r"FIDU1 warrant ([0-9a-f]{64})")
_ILOK1_ANCHOR: Final = re.compile(r"ILOK1 (\S+) (\d+) ([0-9a-f]{64})")
_US: Final = timedelta(microseconds=1)


@dataclass(frozen=True, slots=True)
class Evidence:
    """What the exporter reads beside the store.

    :param ledger: A governor of the deployment's AgentGov ledger.
    :param ledger_signer: The ledger's checkpoint key: it signs the checkpoint
        every footprint entry is proven against (V6).
    :param receipts: The receipt log the lineage's engines issue into, and
        ``receipt_keys``, the keys its receipts and checkpoints verify under.
    :param operator_records: Interlock's operator log, as read; with
        ``operator_keys``, every operator's name and public key.
    :param runtime_records: The ILOK1 log of recovery and extension records,
        and ``runtime_key``, the key it is signed with.
    :param chains: Where each lineage's escrow chain lives.
    :param outbox: Interlock's outbox operations, to find the messages of the
        lineage's plans (``plan_of``); without it, the lineage's messages are
        those its tickets and warrants name.
    :param witnesses: Ledger witnesses to cosign the checkpoint (each with
        ``cosign``, ``latest`` and ``path``, as AgentGov's ``FileWitness``).
    :param service: The service key; when a signer, it also signs the manifest.
    """

    ledger: BudgetManager
    ledger_signer: Signer
    receipts: ReceiptLog | None = None
    receipt_keys: Sequence[Verifier] = ()
    operator_records: Sequence[SignedRecord] = ()
    operator_keys: Mapping[str, str] = field(default_factory=dict)
    runtime_records: Sequence[SignedRecord] = ()
    runtime_key: Verifier | None = None
    chains: Path | None = None
    outbox: OutboxOperations | None = None
    witnesses: Sequence[Any] = ()
    service: Signer | Verifier | None = None


def _json(value: Any) -> bytes:
    return canonical_bytes(value)


def spec(key: Any) -> str:
    """A public key as ``<alg>:<hex>``."""
    return str(key.spec())


def _at(record: SignedRecord) -> datetime:
    return datetime.fromisoformat(record.issued_at)


def sink_json(spec: SinkSpec) -> dict[str, Any]:
    """An installed sink, as the bundle's deployment snapshot holds it."""
    return {
        "name": spec.name,
        "operations": [
            {"name": op.name, "compensation": op.compensation, "schema": op.schema}
            for op in spec.operations
        ],
        "cost_per_call": format(spec.cost_per_call, "f"),
        "idempotency": spec.idempotency,
        "max_payload_bytes": spec.max_payload_bytes,
        "not_after_us": spec.not_after // _US,
        "max_attempts": spec.max_attempts,
        "backoff_base_us": spec.backoff_base // _US,
        "backoff_cap_us": spec.backoff_cap // _US,
        "unknown_outcome": spec.unknown_outcome,
        "kind": spec.kind,
    }


def _digests(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        if _HEX.fullmatch(value):
            yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _digests(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _digests(item)


def _place(kind: str, digest: str, raw: bytes) -> str:
    """Where a document of ``kind`` goes in the bundle."""
    if kind == "approval":
        subject = json.loads(raw)["subject"]["type"]
        folder = {"charter": "charter/approvals", "suspension": "charter/approvals"}.get(
            subject, "mandates/approvals" if subject == "mandate" else "approvals"
        )
        return f"{folder}/{digest}.json"
    folder = {
        "charter": "charter",
        "mandate": "mandates",
        "ticket": "tickets",
        "warrant": "warrants",
        "resolution": "resolutions",
    }[kind]
    return f"{folder}/{digest}.json"


def export_bundle(
    store: Store,
    mandate_id: str,
    *,
    deployment: Deployment,
    evidence: Evidence,
    from_seq: int | None = None,
    to_seq: int | None = None,
    log_id: str = LOG_ID,
) -> bytes:
    """The bundle of lineage ``mandate_id`` and its descendants over records
    ``from_seq`` to ``to_seq`` of the FIDU1 log (module docstring).

    :raises BundleError: On a range the log does not hold, a lineage it does
        not hold, or a range whose last record the ledger has not anchored.
    """
    stored = list(store.records(log_id))
    head = len(stored)
    first, last = from_seq or 1, to_seq or head
    if not 1 <= first <= last <= head:
        raise BundleError(f"the log holds records 1 to {head}, not {first} to {last}")
    records = [Record.loads(row.data) for row in stored[:last]]
    state = State.of(records)
    if mandate_id not in state.lineages:
        raise BundleError(f"no lineage {mandate_id} is issued by record {last}")
    lineages = [mandate_id, *state.descendants(mandate_id)]
    ancestors = []
    current = state.parent_lineage(mandate_id)
    while current is not None:
        ancestors.append(current)
        current = state.parent_lineage(current)
    members: dict[str, bytes] = {}

    # -- the authority history: the log's prefix, its documents and intake ------
    members["log.jsonl"] = b"".join(row.data + b"\n" for row in stored[:last])
    wanted: set[str] = set()
    for record in records:
        wanted.update(_digests(record.content))
    # The prefix names nearly every document the store holds: one read.
    documents = {digest: (kind, data) for digest, kind, data in store.documents()}
    held: dict[str, tuple[str, bytes]] = {}
    while wanted:
        digest = wanted.pop()
        found = documents.get(digest)
        if digest in held or found is None:
            continue
        held[digest] = found
        wanted.update(_digests(json.loads(found[1])))
    subjects: set[str] = set()
    pinned: set[str] = set()
    sinks: set[str] = set()
    for digest, (kind, raw) in sorted(held.items()):
        members[_place(kind, digest, raw)] = raw
        if kind == "mandate":
            mandate = json.loads(raw)
            if mandate["mandate_id"] in lineages:
                subjects.add(mandate["subject"]["scope"])
            pinned.update(pin["digest"] for pin in mandate["tools"]["bindings"])
            sinks.update(mandate["outbound"]["sinks"])
    for digest in sorted(pinned):
        tool = deployment.tools.get(digest)
        if tool is not None:
            members[f"tools/{digest}.json"] = tool.canonical()
    answered = {
        int(r.content["intake"])
        for r in records
        if r.kind in ("approval.recorded", "approval.refused")
    }
    for intake, data in store.intake():
        if intake in answered:
            members[f"intake/{intake:010d}.bin"] = data

    # -- the ledger: the footprint, cut at the anchor of the last record --------
    ledger = evidence.ledger
    ledger.refresh()
    entries = list(ledger.audit_trail())
    memo = anchor_memo(records[-1])
    cut_entry = next(
        (e for e in entries if e.entry_type is EntryType.ANCHOR and e.memo == memo), None
    )
    if cut_entry is None:
        raise BundleError(f"record {last} is not anchored in the ledger yet: export after it is")
    cut, cut_at = cut_entry.sequence, cut_entry.timestamp
    entries = entries[:cut]

    # -- ILOK1: recoveries and extensions; operator acts on the lineage ----------
    runtime = [
        r
        for r in evidence.runtime_records
        if _at(r) <= cut_at and any(admits_scope(s, r.scope) for s in subjects)
    ]
    messages, warrants = _lineage_messages(state, store, lineages, held)
    chains = _chains(evidence.chains, lineages)
    plans = {str(r.plan_id) for _, kept in chains.values() for r in kept if r.timestamp <= cut_at}
    operators = _operator_records(evidence, cut_at, warrants, messages, plans)
    for name, chosen in (("runtime", runtime), ("operators", operators)):
        members[f"ilok1/{name}.jsonl"] = b"".join(_json(r.to_json()) + b"\n" for r in chosen)
    ilok1 = {(r.log, r.seq, r.record_hash) for r in (*runtime, *operators)}

    footprint = _footprint(entries, subjects, ilok1)
    members.update(
        _ledger_members(entries, footprint, cut, cut_entry, evidence.ledger_signer, evidence)
    )

    # -- receipts and escrow chains ---------------------------------------------------
    members.update(_receipts(evidence, lineages, cut))
    for lineage, (path, kept) in chains.items():
        count = sum(1 for r in kept if r.timestamp <= cut_at)
        if count:
            lines = path.read_bytes().splitlines(keepends=True)
            members[f"escrow/{lineage}.chain"] = b"".join(lines[:count])

    # -- the deployment compilation reads, and the keys ------------------------------
    depths = {}
    for mandate_id_ in [*lineages, *ancestors]:
        root = state.lineages.get(mandate_id_)
        if root is None:
            continue
        doc = json.loads(held[root[0][1]][1]) if root[0][1] in held else None
        if doc is not None and doc["parent"] is None:
            scope = doc["subject"]["funded_from"]
            depths[scope] = ledger.node(scope).depth
    members["deployment.json"] = _json(
        {
            "ledger": deployment.ledger,
            "tables": {t: sorted(cols) for t, cols in sorted(deployment.tables.items())},
            "sinks": [
                sink_json(spec)
                for spec in (deployment.sinks.get(n) for n in sorted(sinks))
                if spec is not None
            ],
            "directives": dict(sorted(deployment.directives.items())),
            "depths": dict(sorted(depths.items())),
        }
    )
    members["keys.json"] = _json(_keys(evidence, held))

    # -- the manifest ---------------------------------------------------------------
    manifest: dict[str, Any] = {
        "v": "FIDU1",
        "type": "bundle",
        "format": FORMAT,
        "exporter": f"fiduciary {__version__}",
        "canonical_digests": canonical_digests(),
        "lineage": mandate_id,
        "lineages": sorted(lineages),
        "subjects": sorted(subjects),
        "range": {"from": first, "to": last},
        "ledger": {"first": entries[0].entry_hash, "cut": cut},
        "members": [
            {"path": path, "type": _type_of(path), "sha256": hashlib.sha256(data).hexdigest()}
            for path, data in sorted(members.items())
        ],
    }
    if hasattr(evidence.service, "sign"):
        signature = fidu1.sign("bundle", manifest, evidence.service)  # type: ignore[arg-type]
        manifest["sig"] = signature.to_json()
    members["manifest.json"] = _json(manifest)
    return pack(members)


def _type_of(path: str) -> str:
    top = path.split("/")[0]
    if path.startswith(("charter/approvals/", "mandates/approvals/")) or top == "approvals":
        return "approval"
    return {
        "charter": "charter",
        "mandates": "mandate",
        "tools": "tool",
        "tickets": "ticket",
        "warrants": "warrant",
        "resolutions": "resolution",
        "intake": "intake",
        "receipts": "receipt",
        "ilok1": "ilok1",
        "escrow": "escrow",
        "ledger": "ledger",
    }.get(top, path.removesuffix(".json").removesuffix(".jsonl"))


def _lineage_messages(
    state: State, store: Store, lineages: Sequence[str], held: Mapping[str, tuple[str, bytes]]
) -> tuple[set[str], set[str]]:
    """The outbox messages the lineage's tickets and warrants name, and its
    warrants' digests."""
    messages: set[str] = set()
    warrants: set[str] = set()
    for digest, warrant in state.warrants.items():
        if warrant.mandate_id in lineages:
            warrants.add(digest)
    for ticket_row in state.tickets.values():
        if ticket_row.mandate_id not in lineages:
            continue
        raw = held.get(ticket_row.digest)
        if raw is None:
            continue
        ticket = json.loads(raw[1])
        for message in _message_ids(ticket.get("evidence", {})):
            messages.add(message)
    return messages, warrants


def _message_ids(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        if "id" in value and "head" in value and isinstance(value["id"], str):
            yield value["id"]
        for item in value.values():
            yield from _message_ids(item)
    elif isinstance(value, list):
        for item in value:
            yield from _message_ids(item)


def _chains(
    chains: Path | None, lineages: Sequence[str]
) -> dict[str, tuple[Path, tuple[EscrowRecord, ...]]]:
    """Each lineage's escrow chain, read once: its file and its records."""
    found: dict[str, tuple[Path, tuple[EscrowRecord, ...]]] = {}
    for lineage in lineages:
        path = None if chains is None else chains / f"{lineage}.chain"
        if path is not None and path.exists():
            found[lineage] = (path, EscrowChain.load(path).records())
    return found


def _operator_records(
    evidence: Evidence,
    cut_at: datetime,
    warrants: set[str],
    messages: set[str],
    plans: set[str],
) -> list[SignedRecord]:
    """The operator log's records on the lineage: intents naming one of its
    warrants or acting on one of its messages, their outcomes, every key
    registration, and every install record."""
    outbox = evidence.outbox
    chosen: list[SignedRecord] = []
    intents: set[str] = set()
    plan_of: dict[str, str | None] = {}

    def planned(message: str) -> bool:
        assert outbox is not None
        if message not in plan_of:
            plan_of[message] = _plan_of(outbox, message)
        return plan_of[message] in plans

    for record in evidence.operator_records:
        if _at(record) > cut_at:
            continue
        body = record.body
        if record.kind in ("key.registered", "operator.installed"):
            chosen.append(record)
            continue
        if record.kind == "operator.intent":
            named = _WARRANT.search(str(body.get("reason", "")))
            targets = [str(t.get("message")) for t in body.get("targets", ())]
            ours = any(t in messages for t in targets)
            if not ours and outbox is not None:
                ours = any(planned(t) for t in targets)
            if (named is not None and named.group(1) in warrants) or ours:
                chosen.append(record)
                intents.add(record.record_hash)
            continue
        if body.get("intent", {}).get("hash") in intents:
            chosen.append(record)
    return chosen


def _plan_of(outbox: OutboxOperations, message: str) -> str | None:
    import uuid

    try:
        found = outbox.plan_of(uuid.UUID(message))
    except (ValueError, LookupError):
        return None
    return None if found is None else str(found)


def _footprint(
    entries: Sequence[LedgerEntry], subjects: set[str], ilok1: set[tuple[str, int, str]]
) -> list[LedgerEntry]:
    """The lineage's footprint: every entry on a subject scope or a
    satellite, the log's anchors, the anchors of the ILOK1 records the bundle
    carries, and the other lines of each transaction those are in."""
    chosen = set()
    for entry in entries:
        on_scope = any(admits_scope(s, entry.scope_id) for s in subjects)
        anchor = entry.entry_type is EntryType.ANCHOR
        ours = anchor and entry.scope_id == ANCHOR_SCOPE and entry.memo.startswith("FIDU1 ")
        if anchor and not ours:
            found = _ILOK1_ANCHOR.fullmatch(entry.memo)
            ours = found is not None and (found[1], int(found[2]), found[3]) in ilok1
        if on_scope or ours:
            chosen.add(entry.transaction_id)
    return [e for e in entries if e.transaction_id in chosen]


def _ledger_members(
    entries: Sequence[LedgerEntry],
    footprint: Sequence[LedgerEntry],
    cut: int,
    cut_entry: LedgerEntry,
    signer: Signer,
    evidence: Evidence,
) -> dict[str, bytes]:
    tree = MerkleTree(leaf_hash(bytes.fromhex(e.entry_hash)) for e in entries)
    root = tree.root()
    checkpoint = Checkpoint(
        log_id=f"agentgov-ledger:{entries[0].entry_hash}",
        tree_size=cut,
        root_hash=root.hex(),
        issued_at=cut_entry.timestamp,
    ).sign(signer)
    lines = []
    for entry in footprint:
        proof = tree.inclusion_proof(entry.sequence - 1, cut)
        lines.append(
            _json(
                {
                    "entry": entry.to_audit_record(),
                    "index": entry.sequence - 1,
                    "proof": [p.hex() for p in proof],
                }
            )
            + b"\n"
        )
    cosignatures = _cosign(checkpoint, tree, evidence.witnesses)
    return {
        "ledger/checkpoint.json": _json(checkpoint.to_json()),
        "ledger/cosignatures.json": _json([c.to_json() for c in cosignatures]),
        "ledger/entries.jsonl": b"".join(lines),
    }


def _cosign(
    checkpoint: Checkpoint, tree: MerkleTree, witnesses: Sequence[Any]
) -> list[Cosignature]:
    """Each witness's cosignature of ``checkpoint``: asked for once, and read
    back from the witness's own file ever after, so a second export of the
    same range carries the same bytes."""
    from agentgov.receipts import load_cosignatures

    found: list[Cosignature] = []
    for witness in witnesses:
        seen = witness.latest(checkpoint.log_id)
        if seen is None or seen.tree_size < checkpoint.tree_size:
            old = 0 if seen is None else seen.tree_size
            witness.cosign(checkpoint, tree.consistency_proof(old, checkpoint.tree_size))
        found.extend(
            c
            for c in load_cosignatures(witness.path)
            if (c.log_id, c.tree_size, c.root_hash)
            == (checkpoint.log_id, checkpoint.tree_size, checkpoint.root_hash)
        )
    return found


def _receipts(evidence: Evidence, lineages: Sequence[str], cut: int) -> dict[str, bytes]:
    log = evidence.receipts
    if log is None:
        return {}
    issuers = {f"fiduciary:{m}" for m in lineages}
    documents = log.receipts()
    chosen: list[tuple[int, str]] = []
    actions: set[str] = set()
    for index, document in enumerate(documents):
        anchor = document.anchors.agentgov
        if anchor is None or anchor.seq > cut:
            continue
        if isinstance(document, ActionReceipt) and document.issuer in issuers:
            chosen.append((index, document.receipt_id))
            actions.add(document.receipt_id)
    for index, document in enumerate(documents):
        if isinstance(document, DeliveryReceipt) and document.action.receipt_id in actions:
            anchor = document.anchors.agentgov
            if anchor is not None and anchor.seq <= cut:
                chosen.append((index, document.receipt_id))
    if not chosen:
        return {}
    needed = max(index for index, _ in chosen) + 1
    covering = sorted(
        (c for c in log.checkpoints() if c.tree_size >= needed), key=lambda c: c.tree_size
    )
    checkpoint = covering[0] if covering else log.checkpoint()
    members = {
        f"receipts/{receipt_id}.json": _json(log.bundle(index, checkpoint).to_json())
        for index, receipt_id in chosen
    }
    members["receipts/transitions.json"] = _json([t.to_json() for t in log.transitions()])
    return members


def _keys(evidence: Evidence, held: Mapping[str, tuple[str, bytes]]) -> dict[str, Any]:
    """The public keys the bundle verifies under. Principals' keys are the
    charters' (the pinned digest vouches for them); every other key is the
    exporter's statement, which an auditor pins with ``--keys``."""
    principals: dict[str, str] = {}
    for kind, raw in held.values():
        if kind == "charter":
            for principal in json.loads(raw)["principals"]:
                principals[principal["name"]] = principal["key"]
    service = evidence.service
    service_key = None
    if service is not None:
        service_key = spec(verifier_of(service) if hasattr(service, "sign") else service)  # type: ignore[arg-type]
    return {
        "principals": dict(sorted(principals.items())),
        "service": service_key,
        "ledger": spec(verifier_of(evidence.ledger_signer)),
        "receipts": sorted(spec(k) for k in evidence.receipt_keys),
        "runtime": None if evidence.runtime_key is None else spec(evidence.runtime_key),
        "operators": dict(sorted(evidence.operator_keys.items())),
    }

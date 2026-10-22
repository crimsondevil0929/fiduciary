"""Verifying a bundle offline (``docs/FIDUCIARY_SPEC.md`` §9.3, §9.4).

:func:`verify_bundle` runs V1 to V12 over a bundle's bytes with no database
and no network (F9-3), and reports every finding of every check. It trusts
two things from outside the bundle: the charter's pinned digest, and,
optionally, the auditor's keys (``--keys``: the service, ledger checkpoint,
receipt log, runtime and operator keys). Without them it verifies under the
keys ``keys.json`` names, and says so: those are the exporter's statement.
Principals' keys always come from the charter the pinned digest vouches for.

The decisions in the log are taken again from the state before each record,
by the same rules the service took them by (:func:`fiduciary.authority.replay_each`),
over an in-memory store holding only what the bundle carries.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import tempfile
import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from agentgov.core import Direction, EntryType, LedgerEntry
from agentgov.receipts import (
    ActionReceipt,
    Checkpoint,
    Cosignature,
    DeliveryReceipt,
    Keyring,
    KeyTransition,
    ReceiptBundle,
    find_cosignature,
    parse_key,
    verify_inclusion,
)
from agentgov.receipts import (
    verify_bundle as verify_receipt,
)
from agentgov.receipts.merkle import leaf_hash
from agentgov.receipts.signing import Verifier
from interlock.chain import EscrowChain, EscrowRecord, RecordType
from interlock.outbound import OperationSpec, SinkRegistry, SinkSpec
from interlock.records import SignedRecord

from fiduciary import documents, fidu1
from fiduciary.authority import Decide, replay_each
from fiduciary.bundle.export import _type_of
from fiduciary.bundle.format import FORMAT, BundleError, BundleReport, unpack
from fiduciary.checkers import admits_scope
from fiduciary.compiler import CompiledMandate, Deployment, compile, receipt_problems
from fiduciary.documents import Charter, Mandate, Record, ToolBinding
from fiduciary.exceptions import FiduciaryError
from fiduciary.log import LOG_ID, check_anchors, parse_memo
from fiduciary.store import State, Store, StoredRecord
from fiduciary.verify import ordering_problems

__all__ = ["TrustedKeys", "verify_bundle"]

_SESSION: Final = re.compile(r"FIDU1 session ([0-9a-f-]{36}) ")
_WARRANT: Final = re.compile(r"FIDU1 warrant ([0-9a-f]{64})")
_RESET: Final = re.compile(r"FIDU1 warrant ([0-9a-f]{64}) reset (\S+)")
_ILOK1: Final = re.compile(r"ILOK1 (\S+) (\d+) ([0-9a-f]{64})")
_INTERLOCK: Final = re.compile(r"interlock:([0-9a-f]{16})")
_BIND: Final = re.compile(r"FIDU1 bind ([0-9a-f]{64})")
_GRANT: Final = re.compile(
    r"extension (\S+) for quote \S+, approved by FIDU1 warrant ([0-9a-f]{64})"
)
_CARRIED: Final = re.compile(r"carried into \S+ by extension (\S+)")
_RESERVE: Final = re.compile(r"recovery reserve for (\S+?)(?:, carved out)?")
_RETURNED: Final = re.compile(r"unused recovery reserve for (\S+) returned")
_STEP: Final = re.compile(r"recovery step (\d+)(?:: \S+| settled)")
_TOOL: Final = re.compile(r"(?:repair of plan-[0-9a-f]+: )?FIDU1 tool (\S+) ([0-9a-f]{16})")
_TIME_WORDS: Final = ("expired", "runs until", "not_after", "after its deadline")


@dataclass(frozen=True, slots=True)
class TrustedKeys:
    """The keys an auditor pins, as ``keys.json`` writes them (each
    ``<alg>:<hex>``): ``service``, ``ledger``, ``receipts`` (a list),
    ``runtime`` and ``operators`` (by name)."""

    raw: Mapping[str, Any]

    @classmethod
    def loads(cls, data: bytes | str) -> TrustedKeys:
        found = fidu1.parse(data)
        if not isinstance(found, dict):
            raise BundleError("keys.json is a JSON object")
        return cls(found)


class _MemoryStore(Store):
    """What the bundle carries, as a store the rules can read."""

    def __init__(
        self,
        docs: Mapping[str, tuple[str, bytes]],
        records: Sequence[StoredRecord],
        intake: Mapping[int, bytes],
    ) -> None:
        super().__init__()
        self._docs = dict(docs)
        self._records = list(records)
        self._intake = dict(intake)

    def transaction(self) -> AbstractContextManager[None]:
        return contextlib.nullcontext()

    def claim(self, log: str) -> AbstractContextManager[None]:
        return contextlib.nullcontext()

    def put_document(self, digest: str, kind: str, data: bytes) -> None:
        self._docs.setdefault(digest, (kind, data))

    def document(self, digest: str) -> tuple[str, bytes] | None:
        return self._docs.get(digest)

    def documents(self) -> Iterator[tuple[str, str, bytes]]:
        for digest, (kind, data) in sorted(self._docs.items()):
            yield digest, kind, data

    def head(self, log: str) -> tuple[int, str]:
        return (len(self._records), self._records[-1].hash) if self._records else (0, fidu1.GENESIS)

    def insert_record(self, record: StoredRecord) -> None:  # pragma: no cover - read-only
        raise FiduciaryError("a bundle is read-only")

    def records(self, log: str) -> Iterator[StoredRecord]:
        yield from self._records

    def insert_row(self, table: str, row: tuple[Any, ...]) -> None:  # pragma: no cover
        raise FiduciaryError("a bundle is read-only")

    def rows(self, table: str) -> list[tuple[Any, ...]]:  # pragma: no cover - no projections
        return []

    def submit(self, data: bytes) -> int:  # pragma: no cover - read-only
        raise FiduciaryError("a bundle is read-only")

    def intake(self) -> Iterator[tuple[int, bytes]]:
        yield from sorted(self._intake.items())

    def close(self) -> None:
        pass


def _entry(raw: Mapping[str, str]) -> LedgerEntry:
    """A ledger entry from its audit record (``LedgerEntry.to_audit_record``)."""
    return LedgerEntry(
        sequence=int(raw["sequence"]),
        entry_id=uuid.UUID(raw["entry_id"]),
        transaction_id=uuid.UUID(raw["transaction_id"]),
        timestamp=datetime.fromisoformat(raw["timestamp"]),
        entry_type=EntryType(raw["entry_type"]),
        direction=Direction(raw["direction"]),
        scope_id=raw["scope_id"],
        counterparty_id=raw["counterparty_id"] or None,
        amount=Decimal(raw["amount"]),
        balance_after=Decimal(raw["balance_after"]),
        prev_hash=raw["prev_hash"],
        entry_hash=raw["entry_hash"],
        memo=raw["memo"],
        ref=uuid.UUID(raw["ref"]) if raw["ref"] else None,
        version=raw["version"],
    )


def _sink(raw: Mapping[str, Any]) -> SinkSpec:
    us = timedelta(microseconds=1)
    return SinkSpec(
        raw["name"],
        tuple(
            OperationSpec(op["name"], compensation=op["compensation"], schema=op["schema"])
            for op in raw["operations"]
        ),
        cost_per_call=Decimal(raw["cost_per_call"]),
        idempotency=raw["idempotency"],
        max_payload_bytes=raw["max_payload_bytes"],
        not_after=raw["not_after_us"] * us,
        max_attempts=raw["max_attempts"],
        backoff_base=raw["backoff_base_us"] * us,
        backoff_cap=raw["backoff_cap_us"] * us,
        unknown_outcome=raw["unknown_outcome"],
        kind=raw["kind"],
    )


def _keyring(keys: Iterable[str]) -> Keyring:
    return Keyring([k for k in keys if k])


@dataclass(slots=True)
class _Bundle:
    """What V1 let through: every member that matches its digest and parses."""

    manifest: dict[str, Any] = field(default_factory=dict)
    docs: dict[str, tuple[str, bytes]] = field(default_factory=dict)
    stored: list[StoredRecord] = field(default_factory=list)
    records: list[Record] = field(default_factory=list)
    intake: dict[int, bytes] = field(default_factory=dict)
    entries: list[tuple[LedgerEntry, int, list[bytes]]] = field(default_factory=list)
    checkpoint: Checkpoint | None = None
    cosignatures: list[Cosignature] = field(default_factory=list)
    receipts: dict[str, ReceiptBundle] = field(default_factory=dict)
    transitions: list[KeyTransition] = field(default_factory=list)
    runtime: list[SignedRecord] = field(default_factory=list)
    operators: list[SignedRecord] = field(default_factory=list)
    chains: dict[str, bytes] = field(default_factory=dict)
    deployment: dict[str, Any] = field(default_factory=dict)
    keys: dict[str, Any] = field(default_factory=dict)


def _lines(data: bytes) -> list[bytes]:
    """A line-delimited member's lines: each ends in a newline, none is empty."""
    if not data:
        return []
    if not data.endswith(b"\n"):
        raise FiduciaryError("its last line has no newline")
    lines = data[:-1].split(b"\n")
    if not all(lines):
        raise FiduciaryError("it holds an empty line")
    return lines


def _v1(members: list[tuple[str, bytes]], report: BundleReport) -> _Bundle:
    """V1 schema: every member parses strictly and matches its digest."""
    out = _Bundle()
    names = [name for name, _ in members]
    if names != sorted(names):
        report.add("V1", "malformed", "the members are not in sorted order (§9.2)")
    for name in sorted({n for n in names if names.count(n) > 1}):
        report.add("V1", "malformed", f"member {name} appears {names.count(name)} times")
    held = dict(members)
    raw = held.get("manifest.json")
    if raw is None:
        report.add("V1", "malformed", "no manifest.json")
        return out
    try:
        manifest = fidu1.parse(raw)
        if fidu1.canonical(manifest) != raw:
            raise FiduciaryError("manifest.json is not canonical JSON")
        if manifest.get("format") != FORMAT or manifest.get("type") != "bundle":
            raise FiduciaryError(f"manifest.json is no {FORMAT} manifest")
        listed = {m["path"]: m for m in manifest["members"]}
    except (FiduciaryError, KeyError, TypeError, AttributeError) as exc:
        report.add("V1", "malformed", f"manifest.json: {exc}")
        return out
    out.manifest = manifest
    for path in sorted(set(held) - set(listed) - {"manifest.json"}):
        report.add("V1", "malformed", f"member {path} is not in the manifest")
    good: dict[str, bytes] = {}
    for path, entry in sorted(listed.items()):
        data = held.get(path)
        if data is None:
            report.add("V1", "malformed", f"member {path} is listed and missing")
        elif hashlib.sha256(data).hexdigest() != entry.get("sha256"):
            report.add("V1", "malformed", f"member {path} does not match its manifest digest")
        elif entry.get("type") != _type_of(path):
            report.add("V1", "malformed", f"member {path} is listed as {entry.get('type')!r}")
        else:
            good[path] = data
    for path, data in good.items():
        try:
            _parse(path, data, out)
        except Exception as exc:  # each library's parser raises its own errors
            report.add("V1", "malformed", f"member {path} does not parse: {exc}")
    return out


def _parse(path: str, data: bytes, out: _Bundle) -> None:
    top = path.split("/")[0]
    if top in ("charter", "mandates", "tickets", "approvals", "warrants", "tools"):
        doc = documents.loads(data, signed=True)
        digest = path.rsplit("/", 1)[-1].removesuffix(".json")
        if doc.digest != digest:
            raise FiduciaryError(f"its digest is {doc.digest[:16]}, not its name's")
        out.docs[digest] = (doc.TYPE, data)
    elif top == "resolutions":
        resolution = documents.Resolution.read(fidu1.parse(data))
        if fidu1.canonical(resolution.to_json()) != data:
            raise FiduciaryError("not a canonical resolution")
        out.docs[resolution.digest] = ("resolution", data)
    elif path == "log.jsonl":
        for line in _lines(data):
            record = Record.loads(line)
            out.records.append(record)
            out.stored.append(
                StoredRecord(
                    record.log,
                    record.seq,
                    hashlib.sha256(line).hexdigest(),
                    record.prev,
                    record.kind,
                    line,
                )
            )
    elif top == "intake":
        out.intake[int(path.rsplit("/", 1)[-1].removesuffix(".bin"))] = data
    elif path == "ledger/entries.jsonl":
        for line in _lines(data):
            item = fidu1.parse(line)
            proof = [bytes.fromhex(p) for p in item["proof"]]
            out.entries.append((_entry(item["entry"]), int(item["index"]), proof))
    elif path == "ledger/checkpoint.json":
        out.checkpoint = Checkpoint.from_json(fidu1.parse(data))
    elif path == "ledger/cosignatures.json":
        out.cosignatures = [Cosignature.from_json(c) for c in fidu1.parse(data)]
    elif path == "receipts/transitions.json":
        out.transitions = [KeyTransition.from_json(t) for t in fidu1.parse(data)]
    elif top == "receipts":
        bundle = ReceiptBundle.loads(data)
        out.receipts[path.rsplit("/", 1)[-1].removesuffix(".json")] = bundle
    elif top == "ilok1":
        records = [SignedRecord.from_json(fidu1.parse(line)) for line in _lines(data)]
        (out.runtime if path.endswith("runtime.jsonl") else out.operators).extend(records)
    elif top == "escrow":
        for line in _lines(data):
            fidu1.parse(line)
        out.chains[path.rsplit("/", 1)[-1].removesuffix(".chain")] = data
    elif path == "deployment.json":
        raw = fidu1.parse(data)
        str(raw["ledger"])
        {t: frozenset(map(str, cols)) for t, cols in raw["tables"].items()}
        SinkRegistry([_sink(s) for s in raw["sinks"]])
        {str(k): str(v) for k, v in raw["directives"].items()}
        {str(k): int(v) for k, v in raw.get("depths", {}).items()}
        out.deployment = raw
    elif path == "keys.json":
        out.keys.update(fidu1.parse(data))
    else:
        raise FiduciaryError("no member of the format has this name")


@dataclass(slots=True)
class _Keys:
    service: Verifier | None
    ledger: Keyring
    receipts: Keyring
    runtime: Keyring
    operators: dict[str, str]


def _trusted(bundle: _Bundle, keys: TrustedKeys | None, report: BundleReport) -> _Keys:
    """The keys to verify under: the auditor's, else the bundle's, noted."""
    named = bundle.keys
    pinned = dict(keys.raw) if keys is not None else {}
    for role in ("service", "ledger", "receipts", "runtime", "operators"):
        if role not in pinned and named.get(role):
            report.notes.append(
                f"the {role} key(s) are the exporter's statement (keys.json): pin them with --keys"
            )
    for role in ("service", "ledger", "runtime", "operators"):
        if role in pinned and role in named and named[role] != pinned[role]:
            report.add("V1", "malformed", f"keys.json names another {role} key than the pinned one")
    if "receipts" in pinned and "receipts" in named:
        if sorted(named["receipts"] or ()) != sorted(pinned["receipts"] or ()):
            report.add("V1", "malformed", "keys.json names other receipt keys than the pinned ones")
    chosen = {role: pinned.get(role, named.get(role)) for role in named.keys() | pinned.keys()}
    service = chosen.get("service")
    operators = dict(chosen.get("operators") or {})
    return _Keys(
        service=parse_key(service) if service else None,
        ledger=_keyring([chosen.get("ledger") or ""]),
        receipts=_keyring(chosen.get("receipts") or ()),
        runtime=_keyring([chosen.get("runtime") or ""]),
        operators=operators,
    )


def verify_bundle(
    data: bytes,
    *,
    charter: str,
    keys: TrustedKeys | None = None,
    witness: Verifier | None = None,
    skew_seconds: int = 5,
) -> BundleReport:
    """Run V1 to V12 over a bundle (module docstring).

    :param charter: Charter version 1's digest, pinned from outside the bundle.
    :param keys: The auditor's keys; without them, the bundle's own, noted.
    :param witness: A ledger witness's key: its cosignature of the ledger
        checkpoint is then required.
    :param skew_seconds: How far an instant the service wrote may stray from
        the ledger order of its anchor (V12).
    :raises BundleError: When the bytes are no bundle at all (exit 2).
    """
    report = BundleReport()
    bundle = _v1(unpack(data), report)
    if not bundle.manifest:
        return report
    trust = _trusted(bundle, keys, report)
    _Checks(bundle, trust, report, charter, witness, skew_seconds).run()
    report.notes.append(
        "not claimed (F9-2): the outbox's delivery logs and interlock's verify_operators; "
        "operator acts are checked on the messages the lineage's tickets and warrants name"
    )
    return report


class _Checks:
    """V2 to V12 over what V1 let through."""

    def __init__(
        self,
        bundle: _Bundle,
        trust: _Keys,
        report: BundleReport,
        charter: str,
        witness: Verifier | None,
        skew: int,
    ) -> None:
        self.b = bundle
        self.k = trust
        self.r = report
        self.pinned = charter
        self.witness = witness
        self.skew = timedelta(seconds=skew)
        manifest = bundle.manifest
        self.lineages = set(manifest.get("lineages", ()))
        self.subjects = set(manifest.get("subjects", ()))
        rng = manifest.get("range", {})
        self.first, self.last = int(rng.get("from", 1)), int(rng.get("to", 0))
        self.store = _MemoryStore(bundle.docs, bundle.stored, bundle.intake)
        self.ledger: dict[int, LedgerEntry] = {}
        self.anchors: dict[int, LedgerEntry] = {}
        self.state = State()
        self.compiled: dict[tuple[str, int], CompiledMandate] = {}
        self.escrow: dict[str, list[EscrowRecord]] = {}
        self.break_glass: set[str] = set()
        self.depths: dict[str, Any] = {}

    def add(self, check: str, kind: str, message: str, label: str = "") -> None:
        self.r.add(check, kind, message, label)

    def run(self) -> None:
        if self.k.service is None:
            self.add("V9", "signature", "no service key: the log's signatures cannot be checked")
            return
        self._v9()
        self._v9_manifest()
        charter_ok = self._v2_pinned()
        self._v6()
        self._escrow()
        if charter_ok:
            self._replay()
        else:  # nothing to replay under: what the log says, for the checks that read it
            self.state = State.of(self.b.records)
        self._v5()
        self._v10()
        self._v7()
        self._v8_evidence()
        self._v11()
        self._v12()

    # -- V9 records --------------------------------------------------------------------

    def _v9(self) -> None:
        assert self.k.service is not None
        previous, expected = fidu1.GENESIS, 1
        for stored, record in zip(self.b.stored, self.b.records, strict=True):
            where = f"record {record.seq}"
            try:
                record.verify(self.k.service)
            except FiduciaryError as exc:
                self.add("V9", "signature", f"{where}: {exc}")
            if record.log != LOG_ID:
                self.add("V9", "signature", f"{where}: names log {record.log!r}")
            if record.seq != expected:
                self.add("V9", "signature", f"{where}: expected {expected}: records lost or moved")
            if record.prev != previous:
                self.add("V9", "signature", f"{where}: does not link to the record before it")
            previous, expected = stored.hash, record.seq + 1
        if len(self.b.records) != self.last:
            self.add(
                "V9", "signature", f"the log holds {len(self.b.records)} records, not {self.last}"
            )

    def _v9_manifest(self) -> None:
        """A signed manifest is the service's: its signature must hold."""
        assert self.k.service is not None
        manifest = dict(self.b.manifest)
        raw = manifest.pop("sig", None)
        if raw is None:
            self.r.notes.append("the manifest is not signed: the service did not vouch for it")
            return
        try:
            fidu1.verify("bundle", manifest, fidu1.Signature.from_json(raw), self.k.service)
        except FiduciaryError as exc:
            self.add("V9", "signature", f"manifest.json: {exc}")

    # -- V2 charter ----------------------------------------------------------------------

    def _v2_pinned(self) -> bool:
        found = self.b.docs.get(self.pinned)
        if found is None or found[0] != "charter":
            self.add(
                "V2",
                "authority",
                f"the bundle holds no charter with the pinned digest {self.pinned[:16]}",
            )
            return False
        charter = documents.loads(found[1])
        assert isinstance(charter, Charter)
        self.charter_v1 = charter
        ledger = self.b.manifest.get("ledger", {}).get("first")
        if charter.deployment.ledger != ledger:
            self.add(
                "V2",
                "authority",
                "the pinned charter binds another ledger than the bundle's (A5)",
            )
        principals: dict[str, str] = {}
        for kind, raw in self.b.docs.values():
            if kind == "charter":
                adopted = json.loads(raw)
                self.break_glass.update(adopted.get("break_glass", ()))
                principals.update({p["name"]: p["key"] for p in adopted["principals"]})
        named = self.b.keys.get("principals")
        if named is not None and named != principals:
            self.add("V2", "authority", "keys.json names other principals' keys than the charters")
        return True

    # -- replay: V2, V3, V8, and V4's compilations --------------------------------------

    def _replay(self) -> None:
        assert self.k.service is not None
        deployment = self._deployment()
        decide = Decide(self.store, self.pinned, self.k.service)
        for record, state, problem in replay_each(
            self.b.records, self.store, pinned=self.pinned, service_key=self.k.service
        ):
            if problem is not None:
                check, kind = self._classify(record, problem)
                self.add(check, kind, problem)
            elif record.kind == "mandate.bound" and deployment is not None:
                self._v4_bound(decide, state, record, deployment)
            self.state = state
        self.state = State.of(self.b.records)
        self._v4_receipts()

    def _classify(self, record: Record, problem: str) -> tuple[str, str]:
        kind = record.kind
        if kind in ("charter.adopted", "principal.suspended"):
            return "V2", "authority"
        if kind.startswith("approval."):
            subject = record.content.get("subject", {}).get("type")
            if subject in ("charter", "suspension"):
                return "V2", "authority"
            if subject == "mandate":
                return "V3", "authority"
            return "V8", "authority"
        if kind.startswith(("mandate.", "session.")):
            return "V3", "monotonicity" if "refine" in problem else "authority"
        if any(word in problem for word in _TIME_WORDS):
            return "V8", "time"
        return "V8", "authority"

    # -- V4 compiled policy ------------------------------------------------------------

    def _deployment(self) -> Deployment | None:
        raw = self.b.deployment
        if not raw:
            self.add("V4", "policy", "no deployment.json: the compiled policy cannot be recomputed")
            return None
        tools = {
            digest: documents.loads(data)
            for digest, (kind, data) in self.b.docs.items()
            if kind == "tool"
        }
        self.depths = dict(raw.get("depths", {}))
        return Deployment(
            ledger=raw["ledger"],
            tables={t: frozenset(cols) for t, cols in raw["tables"].items()},
            sinks=SinkRegistry([_sink(s) for s in raw["sinks"]]),
            tools={d: t for d, t in tools.items() if isinstance(t, ToolBinding)},
            directives=dict(raw["directives"]),
        )

    def _v4_bound(
        self, decide: Decide, state: State, record: Record, deployment: Deployment
    ) -> None:
        body = record.content
        mandate_id = str(body["mandate_id"])
        if mandate_id not in self.lineages:
            return
        try:
            mandate = decide.bindable(state, str(body["mandate"]))
            lineage = [mandate]
            current = state.parent_lineage(mandate_id)
            while current is not None:
                lineage.append(decide.head_mandate(state, current))
                current = state.parent_lineage(current)
            lineage.reverse()
            depth = int(self.depths.get(lineage[0].subject.funded_from, 0))
            compiled = compile(lineage, deployment=_with_depth(deployment, depth))
        except FiduciaryError as exc:
            self.add(
                "V4", "policy", f"record {record.seq}: the bound version does not compile: {exc}"
            )
            return
        if compiled.digest != body["compiled"]:
            self.add(
                "V4",
                "policy",
                f"record {record.seq}: version {mandate.version} compiles to "
                f"{compiled.digest[:16]}, not the {str(body['compiled'])[:16]} it was bound as",
            )
        self.compiled[(mandate_id, mandate.version)] = compiled

    def _v4_receipts(self) -> None:
        strict = bool(self.b.manifest.get("canonical_digests"))
        for name, bundle in sorted(self.b.receipts.items()):
            receipt = bundle.receipt
            if not isinstance(receipt, ActionReceipt):
                continue
            mandate_id = receipt.issuer.removeprefix("fiduciary:")
            compiled = self.compiled.get((mandate_id, receipt.decision.policy_epoch))
            if compiled is None:
                self.add(
                    "V4",
                    "policy",
                    f"receipt {name}: no version {receipt.decision.policy_epoch} of "
                    f"{mandate_id} was bound to issue it",
                )
                continue
            for problem in receipt_problems(receipt, compiled, strict=strict):
                self.add("V4", "policy", f"receipt {name}: {problem}")

    # -- V5 receipts -------------------------------------------------------------------

    def _v5(self) -> None:
        actions = {
            name: b for name, b in self.b.receipts.items() if isinstance(b.receipt, ActionReceipt)
        }
        for name, bundle in sorted(self.b.receipts.items()):
            action = None
            if isinstance(bundle.receipt, DeliveryReceipt):
                action = actions.get(bundle.receipt.action.receipt_id)
            report = verify_receipt(
                bundle,
                issuer_key=self.k.receipts,
                log_key=self.k.receipts,
                transitions=self.b.transitions or None,
                action=action,
            )
            if not report.passed:
                failure = report.first_failure
                text = f"{failure.name}: {failure.detail}" if failure is not None else "fails"
                kind = "signature" if "signature" in text else "evidence"
                self.add("V5", kind, f"receipt {name}: {text}")

    # -- V6 ledger ---------------------------------------------------------------------

    def _v6(self) -> None:
        checkpoint = self.b.checkpoint
        cut = int(self.b.manifest.get("ledger", {}).get("cut", 0))
        if checkpoint is None:
            self.add("V6", "evidence", "no ledger checkpoint")
            return
        first = self.b.manifest.get("ledger", {}).get("first")
        if checkpoint.log_id != f"agentgov-ledger:{first}":
            self.add("V6", "evidence", f"the checkpoint is of log {checkpoint.log_id}")
        if checkpoint.tree_size != cut:
            self.add(
                "V6", "evidence", f"the checkpoint is of {checkpoint.tree_size} entries, not {cut}"
            )
        try:
            checkpoint.verify(self.k.ledger)
        except Exception as exc:  # agentgov raises its own errors on a bad signature
            self.add("V6", "evidence", f"the checkpoint is not signed by the ledger's key: {exc}")
            return
        if self.witness is not None:
            try:
                find_cosignature(checkpoint, self.b.cosignatures, self.witness)
            except Exception as exc:
                self.add(
                    "V6", "evidence", f"no cosignature of the checkpoint by the witness: {exc}"
                )
        elif self.b.cosignatures:
            self.r.notes.append(
                "the ledger checkpoint's cosignatures were not checked: give --witness"
            )
        root = bytes.fromhex(checkpoint.root_hash)
        for entry, index, proof in self.b.entries:
            where = f"ledger entry {entry.sequence}"
            try:
                recomputed = entry.recompute_hash()
            except ValueError as exc:
                self.add("V6", "evidence", f"{where}: {exc}")
                continue
            if recomputed != entry.entry_hash:
                self.add("V6", "evidence", f"{where}: its fields do not hash to its entry hash")
            elif index != entry.sequence - 1 or not verify_inclusion(
                leaf_hash(bytes.fromhex(entry.entry_hash)), index, checkpoint.tree_size, proof, root
            ):
                self.add("V6", "evidence", f"{where}: not included in the signed checkpoint")
            else:
                self.ledger[entry.sequence] = entry
        for entry in self.ledger.values():
            found = parse_memo(entry.memo) if entry.entry_type is EntryType.ANCHOR else None
            if found is not None and found[0] == LOG_ID:
                self.anchors.setdefault(found[1], entry)

    # -- the escrow chains -----------------------------------------------------------

    def _escrow(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            for lineage, data in sorted(self.b.chains.items()):
                path = Path(scratch) / f"{lineage}.chain"
                path.write_bytes(data)
                try:
                    chain = EscrowChain.load(path)
                    chain.verify()
                    self.escrow[lineage] = list(chain.records())
                except Exception as exc:  # interlock's own integrity errors
                    self.add("V7", "evidence", f"escrow chain {lineage}: {exc}")

    # -- V10 anchors -------------------------------------------------------------------

    def _v10(self) -> None:
        in_range = [r for r in self.b.records if self.first <= r.seq <= self.last]
        problems, _, missing = check_anchors(in_range, self.ledger.values(), LOG_ID)
        for problem in problems:
            self.add("V10", "evidence", problem)
        for seq in missing:
            self.add("V10", "evidence", f"record {seq} is in range, and no anchor names it")

    # -- V7 completeness ---------------------------------------------------------------

    def _v7(self) -> None:
        state = self.state
        explained = _Explained(self, state)
        holds: dict[uuid.UUID, bool] = {}
        for seq in sorted(self.ledger):
            entry = self.ledger[seq]
            on_scope = any(admits_scope(s, entry.scope_id) for s in self.subjects)
            if not on_scope:
                continue
            why = explained.entry(entry, holds)
            holds[entry.entry_id] = why is None
            if why is not None:
                self.add(
                    "V7",
                    "ambient",
                    f"ledger entry {seq} ({entry.entry_type.value}) on {entry.scope_id}: {why}",
                )
        explained.receipts()
        explained.plans()
        explained.operators()

    # -- V8 step evidence --------------------------------------------------------------

    def _v8_evidence(self) -> None:
        operators = {r.record_hash: r for r in self.b.operators}
        runtime = {r.record_hash: r for r in self.b.runtime}
        committed = {
            r.record_hash
            for records in self.escrow.values()
            for r in records
            if r.record_type is RecordType.COMMITTED
        }
        for warrant, events in sorted(self.state.executions.items()):
            if self.state.warrants.get(warrant) is None:
                continue
            if self.state.warrants[warrant].mandate_id not in self.lineages:
                continue
            intent = next((body for event, body, _ in events if event == "intent"), None)
            for event, body, seq in events:
                if event not in ("applied", "abandoned") or intent is None:
                    continue
                for mismatch in body.get("mismatches", ()):
                    self.add(
                        "V8",
                        "authority",
                        f"record {seq}: step {mismatch['step']} acted on message "
                        f"{mismatch['message_id']} at a head the ticket did not observe "
                        f"(F7-15, U-4)",
                    )
                steps = {int(s["step"]): s for s in intent["steps"]}
                for result in body.get("results", ()):
                    step = steps.get(int(result["step"]), {})
                    self._v8_step(warrant, seq, step, result, operators, runtime, committed)

    def _v8_step(
        self,
        warrant: str,
        seq: int,
        step: Mapping[str, Any],
        result: Mapping[str, Any],
        operators: Mapping[str, SignedRecord],
        runtime: Mapping[str, SignedRecord],
        committed: set[str],
    ) -> None:
        where = f"record {seq}: step {result['step']} ({result['kind']})"
        reason = f"FIDU1 warrant {warrant}"
        kind = result["kind"]
        if "intent" in result:
            intent = operators.get(str(result["intent"]))
            if intent is None:
                self.add("V8", "authority", f"{where}: its operator intent is not in the bundle")
                return
            if intent.body.get("reason") != reason:
                self.add("V8", "authority", f"{where}: the operator intent names another reason")
            targets = {str(t["message"]) for t in intent.body.get("targets", ())}
            wanted = {str(t["message_id"]) for t in step.get("targets", ())}
            if targets != wanted:
                self.add("V8", "authority", f"{where}: the operator acted on other messages")
            if str(result.get("outcome")) not in operators:
                self.add("V8", "authority", f"{where}: its operator outcome is not in the bundle")
        elif kind == "grant" and result.get("record") is not None:
            granted = runtime.get(str(result["record"]))
            if granted is None:
                self.add("V8", "authority", f"{where}: its extension record is not in the bundle")
            elif granted.body.get("approved_by") != reason or Decimal(
                str(granted.body.get("amount"))
            ) != Decimal(str(step.get("amount"))):
                self.add("V8", "authority", f"{where}: the grant is not the one the warrant names")
        elif kind == "reset":
            anchor = self.ledger.get(int(result.get("anchor_seq", 0)))
            reset = self.ledger.get(int(result.get("reset_seq", 0)))
            if (
                anchor is None
                or reset is None
                or anchor.memo != f"{reason} reset {step.get('scope')}"
            ):
                self.add("V8", "authority", f"{where}: its anchor and reset are not in the ledger")
        elif kind == "accept_repair" and str(result.get("record")) not in committed:
            self.add("V8", "authority", f"{where}: the repair's commit is not in the escrow chain")

    # -- V11 revocation ----------------------------------------------------------------

    def _v11(self) -> None:
        trips: dict[str, int] = {}
        for mandate_id in self.lineages:
            for event, _, _, seq in self.state.events.get(mandate_id, ()):
                if event not in ("revoked", "expired", "suspended"):
                    continue
                ended = self.b.records[seq - 1]
                found = [int(t) for t in ended.content.get("trips", ())]
                if found:
                    trips[mandate_id] = min(trips.get(mandate_id, found[0]), *found)
        for mandate_id, trip in sorted(trips.items()):
            for step in self.escrow.get(mandate_id, ()):
                if step.record_type is RecordType.COMMITTED and step.agentgov_sequence >= trip:
                    self.add(
                        "V11",
                        "revocation",
                        f"plan {step.plan_id} committed at ledger {step.agentgov_sequence}, "
                        f"after the trip at {trip} (G7)",
                    )
            issuer = f"fiduciary:{mandate_id}"
            for name, bundle in sorted(self.b.receipts.items()):
                receipt = bundle.receipt
                anchor = receipt.anchors.agentgov
                if anchor is None or anchor.seq < trip:
                    continue
                if (
                    isinstance(receipt, ActionReceipt)
                    and receipt.issuer == issuer
                    and receipt.decision.admitted
                ):
                    self.add(
                        "V11", "revocation", f"receipt {name} commits after the trip at {trip}"
                    )
                if isinstance(receipt, DeliveryReceipt):
                    self.add(
                        "V11", "revocation", f"receipt {name} delivers after the trip at {trip}"
                    )

    # -- V12 ordering ------------------------------------------------------------------

    def _v12(self) -> None:
        in_range = [r for r in self.b.records if self.first <= r.seq <= self.last]
        for problem in ordering_problems(in_range, self.anchors, self.skew):
            self.add("V12", "time", problem)


def _with_depth(deployment: Deployment, depth: int) -> Deployment:
    import dataclasses

    return dataclasses.replace(deployment, root_depth=depth)


class _Explained:
    """V7's rules: what explains an act on the lineage's footprint (§9.3)."""

    def __init__(self, checks: _Checks, state: State) -> None:
        self.c = checks
        self.state = state
        lineages = checks.lineages
        self.warrants = {d for d, w in state.warrants.items() if w.mandate_id in lineages}
        self.bound: set[str] = set()
        self.versions: set[str] = set()
        for mandate_id in lineages:
            self.versions.update(digest for _, digest, _, _ in state.lineages.get(mandate_id, ()))
            for event, _, _, seq in state.events.get(mandate_id, ()):
                if event == "bound":
                    self.bound.update(str(e) for e in checks.b.records[seq - 1].content["entries"])
        self.prefixes = {r.record_hash[:16] for records in checks.escrow.values() for r in records}
        runtime = checks.b.runtime
        self.runtime = {(r.log, r.seq, r.record_hash) for r in runtime}
        self.ilok1 = self.runtime | {(r.log, r.seq, r.record_hash) for r in checks.b.operators}
        self.opened = {
            str(r.body.get("recovery_scope")) for r in runtime if r.kind == "recovery.opened"
        }
        self.closed = {
            str(r.body.get("recovery_scope")) for r in runtime if r.kind == "recovery.closed"
        }
        self.steps = {int(r.body["step"]) for r in runtime if r.kind == "recovery.step"}
        self.granted = {str(r.body["grant"]): r for r in runtime if r.kind == "extension.granted"}
        self._verify_ilok1()
        self.sessions = self._sessions()

    def _verify_ilok1(self) -> None:
        c = self.c
        for record in c.b.runtime:
            if not _signed(record, c.k.runtime):
                c.add(
                    "V7", "evidence", f"runtime record {record.seq}: not signed by the runtime key"
                )
        operators = _keyring(c.k.operators.values())
        for record in c.b.operators:
            if not _signed(record, operators):
                c.add(
                    "V7", "evidence", f"operator record {record.seq}: not signed by an operator key"
                )

    def _sessions(self) -> dict[str, tuple[int, int, set[str] | None]]:
        """Each session: the ledger window between its opening's anchor and its
        close's, and the scopes it billed (``None`` while open)."""
        found: dict[str, tuple[int, int, set[str] | None]] = {}
        for session, events in self.state.sessions.items():
            if events[0][1].get("mandate_id") not in self.c.lineages:
                continue
            opened = self.c.anchors.get(events[0][2])
            start = opened.sequence if opened is not None else 0
            end, scopes = 1 << 62, None
            for event, body, seq in events:
                if event == "closed":
                    closed = self.c.anchors.get(seq)
                    end = closed.sequence if closed is not None else end
                    scopes = {str(s) for s in body["scopes"]}
            found[session] = (start, end, scopes)
        return found

    def entry(self, entry: LedgerEntry, holds: Mapping[uuid.UUID, bool]) -> str | None:
        """``None`` when ``entry`` is explained; else why it is not."""
        memo, kind = entry.memo, entry.entry_type
        if kind is EntryType.CIRCUIT_TRIPPED:
            return None  # a halt grants nothing
        found = _ILOK1.fullmatch(memo)
        if found is not None:
            if (found[1], int(found[2]), found[3]) in self.ilok1:
                return None
            return f"anchors ILOK1 record {found[2]} of {found[1]}, which the bundle does not hold"
        found = _INTERLOCK.fullmatch(memo)
        if found is not None:
            return (
                None
                if found[1] in self.prefixes
                else "names no record of the lineage's escrow chain"
            )
        found = _SESSION.match(memo)
        if found is not None:
            window = self.sessions.get(found[1])
            if window is None:
                return f"names session {found[1]}, which the lineage never opened"
            start, end, scopes = window
            if not start <= entry.sequence <= end:
                return f"is outside session {found[1]}'s life"
            if scopes is not None and entry.scope_id not in scopes:
                return f"bills a scope session {found[1]} did not"
            return None
        if entry.ref is not None and holds.get(entry.ref):
            return None  # settles or voids a hold that is explained
        if kind is EntryType.CIRCUIT_RESET:
            before = self.c.ledger.get(entry.sequence - 1)
            reset = _RESET.fullmatch(before.memo) if before is not None else None
            if reset is not None and reset[1] in self.warrants and reset[2] == entry.scope_id:
                return None
            return "a reset no warrant of the lineage names"
        found = _BIND.fullmatch(memo)
        if found is not None:
            if found[1] in self.versions and self.bound & self._siblings(entry):
                return None
            return "a delegation no binding of the lineage records"
        found = _GRANT.fullmatch(memo)
        if found is not None:
            if found[1] in self.granted and found[2] in self.warrants:
                return None
            return "an extension no warrant of the lineage granted"
        found = _CARRIED.fullmatch(memo)
        if found is not None:
            return None if found[1] in self.granted else "carried by an extension never granted"
        found = _RESERVE.fullmatch(memo)
        if found is not None:
            return None if f"{found[1]}/recovery" in self.opened else "a reserve no recovery opened"
        found = _RETURNED.fullmatch(memo)
        if found is not None:
            return None if f"{found[1]}/recovery" in self.closed else "a return no recovery closed"
        found = _STEP.fullmatch(memo)
        if found is not None:
            return None if int(found[1]) in self.steps else "a recovery step never recorded"
        if kind is EntryType.FUNDING and any(
            g.body.get("source") == "fund" and g.body.get("scope") == entry.scope_id
            for g in self.granted.values()
        ):
            return None
        return f"'{memo[:80]}' traces to no bind, plan, session, recovery, extension or warrant"

    def _siblings(self, entry: LedgerEntry) -> set[str]:
        """The ids of the entries in ``entry``'s transaction, its own among
        them: a binding names the line it read back, which may be either side."""
        return {
            str(e.entry_id)
            for e in self.c.ledger.values()
            if e.transaction_id == entry.transaction_id
        }

    def receipts(self) -> None:
        for name, bundle in sorted(self.c.b.receipts.items()):
            receipt = bundle.receipt
            if not isinstance(receipt, ActionReceipt):
                continue
            escrow = receipt.anchors.escrow
            if escrow is None or escrow.head[:16] not in self.prefixes:
                self.c.add(
                    "V7", "ambient", f"receipt {name} names no record of the lineage's escrow chain"
                )

    def plans(self) -> None:
        """Every plan admitted on the lineage's chain was a pinned tool's call."""
        pinned: set[str] = set()
        for mandate_id in self.c.lineages:
            for _, digest, _, _ in self.state.lineages.get(mandate_id, ()):
                found = self.c.b.docs.get(digest)
                if found is not None:
                    mandate = documents.loads(found[1])
                    assert isinstance(mandate, Mandate)
                    pinned.update(pin.digest[:16] for pin in mandate.tools.bindings)
        for lineage, records in sorted(self.c.escrow.items()):
            for record in records:
                if record.record_type is not RecordType.PLAN_ADMITTED:
                    continue
                tool = _TOOL.fullmatch(record.note)
                if tool is None or tool[2] not in pinned:
                    self.c.add(
                        "V7",
                        "ambient",
                        f"plan {record.plan_id} on {lineage}'s chain was admitted for "
                        f"'{record.note[:60]}', no pinned tool's call",
                    )

    def operators(self) -> None:
        for record in self.c.b.operators:
            if record.kind != "operator.intent":
                continue
            named = _WARRANT.search(str(record.body.get("reason", "")))
            if named is not None and named[1] in self.warrants:
                continue
            label = "break-glass" if record.key_id in self.c.break_glass else ""
            action = record.body.get("action")
            self.c.add(
                "V7",
                "ambient",
                f"operator record {record.seq} ({action}) by key {record.key_id} acts on the "
                f"lineage's messages under no warrant of it",
                label,
            )


def _signed(record: SignedRecord, keyring: Keyring) -> bool:
    try:
        key = keyring.get(record.key_id)
        if key is None:
            return False
        record.verify(key)
    except Exception:  # interlock's own signature errors
        return False
    return True

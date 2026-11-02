"""The interactive loopback control plane for ``fiduciary demo --serve``.

Serves a demo or deployment directory on loopback with static assets (rendering
``dashboard.html`` dynamically from ``demo.json`` or ``attestation.json`` with
the current console assets when available, without mutating files on disk) and
four JSON endpoints:

- ``GET /api/status``: ``mode: "live-server"``, available domains, open and
  held tickets, on-disk bundles, presets, principals, and current substrate,
  ledger and rate-window state.
- ``POST /api/verify-bundle``: verifies an on-disk, sandbox-exported or
  uploaded ``.fidb`` archive (``fidb/1`` or sealed ``fidb/2``) offline in
  process via :func:`~fiduciary.bundle.verify_bundle` and
  :func:`~fiduciary.attest.attest`, returning the ``V1``-``V12`` breakdown,
  two-tag seal and withheld-record status, and the nine framework mappings.
- ``POST /api/sandbox/call``: runs a preset or custom tool call against an
  isolated per-domain live sandbox (SQLite, FHIR R4 loopback, Kubernetes
  loopback, or Git), returning the input-gateway decision, staged
  ``EffectDiff``, checker findings, ``ARC1 1.4`` receipt, any raised ticket,
  and updated ledger and rate-window state.
- ``POST /api/warrant/vote``: signs a ``FIDU1`` ``Approval`` with one of the
  domain's principal keys, processes intake, forms the 2-of-3 ``Warrant`` once
  quorum is reached, and executes/releases the held or repaired action at
  ``expected_heads``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import http.server
import json
import mimetypes
import tempfile
import threading
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from fiduciary import __version__, report
from fiduciary.attest import attest
from fiduciary.bridge import SessionHaltedError
from fiduciary.bundle import BundleError, BundleReport, TrustedKeys, export_bundle, verify_bundle
from fiduciary.bundle.format import CHECKS, EXIT, FORMAT, SEALED, unpack
from fiduciary.bundle.format import pack as pack_tar
from fiduciary.demo import acts as fintech_acts
from fiduciary.demo import packs
from fiduciary.demo.gateway import Gateway
from fiduciary.demo.packs import coding_agent, healthcare, kit, saas, sre, supply_chain
from fiduciary.demo.world import (
    ORDERS,
    World,
    tenant_of,
)
from fiduciary.demo.world import (
    PRINCIPALS as FINTECH_PRINCIPALS,
)
from fiduciary.demo.world import (
    SCOPE as FINTECH_SCOPE,
)
from fiduciary.demo.world import (
    TENANTS as FINTECH_TENANTS,
)
from fiduciary.demo.world import (
    build as build_fintech,
)
from fiduciary.documents import Charter, Mandate, Ticket, Warrant, loads
from fiduciary.exceptions import FiduciaryError
from fiduciary.log import LOG_ID, Record
from fiduciary.store import State

__all__ = ["ControlPlane", "ControlPlaneServer", "serve_control_plane"]

MAX_BODY_BYTES: Final = 16 * 1024 * 1024
LOOPBACK_HOSTS: Final = ("127.0.0.1", "localhost", "::1")


def _trusted_keys(pinned_map: Mapping[str, Any]) -> TrustedKeys | None:
    """Turn ``pinned-keys.json`` (or a request's ``keys`` map) into :class:`TrustedKeys`."""
    if not pinned_map:
        return None
    return TrustedKeys.loads(json.dumps(dict(pinned_map), sort_keys=True).encode("utf-8"))


def _presets_for(domain: str) -> list[dict[str, Any]]:
    """Three interactive presets per industry: normal commit, hidden trap, and 2-of-3 warrant."""
    if domain == "fintech":
        return [
            {
                "id": "normal",
                "kind": "normal",
                "label": "Everyday refund ($12.50)",
                "description": (
                    "Refund $12.50 on Acme order 5; commits and queues one Stripe refund."
                ),
                "tool": "refund_order",
                "arguments": {
                    "order_id": 5,
                    "amount": "12.50",
                    "payment_intent": "pi_0000000005",
                },
                "tenant": "acme",
                "hold_reason": None,
            },
            {
                "id": "trap",
                "kind": "trap",
                "label": "Dispute settlement cascade (50 rows, 4 tenants)",
                "description": (
                    "Gateway PERMITs flagging order 17; a settlement trigger puts 49 sibling "
                    "orders across 4 tenants on hold, and Interlock rolls the transaction back."
                ),
                "tool": "flag_dispute",
                "arguments": {"order_id": 17},
                "tenant": "acme",
                "hold_reason": None,
            },
            {
                "id": "held",
                "kind": "held",
                "label": "Held refund under review (2-of-3 warrant)",
                "description": (
                    "Commits a $25.00 refund while on-call pauses outbound delivery; raises a "
                    "held ticket requiring 2 of 3 principals to release."
                ),
                "tool": "refund_order",
                "arguments": {
                    "order_id": 25,
                    "amount": "25.00",
                    "payment_intent": "pi_0000000025",
                },
                "tenant": "acme",
                "hold_reason": "paused by on-call: refund volume under review",
            },
        ]
    if domain == "healthcare":
        return [
            {
                "id": "normal",
                "kind": "normal",
                "label": "Draft inpatient acetaminophen (bed 4A)",
                "description": (
                    "Resolves bed 4A on the FHIR R4 server, predicts and writes one "
                    "MedicationRequest, verifies read-back, and sends dispense to pharmacy."
                ),
                "tool": "draft_order",
                "arguments": dict(healthcare.ACETAMINOPHEN),
                "tenant": healthcare.CHARTS[0],
                "hold_reason": None,
            },
            {
                "id": "trap",
                "kind": "trap",
                "label": "Discontinue order (EHR rule read-back put-back)",
                "description": (
                    "EHR encounter-stop rule stops sibling orders on write; FHIR read-back "
                    "catches the discrepancy, puts every record back, and emits an ARC1 1.4 "
                    "compensated receipt."
                ),
                "tool": "discontinue_order",
                "arguments": {"order_id": healthcare.first_order(3)},
                "tenant": healthcare.CHARTS[3],
                "hold_reason": None,
            },
            {
                "id": "held",
                "kind": "held",
                "label": "Schedule II oxycodone dispense (2-of-3 warrant)",
                "description": (
                    "Controlled-substance dispense is held in the pharmacy outbox until 2 of 3 "
                    "clinical principals sign a release warrant."
                ),
                "tool": "order_schedule_ii",
                "arguments": {
                    "bed": "4B",
                    "drug": "oxycodone",
                    "dose_mg": 10,
                    "mme_per_day": 30,
                    "schedule": "q6h",
                },
                "tenant": healthcare.CHARTS[1],
                "hold_reason": None,
            },
        ]
    if domain == "sre":
        return [
            {
                "id": "normal",
                "kind": "normal",
                "label": "Scale checkout 3 → 4 replicas",
                "description": (
                    "Kubernetes dry-run measures +1 replica (+2 vCPU), policy analyzer signs "
                    "clean, and stage applies on the measured resourceVersion."
                ),
                "tool": "scale_deployment",
                "arguments": {"deployment": "checkout", "replicas": 4},
                "tenant": sre.PROD,
                "hold_reason": None,
            },
            {
                "id": "trap",
                "kind": "trap",
                "label": "Scale api -> 6 (mutating webhook x10 -> 60)",
                "description": (
                    "Gateway PERMITs replicas=6; a mutating admission webhook multiplies api "
                    "replicas to 60 in dry-run, and drawdown + sensor + vCPU checks refuse it."
                ),
                "tool": "scale_deployment",
                "arguments": {"deployment": "api", "replicas": 6},
                "tenant": sre.PROD,
                "hold_reason": None,
            },
            {
                "id": "held",
                "kind": "held",
                "label": "Open security-group TCP 443 (2-of-3 warrant)",
                "description": (
                    "Cloud security-group rule change waits in the held outbox until 2 of 3 "
                    "on-call principals form a release warrant."
                ),
                "tool": "open_port",
                "arguments": {"security_group_id": 1, "port": 443},
                "tenant": sre.PROD,
                "hold_reason": None,
            },
        ]
    if domain == "supply-chain":
        east = supply_chain.WAREHOUSES[0]
        return [
            {
                "id": "normal",
                "kind": "normal",
                "label": "Raise PO line B-2002 x 120 units",
                "description": "Inserts one purchase-order line in wh-east and sends EDI order.",
                "tool": "create_po",
                "arguments": {"sku": "B-2002", "supplier_id": 4, "qty": 120, "uom": "each"},
                "tenant": east,
                "hold_reason": None,
            },
            {
                "id": "trap",
                "kind": "trap",
                "label": "Raise reorder point A-1001 → 500 (BOM explosion)",
                "description": (
                    "MRP trigger explodes bill of materials across 18 components and multiple "
                    "suppliers; rolled back before commit."
                ),
                "tool": "set_reorder_point",
                "arguments": {
                    "reorder_point_id": supply_chain.reorder_point(0, "A-1001"),
                    "point": 500,
                },
                "tenant": east,
                "hold_reason": None,
            },
            {
                "id": "held",
                "kind": "held",
                "label": "Held supplier-7 PO under treasury review (2-of-3 warrant)",
                "description": (
                    "Commits PO line while treasury pauses outbound EDI for supplier review; "
                    "raises a 2-of-3 held ticket."
                ),
                "tool": "create_po",
                "arguments": {"sku": "A-1001", "supplier_id": 7, "qty": 80, "uom": "each"},
                "tenant": east,
                "hold_reason": "paused by treasury: supplier 7 under review",
            },
        ]
    if domain == "saas":
        return [
            {
                "id": "normal",
                "kind": "normal",
                "label": "Mark Acme workspace opportunity won",
                "description": (
                    "Updates one Acme opportunity inside Acme's derived workspace mandate."
                ),
                "tool": "update_opportunity",
                "arguments": {"opportunity_id": saas.opportunity(0, 4), "stage": "won"},
                "tenant": saas.ACME,
                "hold_reason": None,
            },
            {
                "id": "trap",
                "kind": "trap",
                "label": "Bulk reassign lost deals (shared territory leak)",
                "description": (
                    "Territory subquery reaches other customer orgs sharing territory names; "
                    "tenant isolation rolls the transaction back."
                ),
                "tool": "bulk_reassign",
                "arguments": {"stage": "lost", "owner": "u77@acme.example"},
                "tenant": saas.ACME,
                "hold_reason": None,
            },
            {
                "id": "held",
                "kind": "held",
                "label": "Held workspace role invite (2-of-3 warrant)",
                "description": (
                    "Commits role invitation while trust-and-safety pauses outbound email; "
                    "raises a 2-of-3 held ticket in Globex's workspace."
                ),
                "tool": "assign_role",
                "arguments": {"user": "sam@globex.example", "role": "sales-rep"},
                "tenant": saas.ORGS[1],
                "hold_reason": "paused by trust and safety: phishing review",
            },
        ]
    if domain == "coding-agent":
        return [
            {
                "id": "normal",
                "kind": "normal",
                "label": "Fix invoice rounding in src/billing/invoice.py",
                "description": (
                    "Stages 1-line edit in Git worktree and commits onto main with "
                    "compare-and-swap on the measured HEAD."
                ),
                "tool": "edit_file",
                "arguments": {
                    "path": coding_agent.INVOICE,
                    "old": coding_agent.BUG,
                    "new": coding_agent.FIXED,
                },
                "tenant": coding_agent.REPO,
                "hold_reason": None,
            },
            {
                "id": "trap",
                "kind": "trap",
                "label": "Bump API schema version (35-file codegen cascade)",
                "description": (
                    "Post-edit code generator rewrites 35 client files; blast-radius and "
                    "stated-footprint checks refuse the stage before main moves."
                ),
                "tool": "edit_file",
                "arguments": {
                    "path": coding_agent.SCHEMA,
                    "old": '"version": "1.4"',
                    "new": '"version": "1.5"',
                },
                "tenant": coding_agent.REPO,
                "hold_reason": None,
            },
            {
                "id": "held",
                "kind": "held",
                "label": "Bump httpx 0.28.1 with CI facts (2-of-3 repair warrant)",
                "description": (
                    "Runs attested CI for httpx 0.28.1, attempts bump including protected "
                    "workflow matrix, and raises a 2-of-3 repair ticket pinned to Git HEAD."
                ),
                "tool": "bump_dependency",
                "arguments": {"package": "httpx", "version": "0.28.1"},
                "tenant": coding_agent.REPO,
                "hold_reason": None,
            },
        ]
    return []


@dataclass
class _TicketVotes:
    """Tracked interactive vote history for one ticket in a sandbox."""

    approved_by: list[str] = field(default_factory=list)
    declined_by: list[str] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    executed_records: list[str] = field(default_factory=list)
    committed: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _DomainSandbox:
    """An isolated live deployment for one domain, held in a temporary directory."""

    key: str
    directory: Path
    world: World
    fintech_run: fintech_acts.Run | None = None
    fintech_stripe: fintech_acts.Stripe | None = None
    pack_run: kit.PackRun | None = None
    coding_ci: coding_agent.Ci | None = None
    call_seq: int = 0
    votes: dict[str, _TicketVotes] = field(default_factory=dict)
    ticket_mandates: dict[str, Mandate] = field(default_factory=dict)
    window_usage: dict[str, str] = field(default_factory=dict)

    @property
    def principals(self) -> tuple[str, ...]:
        if self.pack_run is not None:
            return tuple(self.pack_run.pack.principals)
        return FINTECH_PRINCIPALS

    @property
    def operator(self) -> str:
        if self.pack_run is not None:
            return self.pack_run.pack.operator
        return "night-shift"

    @property
    def scope(self) -> str:
        if self.pack_run is not None:
            return self.pack_run.pack.scope
        return FINTECH_SCOPE

    def drain(self) -> None:
        if self.pack_run is not None:
            assert isinstance(self.world, kit.PackWorld)
            kit.drain(self.world, self.pack_run.vendor)
        else:
            assert self.fintech_stripe is not None
            fintech_acts.drain(self.world, self.fintech_stripe)

    def mandate_for_tenant(self, tenant: str) -> Mandate:
        assert self.world.mandate is not None
        if isinstance(self.world, kit.PackWorld) and tenant in self.world.children:
            return self.world.children[tenant]
        return self.world.mandate

    def close(self) -> None:
        resolved = self.directory.resolve()
        self.world.close()
        healthcare._EHRS.pop(resolved, None)
        sre._CLUSTERS.pop(resolved, None)


def _domain_catalog() -> dict[str, dict[str, Any]]:
    """Static metadata for every supported industry."""
    catalog: dict[str, dict[str, Any]] = {
        "fintech": {
            "key": "fintech",
            "industry": "Fintech",
            "title": "Support refunds and order adjustments",
            "substrate": "sqlite",
            "measurement": "transactional",
            "measurement_class": "T",
            "scope": FINTECH_SCOPE,
            "partition": "tenant",
            "default_tenant": FINTECH_TENANTS[0],
            "tenants": list(FINTECH_TENANTS),
            "principals": list(FINTECH_PRINCIPALS),
            "operator": "night-shift",
        }
    }
    for key, pack in packs.PACKS.items():
        if key == "healthcare":
            substrate, measurement, mclass = "fhir", "predicted", "V"
        elif key == "sre":
            substrate, measurement, mclass = "kubernetes", "plan-bound", "P"
        elif key == "coding-agent":
            substrate, measurement, mclass = "git", "transactional", "T"
        else:
            substrate, measurement, mclass = "sqlite", "transactional", "T"
        catalog[key] = {
            "key": key,
            "industry": pack.industry,
            "title": pack.title,
            "substrate": substrate,
            "measurement": measurement,
            "measurement_class": mclass,
            "scope": pack.scope,
            "partition": pack.partition,
            "default_tenant": pack.tenants[0],
            "tenants": list(pack.tenants),
            "principals": list(pack.principals),
            "operator": pack.operator,
        }
    return catalog


def _seal_summary(data: bytes, report: BundleReport) -> dict[str, Any]:
    """Extract ``fidb/1`` / ``fidb/2`` two-tag seal and withheld-record telemetry."""
    members = dict(unpack(data))
    manifest = json.loads(members["manifest.json"]) if "manifest.json" in members else {}
    fmt = str(manifest.get("format", FORMAT))
    is_sealed = fmt == SEALED
    log_records: list[Any] = []
    if "log.jsonl" in members:
        log_records = [
            json.loads(line)
            for line in members["log.jsonl"].decode("utf-8").splitlines()
            if line.strip()
        ]
    elif "log.json" in members:
        parsed_log = json.loads(members["log.json"])
        if isinstance(parsed_log, list):
            log_records = parsed_log
    withheld_records: list[Any] = []
    if "withheld.json" in members:
        parsed_wh = json.loads(members["withheld.json"])
        if isinstance(parsed_wh, Mapping) and isinstance(parsed_wh.get("records"), list):
            withheld_records = parsed_wh["records"]
        elif isinstance(parsed_wh, list):
            withheld_records = parsed_wh
    two_tag = sum(
        1
        for rec in log_records
        if isinstance(rec, Mapping)
        and isinstance(rec.get("seal"), list | Mapping)
        and len(rec["seal"]) > 0
    )
    seal_checks_ok = not (report.failed("V1") or report.failed("V9") or report.failed("V10"))
    if not seal_checks_ok:
        status = "failed"
    elif is_sealed:
        status = "verified"
    else:
        status = "unsealed"
    return {
        "format": fmt,
        "sealed": is_sealed,
        "verified": seal_checks_ok,
        "status": status,
        "records_whole": len(log_records),
        "records_two_tag": two_tag,
        "records_withheld": len(withheld_records),
    }


class ControlPlane:
    """Thread-safe controller backing the HTTP API over a deployment directory."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._lock = threading.RLock()
        self._scratch = tempfile.TemporaryDirectory(prefix="fiduciary-control-")
        self._seq = 0
        self._catalog = _domain_catalog()
        self._sandboxes: dict[str, _DomainSandbox] = {}
        self._disk_dirs = self._discover_disk_domains()

    def _discover_disk_domains(self) -> dict[str, Path]:
        """Map domain keys that have deployment artifacts under ``self.root``."""
        found: dict[str, Path] = {}
        demo_file = self.root / "demo.json"
        if demo_file.is_file():
            try:
                payload = json.loads(demo_file.read_text(encoding="utf-8"))
                dom = payload.get("demo", {}).get("domain")
                primary_key = (
                    str(dom["key"]) if isinstance(dom, Mapping) and "key" in dom else "fintech"
                )
                if primary_key in self._catalog:
                    found[primary_key] = self.root
            except (OSError, ValueError, KeyError):
                found["fintech"] = self.root
        elif (self.root / "charter.digest").is_file() or (self.root / "fiduciary.toml").is_file():
            found["fintech"] = self.root
        for key in packs.PACKS:
            sub = self.root / key
            if sub.is_dir() and (
                (sub / "charter.digest").is_file()
                or (sub / "demo.json").is_file()
                or (sub / "fiduciary.toml").is_file()
            ):
                found[key] = sub
        return found

    def close(self) -> None:
        with self._lock:
            for sandbox in list(self._sandboxes.values()):
                sandbox.close()
            self._sandboxes.clear()
            self._scratch.cleanup()

    def dashboard_html(self) -> bytes | None:
        """Render ``dashboard.html`` with current console assets from ``demo.json``
        or ``attestation.json``, falling back to ``dashboard.html`` on disk."""
        demo_json = self.root / "demo.json"
        if demo_json.is_file():
            payload = json.loads(demo_json.read_text(encoding="utf-8"))
            return report.html(payload).encode("utf-8")
        att_json = self.root / "attestation.json"
        if att_json.is_file():
            payload = json.loads(att_json.read_text(encoding="utf-8"))
            return report.html(payload).encode("utf-8")
        on_disk = self.root / "dashboard.html"
        if on_disk.is_file():
            return on_disk.read_bytes()
        return None

    def _ensure_sandbox(self, domain: str) -> _DomainSandbox:
        if domain not in self._catalog:
            raise ValueError(
                f"unknown domain {domain!r}; expected one of {', '.join(self._catalog)}"
            )
        existing = self._sandboxes.get(domain)
        if existing is not None:
            return existing
        self._seq += 1
        target = Path(self._scratch.name) / f"{domain}-{self._seq}"
        if domain == "fintech":
            world = build_fintech(target)
            gateway = Gateway(
                FINTECH_SCOPE,
                "acme",
                {n: tenant_of(n) for n in range(1, ORDERS + 1)},
                engine="auto",
            )
            fintech_run = fintech_acts.Run(world, gateway)
            fintech_run.observe()
            sandbox = _DomainSandbox(
                key=domain,
                directory=target,
                world=world,
                fintech_run=fintech_run,
                fintech_stripe=fintech_acts.Stripe(),
            )
        else:
            pack = packs.PACKS[domain]
            pworld = kit.build(target, pack)
            pgateway = kit.PackGateway(pack, pack.tenants[0], engine="auto")
            pack_run = kit.PackRun(pworld, pgateway)
            pack_run.observe()
            if domain == "saas":
                saas._workspaces(pack_run)
            ci = coding_agent.Ci(pworld.system) if domain == "coding-agent" else None
            sandbox = _DomainSandbox(
                key=domain,
                directory=target,
                world=pworld,
                pack_run=pack_run,
                coding_ci=ci,
            )
        self._sandboxes[domain] = sandbox
        return sandbox

    def reset_sandbox(self, domain: str) -> dict[str, Any]:
        with self._lock:
            if domain not in self._catalog:
                raise ValueError(f"unknown domain {domain!r}")
            old = self._sandboxes.pop(domain, None)
            if old is not None:
                old.close()
            sandbox = self._ensure_sandbox(domain)
            return self._domain_status(domain, sandbox=sandbox)

    def _bundles_for_dir(self, directory: Path | None) -> list[dict[str, Any]]:
        if directory is None or not directory.is_dir():
            return []
        items: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.fidb")):
            rel = path.relative_to(self.root).as_posix()
            items.append(
                {
                    "name": path.name,
                    "path": rel,
                    "size_bytes": path.stat().st_size,
                    "sealed_hint": path.name.startswith("customer-"),
                }
            )
        return items

    def _tools_for(self, domain: str, sandbox: _DomainSandbox | None) -> list[dict[str, Any]]:
        if sandbox is not None:
            return [
                {
                    "name": name,
                    "description": binding.description,
                    "input_schema": binding.body()["input"],
                }
                for name, binding in sorted(sandbox.world.tools.items())
            ]
        raw_tools = (
            packs.PACKS[domain].tools()
            if domain in packs.PACKS
            else [
                {
                    "name": "lookup_order",
                    "description": "Read one order of the session's tenant.",
                    "input": {"type": "object", "properties": {"order_id": {"type": "integer"}}},
                },
                {
                    "name": "refund_order",
                    "description": "Refund an amount on an order and queue a Stripe refund.",
                    "input": {
                        "type": "object",
                        "properties": {
                            "order_id": {"type": "integer"},
                            "amount": {"type": "string"},
                            "payment_intent": {"type": "string"},
                        },
                    },
                },
                {
                    "name": "apply_discount",
                    "description": "Apply a percentage discount to an open order.",
                    "input": {
                        "type": "object",
                        "properties": {
                            "order_id": {"type": "integer"},
                            "percent": {"type": "integer"},
                        },
                    },
                },
                {
                    "name": "flag_dispute",
                    "description": "Mark an order disputed while settlement investigates.",
                    "input": {
                        "type": "object",
                        "properties": {
                            "order_id": {"type": "integer"},
                        },
                    },
                },
            ]
        )
        out: list[dict[str, Any]] = []
        for t in raw_tools:
            inp = t.get("input")
            out.append(
                {
                    "name": str(t["name"]),
                    "description": str(t.get("description", "")),
                    "input_schema": dict(inp) if isinstance(inp, Mapping) else {},
                }
            )
        return out

    def _ledger_state(self, domain: str, sandbox: _DomainSandbox | None) -> dict[str, Any]:
        meta = self._catalog[domain]
        scope = str(meta["scope"])
        if sandbox is None:
            return {
                "scope": scope,
                "usd": {
                    "envelope": "25.00000000",
                    "available": "25.00000000",
                    "spent": "0",
                    "halted": False,
                },
                "units": {},
            }
        world = sandbox.world
        assert world.mandate is not None
        body = world.mandate.body()
        envelope_str = str(body["budget"]["envelope"])
        envelope = Decimal(envelope_str)
        world.governor.refresh()
        avail = world.governor.available(scope)
        spent = max(Decimal(0), envelope - avail)
        units: dict[str, Any] = {}
        if isinstance(world, kit.PackWorld):
            budgets = body.get("budgets", {})
            for code, uledger in sorted(world.ledgers.items()):
                uledger.refresh()
                uenv_str = str(budgets.get(code, {}).get("envelope", "0"))
                uenv = Decimal(uenv_str)
                uavail = uledger.available(scope)
                uspent = max(Decimal(0), uenv - uavail)
                units[code] = {
                    "envelope": format(uenv.normalize(), "f"),
                    "available": format(uavail.normalize(), "f"),
                    "spent": format(uspent.normalize(), "f"),
                    "halted": uledger.is_halted(scope),
                }
        return {
            "scope": scope,
            "usd": {
                "envelope": format(envelope.normalize(), "f"),
                "available": format(avail.normalize(), "f"),
                "spent": format(spent.normalize(), "f"),
                "halted": world.governor.is_halted(scope),
            },
            "units": units,
        }

    def _windows_state(self, domain: str, sandbox: _DomainSandbox | None) -> list[dict[str, Any]]:
        if sandbox is None:
            if domain in packs.PACKS:
                raw_win = packs.PACKS[domain].windows()
            else:
                raw_win = [{"name": "hourly_refund_usd", "limit": "500.00", "span_seconds": 3600}]
            return [
                {
                    "name": str(w["name"]),
                    "limit": str(w["limit"]),
                    "span_seconds": int(w["span_seconds"]),
                    "used": "0",
                }
                for w in raw_win
            ]
        assert sandbox.world.mandate is not None
        windows = sandbox.world.mandate.body().get("windows", [])
        out: list[dict[str, Any]] = []
        for w in windows:
            name = str(w["name"])
            out.append(
                {
                    "name": name,
                    "limit": str(w["limit"]),
                    "span_seconds": int(w["span_seconds"]),
                    "used": sandbox.window_usage.get(name, "0"),
                }
            )
        return out

    def _store_state(self, sandbox: _DomainSandbox) -> State:
        return State.of([Record.loads(r.data) for r in sandbox.world.store.records(LOG_ID)])

    def _load_ticket(
        self, sandbox: _DomainSandbox, ticket_id: str, state: State | None = None
    ) -> Ticket:
        st = state or self._store_state(sandbox)
        row = st.tickets.get(ticket_id)
        if row is None:
            raise KeyError(f"ticket {ticket_id!r} not found in {sandbox.key}")
        found = sandbox.world.store.document(row.digest)
        if found is None:
            raise KeyError(f"ticket document {row.digest!r} not found in {sandbox.key}")
        doc = loads(found[1], signed=True)
        if not isinstance(doc, Ticket):
            raise KeyError(f"{ticket_id!r} is not a ticket")
        return doc

    @staticmethod
    def _expected_heads_of(res_json: Mapping[str, Any]) -> list[Any]:
        raw = res_json.get("expected_heads")
        if isinstance(raw, Mapping):
            return [f"{k}:{v}" for k, v in raw.items()]
        if isinstance(raw, (list, tuple)):
            return list(raw)
        msgs = res_json.get("messages")
        if isinstance(msgs, (list, tuple)):
            return list(msgs)
        head = res_json.get("expected_head") or res_json.get("head")
        return [head] if head is not None else []

    def _serialize_ticket(
        self, sandbox: _DomainSandbox, ticket: Ticket, *, state: State | None = None
    ) -> dict[str, Any]:
        escalation = sandbox.world.fid.escalation
        st = state or self._store_state(sandbox)
        open_ids = {row.ticket_id for row in st.open_tickets()}
        warrant = escalation.warrant_for_ticket(ticket.ticket_id)
        tracked = sandbox.votes.get(ticket.ticket_id, _TicketVotes())
        primary = ticket.options[0]
        for opt in ticket.options:
            if opt.resolution.action in ("release", "accept_repair", "amend"):
                primary = opt
                break
        declined_out = (len(sandbox.principals) - len(tracked.declined_by)) < primary.quorum
        if warrant is not None:
            status = "executed" if tracked.executed_records else "warrant_formed"
        elif ticket.ticket_id in open_ids and not declined_out:
            status = "open"
        else:
            status = "declined"
        warrant_json: dict[str, Any] | None = None
        if warrant is not None:
            warrant_json = self._serialize_warrant(warrant, tracked)
        is_open = (ticket.ticket_id in open_ids) and not declined_out and (warrant is None)
        return {
            "domain": sandbox.key,
            "ticket_id": ticket.ticket_id,
            "digest": ticket.digest,
            "class": ticket.ticket_class,
            "status": status,
            "open": is_open,
            "principals": list(sandbox.principals),
            "observed": ticket.observed.to_json(),
            "options": [
                {
                    "action": o.resolution.action,
                    "digest": o.digest,
                    "quorum": o.quorum,
                    "expected_heads": self._expected_heads_of(o.resolution.to_json()),
                }
                for o in ticket.options
            ],
            "quorum": {
                "needed": primary.quorum,
                "total_principals": len(sandbox.principals),
                "approved_by": list(tracked.approved_by),
                "declined_by": list(tracked.declined_by),
                "reached": warrant is not None,
                "status": status,
            },
            "votes": list(tracked.history),
            "warrant": warrant_json,
        }

    def _serialize_warrant(self, warrant: Warrant, tracked: _TicketVotes) -> dict[str, Any]:
        body = warrant.body()
        return {
            "warrant_id": warrant.warrant_id,
            "digest": warrant.digest,
            "action": warrant.resolution.action,
            "resolution_digest": warrant.resolution_digest,
            "basis": list(warrant.basis.approvals),
            "expected_heads": self._expected_heads_of(warrant.resolution.to_json()),
            "formed_at": str(body["formed_at"]),
            "not_after": str(body["not_after"]),
            "executed": list(tracked.executed_records),
            "released": bool(tracked.executed_records),
            "committed": list(tracked.committed),
        }

    def _tickets_for(self, sandbox: _DomainSandbox | None) -> list[dict[str, Any]]:
        if sandbox is None:
            return []
        st = self._store_state(sandbox)
        seen_ids: list[str] = list(sandbox.votes)
        for row in st.open_tickets():
            if row.ticket_id not in seen_ids:
                seen_ids.append(row.ticket_id)
        return [
            self._serialize_ticket(sandbox, self._load_ticket(sandbox, tid, st), state=st)
            for tid in seen_ids
            if tid in st.tickets
        ]

    def _domain_status(
        self, domain: str, *, sandbox: _DomainSandbox | None = None
    ) -> dict[str, Any]:
        meta = self._catalog[domain]
        sb = sandbox or self._sandboxes.get(domain)
        disk_dir = self._disk_dirs.get(domain)
        tickets = self._tickets_for(sb)
        outbox = sb.world.outbox_states() if sb is not None else {}
        git_head = (
            sb.world.system.head()
            if sb is not None and domain == "coding-agent" and isinstance(sb.world, kit.PackWorld)
            else None
        )
        return {
            **meta,
            "on_disk": disk_dir is not None,
            "sandbox_active": sb is not None,
            "bundles": self._bundles_for_dir(disk_dir),
            "presets": _presets_for(domain),
            "tools": self._tools_for(domain, sb),
            "tickets": tickets,
            "open_tickets": [t for t in tickets if t["open"]],
            "substrate_state": {
                "kind": meta["substrate"],
                "measurement": meta["measurement"],
                "healthy": True,
                "outbox": outbox,
                **({"git_head": git_head} if git_head is not None else {}),
            },
            "ledger": self._ledger_state(domain, sb),
            "windows": self._windows_state(domain, sb),
        }

    def status(self) -> dict[str, Any]:
        """``GET /api/status``."""
        with self._lock:
            domains = [self._domain_status(key) for key in self._catalog]
            open_tickets = [t for d in domains for t in d["open_tickets"]]
            held_tickets = [t for t in open_tickets if t["class"] == "held"]
            all_bundles = [{**b, "domain": d["key"]} for d in domains for b in d["bundles"]]
            return {
                "mode": "live-server",
                "version": __version__,
                "root": self.root.name,
                "domains": domains,
                "open_tickets": open_tickets,
                "held_tickets": held_tickets,
                "bundles": all_bundles,
            }

    # ------------------------------------------------------------------
    # POST /api/verify-bundle
    # ------------------------------------------------------------------

    def _resolve_bundle_context(
        self,
        domain: str | None,
        data: bytes | None,
        explicit_charter: str | None,
        explicit_keys: Mapping[str, Any] | None,
    ) -> tuple[str, str, dict[str, Any]]:
        """Determine ``(domain_key, charter_digest, pinned_keys_dict)``."""
        chosen = domain if domain in self._catalog else None
        if chosen is None and data is not None and explicit_charter is None:
            # Match an uploaded bundle's charter digest to a known domain on disk or in sandbox.
            try:
                for name, raw in unpack(data):
                    if name.startswith("charter/") and name.endswith(".json"):
                        cdigest = Charter.from_json(json.loads(raw)).digest
                        for key, ddir in self._disk_dirs.items():
                            cpath = ddir / "charter.digest"
                            if (
                                cpath.is_file()
                                and cpath.read_text(encoding="utf-8").strip() == cdigest
                            ):
                                chosen = key
                                break
                        if chosen is None:
                            for key, sb in self._sandboxes.items():
                                if sb.world.charter.digest == cdigest:
                                    chosen = key
                                    break
                        break
            except (BundleError, ValueError, KeyError, FiduciaryError):
                chosen = None

        resolved_domain = chosen or next(iter(self._disk_dirs), "fintech")
        charter_digest = explicit_charter
        keys_dict: dict[str, Any] = dict(explicit_keys) if explicit_keys is not None else {}

        if charter_digest is None or explicit_keys is None:
            disk_dir = self._disk_dirs.get(resolved_domain)
            if disk_dir is not None and (disk_dir / "charter.digest").is_file():
                if charter_digest is None:
                    charter_digest = (
                        (disk_dir / "charter.digest").read_text(encoding="utf-8").strip()
                    )
                if explicit_keys is None and (disk_dir / "pinned-keys.json").is_file():
                    keys_dict = json.loads(
                        (disk_dir / "pinned-keys.json").read_text(encoding="utf-8")
                    )
            else:
                sb = self._ensure_sandbox(resolved_domain)
                if charter_digest is None:
                    charter_digest = sb.world.charter.digest
                if explicit_keys is None:
                    keys_dict = (
                        kit.pinned(sb.world)
                        if isinstance(sb.world, kit.PackWorld)
                        else fintech_acts.pinned(sb.world)
                    )
        assert charter_digest is not None
        return resolved_domain, charter_digest, keys_dict

    def _export_sandbox_bundle(
        self, domain: str, *, sealed: bool = False
    ) -> tuple[bytes, str, dict[str, Any]]:
        sb = self._ensure_sandbox(domain)
        world = sb.world
        assert world.mandate is not None
        target_mandate = world.mandate
        if domain == "saas" and isinstance(world, kit.PackWorld) and sealed and world.children:
            target_mandate = next(iter(world.children.values()))
        self._seq += 1
        shift = f"control-plane-export-{self._seq}"
        if isinstance(world, kit.PackWorld):
            data = kit.export(world, shift, target_mandate, sealed=sealed)
            return data, world.charter.digest, kit.pinned(world)
        with world.fid.session(
            sb.scope,
            tenant=self._catalog[domain]["default_tenant"],
            trajectory=shift,
        ):
            pass
        world.governor.refresh()
        data = export_bundle(
            world.store,
            target_mandate.mandate_id,
            deployment=world.deployment,
            evidence=fintech_acts.evidence(world),
            sealed=sealed,
        )
        return data, world.charter.digest, fintech_acts.pinned(world)

    def verify_bundle_request(
        self,
        payload: Mapping[str, Any],
        *,
        raw_bytes: bytes | None = None,
    ) -> dict[str, Any]:
        """``POST /api/verify-bundle``."""
        with self._lock:
            domain_arg = payload.get("domain")
            domain = str(domain_arg) if isinstance(domain_arg, str) and domain_arg else None
            filename = str(payload.get("filename") or payload.get("bundle") or "uploaded.fidb")
            explicit_charter = (
                str(payload["charter"]).strip()
                if isinstance(payload.get("charter"), str) and payload["charter"]
                else None
            )
            raw_keys = payload.get("keys")
            explicit_keys = dict(raw_keys) if isinstance(raw_keys, Mapping) else None

            data: bytes
            if raw_bytes is not None:
                data = raw_bytes
            elif isinstance(payload.get("data_base64"), str):
                try:
                    data = base64.b64decode(payload["data_base64"], validate=False)
                except (binascii.Error, ValueError) as exc:
                    raise ValueError(f"invalid base64 bundle payload: {exc}") from exc
            elif isinstance(payload.get("members"), Mapping):
                packed_members: dict[str, bytes] = {}
                for mname, mval in payload["members"].items():
                    if isinstance(mval, str):
                        packed_members[str(mname)] = mval.encode("utf-8")
                    else:
                        packed_members[str(mname)] = (
                            json.dumps(mval, indent=2, sort_keys=True) + "\n"
                        ).encode("utf-8")
                data = pack_tar(packed_members)
            elif (
                payload.get("bundle") in ("@sandbox", "@sandbox-sealed")
                or payload.get("source") == "sandbox"
            ):
                target_dom = domain or "fintech"
                sealed_req = (
                    bool(payload.get("sealed")) or payload.get("bundle") == "@sandbox-sealed"
                )
                data, s_charter, s_keys = self._export_sandbox_bundle(target_dom, sealed=sealed_req)
                domain = target_dom
                filename = f"{target_dom}-sandbox{'-sealed' if sealed_req else ''}.fidb"
                if explicit_charter is None:
                    explicit_charter = s_charter
                if explicit_keys is None:
                    explicit_keys = s_keys
            elif isinstance(payload.get("bundle"), str):
                rel = payload["bundle"].lstrip("/")
                base_dir = self._disk_dirs.get(domain, self.root) if domain else self.root
                candidate = (base_dir / rel).resolve()
                if not candidate.is_file():
                    candidate = (self.root / rel).resolve()
                if not candidate.is_relative_to(self.root) or not candidate.is_file():
                    raise FileNotFoundError(f"bundle {rel!r} not found under {self.root.name}")
                data = candidate.read_bytes()
                filename = candidate.relative_to(self.root).as_posix()
            else:
                raise ValueError(
                    "verify-bundle expects 'bundle', 'data_base64', 'members', or raw bytes"
                )

            sha256 = hashlib.sha256(data).hexdigest()
            resolved_domain, charter_digest, keys_dict = self._resolve_bundle_context(
                domain, data, explicit_charter, explicit_keys
            )
            try:
                bundle_report = verify_bundle(
                    data,
                    charter=charter_digest,
                    keys=_trusted_keys(keys_dict),
                )
                attestation = attest(
                    data,
                    bundle_report,
                    charter=charter_digest,
                    pinned=sorted(keys_dict),
                )
                seal = _seal_summary(data, bundle_report)
            except BundleError as exc:
                checks: list[dict[str, Any]] = [
                    {
                        "id": cid,
                        "name": cname,
                        "status": "fail" if cid == "V1" else "not_run",
                        "proves": cname,
                        "findings": (
                            [
                                {
                                    "kind": "malformed",
                                    "label": "",
                                    "message": str(exc),
                                    "exit_code": 2,
                                }
                            ]
                            if cid == "V1"
                            else []
                        ),
                        "controls": {},
                    }
                    for cid, cname in CHECKS
                ]
                return {
                    "passed": False,
                    "exit_code": 2,
                    "domain": resolved_domain,
                    "verdict": {
                        "passed": False,
                        "status": "UNREADABLE",
                        "exit_code": 2,
                        "findings": 1,
                        "labels": [],
                    },
                    "bundle": {
                        "filename": filename,
                        "domain": resolved_domain,
                        "sha256": sha256,
                        "size_bytes": len(data),
                        "format": None,
                        "members": 0,
                    },
                    "seal": {
                        "format": None,
                        "sealed": False,
                        "verified": False,
                        "status": "failed",
                        "records_whole": 0,
                        "records_two_tag": 0,
                        "records_withheld": 0,
                    },
                    "checks": checks,
                    "findings": [
                        {
                            "check": "V1",
                            "kind": "malformed",
                            "label": "",
                            "message": str(exc),
                            "exit_code": 2,
                        }
                    ],
                    "frameworks": {},
                    "mappings": {"packs": []},
                    "attestation": None,
                }

            return {
                "passed": bundle_report.passed,
                "exit_code": bundle_report.exit_code,
                "domain": resolved_domain,
                "verdict": attestation["verdict"],
                "bundle": {
                    **attestation["bundle"],
                    "filename": filename,
                    "domain": resolved_domain,
                },
                "seal": seal,
                "checks": attestation["checks"],
                "findings": [
                    {
                        "check": f.check,
                        "kind": f.kind,
                        "label": f.label,
                        "message": f.message,
                        "exit_code": EXIT.get(f.kind, bundle_report.exit_code),
                    }
                    for f in bundle_report.findings
                ],
                "frameworks": attestation["frameworks"],
                "mappings": attestation["mappings"],
                "attestation": attestation,
            }

    # ------------------------------------------------------------------
    # POST /api/sandbox/call
    # ------------------------------------------------------------------

    def _read_new_receipts(self, receipts_path: Path, before_lines: int) -> list[dict[str, Any]]:
        if not receipts_path.is_file():
            return []
        lines = [ln for ln in receipts_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        parsed: list[dict[str, Any]] = []
        for raw in lines[before_lines:]:
            doc = json.loads(raw)
            parsed.append(
                {
                    "arc": doc.get("arc"),
                    "receipt_id": doc.get("receipt_id"),
                    "plan_hash": doc.get("plan_hash"),
                    "admitted": doc.get("decision", {}).get("admitted"),
                    "outcome": doc.get("outcome", {}).get("status"),
                    "substrate_txid": doc.get("outcome", {}).get("substrate_txid"),
                    "measurement": doc.get("coverage", {}).get("measurement") or "transactional",
                    "substrate": (
                        doc.get("effect", {}).get("substrate_id")
                        or doc.get("subject", {}).get("substrate")
                        or "sqlite"
                    ),
                    "raw": doc,
                }
            )
        return parsed

    def sandbox_call(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """``POST /api/sandbox/call``."""
        with self._lock:
            domain = str(payload.get("domain") or "fintech")
            sb = self._ensure_sandbox(domain)
            preset_id = payload.get("preset")
            presets = {p["id"]: p for p in _presets_for(domain)}

            if isinstance(preset_id, str) and preset_id:
                if preset_id not in presets:
                    raise ValueError(f"unknown preset {preset_id!r} for domain {domain!r}")
                chosen = presets[preset_id]
                tool = str(chosen["tool"])
                arguments = dict(chosen["arguments"])
                tenant = str(payload.get("tenant") or chosen["tenant"])
                hold_reason = (
                    str(payload["hold_reason"])
                    if isinstance(payload.get("hold_reason"), str)
                    else chosen["hold_reason"]
                )
            else:
                if not isinstance(payload.get("tool"), str) or not payload["tool"]:
                    raise ValueError("sandbox/call requires either 'preset' or 'tool'")
                tool = str(payload["tool"])
                raw_args = payload.get("arguments", {})
                if not isinstance(raw_args, Mapping):
                    raise ValueError("'arguments' must be a JSON object")
                arguments = dict(raw_args)
                tenant = str(payload.get("tenant") or self._catalog[domain]["default_tenant"])
                hold_reason = (
                    str(payload["hold_reason"])
                    if isinstance(payload.get("hold_reason"), str) and payload["hold_reason"]
                    else (
                        "paused by operator: interactive quorum review"
                        if bool(payload.get("hold"))
                        else None
                    )
                )

            sb.call_seq += 1
            trajectory = f"interactive-{sb.call_seq}"
            receipts_file = sb.world.root / "receipts.jsonl"
            receipt_lines_before = (
                len(
                    [
                        ln
                        for ln in receipts_file.read_text(encoding="utf-8").splitlines()
                        if ln.strip()
                    ]
                )
                if receipts_file.is_file()
                else 0
            )

            # Drain any earlier non-held pending messages before staging a new call.
            sb.drain()
            held_before = {m for m, s in sb.world.outbox_states().items() if s == "held"}

            # Coding-agent dependency-bump warrant flow dynamically prepares CI facts at HEAD.
            ci_summary: dict[str, Any] | None = None
            if (
                domain == "coding-agent"
                and tool == "bump_dependency"
                and "manifest" not in arguments
                and sb.pack_run is not None
                and sb.coding_ci is not None
            ):
                pkg = str(arguments.get("package", "httpx"))
                ver = str(arguments.get("version", "0.28.1"))
                kit.one(
                    sb.pack_run,
                    "request_ci",
                    {"package": pkg, "version": ver},
                    tenant=tenant,
                    trajectory=f"{trajectory}-ci",
                )
                webhooks = coding_agent.run_ci(sb.pack_run, sb.coding_ci)
                assert isinstance(sb.world, kit.PackWorld)
                repo: coding_agent.Repository = sb.world.system
                bumped_files = coding_agent.bumped(repo.files(), pkg, ver)
                arguments = {
                    "base": repo.head(),
                    "manifest": bumped_files["pyproject.toml"],
                    "lock": bumped_files["uv.lock"],
                    "matrix": bumped_files[".github/workflows/ci.yml"],
                }
                ci_summary = {
                    "package": pkg,
                    "version": ver,
                    "webhooks_accepted": sum(1 for w in webhooks if w["status"] < 300),
                    "webhooks_refused": sum(1 for w in webhooks if w["status"] >= 300),
                }

            mandate = sb.mandate_for_tenant(tenant)
            session_scope = (
                mandate.mandate_id if mandate.parent is not None else mandate.subject.scope
            )
            raised_ticket: Ticket | None = None

            try:
                if sb.fintech_run is not None:
                    with sb.world.fid.session(
                        session_scope, tenant=tenant, trajectory=trajectory
                    ) as session:
                        fcall = fintech_acts.call(sb.fintech_run, session, tool, arguments)
                    call_json = fcall.to_json()
                    call_result = fcall.result
                else:
                    assert sb.pack_run is not None
                    with sb.world.fid.session(
                        session_scope, tenant=tenant, trajectory=trajectory
                    ) as session:
                        pcall = kit.call(sb.pack_run, session, tool, arguments)
                    call_json = pcall.to_json()
                    call_result = pcall.result
            except SessionHaltedError as exc:
                return {
                    "domain": domain,
                    "tool": tool,
                    "arguments": arguments,
                    "tenant": tenant,
                    "outcome": "halted",
                    "feedback": str(exc),
                    "gateway": {"decision": "PERMIT", "permitted": True, "policies": []},
                    "measured": None,
                    "findings": [],
                    "receipt": None,
                    "ticket": next(iter(self._tickets_for(sb)), None),
                    "ledger": self._ledger_state(domain, sb),
                    "windows": self._windows_state(domain, sb),
                    "outbox": sb.world.outbox_states(),
                }

            # Track window usage from committed stage diffs.
            measured_data = call_json.get("measured")
            if isinstance(measured_data, Mapping) and measured_data.get("committed"):
                for w in measured_data.get("windows", ()):
                    if isinstance(w, Mapping) and "window" in w:
                        used_total = Decimal(str(w.get("history", "0"))) + Decimal(
                            str(w.get("amount", "0"))
                        )
                        sb.window_usage[str(w["window"])] = format(used_total.normalize(), "f")

            # Check if session.call itself raised a refused/repair ticket.
            if call_result is not None and call_result.ticket is not None:
                raised_ticket = self._load_ticket(sb, call_result.ticket.ticket_id)
                sb.votes.setdefault(raised_ticket.ticket_id, _TicketVotes())
                sb.ticket_mandates[raised_ticket.ticket_id] = mandate

            # Trip pause if requested, drain relay, and raise a held ticket for any newly held msg.
            if call_result is not None and call_result.committed:
                if hold_reason is not None:
                    sb.world.governor.trip(mandate.subject.scope, hold_reason)
                sb.drain()
                held_now = {m for m, s in sb.world.outbox_states().items() if s == "held"}
                if held_now - held_before:
                    hticket, _ = sb.world.fid.escalation.raise_held(
                        mandate.mandate_id, sb.world.outbox
                    )
                    if hticket is not None:
                        raised_ticket = hticket
                        sb.votes.setdefault(hticket.ticket_id, _TicketVotes())
                        sb.ticket_mandates[hticket.ticket_id] = mandate

            new_receipts = self._read_new_receipts(receipts_file, receipt_lines_before)
            latest_receipt = new_receipts[-1] if new_receipts else None
            findings = (
                list(measured_data.get("findings", ()))
                if isinstance(measured_data, Mapping)
                else []
            )

            return {
                "domain": domain,
                "tool": tool,
                "arguments": arguments,
                "tenant": tenant,
                "gateway": call_json["gateway"],
                "outcome": (
                    "compensated"
                    if isinstance(measured_data, Mapping) and measured_data.get("compensated")
                    else call_json["outcome"]
                ),
                "feedback": call_json["feedback"],
                "measured": measured_data,
                "unchanged": call_json.get("unchanged"),
                "findings": findings,
                "receipt": latest_receipt,
                "receipts": new_receipts,
                "ci": ci_summary,
                "ticket": (
                    self._serialize_ticket(sb, raised_ticket) if raised_ticket is not None else None
                ),
                "ledger": self._ledger_state(domain, sb),
                "windows": self._windows_state(domain, sb),
                "outbox": sb.world.outbox_states(),
            }

    # ------------------------------------------------------------------
    # POST /api/warrant/vote
    # ------------------------------------------------------------------

    def warrant_vote(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """``POST /api/warrant/vote``."""
        with self._lock:
            domain = str(payload.get("domain") or "fintech")
            sb = self._ensure_sandbox(domain)
            ticket_id = str(payload.get("ticket_id") or "").strip()
            if not ticket_id:
                raise ValueError("warrant/vote requires 'ticket_id'")
            principal = str(payload.get("principal") or "").strip()
            if principal not in sb.principals:
                raise ValueError(
                    f"principal {principal!r} is not in {domain}'s charter roster "
                    f"({', '.join(sb.principals)})"
                )
            raw_decision = str(payload.get("decision") or "approve").strip().lower()
            if raw_decision not in ("approve", "reject", "decline"):
                raise ValueError("decision must be 'approve', 'reject', or 'decline'")

            escalation = sb.world.fid.escalation
            ticket = self._load_ticket(sb, ticket_id)

            requested_res = payload.get("resolution")
            chosen_opt = ticket.options[0]
            if isinstance(requested_res, str) and requested_res:
                matched = next(
                    (
                        o
                        for o in ticket.options
                        if o.resolution.action == requested_res or o.digest == requested_res
                    ),
                    None,
                )
                if matched is None:
                    raise ValueError(f"unknown resolution {requested_res!r} for ticket {ticket_id}")
                chosen_opt = matched
            else:
                for o in ticket.options:
                    if o.resolution.action in ("release", "accept_repair", "amend"):
                        chosen_opt = o
                        break

            # On amend tickets where 'reject' is an explicit resolution option, allow voting for it;
            # on held/refused tickets, 'reject' or 'decline' signs a FIDU1 'decline' approval.
            fidu_decision = "approve"
            if raw_decision in ("reject", "decline"):
                reject_opt = next(
                    (o for o in ticket.options if o.resolution.action == "reject"), None
                )
                if raw_decision == "reject" and reject_opt is not None and not requested_res:
                    chosen_opt = reject_opt
                    fidu_decision = "approve"
                else:
                    fidu_decision = "decline"

            tracked = sb.votes.setdefault(ticket_id, _TicketVotes())
            already_approved = principal in tracked.approved_by
            already_declined = principal in tracked.declined_by

            approval = sb.world.approve(
                principal,
                "ticket",
                ticket.digest,
                resolution=chosen_opt.digest,
                decision=fidu_decision,
            )
            intake_records = escalation.process_intake()
            recorded_kinds = [r.kind for r in intake_records]
            was_recorded = "approval.recorded" in recorded_kinds

            duplicate = False
            if was_recorded:
                if fidu_decision == "approve":
                    if already_approved:
                        duplicate = True
                    else:
                        tracked.approved_by.append(principal)
                else:
                    if already_declined:
                        duplicate = True
                    else:
                        tracked.declined_by.append(principal)

            warrant = escalation.warrant_for_ticket(ticket_id)
            newly_executed = False
            if warrant is not None and not tracked.executed_records:
                results_list = (
                    sb.pack_run.results
                    if sb.pack_run is not None
                    else (sb.fintech_run.results if sb.fintech_run is not None else [])
                )
                seen = len(results_list)
                exec_records = sb.world.fid.executor.execute(warrant)
                tracked.executed_records = [r.kind for r in exec_records]
                tracked.committed = [
                    kit.measured(r) if sb.pack_run is not None else fintech_acts.measured(r)
                    for r in results_list[seen:]
                ]
                sb.drain()
                newly_executed = True

            vote_entry = {
                "principal": principal,
                "decision": raw_decision,
                "fidu_decision": fidu_decision,
                "resolution": chosen_opt.resolution.action,
                "resolution_digest": chosen_opt.digest,
                "approval_id": approval.approval_id,
                "approval_digest": approval.digest,
                "key_id": sb.world.key_id(principal),
                "recorded": recorded_kinds,
                "counted": was_recorded and not duplicate,
                "duplicate": duplicate,
                "warrant_digest": warrant.digest if warrant is not None else None,
            }
            tracked.history.append(vote_entry)
            serialized_ticket = self._serialize_ticket(sb, ticket)

            return {
                "domain": domain,
                "ticket_id": ticket_id,
                "ticket_digest": ticket.digest,
                "vote": vote_entry,
                "quorum": serialized_ticket["quorum"],
                "warrant": serialized_ticket["warrant"],
                "newly_executed": newly_executed,
                "ticket": serialized_ticket,
                "outbox": sb.world.outbox_states(),
                "ledger": self._ledger_state(domain, sb),
                "windows": self._windows_state(domain, sb),
            }


class ControlPlaneServer(http.server.ThreadingHTTPServer):
    """Loopback ``ThreadingHTTPServer`` carrying a :class:`ControlPlane`."""

    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], control: ControlPlane) -> None:
        self.control = control
        super().__init__(server_address, _ControlHandler)

    def server_close(self) -> None:
        try:
            self.control.close()
        finally:
            super().server_close()


class _ControlHandler(http.server.BaseHTTPRequestHandler):
    """Serves ``/api/*`` JSON endpoints alongside static files under ``control.root``."""

    server: ControlPlaneServer

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def _send_json(self, status: int, body: Mapping[str, Any]) -> None:
        encoded = (json.dumps(body, sort_keys=True, default=str) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _send_bytes(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> bytes:
        raw_len = self.headers.get("Content-Length")
        if raw_len is None:
            return b""
        length = int(raw_len)
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError(f"request body exceeds {MAX_BODY_BYTES} bytes")
        return self.rfile.read(length)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        route = parsed.path
        control = self.server.control

        if route == "/api/status":
            self._send_json(200, control.status())
            return

        if route in ("/", "/dashboard.html"):
            rendered = control.dashboard_html()
            if rendered is not None:
                self._send_bytes(200, rendered, "text/html; charset=utf-8")
                return

        rel = urllib.parse.unquote(route).lstrip("/")
        if not rel:
            self._send_json(404, {"error": "not found"})
            return
        candidate = (control.root / rel).resolve()
        if not candidate.is_relative_to(control.root) or not candidate.is_file():
            self._send_json(404, {"error": f"{rel!r} not found"})
            return
        guessed, _ = mimetypes.guess_type(candidate.name)
        ctype = guessed or (
            "application/x-tar" if candidate.suffix == ".fidb" else "application/octet-stream"
        )
        self._send_bytes(200, candidate.read_bytes(), ctype)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        route = parsed.path
        control = self.server.control

        try:
            body = self._read_body()
        except ValueError as exc:
            self._send_json(413, {"error": str(exc)})
            return

        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        query = dict(urllib.parse.parse_qsl(parsed.query))

        try:
            if route == "/api/verify-bundle":
                if ctype in ("application/octet-stream", "application/x-tar") or (
                    body and not body.lstrip().startswith(b"{")
                ):
                    result = control.verify_bundle_request(query, raw_bytes=body)
                else:
                    payload = json.loads(body.decode("utf-8")) if body else {}
                    if not isinstance(payload, Mapping):
                        raise ValueError("JSON body must be an object")
                    result = control.verify_bundle_request(payload)
                self._send_json(200, result)
                return

            payload = json.loads(body.decode("utf-8")) if body else {}
            if not isinstance(payload, Mapping):
                raise ValueError("JSON body must be an object")

            if route == "/api/sandbox/call":
                self._send_json(200, control.sandbox_call(payload))
                return
            if route == "/api/sandbox/reset":
                domain = str(payload.get("domain") or "fintech")
                self._send_json(200, control.reset_sandbox(domain))
                return
            if route == "/api/warrant/vote":
                self._send_json(200, control.warrant_vote(payload))
                return

            self._send_json(404, {"error": f"unknown endpoint {route!r}"})
        except FileNotFoundError as exc:
            self._send_json(404, {"error": str(exc)})
        except KeyError as exc:
            self._send_json(404, {"error": str(exc.args[0] if exc.args else exc)})
        except (ValueError, json.JSONDecodeError, FiduciaryError) as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:  # pragma: no cover - defensive JSON error guard
            self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})


def serve_control_plane(
    root: Path, where: str = "127.0.0.1:8740"
) -> tuple[ControlPlaneServer, str]:
    """Start the loopback interactive control plane over ``root``.

    :returns: ``(server, dashboard_url)``.
    :raises ValueError: If ``where`` is not a loopback ``HOST:PORT``.
    """
    host, _, port_str = (where or "127.0.0.1:8740").rpartition(":")
    host = host or "127.0.0.1"
    if host not in LOOPBACK_HOSTS:
        raise ValueError("--serve listens on loopback only: 127.0.0.1:<port>")
    try:
        port = int(port_str)
    except ValueError as exc:
        raise ValueError("--serve is HOST:PORT") from exc
    control = ControlPlane(root)
    try:
        server = ControlPlaneServer((host, port), control)
    except OSError:
        control.close()
        raise
    bound_port = server.server_address[1]
    url_host = f"[{host}]" if ":" in host else host
    return server, f"http://{url_host}:{bound_port}/dashboard.html"

"""The console's sandbox: each industry's deployment, built fresh in a scratch
directory, for calls and votes made in the browser.

A sandbox is a :class:`~fiduciary.control.Deployment` like any other: the desk
reads its tickets, drafts its approvals and checks their signatures the same
way. Two things differ, and the page says so. The console holds the demo's
principal keys (``signs``), so a vote can be signed for a principal with one
click; and the console is its writer, so an approval it takes is recorded at
once, a warrant forms at quorum, and the warrant is executed at the heads it
pins, then the relay delivers what it released.

Nothing here touches a recorded run on disk: every sandbox lives in a
temporary directory the console removes when it stops, and reset builds it
again from nothing.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from fiduciary.bridge import SessionHaltedError
from fiduciary.control import ControlError, Deployment, Pins, _Receipts, receipt_view
from fiduciary.demo import acts as fintech
from fiduciary.demo import packs
from fiduciary.demo.gateway import Gateway
from fiduciary.demo.overview import INDUSTRIES
from fiduciary.demo.packs import coding_agent, healthcare, kit, saas, sre, supply_chain
from fiduciary.demo.world import ORDERS, PRINCIPALS, SCOPE, TENANTS, World, tenant_of
from fiduciary.demo.world import build as build_fintech
from fiduciary.documents import Approval, Record
from fiduciary.exceptions import FiduciaryError
from fiduciary.log import LOG_ID
from fiduciary.store import State, Store

__all__ = ["PRESETS", "Sandbox"]

_RUNG_NAMES: Final = {"T": "transactional", "P": "plan-bound", "V": "predicted"}


@dataclass(frozen=True, slots=True)
class Preset:
    """A call to try: what it is, and what the stack is expected to do with it."""

    id: str
    expect: str
    """``commits``, ``refused`` (a hidden consequence the stack stops), or
    ``held`` (a ticket for a quorum)."""
    label: str
    note: str
    tool: str
    arguments: Mapping[str, Any]
    tenant: str | None = None
    hold: str | None = None
    """The pause an approver puts on the scope after commit, which holds the
    outbound request for a release warrant."""

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "expect": self.expect,
            "label": self.label,
            "note": self.note,
            "tool": self.tool,
            "arguments": dict(self.arguments),
            "tenant": self.tenant,
            "hold": self.hold,
        }


def _presets() -> dict[str, tuple[Preset, ...]]:
    east = supply_chain.WAREHOUSES[0]
    return {
        "fintech": (
            Preset(
                "everyday",
                "commits",
                "Refund $12.50 on order 5",
                "Two rows and one Stripe refund, inside every limit.",
                "refund_order",
                {"order_id": 5, "amount": "12.50", "payment_intent": "pi_0000000005"},
                "acme",
            ),
            Preset(
                "cascade",
                "refused",
                "Flag order 17 disputed",
                "The gateway allows it. A settlement trigger puts 49 more orders across four "
                "tenants on hold, and the stage measures that and rolls it back.",
                "flag_dispute",
                {"order_id": 17},
                "acme",
            ),
            Preset(
                "held",
                "held",
                "Refund $25.00 on order 25, refunds paused",
                "Commits. The on-call pause then holds the Stripe refund until two of three "
                "principals sign its release.",
                "refund_order",
                {"order_id": 25, "amount": "25.00", "payment_intent": "pi_0000000025"},
                "acme",
                "paused by on-call: refund volume under review",
            ),
        ),
        "healthcare": (
            Preset(
                "everyday",
                "commits",
                "Draft acetaminophen for bed 4A",
                "One MedicationRequest, predicted, written on the version read and read back. "
                "One dispense request goes to the pharmacy.",
                "draft_order",
                dict(healthcare.ACETAMINOPHEN),
                healthcare.CHARTS[0],
            ),
            Preset(
                "cascade",
                "refused",
                "Discontinue one order on chart 3",
                "The EHR's encounter rule stops the chart's other orders too. The read-back "
                "differs from the prediction, so every record is put back.",
                "discontinue_order",
                {"order_id": healthcare.first_order(3)},
                healthcare.CHARTS[3],
            ),
            Preset(
                "held",
                "held",
                "Order oxycodone 10 mg for bed 4B",
                "A Schedule II dispense waits in the pharmacy outbox for two of three "
                "clinical principals.",
                "order_schedule_ii",
                {
                    "bed": "4B",
                    "drug": "oxycodone",
                    "dose_mg": 10,
                    "mme_per_day": 30,
                    "schedule": "q6h",
                },
                healthcare.CHARTS[1],
            ),
        ),
        "sre": (
            Preset(
                "everyday",
                "commits",
                "Scale checkout from 3 to 4 replicas",
                "The API server's dry run measures one more replica and two vCPU, and the "
                "change is applied on the measured resourceVersion.",
                "scale_deployment",
                {"deployment": "checkout", "replicas": 4},
                sre.PROD,
            ),
            Preset(
                "cascade",
                "refused",
                "Scale api to 6 replicas",
                "A mutating webhook makes it 60 in the dry run, so the vCPU and drawdown limits "
                "refuse the plan before anything is applied.",
                "scale_deployment",
                {"deployment": "api", "replicas": 6},
                sre.PROD,
            ),
            Preset(
                "held",
                "held",
                "Open TCP 443 on security group 1",
                "The cloud API request is held until two of three on-call principals sign.",
                "open_port",
                {"security_group_id": 1, "port": 443},
                sre.PROD,
            ),
        ),
        "supply-chain": (
            Preset(
                "everyday",
                "commits",
                "Order 120 of B-2002 from supplier 4",
                "One purchase-order line in wh-east and one EDI order.",
                "create_po",
                {"sku": "B-2002", "supplier_id": 4, "qty": 120, "uom": "each"},
                east,
            ),
            Preset(
                "cascade",
                "refused",
                "Raise A-1001's reorder point to 500",
                "The MRP trigger explodes the bill of materials across its components and "
                "suppliers, so the plan is refused and rolled back.",
                "set_reorder_point",
                {"reorder_point_id": supply_chain.reorder_point(0, "A-1001"), "point": 500},
                east,
            ),
            Preset(
                "held",
                "held",
                "Order 80 of A-1001 from supplier 7, EDI paused",
                "Commits. Treasury's pause then holds the EDI order until a warrant releases it.",
                "create_po",
                {"sku": "A-1001", "supplier_id": 7, "qty": 80, "uom": "each"},
                east,
                "paused by treasury: supplier 7 under review",
            ),
        ),
        "saas": (
            Preset(
                "everyday",
                "commits",
                "Mark one Acme opportunity won",
                "One row, inside Acme's own workspace mandate.",
                "update_opportunity",
                {"opportunity_id": saas.opportunity(0, 4), "stage": "won"},
                saas.ACME,
            ),
            Preset(
                "cascade",
                "refused",
                "Reassign Acme's lost deals",
                "The territory subquery reaches other customers' rows, so tenant isolation "
                "refuses the plan.",
                "bulk_reassign",
                {"stage": "lost", "owner": "u77@acme.example"},
                saas.ACME,
            ),
            Preset(
                "held",
                "held",
                "Invite a sales rep to Globex, email paused",
                "Commits. Trust and safety's pause then holds the invitation email until "
                "it's released.",
                "assign_role",
                {"user": "sam@globex.example", "role": "sales-rep"},
                saas.ORGS[1],
                "paused by trust and safety: phishing review",
            ),
        ),
        "coding-agent": (
            Preset(
                "everyday",
                "commits",
                "Fix invoice rounding",
                "A one-line edit, staged in a worktree and committed onto main with a "
                "compare-and-swap from the measured head.",
                "edit_file",
                {"path": coding_agent.INVOICE, "old": coding_agent.BUG, "new": coding_agent.FIXED},
                coding_agent.REPO,
            ),
            Preset(
                "cascade",
                "refused",
                "Bump the API schema to 1.5",
                "The code generator rewrites 35 client files, so the blast-radius limit refuses "
                "the stage before main moves.",
                "edit_file",
                {"path": coding_agent.SCHEMA, "old": '"version": "1.4"', "new": '"version": "1.5"'},
                coding_agent.REPO,
            ),
            Preset(
                "held",
                "held",
                "Bump httpx to 0.28.1",
                "CI runs and signs its result. The bump also touches the protected workflow "
                "file, so a repair ticket, pinned to the head, waits for two of three maintainers.",
                "bump_dependency",
                {"package": "httpx", "version": "0.28.1"},
                coding_agent.REPO,
            ),
        ),
    }


PRESETS: Final = _presets()


def _profile(key: str) -> dict[str, Any]:
    entry = next(e for e in INDUSTRIES if e["key"] == key)
    pack = packs.PACKS.get(key)
    rung = str(entry["class"])
    return {
        "key": key,
        "industry": pack.industry if pack is not None else str(entry["industry"]),
        "title": pack.title if pack is not None else str(entry["title"]),
        "substrate": str(entry.get("substrate", "SQLite")),
        "rung": rung,
        "measurement": _RUNG_NAMES[rung],
        "principals": list(pack.principals if pack is not None else PRINCIPALS),
        "tenants": list(pack.tenants if pack is not None else TENANTS),
        "partition": pack.partition if pack is not None else "tenant",
        "nouns": dict(pack.nouns) if pack is not None else {},
        "presets": [p.to_json() for p in PRESETS[key]],
    }


@dataclass
class _Built:
    world: World
    run: fintech.Run | kit.PackRun
    stripe: fintech.Stripe | None = None
    ci: coding_agent.Ci | None = None
    calls: int = 0
    held: set[str] = field(default_factory=set)


class SandboxDeployment(Deployment):
    """One industry's scratch deployment. The console is its writer."""

    kind = "sandbox"
    signs = True

    def __init__(self, key: str, directory: Path) -> None:
        super().__init__(f"sandbox-{key}", f"{_profile(key)['industry']} sandbox", accepts=True)
        self.key = key
        self.directory = directory
        self._built: _Built | None = None
        self._state: State | None = None

    @property
    def active(self) -> bool:
        return self._built is not None

    def describe(self) -> dict[str, str]:
        return {"domain": self.key}

    def built(self) -> _Built:
        if self._built is None:
            self._built = self._build()
        return self._built

    def _build(self) -> _Built:
        target = self.directory / f"{self.key}-{len(list(self.directory.glob(self.key + '-*')))}"
        if self.key == "fintech":
            world = build_fintech(target)
            gateway = Gateway(
                SCOPE, "acme", {n: tenant_of(n) for n in range(1, ORDERS + 1)}, engine="auto"
            )
            run: fintech.Run | kit.PackRun = fintech.Run(world, gateway)
            run.observe()
            return _Built(world, run, stripe=fintech.Stripe())
        pack = packs.PACKS[self.key]
        pworld = kit.build(target, pack)
        prun = kit.PackRun(pworld, kit.PackGateway(pack, pack.tenants[0], engine="auto"))
        prun.observe()
        if self.key == "saas":
            saas._workspaces(prun)
        ci = coding_agent.Ci(pworld.system) if self.key == "coding-agent" else None
        return _Built(pworld, prun, ci=ci)

    # -- Deployment -------------------------------------------------------------

    def read(self) -> tuple[Store, State]:
        world = self.built().world
        state = self._state or State.of([])
        head = world.store.head(LOG_ID)[0]
        if head != state.head_seq:
            state = state.copy()
            for stored in world.store.records(LOG_ID, after=state.head_seq):
                state.apply(Record.loads(stored.data))
        self._state = state
        return world.store, state

    def receipts(self) -> _Receipts:
        return _Receipts(self.built().world.root / "receipts.jsonl")

    def outbox(self) -> str | None:
        return str(self.built().world.root / "back_office.db")

    def bindings(self) -> dict[str, Any]:
        return {b.digest: b for b in self.built().world.tools.values()}

    def holds(self) -> dict[tuple[str, str], bool]:
        sinks = self.built().world.deployment.sinks
        return {(s.name, op.name): op.hold for s in sinks._sinks.values() for op in s.operations}

    def pins(self) -> Pins | None:
        if self._built is None:
            return None
        world = self._built.world
        keys = kit.pinned(world) if isinstance(world, kit.PackWorld) else fintech.pinned(world)
        return Pins(world.charter.digest, keys)

    def sign(self, draft: Approval) -> Approval:
        signer = self.built().world.signers.get(draft.principal)
        if signer is None:
            raise ControlError(400, f"the sandbox holds no key of {draft.principal!r}")
        return draft.signed(signer)

    def submit(self, approval: Approval) -> dict[str, Any]:
        """Into the intake, then one writer turn: record, form, execute, deliver."""
        built = self.built()
        world = built.world
        intake = world.store.submit(approval.canonical())
        records = list(world.fid.escalation.process_intake())
        _, state = self.read()
        seen = len(built.run.results)
        for row in state.unexecuted_warrants():
            records.extend(world.fid.executor.execute(row.digest))
        self._drain(built)
        measure = kit.measured if isinstance(built.run, kit.PackRun) else fintech.measured
        return {
            "intake": intake,
            "recorded": [r.kind for r in records],
            "released": [measure(r) for r in built.run.results[seen:]],
            "outbox": world.outbox_states(),
        }

    def close(self) -> None:
        if self._built is not None:
            root = self._built.world.root.resolve()
            self._built.world.close()
            healthcare._EHRS.pop(root, None)
            sre._CLUSTERS.pop(root, None)
            self._built = None
            self._state = None

    # -- calls ------------------------------------------------------------------

    @staticmethod
    def _drain(built: _Built) -> None:
        if isinstance(built.run, kit.PackRun):
            assert isinstance(built.world, kit.PackWorld)
            kit.drain(built.world, built.run.vendor)
        else:
            assert built.stripe is not None
            fintech.drain(built.world, built.stripe)

    def tools(self) -> list[dict[str, Any]]:
        return [
            {"name": name, "description": b.description, "input": b.body()["input"]}
            for name, b in sorted(self.built().world.tools.items())
        ]

    def ledger(self) -> dict[str, Any]:
        world = self.built().world
        assert world.mandate is not None
        body = world.mandate.body()
        scope = world.mandate.subject.scope
        world.governor.refresh()
        envelope = Decimal(str(body["budget"]["envelope"]))
        units = {}
        if isinstance(world, kit.PackWorld):
            for code, ledger in sorted(world.ledgers.items()):
                ledger.refresh()
                funded = Decimal(str(body.get("budgets", {}).get(code, {}).get("envelope", "0")))
                units[code] = {
                    "envelope": _plain(funded),
                    "available": _plain(ledger.available(scope)),
                    "halted": ledger.is_halted(scope),
                }
        return {
            "scope": scope,
            "envelope": _plain(envelope),
            "available": _plain(world.governor.available(scope)),
            "halted": world.governor.is_halted(scope),
            "units": units,
        }

    def call(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        built = self.built()
        world = built.world
        preset = next((p for p in PRESETS[self.key] if p.id == payload.get("preset")), None)
        if payload.get("preset") and preset is None:
            raise ControlError(400, f"no preset {payload.get('preset')!r} in {self.key}")
        if preset is not None:
            tool, arguments = preset.tool, dict(preset.arguments)
            tenant, hold = preset.tenant, preset.hold
        else:
            tool = str(payload.get("tool") or "")
            if tool not in world.tools:
                raise ControlError(400, f"{tool!r} is no tool of the {self.key} sandbox")
            raw = payload.get("arguments", {})
            if not isinstance(raw, Mapping):
                raise ControlError(400, "arguments is a JSON object")
            arguments = dict(raw)
            tenant = str(payload.get("tenant") or "") or None
            hold = "paused by an approver: review before release" if payload.get("hold") else None
        profile = _profile(self.key)
        tenant = tenant or profile["tenants"][0]
        if tenant not in profile["tenants"]:
            raise ControlError(400, f"{tenant!r} is no {profile['partition']} of this sandbox")
        built.calls += 1
        trajectory = f"console-{built.calls}"
        receipts_before = self._receipt_count(world)
        self._drain(built)
        held_before = {m for m, s in world.outbox_states().items() if s == "held"}
        ci: dict[str, Any] | None = None
        if tool == "bump_dependency" and "manifest" not in arguments:
            arguments, ci = self._ci(built, arguments, tenant, trajectory)
        mandate = world.mandate
        assert mandate is not None
        if isinstance(world, kit.PackWorld) and tenant in world.children:
            mandate = world.children[tenant]
        scope = mandate.mandate_id if mandate.parent is not None else mandate.subject.scope
        try:
            with world.fid.session(scope, tenant=tenant, trajectory=trajectory) as session:
                if isinstance(built.run, kit.PackRun):
                    called: Any = kit.call(built.run, session, tool, arguments)
                else:
                    called = fintech.call(built.run, session, tool, arguments)
        except SessionHaltedError as exc:
            return {"tool": tool, "arguments": arguments, "outcome": "halted", "feedback": str(exc)}
        except FiduciaryError as exc:
            raise ControlError(400, str(exc)) from exc
        found = called.to_json()
        ticket_id: str | None = None
        result = called.result
        if result is not None and result.ticket is not None:
            ticket_id = result.ticket.ticket_id
        if result is not None and result.committed:
            if hold is not None:
                world.governor.trip(mandate.subject.scope, hold)
            self._drain(built)
            held_now = {m for m, s in world.outbox_states().items() if s == "held"}
            if held_now - held_before:
                raised, _ = world.fid.escalation.raise_held(mandate.mandate_id, world.outbox)
                if raised is not None:
                    ticket_id = raised.ticket_id
        receipts = self._new_receipts(world, receipts_before)
        measured = found.get("measured")
        if isinstance(measured, Mapping) and measured.get("compensated"):
            found["outcome"] = "compensated"
        return {
            **found,
            "tenant": tenant,
            "preset": preset.id if preset is not None else None,
            "ci": ci,
            "receipts": receipts,
            "ticket": ticket_id,
            "outbox": world.outbox_states(),
            "ledger": self.ledger(),
        }

    def _ci(
        self, built: _Built, arguments: Mapping[str, Any], tenant: str, trajectory: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Attested CI for a dependency bump, then the bump's files at the head."""
        assert isinstance(built.run, kit.PackRun) and built.ci is not None
        package = str(arguments.get("package", "httpx"))
        version = str(arguments.get("version", "0.28.1"))
        kit.one(
            built.run,
            "request_ci",
            {"package": package, "version": version},
            tenant=tenant,
            trajectory=f"{trajectory}-ci",
        )
        hooks = coding_agent.run_ci(built.run, built.ci)
        assert isinstance(built.world, kit.PackWorld)
        repo: coding_agent.Repository = built.world.system
        files = coding_agent.bumped(repo.files(), package, version)
        bumped = {
            "base": repo.head(),
            "manifest": files["pyproject.toml"],
            "lock": files["uv.lock"],
            "matrix": files[".github/workflows/ci.yml"],
        }
        return bumped, {
            "package": package,
            "version": version,
            "accepted": sum(1 for w in hooks if w["status"] < 300),
            "refused": sum(1 for w in hooks if w["status"] >= 300),
        }

    @staticmethod
    def _receipt_count(world: World) -> int:
        path = world.root / "receipts.jsonl"
        if not path.is_file():
            return 0
        with path.open(encoding="utf-8") as lines:
            return sum(1 for line in lines if line.strip())

    @staticmethod
    def _new_receipts(world: World, before: int) -> list[dict[str, Any]]:
        path = world.root / "receipts.jsonl"
        if not path.is_file():
            return []
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [receipt_view(json.loads(line)) for line in lines[before:]]


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


class Sandbox:
    """Every industry's sandbox, built on first use, in one scratch directory."""

    def __init__(self) -> None:
        self._dir = Path(tempfile.mkdtemp(prefix="fiduciary-sandbox-"))
        self._lock = threading.RLock()
        self._domains = {
            key: SandboxDeployment(key, self._dir) for key in [str(e["key"]) for e in INDUSTRIES]
        }

    def deployments(self) -> list[Deployment]:
        return [d for d in self._domains.values() if d.active]

    def deployment(self, ident: str) -> Deployment | None:
        found = self._domains.get(ident.removeprefix("sandbox-"))
        return (
            found if found is not None and found.active and ident.startswith("sandbox-") else None
        )

    def _domain(self, key: str) -> SandboxDeployment:
        found = self._domains.get(key)
        if found is None:
            raise ControlError(404, f"no sandbox {key!r}; there are {', '.join(self._domains)}")
        return found

    def describe(self) -> dict[str, Any]:
        out = []
        for key, each in self._domains.items():
            entry: dict[str, Any] = {**_profile(key), "id": each.ident, "active": each.active}
            if each.active:
                with each.lock:
                    entry["tools"] = each.tools()
                    entry["ledger"] = each.ledger()
            out.append(entry)
        return {"domains": out}

    def open(self, key: str) -> dict[str, Any]:
        """Build ``key``'s sandbox, if it is not yet, and describe it."""
        each = self._domain(key)
        with each.lock:
            each.built()
            return {
                **_profile(key),
                "id": each.ident,
                "active": True,
                "tools": each.tools(),
                "ledger": each.ledger(),
            }

    def call(self, key: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        each = self._domain(key)
        with each.lock:
            return each.call(payload)

    def reset(self, key: str) -> dict[str, Any]:
        each = self._domain(key)
        with each.lock:
            each.close()
        return self.open(key)

    def close(self) -> None:
        with self._lock:
            for each in self._domains.values():
                each.close()
            shutil.rmtree(self._dir, ignore_errors=True)

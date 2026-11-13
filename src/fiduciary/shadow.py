"""What a shadow mandate let through: ``fiduciary shadow report`` (1.14.0).

Under a shadow mandate every plan commits, and the engine records on each lineage's escrow
chain what would have refused it. The report reads those chains, which are hash-linked and
verified as they load, so it answers from the deployment's own records and not from what
any process remembers:

- how many plans were staged, committed, and refused (a shadow mandate still refuses what
  is not its own: a plan for another scope);
- for each check, how many plans it would have refused, and which tools made them;
- the largest changes measured, by count, with the check that would have stopped each.

It reads only the chain: the checks' names, the measured counts and the tools' names. The
verdicts' full text, which can name other tenants, is in the operator's view and the audit
bundle, not here.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Final

__all__ = ["Lineage", "Plan", "read_chain", "render", "report"]

_OBSERVED: Final = "admitted; observed, not enforced: "
_TOOL: Final = re.compile(r"(?:fiduciary sdk|FIDU1 tool) ([a-z][a-z0-9_]{0,63})")
_COUNT: Final = re.compile(r"(\d+) ([a-z]+(?: [a-z]+)?)")


@dataclass
class Plan:
    """One plan, as its records on the chain describe it."""

    plan_id: str
    tool: str = ""
    at: datetime | None = None
    changes: int = 0
    noun: str = "changes"
    would_refuse: tuple[str, ...] = ()
    blocked_by: tuple[str, ...] = ()
    outcome: str = "open"
    """``committed``, ``aborted``, ``compensated``, or ``open`` while no terminal record
    follows."""


@dataclass
class Lineage:
    """One lineage's chain, read."""

    mandate_id: str
    plans: list[Plan] = field(default_factory=list)

    @property
    def observed(self) -> list[Plan]:
        return [p for p in self.plans if p.would_refuse]

    def by_check(self) -> dict[str, Counter[str]]:
        """For each check that would have refused a plan, the tools whose plans it was."""
        found: dict[str, Counter[str]] = defaultdict(Counter)
        for plan in self.observed:
            for check in plan.would_refuse:
                found[check][plan.tool or "?"] += 1
        return dict(found)

    def to_json(self, top: int = 5) -> dict[str, Any]:
        outcomes = Counter(p.outcome for p in self.plans)
        return {
            "mandate_id": self.mandate_id,
            "plans": len(self.plans),
            "committed": outcomes["committed"],
            "refused": outcomes["aborted"],
            "compensated": outcomes["compensated"],
            "would_have_refused": len(self.observed),
            "checks": {
                check: {"plans": sum(tools.values()), "tools": dict(sorted(tools.items()))}
                for check, tools in sorted(self.by_check().items())
            },
            "largest": [
                {
                    "plan_id": p.plan_id,
                    "tool": p.tool,
                    "changes": p.changes,
                    "noun": p.noun,
                    "would_refuse": list(p.would_refuse),
                    "at": None if p.at is None else p.at.isoformat(),
                }
                for p in _largest(self.plans, top)
            ],
        }


def _largest(plans: Iterable[Plan], top: int) -> list[Plan]:
    return sorted(plans, key=lambda p: (-p.changes, p.plan_id))[:top]


def read_chain(path: Path) -> Lineage:
    """One lineage's chain file (``<mandate_id>.chain``), verified as it loads.

    :raises interlock.exceptions.ChainIntegrityError: If its links or digests do not hold.
    """
    from interlock.chain import EscrowChain, RecordType

    chain = EscrowChain.load(path)
    plans: dict[str, Plan] = {}
    for record in chain.records():
        plan = plans.setdefault(str(record.plan_id), Plan(str(record.plan_id)))
        note = record.note
        kind = record.record_type
        if kind is RecordType.PLAN_ADMITTED:
            plan.at = record.timestamp
            named = _TOOL.search(note)
            plan.tool = named.group(1) if named else note[:40]
        elif kind is RecordType.DIFF_COMPUTED:
            counted = _COUNT.match(note)
            if counted:
                plan.changes = int(counted.group(1))
                plan.noun = counted.group(2)
        elif kind is RecordType.VERDICT:
            if note.startswith(_OBSERVED):
                plan.would_refuse = tuple(note.removeprefix(_OBSERVED).split(","))
            elif note != "admitted":
                plan.blocked_by = tuple(note.split(","))
        elif kind in (RecordType.COMMITTED, RecordType.ABORTED, RecordType.COMPENSATED):
            plan.outcome = kind.value
    staged = [p for p in plans.values() if p.at is not None]
    return Lineage(path.stem, staged)


def report(chains: Path, mandate: str | None = None) -> list[Lineage]:
    """Every lineage's chain in ``chains``, or only ``mandate``'s."""
    paths = sorted(chains.glob("*.chain"))
    if mandate is not None:
        paths = [p for p in paths if p.stem == mandate]
    return [read_chain(p) for p in paths]


def _plural(count: int, noun: str) -> str:
    return noun if count == 1 else noun + ("es" if noun.endswith(("s", "x")) else "s")


def render(lineages: Sequence[Lineage], *, top: int = 5) -> str:
    """The report, as a person reads it."""
    if not lineages:
        return "No lineage has staged a plan here yet."
    out: list[str] = []
    for lineage in lineages:
        summary = lineage.to_json(top)
        plans = summary["plans"]
        out.append(f"Mandate {lineage.mandate_id}")
        out.append(
            f"  {plans} {_plural(plans, 'plan')} staged: {summary['committed']} committed, "
            f"{summary['refused']} refused, {summary['compensated']} put back."
        )
        observed = summary["would_have_refused"]
        if not observed:
            out.append("  Nothing would have been refused.")
        else:
            out.append(
                f"  {observed} of them would have been refused if the mandate were enforced:"
            )
            for check, found in summary["checks"].items():
                tools = ", ".join(f"{tool} ({n})" for tool, n in found["tools"].items())
                out.append(
                    f"    {check}: {found['plans']} {_plural(found['plans'], 'plan')}, from {tools}"
                )
        largest = [p for p in summary["largest"] if p["changes"]]
        if largest:
            out.append("  The largest changes measured:")
            for p in largest:
                why = (
                    f", would have been refused by {', '.join(p['would_refuse'])}"
                    if p["would_refuse"]
                    else ""
                )
                out.append(f"    {p['changes']} {p['noun']}: {p['tool']}, plan {p['plan_id']}{why}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"

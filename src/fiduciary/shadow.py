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

*Added in 1.19.0:* what left each session, from the deployment's own log (``session.sent`` and
``session.read``, F8-14): how many sends were judged before they left, how many were refused,
and, under a shadow mandate, how many would have been and by which rule (a credential the
session had read, the rule of two, a destination, a budget), with the tools that made them;
and the reads the read budget withheld, or would have.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Final

__all__ = ["Flow", "Lineage", "Plan", "flows", "read_chain", "render", "report"]

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


RULE_WORDS: Final = {
    "value:secret": "a credential the session had read, in what it sent",
    "value:private": "a private value the session had read, sent where the mandate does not list",
    "rule_of_two": "the rule of two: untrusted input and sensitive data, then a send",
    "destination:url": "a host the mandate does not list, after sensitive data",
    "destination:email": "an email domain the mandate does not list, after sensitive data",
    "destination:repository": "a repository not listed as private, after sensitive data",
    "budget:bytes_sent": "past the send budget",
    "budget:records_read": "past the read budget",
}
"""Each outbound rule (F8-14), as the report says it."""


@dataclass
class Flow:
    """What left one lineage's sessions (1.19.0), from its ``session.sent`` and
    ``session.read`` records."""

    mandate_id: str
    sends: int = 0
    refused: int = 0
    """Sends refused under an enforcing mandate."""
    observed: int = 0
    """Sends a shadow mandate let through that it would have refused."""
    rules: dict[str, Counter[str]] = field(default_factory=dict)
    """For each rule a send broke (refused or observed), the tools whose sends it was."""
    withheld: int = 0
    would_withhold: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "sends": self.sends,
            "refused": self.refused,
            "would_have_refused": self.observed,
            "rules": {
                rule: {"sends": sum(tools.values()), "tools": dict(sorted(tools.items()))}
                for rule, tools in sorted(self.rules.items())
            },
            "reads_withheld": self.withheld,
            "reads_would_have_withheld": self.would_withhold,
        }


def flows(sessions: Mapping[str, Sequence[tuple[str, Mapping[str, Any], int]]]) -> dict[str, Flow]:
    """Each lineage's :class:`Flow`, from a store's sessions (``State.sessions``)."""
    found: dict[str, Flow] = {}
    for events in sessions.values():
        for event, body, _ in events:
            if event not in ("read", "sent"):
                continue
            flow = found.setdefault(str(body["mandate_id"]), Flow(str(body["mandate_id"])))
            outcome = str(body.get("outcome", ""))
            if event == "read":
                flow.withheld += outcome == "withheld"
                flow.would_withhold += outcome == "shadow"
            else:
                flow.sends += 1
                flow.refused += outcome == "refused"
                flow.observed += outcome == "shadow"
            for rule in body.get("rules", ()):
                flow.rules.setdefault(str(rule), Counter())[str(body["tool"])] += 1
    return found


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


def _flow_lines(flow: Flow) -> list[str]:
    out = []
    if flow.sends:
        out.append(
            f"  {flow.sends} {_plural(flow.sends, 'send')} judged before leaving: "
            f"{flow.refused} refused."
        )
        if flow.observed:
            out.append(
                f"  {flow.observed} {_plural(flow.observed, 'send')} would have been refused "
                f"if the mandate were enforced:"
            )
    for rule, tools in sorted(flow.rules.items()):
        if rule == "budget:records_read":
            continue
        named = ", ".join(f"{tool} ({n})" for tool, n in sorted(tools.items()))
        total = sum(tools.values())
        out.append(
            f"    {RULE_WORDS.get(rule, rule)}: {total} {_plural(total, 'send')}, from {named}"
        )
    if flow.withheld or flow.would_withhold:
        out.append(
            f"  Reads past the read budget: {flow.withheld} withheld"
            + (f", {flow.would_withhold} would have been" if flow.would_withhold else "")
            + "."
        )
    return out


def render(
    lineages: Sequence[Lineage], *, top: int = 5, outbound: Mapping[str, Flow] | None = None
) -> str:
    """The report, as a person reads it."""
    flowing = dict(outbound or {})
    if not lineages and not flowing:
        return "No lineage has staged a plan or sent anything here yet."
    out: list[str] = []
    for lineage in [
        *lineages,
        *(
            Lineage(m)
            for m in sorted(flowing)
            if m not in {lineage.mandate_id for lineage in lineages}
        ),
    ]:
        summary = lineage.to_json(top)
        plans = summary["plans"]
        out.append(f"Mandate {lineage.mandate_id}")
        if not plans and lineage.mandate_id in flowing:
            out.extend(_flow_lines(flowing[lineage.mandate_id]))
            out.append("")
            continue
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
        flow = flowing.get(lineage.mandate_id)
        if flow is not None:
            out.extend(_flow_lines(flow))
        out.append("")
    return "\n".join(out).rstrip() + "\n"

"""``fiduciary init``: a deployment, ready to run, in a new directory.

    fiduciary init --out desk --mcp -- python -m my_mcp_server --stdio

With ``--mcp`` it starts the MCP server, asks it ``tools/list``, and governs each tool by
what the server says of it (MCP's tool annotations), conservatively:

=====================================  ========================================================
The server says                        The tool is
=====================================  ========================================================
``readOnlyHint: true``                 a ``passthrough`` binding: called directly, metered,
                                       counted by the breaker and the velocity bound
``destructiveHint: false`` (a write)   a ``plan`` binding whose one request goes to the
                                       server through the outbox, at most once, receipted
                                       ``request-only``
anything else                          the same, its operation held (``hold = true``): each
                                       call waits for a warrant of two of three principals
=====================================  ========================================================

MCP's own default for a write is ``destructiveHint: true``, so a tool that says nothing is
held. A tool whose input the binding grammar cannot hold whole (a number, which FIDU1 keeps
as a decimal string; a keyword it would not enforce) is left out and named: nothing is
governed by a schema that says less than the server's.

What it writes (the demo's builder, :func:`fiduciary.demo.packs.kit.build`, the one every
demo industry is stood up with): keys, an AgentGov ledger, a store, a charter its three
principals adopt, a root mandate pinning every binding, issued by two of them and bound, the
outbox, ``interlock.toml`` (its ``mcp`` sink, and the relay that calls the server) and
``fiduciary.toml``; and ``README.md``, which says how to run it. Without ``--mcp``, the same
deployment with no tools: the place to add bindings of your own.

The principals' keys are written under ``keys/``. They sign every approval: move each to its
principal, who signs with it in ``fiduciary console`` or with ``fiduciary approve``, and
delete it here.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

__all__ = ["InitReport", "classify", "discover", "init", "schema_of"]

SINK: Final = "upstream"
READ: Final = "read"
WRITE: Final = "write"
HELD: Final = "held"
_NAME: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")
_KEEP: Final = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "minLength",
        "maxLength",
        "pattern",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
        "title",
        "description",
    }
)
_HARMLESS: Final = frozenset({"$schema", "$id", "examples", "default", "$comment"})
_TYPES: Final = frozenset({"object", "array", "string", "integer", "boolean", "null"})


class Unsupported(ValueError):
    """A tool the binding grammar cannot hold whole."""


@dataclass
class InitReport:
    """What ``init`` made of each tool, and where it wrote the deployment."""

    root: Path
    scope: str
    tools: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    principals: tuple[str, ...] = ()


def discover(command: Sequence[str], *, timeout: float = 30.0) -> list[dict[str, Any]]:
    """Every tool the MCP server ``command`` runs lists (``tools/list``, every page).

    :raises fiduciary.mcp.McpError: When it does not start or answer.
    """
    from fiduciary.mcp import Upstream

    upstream = Upstream(command, timeout=timeout)
    try:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            found = upstream.request("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(t for t in found.get("tools") or () if isinstance(t, dict))
            cursor = found.get("nextCursor")
            if not cursor:
                return tools
    finally:
        upstream.close()


def classify(tool: Mapping[str, Any]) -> tuple[str, str]:
    """How a tool is governed, and why, from MCP's tool annotations."""
    hints = tool.get("annotations") or {}
    if not isinstance(hints, Mapping):
        hints = {}
    if hints.get("readOnlyHint") is True:
        return READ, "the server marks it read-only (readOnlyHint)"
    if hints.get("destructiveHint") is False:
        return WRITE, "a write the server marks non-destructive (destructiveHint: false)"
    if hints.get("destructiveHint") is True:
        return HELD, "the server marks it destructive (destructiveHint)"
    return HELD, "a write that says nothing of its effects: destructive, by MCP's default"


def schema_of(schema: object, path: str = "inputSchema") -> dict[str, Any]:
    """The tool's input schema in the binding grammar, or :class:`Unsupported`."""
    if not isinstance(schema, Mapping):
        raise Unsupported(f"{path} is not an object")
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in _HARMLESS:
            continue
        if key not in _KEEP:
            raise Unsupported(f"{path} uses {key!r}, which a binding would not enforce")
        if key == "type":
            kinds = [value] if isinstance(value, str) else value
            if not isinstance(kinds, list) or not all(isinstance(k, str) for k in kinds):
                raise Unsupported(f"{path}.type is no type")
            if "number" in kinds:
                raise Unsupported(
                    f"{path} is a number: FIDU1 holds no floats (a decimal is a string)"
                )
            if not set(kinds) <= _TYPES:
                raise Unsupported(f"{path}.type {value!r} is outside the binding grammar")
            out[key] = value
        elif key == "properties":
            if not isinstance(value, Mapping):
                raise Unsupported(f"{path}.properties is not an object")
            out[key] = {
                str(name): schema_of(sub, f"{path}.properties.{name}")
                for name, sub in value.items()
            }
        elif key == "items":
            out[key] = schema_of(value, f"{path}.items")
        elif key in ("minimum", "maximum") and isinstance(value, float):
            raise Unsupported(f"{path}.{key} is a float")
        else:
            out[key] = copy.deepcopy(value)
    return out


def _binding(tool: Mapping[str, Any], mode: str) -> dict[str, Any]:
    from fiduciary.demo.packs.kit import request

    name = str(tool["name"])
    schema = schema_of(tool.get("inputSchema") or {"type": "object"})
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    schema["additionalProperties"] = False
    properties = sorted(schema["properties"])
    description = str(tool.get("description") or (tool.get("annotations") or {}).get("title") or name)
    base = {"v": "FIDU1", "type": "tool", "name": name, "description": description[:2000]}
    if mode == READ:
        return {
            **base,
            "input": schema,
            "kind": "passthrough",
            "tenant": "none",
            "effects": [],
            "requests": [],
            "price": "0.00100000",
        }
    # Every argument is the agent's to state: the request's payload names each.
    schema["required"] = properties
    return {
        **base,
        "input": schema,
        "kind": "plan",
        "tenant": "none",
        "effects": [],
        "requests": [request("call", SINK, name, {p: f"$.{p}" for p in properties}, [])],
        "price": "0.00000000",
    }


def _pack(name: str, principals: tuple[str, str, str], tools: list[dict[str, Any]], modes: dict[str, str], schemas: dict[str, dict[str, Any]]) -> Any:
    from interlock.mcp import mcp_sink

    from fiduciary.demo.packs.kit import Pack

    writes = sorted(n for n, m in modes.items() if m != READ)
    sinks = (
        (
            mcp_sink(
                SINK,
                {n: schemas[n] for n in writes},
                held=[n for n in writes if modes[n] == HELD],
                not_after=timedelta(days=1),
                max_attempts=3,
                cost_per_call=Decimal("0.001"),
            ),
        )
        if writes
        else ()
    )
    scope = f"{name}-agent"

    def data() -> dict[str, Any]:
        return {
            "tables": {},
            "tenants": "*",
            "declared_tenants": False,
            "max_rows": 1,
            "max_tenants": 1,
            "drawdown": [],
            "stated_footprint": {"tolerance": "2", "blocking": False},
            "agreements": [],
            "facts": [],
        }

    def outbound() -> dict[str, Any]:
        if not writes:
            return {"sinks": {}, "max_per_plan": 0, "per_sink": {}, "caps": [], "recipients": [], "tenants": {"max_tenants": 1, "require_tenant": False, "fields": {}}}
        return {
            "sinks": {
                SINK: {
                    "operations": writes,
                    "max_attempts": 3,
                    "not_after_seconds": 86400,
                    "max_payload_bytes": 16384,
                }
            },
            "max_per_plan": 1,
            "per_sink": {SINK: 1},
            "caps": [],
            "recipients": [],
            "tenants": {"max_tenants": 1, "require_tenant": False, "fields": {}},
        }

    def windows() -> list[dict[str, Any]]:
        """Each write tool, at most 30 calls an hour, whatever each call alone may do."""
        return [
            {
                "name": f"{tool}_per_hour",
                "measure": {"kind": "requests", "sink": SINK, "operation": tool},
                "span_seconds": 3600,
                "limit": "30",
                "per": "lineage",
            }
            for tool in writes
        ]

    return Pack(
        key=name,
        industry=name,
        title=f"An agent on {name}'s MCP server",
        frameworks=("soc2", "owasp-asi-2026", "eu-ai-act"),
        scope=scope,
        funding="operations",
        label=f"{name}: tools from its MCP server",
        principals=principals,
        operator="break-glass",
        tenants=("default",),
        partition="tenant",
        schema="",
        seed=lambda conn: None,
        tables=(),
        sinks=sinks,
        tools=lambda: [copy.deepcopy(t) for t in tools],
        data=data,
        outbound=outbound,
        windows=windows,
        policies=(),
        entities=lambda: {},
        resource={},
        summed={},
        titles={},
        acts=lambda run: None,
        canaries=lambda run: {},
        nouns={"substrate": "MCP", "unit": "USD", "partition": "the tenant"},
        system="",
        # The back office holds the outbox and nothing else: the plans only
        # enqueue requests (interlock 1.4.1's tables = []).
        interlock=lambda root, database: (
            f'substrate = "sqlite"\ndatabase = {json.dumps(str(database))}\ntables = []\n'
        ),
    )


def init(
    root: Path,
    command: Sequence[str] | None = None,
    *,
    name: str | None = None,
    principals: Sequence[str] = ("approver-1", "approver-2", "approver-3"),
    discovered: Sequence[Mapping[str, Any]] | None = None,
) -> InitReport:
    """Write the deployment into ``root`` (new or empty).

    :param command: The MCP server's command line, or ``None`` for no tools.
    :param discovered: The server's tools, when already listed (tests).
    :raises ValueError: On a name, principals or a directory init cannot use.
    """
    from fiduciary.demo.packs import kit

    label = name or re.sub(r"[^a-z0-9_]", "_", root.name.lower()).strip("_") or "agent"
    if not _NAME.fullmatch(label):
        raise ValueError(f"the deployment's name {label!r} is not a lowercase identifier")
    if len(principals) != 3 or len(set(principals)) != 3:
        raise ValueError("a charter here names three principals: give three distinct names")
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"{root} is not empty: init writes a fresh deployment")
    listed = list(discovered) if discovered is not None else (discover(command) if command else [])
    report = InitReport(root=root.resolve(), scope=f"{label}-agent", principals=tuple(principals))
    bindings: list[dict[str, Any]] = []
    modes: dict[str, str] = {}
    schemas: dict[str, dict[str, Any]] = {}
    for tool in listed:
        tool_name = str(tool.get("name") or "")
        if not _NAME.fullmatch(tool_name) or tool_name.startswith("fiduciary_"):
            report.skipped.append({"name": tool_name, "why": "not a lowercase identifier the bridge can list"})
            continue
        mode, why = classify(tool)
        try:
            binding = _binding(tool, mode)
        except Unsupported as exc:
            report.skipped.append({"name": tool_name, "why": str(exc)})
            continue
        bindings.append(binding)
        modes[tool_name] = mode
        schemas[tool_name] = binding["input"]
        report.tools.append({"name": tool_name, "mode": mode, "why": why})
    pack = _pack(label, (principals[0], principals[1], principals[2]), bindings, modes, schemas)
    world = kit.build(root, pack)
    try:
        if command and any(m != READ for m in modes.values()):
            with (root / "interlock.toml").open("a", encoding="utf-8") as config:
                config.write(_relay_toml(root, command))
        (root / "README.md").write_text(_readme(report, command), encoding="utf-8")
        (root / "init.json").write_text(
            json.dumps(
                {"scope": report.scope, "tools": report.tools, "skipped": report.skipped, "upstream": list(command or [])},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    finally:
        world.close()
    return report


def _relay_toml(root: Path, command: Sequence[str]) -> str:
    """``[relay]``: the relay that makes each released call on the MCP server."""
    from fiduciary.demo.packs.kit import key_spec
    from fiduciary.keys import load_key

    root = root.resolve()
    public = key_spec(load_key(root / "keys" / "relay-a.key").public_key())
    return (
        f'\n[relays.keys]\n"relay-a" = "{public}"\n'
        "\n[relay]\n"
        f"key = {json.dumps(str(root / 'keys' / 'relay-a.key'))}\n"
        f"database = {json.dumps(str(root / 'back_office.db'))}\n"
        f"ledger = {json.dumps(str(root / 'ledger.db'))}\n"
        'breaker = "agentgov"\n'
        "timeout_seconds = 30\nlease_seconds = 60\n"
        "[[relay.endpoints]]\n"
        f'sink = "{SINK}"\n'
        f"command = {json.dumps(list(command))}\n"
    )


def _readme(report: InitReport, command: Sequence[str] | None) -> str:
    rows = "\n".join(f"| `{t['name']}` | {t['mode']} | {t['why']} |" for t in report.tools) or "| (none) | | |"
    skipped = "\n".join(f"- `{s['name']}`: {s['why']}" for s in report.skipped)
    upstream = " ".join(command or [])
    p1, p2, _ = report.principals
    return f"""# {report.scope}

Written by `fiduciary init`. Every path in the configuration files is absolute.

## The tools

| Tool | Governed as | Why |
|---|---|---|
{rows}

- **read**: a passthrough binding, called directly, metered and rate-bounded.
- **write**: a plan whose one request goes to the server through the outbox, at most once.
- **held**: the same, held until two of three principals sign its release.
{chr(10) + "Left out:" + chr(10) + chr(10) + skipped + chr(10) if skipped else ""}
## Run it

```
# the agent's MCP server: only the tools the mandate pins, governed as above
fiduciary --config fiduciary.toml mcp proxy --mandate {report.scope} -- {upstream}

# the writer: approvals recorded, warrants formed and executed, held requests ticketed
fiduciary --config fiduciary.toml serve

# the relay: each released request made on the MCP server
interlock relay --config interlock.toml --relay-id relay-a

# the approvals desk, in the browser
fiduciary console --dir . --accept-approvals --open
```

## The principals' keys

`keys/{p1}.key`, `keys/{p2}.key` and the third sign approvals, and adopted this charter.
Move each to its principal, who signs with it in `fiduciary console` (the key file is read
in the page and never sent) or with
`fiduciary approve <ticket> --principal {p1} --key {p1}.key --submit`, and delete it here.
"""

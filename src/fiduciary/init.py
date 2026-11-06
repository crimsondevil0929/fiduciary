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

from fiduciary.starters import Starter, Target, starter

__all__ = ["InitReport", "Target", "classify", "discover", "init", "schema_of"]

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


class UnsupportedSchemaError(ValueError):
    """A tool the binding grammar cannot hold whole."""


@dataclass
class InitReport:
    """What ``init`` made of each tool, and where it wrote the deployment."""

    root: Path
    scope: str
    tools: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    principals: tuple[str, ...] = ()
    substrate: str = "mcp"
    notes: list[dict[str, str]] = field(default_factory=list)
    """What init changed outside the deployment's directory (a starter's), and why."""


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
    return HELD, "a write with no destructiveHint, which MCP treats as destructive"


def schema_of(schema: object, path: str = "inputSchema") -> dict[str, Any]:
    """The tool's input schema in the binding grammar, or :class:`UnsupportedSchemaError`."""
    if not isinstance(schema, Mapping):
        raise UnsupportedSchemaError(f"{path} is not an object")
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in _HARMLESS:
            continue
        if key not in _KEEP:
            raise UnsupportedSchemaError(f"{path} uses {key!r}, which a binding would not enforce")
        if key == "type":
            kinds = [value] if isinstance(value, str) else value
            if not isinstance(kinds, list) or not all(isinstance(k, str) for k in kinds):
                raise UnsupportedSchemaError(f"{path}.type is no type")
            if "number" in kinds:
                raise UnsupportedSchemaError(
                    f"{path} is a number: FIDU1 holds no floats (a decimal is a string)"
                )
            if not set(kinds) <= _TYPES:
                raise UnsupportedSchemaError(
                    f"{path}.type {value!r} is outside the binding grammar"
                )
            out[key] = value
        elif key == "properties":
            if not isinstance(value, Mapping):
                raise UnsupportedSchemaError(f"{path}.properties is not an object")
            out[key] = {
                str(name): schema_of(sub, f"{path}.properties.{name}")
                for name, sub in value.items()
            }
        elif key == "items":
            out[key] = schema_of(value, f"{path}.items")
        elif key in ("minimum", "maximum") and isinstance(value, float):
            raise UnsupportedSchemaError(f"{path}.{key} is a float")
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
    description = str(
        tool.get("description") or (tool.get("annotations") or {}).get("title") or name
    )
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


def _pack(
    name: str,
    principals: tuple[str, str, str],
    tools: list[dict[str, Any]],
    modes: dict[str, str],
    schemas: dict[str, dict[str, Any]],
) -> Any:
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
            return {
                "sinks": {},
                "max_per_plan": 0,
                "per_sink": {},
                "caps": [],
                "recipients": [],
                "tenants": {"max_tenants": 1, "require_tenant": False, "fields": {}},
            }
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


def _starter_pack(name: str, principals: tuple[str, str, str], st: Starter) -> Any:
    """A :class:`~fiduciary.demo.packs.kit.Pack` for a starter (:mod:`fiduciary.starters`)."""
    from fiduciary.demo.packs.kit import Pack

    tools = st.tools
    return Pack(
        key=name,
        industry=name,
        title=f"An agent on {name}'s {st.nouns['substrate']}",
        frameworks=("soc2", "owasp-asi-2026", "eu-ai-act"),
        scope=f"{name}-agent",
        funding="operations",
        label=f"{name}: starter tools on {st.nouns['substrate']}",
        principals=principals,
        operator="break-glass",
        tenants=("default",),
        partition="tenant",
        schema="",
        seed=lambda conn: None,
        tables=tuple(st.tables),
        sinks=(),
        tools=lambda: [copy.deepcopy(t) for t in tools],
        data=lambda: copy.deepcopy(st.data),
        outbound=lambda: {
            "sinks": {},
            "max_per_plan": 0,
            "per_sink": {},
            "caps": [],
            "recipients": [],
            "tenants": {"max_tenants": 1, "require_tenant": False, "fields": {}},
        },
        windows=lambda: copy.deepcopy(st.windows),
        policies=(),
        entities=lambda: {},
        resource={},
        summed={},
        titles={},
        acts=lambda run: None,
        canaries=lambda run: {},
        nouns=st.nouns,
        system="",
        interlock=st.interlock,
        back_office=st.back_office,
        outbox=st.outbox,
    )


def init(
    root: Path,
    command: Sequence[str] | None = None,
    *,
    name: str | None = None,
    principals: Sequence[str] = ("approver-1", "approver-2", "approver-3"),
    discovered: Sequence[Mapping[str, Any]] | None = None,
    target: Target | None = None,
) -> InitReport:
    """Write the deployment into ``root`` (new or empty).

    :param command: The MCP server's command line, or ``None`` for no tools.
    :param discovered: The server's tools, when already listed (tests).
    :param target: A system the agent changes directly (1.9.0): a database, a
        repository, a cluster, a Terraform configuration or a FHIR server. Its
        starter tools come from :func:`fiduciary.starters.starter`.
    :raises ValueError: On a name, principals or a directory init cannot use,
        or a target it cannot read.
    """
    from fiduciary.demo.packs import kit

    label = name or re.sub(r"[^a-z0-9_]", "_", root.name.lower()).strip("_") or "agent"
    if not _NAME.fullmatch(label):
        raise ValueError(f"the deployment's name {label!r} is not a lowercase identifier")
    if len(principals) != 3 or len(set(principals)) != 3:
        raise ValueError("a charter here names three principals: give three distinct names")
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"{root} is not empty: init writes a fresh deployment")
    trio = (principals[0], principals[1], principals[2])
    if target is not None:
        if command:
            raise ValueError("init takes --mcp or --substrate, not both")
        st = starter(target)
        report = InitReport(
            root=root.resolve(),
            scope=f"{label}-agent",
            principals=tuple(principals),
            substrate=target.substrate,
            tools=list(st.report),
            skipped=list(st.skipped),
            notes=list(st.notes),
        )
        world = kit.build(root, _starter_pack(label, trio, st))
        try:
            if target.substrate == "postgres":
                # init staged nothing: the outbox it made to stand the world up is not
                # the deployment's, which is in PostgreSQL (interlock install)
                world.close()
                for leftover in root.glob("back_office.db*"):
                    leftover.unlink()
            for name, text in st.files.items():
                (root / name).write_text(text, encoding="utf-8")
            _write_report(root, report, None, target)
        finally:
            world.close()
        return report
    listed = list(discovered) if discovered is not None else (discover(command) if command else [])
    report = InitReport(root=root.resolve(), scope=f"{label}-agent", principals=tuple(principals))
    bindings: list[dict[str, Any]] = []
    modes: dict[str, str] = {}
    schemas: dict[str, dict[str, Any]] = {}
    for tool in listed:
        tool_name = str(tool.get("name") or "")
        if not _NAME.fullmatch(tool_name) or tool_name.startswith("fiduciary_"):
            report.skipped.append(
                {"name": tool_name, "why": "not a lowercase identifier the bridge can list"}
            )
            continue
        mode, why = classify(tool)
        try:
            binding = _binding(tool, mode)
        except UnsupportedSchemaError as exc:
            report.skipped.append({"name": tool_name, "why": str(exc)})
            continue
        bindings.append(binding)
        modes[tool_name] = mode
        schemas[tool_name] = binding["input"]
        report.tools.append({"name": tool_name, "mode": mode, "why": why})
    pack = _pack(label, trio, bindings, modes, schemas)
    world = kit.build(root, pack)
    try:
        if command and any(m != READ for m in modes.values()):
            with (root / "interlock.toml").open("a", encoding="utf-8") as config:
                config.write(_relay_toml(root, command))
        _write_report(root, report, command, None)
    finally:
        world.close()
    return report


def _write_report(
    root: Path, report: InitReport, command: Sequence[str] | None, target: Target | None
) -> None:
    readme = _readme(report, command) if target is None else _substrate_readme(report, target)
    (root / "README.md").write_text(readme, encoding="utf-8")
    (root / "init.json").write_text(
        json.dumps(
            {
                "scope": report.scope,
                "substrate": report.substrate,
                "tools": report.tools,
                "skipped": report.skipped,
                "upstream": list(command or []),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _relay_toml(root: Path, command: Sequence[str]) -> str:
    """``[relay]``: the relay that makes each released call on the MCP server."""
    from fiduciary.bundle.export import spec as key_spec
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
    rows = (
        "\n".join(f"| `{t['name']}` | {t['mode']} | {t['why']} |" for t in report.tools)
        or "| (none) | | |"
    )
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

# the writer: records approvals, forms warrants and executes them
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


_SETUP: Final = {
    "sqlite": "Init added Interlock's outbox and window tables to the database, as "
    "`interlock install` does. Nothing else in it changed.",
    "postgres": "The stage connects as the role in `interlock.toml`'s connection string, which "
    "must never be a superuser. As the tables' owner, grant it what `grants.sql` lists, then "
    "install Interlock's schema, signed with the service key:\n\n```\n"
    "psql <owner's connection string> -f grants.sql\n"
    "interlock install --config interlock.toml --database <owner's connection string> "
    "--key keys/service.key\n```",
    "git": "The stage works in its own worktrees of the repository and moves the branch only "
    "with a compare-and-swap. Keep the branch out of any checkout the agent runs in.",
    "kubernetes": "The stage acts with the token in the file `interlock.toml` names. Give that "
    "service account `edit` in the namespaces listed, and nothing cluster-wide.",
    "terraform": "Run `init` in the configuration's directory first. The stage plans with "
    "`-out`, measures the saved plan and applies exactly that plan.",
    "fhir": "The server must allow client-assigned ids and conditional updates (`If-Match`). "
    "Each write is read back and put back if it differs from the prediction.",
}


def _left_out(skipped: str) -> str:
    return f"\nLeft out:\n\n{skipped}\n" if skipped else ""


def _substrate_readme(report: InitReport, target: Target) -> str:
    rows = (
        "\n".join(f"| `{t['name']}` | {t['mode']} | {t['why']} |" for t in report.tools)
        or "| (none) | | |"
    )
    skipped = "\n".join(f"- `{s['name']}`: {s['why']}" for s in report.skipped)
    p1, _, _ = report.principals
    return f"""# {report.scope}

Written by `fiduciary init --substrate {target.substrate}`. Every path in the configuration
files is absolute.

## Tools

| Tool | Governed as | Why |
|---|---|---|
{rows}

- **write**: the change is staged, measured, checked against the mandate and committed, one
  row or object per plan, at most 120 plans an hour.
- A call whose measured change goes further than the mandate allows (a trigger's extra
  rows, a guarded path, a replacement) is refused before anything changes, and the agent is
  told why. Allowing more takes a new mandate version, issued by two of the three principals.
{_left_out(skipped)}
These are starter tools. Edit them under `tools/`, sign them again with `keys/developer.key`,
and issue a new mandate version that pins them.

## Before the first call

{_SETUP[target.substrate]}

## Run it

```
# the agent's tools, over MCP
fiduciary --config fiduciary.toml mcp serve --mandate {report.scope}

# the writer: approvals recorded, warrants formed and executed
fiduciary --config fiduciary.toml serve

# the approvals desk, in the browser
fiduciary console --dir . --accept-approvals --open
```

## The principals' keys

`keys/{p1}.key` and the other two principals' keys sign approvals, and adopted the charter.
Move each to its principal, who signs with it in `fiduciary console` (the key file is read in
the page and never sent) or with `fiduciary approve`, then delete it here.
"""

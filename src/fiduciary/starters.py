"""Starter tool bindings for ``fiduciary init --substrate`` (1.9.0).

``fiduciary init`` points a new deployment at a system an agent will change and
writes conservative bindings for it. They are a place to start. Edit the tools
under ``tools/``, have the developer key sign them again, and reissue the mandate.

==============  ==================================================================
Substrate       Starter tools
==============  ==================================================================
``sqlite``      for each table init can read: ``insert_<table>``, and
``postgres``    ``update_<table>`` by its primary key
``git``         ``write_file`` and ``replace_in_file``
``kubernetes``  ``scale_deployment`` and ``set_image``
``terraform``   ``set_<variable>`` for each input variable
``fhir``        ``set_<type>_status`` for each resource type named
==============  ==================================================================

There are no delete tools. The starter mandate allows inserts and updates, one
row or object per plan (ten resources on Terraform), and nothing else. A call
whose measured change goes further is refused before anything changes, and the
agent is told why: a
trigger that writes a second row, a Git write that lands in the repository's CI,
infrastructure, dependency or governance files, a scale-up past triple the
replicas, a Terraform plan that replaces or destroys a resource. Allowing more
takes a new mandate version, which two of the three principals issue. Columns
and variables whose values FIDU1 can't hold exactly (floats, binary data, JSON)
are left out and named.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

__all__ = [
    "SUBSTRATES",
    "Column",
    "SqlTable",
    "Starter",
    "Target",
    "postgres_tables",
    "sqlite_tables",
    "starter",
    "terraform_config",
]

SUBSTRATES: Final = ("sqlite", "postgres", "git", "kubernetes", "terraform", "fhir")
WRITE: Final = "write"
_IDENT: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}")
_DECIMAL: Final = {"type": "string", "pattern": r"^-?[0-9]{1,30}(\.[0-9]{1,18})?$"}
_TEXT: Final = {"type": "string", "maxLength": 10000}
_WINDOW: Final = {
    "name": "writes_per_hour",
    "measure": {"kind": "plans"},
    "span_seconds": 3600,
    "limit": "120",
    "per": "lineage",
}
"""Every starter deployment with a control database: at most 120 plans an hour."""


@dataclass(frozen=True)
class Target:
    """The system ``fiduciary init`` points the deployment at."""

    substrate: str
    database: str | None = None
    """``sqlite``: the database file. ``postgres``: its connection string."""
    schema: str = "public"
    tables: tuple[str, ...] = ()
    """SQL: only these tables (every table init can read, when empty)."""
    repository: Path | None = None
    branch: str = "main"
    server: str | None = None
    token_file: Path | None = None
    ca_file: Path | None = None
    namespaces: tuple[str, ...] = ()
    directory: Path | None = None
    executable: tuple[str, ...] = ("terraform",)
    types: tuple[str, ...] = ()
    """FHIR: the resource types the agent may change."""

    def check(self) -> None:
        """:raises ValueError: When something the substrate needs is missing."""
        need: dict[str, tuple[str, ...]] = {
            "sqlite": ("database",),
            "postgres": ("database",),
            "git": ("repository",),
            "kubernetes": ("server", "token_file", "namespaces"),
            "terraform": ("directory",),
            "fhir": ("server", "types"),
        }
        if self.substrate not in need:
            raise ValueError(
                f"no substrate {self.substrate!r}; it is one of {', '.join(SUBSTRATES)}"
            )
        flags = {"token_file": "token-file", "namespaces": "namespace", "types": "type"}
        missing = [n for n in need[self.substrate] if not getattr(self, n)]
        if missing:
            named = ", ".join(f"--{flags.get(n, n)}" for n in missing)
            raise ValueError(f"--substrate {self.substrate} needs {named}")


@dataclass(frozen=True)
class Column:
    name: str
    schema: Mapping[str, Any] | None
    """The column's input schema, or ``None`` when FIDU1 can't hold its values."""
    why: str = ""
    nullable: bool = True
    generated: bool = False
    """The database assigns it (a rowid, a sequence, an identity)."""


@dataclass(frozen=True)
class SqlTable:
    name: str
    key: str | None
    columns: tuple[Column, ...]


@dataclass
class Starter:
    """What init writes for one target: the pieces of a demo-kit ``Pack``."""

    tables: list[Any]
    tools: list[dict[str, Any]]
    data: dict[str, Any]
    windows: list[dict[str, Any]]
    interlock: Callable[[Path, Path], str]
    nouns: dict[str, str]
    report: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    back_office: Path | None = None
    outbox: str | None = None
    notes: list[dict[str, str]] = field(default_factory=list)
    """What init changed outside the deployment's directory, and why."""
    files: dict[str, str] = field(default_factory=dict)
    """Files init writes beside the configuration, by name (``grants.sql``)."""


# --------------------------------------------------------------------------
# reading a database
# --------------------------------------------------------------------------


def _own(table: str) -> bool:
    """A table the database or Interlock keeps for itself, not the application's."""
    return table.startswith(("sqlite_", "_interlock", "interlock_"))


def _sqlite_kind(declared: str) -> tuple[Mapping[str, Any] | None, str]:
    """SQLite's type affinity rules, as FIDU1 can hold each."""
    t = declared.upper()
    if "INT" in t:
        return {"type": "integer"}, ""
    if any(k in t for k in ("CHAR", "CLOB", "TEXT")):
        return dict(_TEXT), ""
    if not t or "BLOB" in t:
        return None, "binary or untyped data"
    if any(k in t for k in ("REAL", "FLOA", "DOUB")):
        return None, "a floating-point column: FIDU1 holds no floats"
    if "BOOL" in t:
        return {"type": "boolean"}, ""
    if any(k in t for k in ("DATE", "TIME")):
        return {"type": "string", "maxLength": 64}, ""
    return dict(_DECIMAL), ""


def sqlite_tables(path: str | Path) -> list[SqlTable]:
    """Every table in the SQLite file at ``path`` but its own and Interlock's."""
    found: list[SqlTable] = []
    uri = f"file:{Path(path).resolve()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        names = [
            str(r[0])
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
            if not _own(str(r[0]))
        ]
        for name in names:
            rows = conn.execute(f'PRAGMA table_info("{name}")').fetchall()
            keys = [r for r in rows if r[5]]
            key = str(keys[0][1]) if len(keys) == 1 else None
            columns = []
            for _cid, column, declared, notnull, default, pk in rows:
                kind, why = _sqlite_kind(str(declared or ""))
                # INTEGER PRIMARY KEY is the rowid: SQLite assigns it
                rowid = bool(pk) and len(keys) == 1 and str(declared).upper() == "INTEGER"
                columns.append(
                    Column(
                        str(column),
                        kind,
                        why,
                        nullable=not notnull and not pk,
                        generated=rowid or (default is not None and bool(pk)),
                    )
                )
            found.append(SqlTable(name, key, tuple(columns)))
    return found


_PG_KINDS: Final[Mapping[str, Mapping[str, Any] | None]] = {
    "smallint": {"type": "integer"},
    "integer": {"type": "integer"},
    "bigint": {"type": "integer"},
    "numeric": _DECIMAL,
    "boolean": {"type": "boolean"},
    "text": _TEXT,
    "character varying": _TEXT,
    "character": _TEXT,
    "uuid": {
        "type": "string",
        "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    },
    "date": {"type": "string", "maxLength": 64},
    "timestamp without time zone": {"type": "string", "maxLength": 64},
    "timestamp with time zone": {"type": "string", "maxLength": 64},
}


def postgres_tables(dsn: str, schema: str = "public") -> list[SqlTable]:
    """Every table in ``schema`` of the PostgreSQL database ``dsn``.

    Read from ``pg_catalog``, which shows a role every table whatever its
    privileges (``information_schema`` hides the ones it has none on), so the
    stage's own role can be named before ``interlock install`` grants it any.
    """
    import psycopg

    try:
        return _catalog(psycopg.connect(dsn), schema)
    except psycopg.Error as exc:
        raise ValueError(f"cannot read the tables of schema {schema}: {exc}") from None


def _catalog(conn: Any, schema: str) -> list[SqlTable]:
    with conn:
        relations = [
            (int(r[0]), str(r[1]))
            for r in conn.execute(
                "SELECT c.oid, c.relname FROM pg_catalog.pg_class c "
                "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = %s AND c.relkind IN ('r', 'p') ORDER BY c.relname",
                (schema,),
            )
            if not _own(str(r[1]))
        ]
        found = []
        for oid, name in relations:
            keys = [
                str(r[0])
                for r in conn.execute(
                    "SELECT a.attname FROM pg_catalog.pg_index i "
                    "JOIN pg_catalog.pg_attribute a "
                    "ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
                    "WHERE i.indrelid = %s AND i.indisprimary",
                    (oid,),
                )
            ]
            columns = []
            for column, kind, notnull, default, identity in conn.execute(
                "SELECT a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod), "
                "a.attnotnull, pg_catalog.pg_get_expr(d.adbin, d.adrelid), a.attidentity "
                "FROM pg_catalog.pg_attribute a LEFT JOIN pg_catalog.pg_attrdef d "
                "ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
                "WHERE a.attrelid = %s AND a.attnum > 0 AND NOT a.attisdropped "
                "ORDER BY a.attnum",
                (oid,),
            ):
                base = str(kind).split("(", 1)[0].strip()
                held = _PG_KINDS.get(base)
                columns.append(
                    Column(
                        str(column),
                        dict(held) if held is not None else None,
                        "" if held is not None else f"a {base} column FIDU1 can't hold exactly",
                        nullable=not notnull,
                        generated=bool(identity) or str(default or "").startswith("nextval("),
                    )
                )
            found.append(SqlTable(name, keys[0] if len(keys) == 1 else None, tuple(columns)))
    return found


# --------------------------------------------------------------------------
# the starters
# --------------------------------------------------------------------------


def _tool(
    name: str,
    description: str,
    properties: Mapping[str, Any],
    effects: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "v": "FIDU1",
        "type": "tool",
        "name": name,
        "description": description,
        "input": {
            "type": "object",
            "additionalProperties": False,
            "required": sorted(properties),
            "properties": dict(properties),
        },
        "kind": "plan",
        "tenant": "none",
        "effects": effects,
        "requests": [],
        "price": "0.00000000",
    }


def _effect(
    effect_id: str,
    kind: str,
    table: str,
    statement: str,
    parameters: Mapping[str, str],
    *,
    compensation: str | None = None,
) -> dict[str, Any]:
    return {
        "id": effect_id,
        "kind": kind,
        "table": table,
        "statement": statement,
        "parameters": dict(parameters),
        "stated_rows": 1,
        "after": [],
        **({"compensation": compensation} if compensation is not None else {}),
    }


def _data(tables: Mapping[str, list[str]], *, max_rows: int = 1) -> dict[str, Any]:
    return {
        "tables": {t: sorted(ops) for t, ops in sorted(tables.items())},
        "tenants": "*",
        "declared_tenants": False,
        "max_rows": max_rows,
        "max_tenants": 1,
        "drawdown": [],
        "stated_footprint": {"tolerance": "2", "blocking": False},
        "agreements": [],
        "facts": [],
    }


def _toml(value: object) -> str:
    return json.dumps(str(value) if isinstance(value, Path) else value)


def _nullable(schema: Mapping[str, Any]) -> dict[str, Any]:
    kind = schema["type"]
    return {**schema, "type": [kind, "null"]}


def _sql(target: Target, tables: Sequence[SqlTable]) -> Starter:
    from fiduciary.demo.packs.kit import Table

    def p(name: str) -> str:
        """A statement parameter: ``:name`` on SQLite, psycopg's ``%(name)s`` on PostgreSQL."""
        return f"%({name})s" if target.substrate == "postgres" else f":{name}"

    def q(table: str) -> str:
        """A table as a statement names it: schema-qualified on PostgreSQL."""
        return f'"{target.schema}"."{table}"' if target.substrate == "postgres" else f'"{table}"'

    chosen = [t for t in tables if not target.tables or t.name in target.tables]
    unknown = sorted(set(target.tables) - {t.name for t in tables})
    if unknown:
        raise ValueError(f"no table {', '.join(unknown)} in {target.database}")
    tools: list[dict[str, Any]] = []
    report: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    allowed: dict[str, list[str]] = {}
    specs = []
    for table in chosen:
        if not _IDENT.fullmatch(table.name):
            skipped.append({"name": table.name, "why": "not a plain identifier"})
            continue
        held = [c for c in table.columns if c.schema is not None and _IDENT.fullmatch(c.name)]
        skipped += [
            {"name": f"{table.name}.{c.name}", "why": c.why or "not a plain identifier"}
            for c in table.columns
            if c not in held
        ]
        if table.key is None and target.substrate == "postgres":
            skipped.append(
                {
                    "name": table.name,
                    "why": "no single-column primary key: Interlock can't observe its rows",
                }
            )
            continue
        specs.append(Table(table.name, tuple(c.name for c in table.columns), None))
        allowed[table.name] = ["insert", "update"]
        slug = table.name.lower()
        writable = [c for c in held if not c.generated]
        if writable:
            names = [c.name for c in writable]
            props = {
                c.name: _nullable(dict(c.schema or {})) if c.nullable else dict(c.schema or {})
                for c in writable
            }
            cols = ", ".join(f'"{n}"' for n in names)
            values = ", ".join(p(n) for n in names)
            tools.append(
                _tool(
                    f"insert_{slug}",
                    f"Insert one row into {table.name}.",
                    props,
                    [
                        _effect(
                            "insert",
                            "insert",
                            table.name,
                            f"INSERT INTO {q(table.name)} ({cols}) VALUES ({values})",  # noqa: S608 - names checked
                            {n: f"$.{n}" for n in names},
                        )
                    ],
                )
            )
            report.append(
                {"name": f"insert_{slug}", "mode": WRITE, "why": "an insert, one row a plan"}
            )
        key = next((c for c in held if c.name == table.key), None)
        if key is None:
            skipped.append(
                {
                    "name": f"update_{slug}",
                    "why": f"{table.name} has no single-column primary key init can read",
                }
            )
            continue
        changes = [c for c in held if c.name != key.name and not c.generated]
        if changes:
            props = {key.name: dict(key.schema or {})}
            props |= {c.name: _nullable(dict(c.schema or {})) for c in changes}
            sets = ", ".join(f'"{c.name}" = COALESCE({p(c.name)}, "{c.name}")' for c in changes)
            tools.append(
                _tool(
                    f"update_{slug}",
                    f"Change one row of {table.name}, found by its {key.name}. Pass null for a "
                    "column to leave it as it is.",
                    props,
                    [
                        _effect(
                            "update",
                            "update",
                            table.name,
                            f'UPDATE {q(table.name)} SET {sets} WHERE "{key.name}" = {p(key.name)}',  # noqa: S608
                            {n: f"$.{n}" for n in props},
                        )
                    ],
                )
            )
            report.append(
                {"name": f"update_{slug}", "mode": WRITE, "why": "an update, one row a plan"}
            )
        skipped.append(
            {
                "name": f"delete_{slug}",
                "why": "the starter mandate allows no delete; add the tool and allow delete "
                "in a new mandate version to have one",
            }
        )
    if not specs:
        raise ValueError(f"{target.database} has no table init can govern")
    keys = {t.name: t.key for t in chosen}

    def head(root: Path, control: Path) -> str:
        if target.substrate == "postgres":
            from urllib.parse import urlsplit

            role = urlsplit(str(target.database)).username or ""
            first = (
                f'substrate = "postgres"\ndatabase = {_toml(target.database)}\n'
                f"schema = {_toml(target.schema)}\n"
                # the stage connects as this role: install grants it DML on the
                # observed tables only (it may never be a superuser)
                + (f"stage_roles = [{_toml(role)}]\n" if role else "")
            )
        else:
            first = f'substrate = "sqlite"\ndatabase = {_toml(str(control))}\n'
        # SQLite observes a table with no single-column key by its rowid
        fallback = "rowid" if target.substrate == "sqlite" else None
        return first + "".join(
            f"[[tables]]\nname = {_toml(t.name)}\n"
            f"primary_key = {_toml(keys.get(t.name) or fallback)}\n"
            f"columns = {json.dumps(list(t.columns))}\n"
            for t in specs
        )

    database = (
        Path(target.database).resolve()
        if target.substrate == "sqlite" and target.database
        else None
    )
    files: dict[str, str] = {}
    if target.substrate == "postgres":
        from urllib.parse import urlsplit

        role = urlsplit(str(target.database)).username or "<the stage's role>"
        observed = ", ".join(f'"{target.schema}"."{t.name}"' for t in specs)
        files["grants.sql"] = (
            "-- Run as the tables' owner, before `interlock install`. The stage connects as\n"
            f"-- {role}; on PostgreSQL these grants are the boundary of what it can write.\n"
            f'GRANT USAGE ON SCHEMA "{target.schema}" TO "{role}";\n'
            f'GRANT SELECT, INSERT, UPDATE ON {observed} TO "{role}";\n'
            f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA "{target.schema}" TO "{role}";\n'
        )
    return Starter(
        tables=specs,
        tools=tools,
        data=_data(allowed),
        windows=[dict(_WINDOW)],
        interlock=head,
        nouns={"substrate": target.substrate, "rows": "rows", "tables": "tables"},
        report=report,
        skipped=skipped,
        back_office=database,
        outbox=target.database if target.substrate == "postgres" else None,
        files=files,
    )


_PATH: Final = {
    "type": "string",
    "minLength": 1,
    "maxLength": 400,
    "pattern": r"^(?!/)(?!.*(^|/)\.\.(/|$))[A-Za-z0-9._/@+-]+$",
}
_ALLOWED_CLASSES: Final = ("docs", "other", "src", "tests")


def _git(target: Target) -> Starter:
    from interlock.git_substrate import COLUMNS, DEFAULT_CLASSES, FALLBACK_CLASS

    from fiduciary.demo.packs.kit import Table

    assert target.repository is not None
    repository = target.repository.resolve()
    if not repository.exists():
        raise ValueError(f"no repository at {repository}")
    classes = [c.name for c in DEFAULT_CLASSES] + [FALLBACK_CLASS]
    specs = [Table(name, tuple(COLUMNS), None) for name in classes]
    text = {"type": "string", "maxLength": 200000}
    tools = [
        _tool(
            "write_file",
            "Create or replace one file of the repository with the given text.",
            {"path": dict(_PATH), "text": text},
            [
                _effect(
                    "write",
                    "update",
                    "src",
                    "write :path :text",
                    {"path": "$.path", "text": "$.text"},
                )
            ],
        ),
        _tool(
            "replace_in_file",
            "Replace one passage of text, which must occur exactly once, in one file.",
            {"path": dict(_PATH), "old": text, "new": text},
            [
                _effect(
                    "replace",
                    "update",
                    "src",
                    "replace :path :old :new",
                    {"path": "$.path", "old": "$.old", "new": "$.new"},
                )
            ],
        ),
    ]
    guarded = ", ".join(c for c in classes if c not in _ALLOWED_CLASSES)
    report = [
        {
            "name": "write_file",
            "mode": WRITE,
            "why": f"source, tests, docs and other files; writes to {guarded} are refused",
        },
        {"name": "replace_in_file", "mode": WRITE, "why": "the same paths as write_file"},
    ]
    skipped = [
        {
            "name": "delete_file",
            "why": "the starter mandate allows no delete; add the tool and allow delete for a "
            "path class in a new mandate version to have one",
        }
    ]

    def head(root: Path, control: Path) -> str:
        return (
            f'substrate = "git"\ndatabase = {_toml(str(control))}\n'
            f"[git]\nrepository = {_toml(str(repository))}\nbranch = {_toml(target.branch)}\n"
            f'workspace = "{repository.stem}"\n'
        )

    return Starter(
        tables=specs,
        tools=tools,
        data=_data({c: ["insert", "update"] for c in _ALLOWED_CLASSES}, max_rows=1),
        windows=[dict(_WINDOW)],
        interlock=head,
        nouns={"substrate": "git", "rows": "files", "tables": "path classes"},
        report=report,
        skipped=skipped,
    )


_DNS: Final = {"type": "string", "pattern": "^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$"}
_IMAGE: Final = {"type": "string", "maxLength": 512, "pattern": r"^[A-Za-z0-9./:@_-]+$"}


def _kubernetes(target: Target) -> Starter:
    from interlock.kubernetes import KubeKind

    from fiduciary.demo.packs.kit import Table

    kind = KubeKind(
        "deployments",
        "deployments",
        "Deployment",
        "apps",
        columns={"replicas": "spec.replicas", "image": "spec.template.spec.containers.0.image"},
    )
    specs = [Table(kind.table, tuple(kind.spec().columns), None)]
    namespace = {"type": "string", "enum": list(target.namespaces)}
    where = {"namespace": namespace, "deployment": dict(_DNS)}
    params = {"namespace": "$.namespace", "deployment": "$.deployment"}
    tools = [
        _tool(
            "scale_deployment",
            "Set the number of replicas of one deployment.",
            {**where, "replicas": {"type": "integer", "minimum": 0, "maximum": 50}},
            [
                _effect(
                    "scale",
                    "apply",
                    "deployments",
                    "scale deployments :namespace :deployment :replicas",
                    {**params, "replicas": "$.replicas"},
                )
            ],
        ),
        _tool(
            "set_image",
            "Set the image of the first container of one deployment.",
            {**where, "image": dict(_IMAGE)},
            [
                _effect(
                    "image",
                    "apply",
                    "deployments",
                    "set deployments :namespace :deployment "
                    "'spec.template.spec.containers.0.image' :image",
                    {**params, "image": "$.image"},
                )
            ],
        ),
    ]
    report = [
        {
            "name": "scale_deployment",
            "mode": WRITE,
            "why": "measured by the API server's dry run; at most triples the replicas",
        },
        {"name": "set_image", "mode": WRITE, "why": "measured by the API server's dry run"},
    ]
    skipped = [
        {
            "name": "delete_deployment",
            "why": "the starter mandate allows no delete; add the tool and allow delete in a "
            "new mandate version to have one",
        }
    ]
    data = _data({"deployments": ["apply"]})
    data["drawdown"] = [
        {
            "table": "deployments",
            "column": "replicas",
            "per": "tenant",
            "max_drop": None,
            "max_rise": "2",
        }
    ]

    def head(root: Path, control: Path) -> str:
        token = target.token_file.resolve() if target.token_file else None
        return (
            f'substrate = "kubernetes"\ndatabase = {_toml(str(control))}\n'
            f"[kubernetes]\nserver = {_toml(target.server)}\n"
            f"token_file = {_toml(str(token))}\n"
            + (f"ca_file = {_toml(str(target.ca_file.resolve()))}\n" if target.ca_file else "")
            + f"namespaces = {json.dumps(list(target.namespaces))}\n"
            f"journal = {_toml(str(root / 'journal'))}\n"
            f'[[kubernetes.kinds]]\ntable = "deployments"\nplural = "deployments"\n'
            'kind = "Deployment"\ngroup = "apps"\n'
            'columns = { replicas = "spec.replicas", '
            'image = "spec.template.spec.containers.0.image" }\n'
        )

    return Starter(
        tables=specs,
        tools=tools,
        data=data,
        windows=[dict(_WINDOW)],
        interlock=head,
        nouns={"substrate": "kubernetes", "rows": "objects", "tables": "kinds"},
        report=report,
        skipped=skipped,
    )


_FHIR_ID: Final = {"type": "string", "pattern": r"^[A-Za-z0-9\-.]{1,64}$"}
_FHIR_TYPE: Final = re.compile(r"[A-Z][A-Za-z]{1,63}")


def _fhir(target: Target) -> Starter:
    from interlock.fhir import FhirType

    from fiduciary.demo.packs.kit import Table

    bad = [t for t in target.types if not _FHIR_TYPE.fullmatch(t)]
    if bad:
        raise ValueError(f"not a FHIR resource type: {', '.join(bad)}")
    types = [FhirType(t.lower() + "s", t, columns={"status": "status"}) for t in target.types]
    specs = [Table(t.table, tuple(t.spec().columns), "patient") for t in types]
    tools = []
    report = []
    for t in types:
        name = f"set_{t.table[:-1]}_status"
        tools.append(
            _tool(
                name,
                f"Set the status of one {t.resource_type}.",
                {"id": dict(_FHIR_ID), "status": {"type": "string", "pattern": "^[a-z-]{1,64}$"}},
                [
                    _effect(
                        "status",
                        "update",
                        t.table,
                        f'patch {t.table} :id \'{{"status": ":status"}}\' :status',
                        {"id": "$.id", "status": "$.status"},
                        compensation=f"restore {t.table}",
                    )
                ],
            )
        )
        report.append(
            {
                "name": name,
                "mode": WRITE,
                "why": "predicted, written on the version read, read back, put back on a mismatch",
            }
        )

    def head(root: Path, control: Path) -> str:
        return (
            f'substrate = "fhir"\ndatabase = {_toml(str(control))}\n'
            f"[fhir]\nserver = {_toml(target.server)}\n"
            + (
                f"token_file = {_toml(str(target.token_file.resolve()))}\n"
                if target.token_file
                else ""
            )
            + (f"ca_file = {_toml(str(target.ca_file.resolve()))}\n" if target.ca_file else "")
            + f"journal = {_toml(str(root / 'journal'))}\n"
            + "".join(
                f"[[fhir.types]]\ntable = {_toml(t.table)}\ntype = {_toml(t.resource_type)}\n"
                'columns = { status = "status" }\n'
                for t in types
            )
        )

    return Starter(
        tables=specs,
        tools=tools,
        data=_data({t.table: ["update"] for t in types}),
        windows=[dict(_WINDOW)],
        interlock=head,
        nouns={"substrate": "fhir", "rows": "resources", "tables": "resource types"},
        report=report,
    )


_VARIABLE: Final = re.compile(r'variable\s+"([A-Za-z_][A-Za-z0-9_]*)"\s*\{(.*?)\n\}', re.S)
_RESOURCE: Final = re.compile(r'resource\s+"([a-z0-9_]+)"\s+"[A-Za-z0-9_-]+"')
_TYPE: Final = re.compile(r"^\s*type\s*=\s*([a-z]+)", re.M)


MARKER: Final = "interlock_stage_id"
MARKER_FILE: Final = "interlock_stage.tf"
_MARKER_TF: Final = """# Written by fiduciary init. Interlock passes each stage's id in this
# variable and reads it back from the output: present in the state, the apply
# finished.
variable "interlock_stage_id" {
  type    = string
  default = ""
}

output "interlock_stage_id" {
  value = var.interlock_stage_id
}
"""


def terraform_config(directory: Path) -> tuple[dict[str, str], list[str]]:
    """The input variables of the configuration in ``directory`` (each with its
    type, ``any`` when it declares none) and its resource types, read from its
    ``*.tf`` files."""
    variables: dict[str, str] = {}
    resources: set[str] = set()
    for path in sorted(directory.glob("*.tf")):
        text = path.read_text(encoding="utf-8")
        for name, body in _VARIABLE.findall(text):
            kind = _TYPE.search(body)
            variables[name] = kind.group(1) if kind else "any"
        resources.update(_RESOURCE.findall(text))
    return variables, sorted(resources)


def _terraform(target: Target) -> Starter:
    from interlock.terraform import TfResource

    from fiduciary.demo.packs.kit import Table

    assert target.directory is not None
    directory = target.directory.resolve()
    if not directory.is_dir():
        raise ValueError(f"no directory {directory}")
    variables, types = terraform_config(directory)
    if not types:
        raise ValueError(f"{directory} declares no resource")
    added: list[dict[str, str]] = []
    if MARKER not in variables:
        (directory / MARKER_FILE).write_text(_MARKER_TF, encoding="utf-8")
        added.append(
            {
                "name": MARKER_FILE,
                "why": f"added to {directory}: the stage marker Interlock reads back on recovery",
            }
        )
    variables.pop(MARKER, None)
    resources = [TfResource(t, t) for t in types]
    specs = [Table(r.table, tuple(r.spec().columns), None) for r in resources]
    kinds: Mapping[str, Mapping[str, Any] | None] = {
        "string": {"type": "string", "maxLength": 1000},
        "number": {"type": "integer"},
        "bool": {"type": "boolean"},
    }
    tools = []
    report = []
    skipped = []
    for name, kind in sorted(variables.items()):
        schema = kinds.get(kind)
        if schema is None:
            skipped.append({"name": f"set_{name.lower()}", "why": f"a variable of type {kind}"})
            continue
        tool = f"set_{name.lower()}"
        tools.append(
            _tool(
                tool,
                f"Set the input variable {name} and apply the plan it makes.",
                {"value": dict(schema)},
                [_effect("set", "update", types[0], f"set '{name}' :value", {"value": "$.value"})],
            )
        )
        report.append(
            {
                "name": tool,
                "mode": WRITE,
                "why": "measured by a saved plan; a replacement or a destroy is refused",
            }
        )
    executable = list(target.executable)

    def head(root: Path, control: Path) -> str:
        return (
            f'substrate = "terraform"\n[terraform]\ndirectory = {_toml(str(directory))}\n'
            f"executable = {json.dumps(executable)}\n"
            f"variables = {json.dumps(sorted(variables))}\n"
            f"journal = {_toml(str(root / 'journal'))}\n"
            + "".join(
                f"[[terraform.resources]]\ntable = {_toml(r.table)}\ntype = {_toml(r.type)}\n"
                for r in resources
            )
        )

    return Starter(
        tables=specs,
        tools=tools,
        data=_data({t: ["insert", "update"] for t in types}, max_rows=10),
        windows=[],  # Terraform has no control database for a window's history
        interlock=head,
        nouns={"substrate": "terraform", "rows": "resources", "tables": "resource types"},
        report=report,
        skipped=skipped,
        notes=added,
    )


def starter(target: Target) -> Starter:
    """The starter deployment's pieces for ``target``.

    :raises ValueError: When the target is incomplete or unreadable.
    """
    target.check()
    if target.substrate == "sqlite":
        assert target.database is not None
        if not Path(target.database).is_file():
            raise ValueError(f"no SQLite database at {target.database}")
        return _sql(target, sqlite_tables(target.database))
    if target.substrate == "postgres":
        assert target.database is not None
        return _sql(target, postgres_tables(target.database, target.schema))
    if target.substrate == "git":
        return _git(target)
    if target.substrate == "kubernetes":
        return _kubernetes(target)
    if target.substrate == "fhir":
        return _fhir(target)
    return _terraform(target)

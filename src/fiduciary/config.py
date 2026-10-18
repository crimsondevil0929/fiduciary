"""``fiduciary.toml`` (``docs/FIDUCIARY_SPEC.md`` §10.5).

    database = "postgresql://fiduciary_service@db/acme"  # or a SQLite file
    schema   = "fiduciary"                               # the PostgreSQL schema
    ledger   = "ledger.db"                               # AgentGov ledger to anchor into
    interlock = "interlock.toml"                         # the deployment mandates bind to

    [charter]
    file   = "charter-v1.json"
    digest = "<sha256 of charter version 1>"             # the root of authority

    [service]
    key = "service.key"                                  # or signer = "<name>"

    [signers.<name>]                                     # fiduciary.keys

    [binding]
    tools      = ["refund.tool.json", "acme.tools:lookup"] # tool bindings: files or module:name
    directives = ["stop-retrying.txt"]                   # directive texts, by their bytes' SHA-256
    chains     = "chains"                                # each lineage's <mandate_id>.chain

Paths are relative to the file. ``FIDUCIARY_DATABASE`` and ``FIDUCIARY_LEDGER``
override ``database`` and ``ledger``, which keeps a password out of the file.
``interlock`` and ``[binding]`` are the deployment :func:`fiduciary.compiler.compile`
reads: ``interlock.toml``'s tables and sinks, the tool bindings it runs and the
directive texts. The keys of later phases (``in_process``, ``[executor]``,
``[runtime]``, ``[receipts]``, ``[bridge]``) are read when those phases are.
"""

from __future__ import annotations

import hashlib
import importlib
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from fiduciary import fidu1
from fiduciary.keys import SignerSpec, open_signer, parse_signers

if TYPE_CHECKING:
    from agentgov import BudgetManager
    from agentgov.receipts.signing import Signer

    from fiduciary.compiler import Deployment
    from fiduciary.documents import ToolBinding
    from fiduciary.store import Store

__all__ = ["Config", "ConfigError", "load_config", "load_tool"]

_TOP: Final = frozenset(
    {"database", "schema", "ledger", "interlock", "charter", "service", "signers", "binding"}
)


class ConfigError(ValueError):
    """``fiduciary.toml`` says something it may not."""


def is_dsn(text: str) -> bool:
    return text.startswith(("postgresql://", "postgres://"))


@dataclass(frozen=True, slots=True)
class Config:
    database: str
    schema: str = "fiduciary"
    ledger: str | None = None
    charter_file: Path | None = None
    charter_digest: str | None = None
    service_key: Path | None = None
    service_signer: str | None = None
    signers: Mapping[str, SignerSpec] = field(default_factory=dict)
    interlock: Path | None = None
    tools: tuple[str, ...] = ()
    directives: tuple[Path, ...] = ()
    chains: Path | None = None

    def open_store(self) -> Store:
        """The store ``database`` names."""
        if is_dsn(self.database):
            from fiduciary.postgres import PostgresStore

            return PostgresStore(self.database, schema=self.schema)
        from fiduciary.sqlite import SqliteStore

        return SqliteStore(self.database)

    def open_ledger(self, *, read_only: bool = False) -> BudgetManager | None:
        """A governor of ``ledger`` (write-capable unless ``read_only``), or ``None``."""
        if self.ledger is None:
            return None
        from interlock.wiring import open_ledger

        governor: BudgetManager = open_ledger(self.ledger, read_only=read_only)
        return governor

    def service(self) -> Signer:
        """The service key, which signs the log."""
        if self.service_key is None and self.service_signer is None:
            raise ConfigError("[service] names no key and no signer")
        return open_signer(self.signers, key=self.service_key, signer=self.service_signer)

    def deployment(self, governor: BudgetManager) -> Deployment:
        """The deployment snapshot compilation reads: ``interlock.toml``'s
        tables and sinks, ``[binding]``'s tools and directives, and the
        ledger ``governor`` keeps, by its first entry.

        :raises ConfigError: On a deployment the files do not describe.
        """
        from interlock.config import load_config as load_interlock

        from fiduciary.compiler import Deployment

        if self.interlock is None:
            raise ConfigError('interlock names no deployment: set interlock = "interlock.toml"')
        try:
            found = load_interlock(self.interlock, require_database=False)
        except (OSError, ValueError) as exc:
            raise ConfigError(f"{self.interlock}: {exc}") from exc
        tools = {}
        for target in self.tools:
            tool = load_tool(target)
            tools[tool.digest] = tool
        directives = {}
        for path in self.directives:
            try:
                data = path.read_bytes()
                text = data.decode("utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise ConfigError(f"[binding] directive {path}: {exc}") from exc
            directives[hashlib.sha256(data).hexdigest()] = text
        governor.refresh()
        if len(governor.ledger) == 0:
            raise ConfigError("the ledger is empty: it has no first entry for a charter to name")
        return Deployment(
            ledger=governor.entry(1).entry_hash,
            tables={t.name: frozenset((t.primary_key, *t.columns)) for t in found.tables},
            sinks=found.sink_registry(),
            tools=tools,
            directives=directives,
        )

    def pinned(self) -> str:
        if self.charter_digest is None:
            raise ConfigError("[charter] pins no digest: the root of authority is unset")
        return self.charter_digest


def load_tool(target: str) -> ToolBinding:
    """A tool binding from a file, or from ``module:name`` (a
    :class:`ToolBinding`, or its JSON as a mapping).

    :raises ConfigError: On a target that is no tool binding.
    """
    from fiduciary.documents import ToolBinding, load, loads
    from fiduciary.exceptions import FiduciaryError

    try:
        if Path(target).exists() or ":" not in target:
            document = loads(Path(target).read_bytes())
        else:
            module, _, attribute = target.partition(":")
            value = getattr(importlib.import_module(module), attribute)
            document = (
                value
                if isinstance(value, ToolBinding)
                else load(value, signed=isinstance(value, dict) and "sig" in value)
            )
    except (OSError, ImportError, AttributeError, FiduciaryError) as exc:
        raise ConfigError(f"{target}: {exc}") from exc
    if not isinstance(document, ToolBinding):
        raise ConfigError(f"{target} is no tool binding")
    return document


def load_config(path: str | Path) -> Config:
    """Read ``fiduciary.toml``.

    :raises ConfigError: On anything the file may not say.
    """
    where = Path(path)
    try:
        raw: dict[str, Any] = tomllib.loads(where.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{where}: {exc}") from exc
    base = where.resolve().parent
    unknown = sorted(set(raw) - _TOP)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {', '.join(unknown)}")

    def place(value: object, name: str) -> Path:
        if not isinstance(value, str) or not value:
            raise ConfigError(f"{where}: {name} is a path")
        found = Path(value)
        return found if found.is_absolute() else base / found

    database = os.environ.get("FIDUCIARY_DATABASE") or raw.get("database")
    if not isinstance(database, str) or not database:
        raise ConfigError(f"{where}: database is required (or FIDUCIARY_DATABASE)")
    if not is_dsn(database):
        database = str(place(database, "database"))
    ledger = os.environ.get("FIDUCIARY_LEDGER") or raw.get("ledger")
    if ledger is not None:
        if not isinstance(ledger, str) or not ledger:
            raise ConfigError(f"{where}: ledger is a SQLite file or a postgresql:// DSN")
        if not is_dsn(ledger):
            ledger = str(place(ledger, "ledger"))
    charter = raw.get("charter", {})
    service = raw.get("service", {})
    for name, table, allowed in (
        ("charter", charter, {"file", "digest"}),
        ("service", service, {"key", "signer"}),
    ):
        if not isinstance(table, dict):
            raise ConfigError(f"{where}: [{name}] is a table")
        extra = sorted(set(table) - allowed)
        if extra:
            raise ConfigError(f"{where}: [{name}] has unknown key(s) {', '.join(extra)}")
    digest = charter.get("digest")
    if digest is not None and (not isinstance(digest, str) or not fidu1.HASH.fullmatch(digest)):
        raise ConfigError(f"{where}: [charter] digest is 64 lowercase hex characters")
    try:
        signers = parse_signers(raw.get("signers"))
    except ValueError as exc:
        raise ConfigError(f"{where}: {exc}") from exc
    signer = service.get("signer")
    if signer is not None and signer not in signers:
        raise ConfigError(f"{where}: [service] signer {signer!r} names no [signers.{signer}]")
    if "key" in service and signer is not None:
        raise ConfigError(f"{where}: [service] has a key or a signer, not both")
    schema = raw.get("schema", "fiduciary")
    if not isinstance(schema, str):
        raise ConfigError(f"{where}: schema is a name")
    interlock = raw.get("interlock")
    binding = raw.get("binding", {})
    if not isinstance(binding, dict):
        raise ConfigError(f"{where}: [binding] is a table")
    extra = sorted(set(binding) - {"tools", "directives", "chains"})
    if extra:
        raise ConfigError(f"{where}: [binding] has unknown key(s) {', '.join(extra)}")
    lists: dict[str, list[str]] = {}
    for key in ("tools", "directives"):
        value = binding.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
            raise ConfigError(f"{where}: [binding] {key} is a list of paths")
        lists[key] = value

    def tool(target: str) -> str:
        found = place(target, "[binding] tools")
        return str(found) if found.exists() or ":" not in target else target

    return Config(
        database=database,
        schema=schema,
        ledger=ledger,
        charter_file=None if "file" not in charter else place(charter["file"], "[charter] file"),
        charter_digest=digest,
        service_key=None if "key" not in service else place(service["key"], "[service] key"),
        service_signer=signer,
        signers=signers,
        interlock=None if interlock is None else place(interlock, "interlock"),
        tools=tuple(tool(t) for t in lists["tools"]),
        directives=tuple(place(d, "[binding] directives") for d in lists["directives"]),
        chains=None if "chains" not in binding else place(binding["chains"], "[binding] chains"),
    )

"""``fiduciary.toml`` (``docs/FIDUCIARY_SPEC.md`` §10.5).

    database = "postgresql://fiduciary_service@db/acme"  # or a SQLite file
    schema   = "fiduciary"                               # the PostgreSQL schema
    ledger   = "ledger.db"                               # AgentGov ledger to anchor into

    [charter]
    file   = "charter-v1.json"
    digest = "<sha256 of charter version 1>"             # the root of authority

    [service]
    key = "service.key"                                  # or signer = "<name>"

    [signers.<name>]                                     # fiduciary.keys

Paths are relative to the file. ``FIDUCIARY_DATABASE`` and ``FIDUCIARY_LEDGER``
override ``database`` and ``ledger``, which keeps a password out of the file.
The keys of later phases (``interlock``, ``in_process``, ``[executor]``,
``[runtime]``, ``[receipts]``, ``[bridge]``) are read when those phases are.
"""

from __future__ import annotations

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

    from fiduciary.store import Store

__all__ = ["Config", "ConfigError", "load_config"]

_TOP: Final = frozenset({"database", "schema", "ledger", "charter", "service", "signers"})


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

    def open_store(self) -> Store:
        """The store ``database`` names."""
        if is_dsn(self.database):
            from fiduciary.postgres import PostgresStore

            return PostgresStore(self.database, schema=self.schema)
        from fiduciary.sqlite import SqliteStore

        return SqliteStore(self.database)

    def open_ledger(self) -> BudgetManager | None:
        """A write-capable governor of ``ledger``, or ``None``."""
        if self.ledger is None:
            return None
        from interlock.wiring import open_ledger

        governor: BudgetManager = open_ledger(self.ledger)
        return governor

    def service(self) -> Signer:
        """The service key, which signs the log."""
        if self.service_key is None and self.service_signer is None:
            raise ConfigError("[service] names no key and no signer")
        return open_signer(self.signers, key=self.service_key, signer=self.service_signer)

    def pinned(self) -> str:
        if self.charter_digest is None:
            raise ConfigError("[charter] pins no digest: the root of authority is unset")
        return self.charter_digest


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
    return Config(
        database=database,
        schema=schema,
        ledger=ledger,
        charter_file=None if "file" not in charter else place(charter["file"], "[charter] file"),
        charter_digest=digest,
        service_key=None if "key" not in service else place(service["key"], "[service] key"),
        service_signer=signer,
        signers=signers,
    )

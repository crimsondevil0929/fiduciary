#!/usr/bin/env python3
"""The security audit (``docs/FIDUCIARY_SPEC.md`` §11 Phase 6; F9-1, F4-27):
does any secret, row, payload, transcript or agent text reach a hash, a
document, a log or a bundle of fiduciary's?

By the method of interlock's ``scripts/security_audit.py``, on each store,
SQLite and PostgreSQL:

1. **Canaries.** One session's work, each value a canary of its own:
   - a secret: the runtime key's seed, and an agent's bearer token;
   - row data and a payload: a passthrough's answer, and a plan's argument
     (a Stripe payment intent, which the plan writes and enqueues);
   - a transcript: a model call's message, and the model's reply;
   - agent text: a request's statement longer than its clip (one canary
     within the 512 characters ``agent.statement`` keeps, one beyond them),
     an unpinned tool's name, and an argument the schema refuses.
   Then the lineage's audit bundle is exported.
2. **Taint.** The exact input of every SHA-256 that fiduciary and AgentGov
   compute, and every message their keys sign, recorded through the
   functions that compute them, with the module that asked. (Interlock's
   hashes over the plans it stages are its own audit's.)
3. **Crawl.** Every column of every row of the store and the ledger, read
   from ``sqlite_master`` or ``information_schema``; every file the run wrote;
   every member of the bundle.
4. **Assert.** No canary, and no SHA-256 of one, is anywhere recorded, but
   one: the statement's head, in ``agent.statement`` (F4-18), in the ticket
   and what names it. Each canary is found where it is meant to be kept (the
   back office's rows and outbox, the model client, the key file), so the
   audit is seen to look.

Run::

    uv run --managed-python python scripts/security_audit.py                 # SQLite
    uv run --managed-python python scripts/security_audit.py --dsn postgresql://...  # and PG

Exit status: 0 when nothing leaked; 1 when something did, or a canary was not
found where it was planted; 2 when the audit could not run.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import secrets
import sqlite3
import sys
import tarfile
import tempfile
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EXIT_CLEAN, EXIT_LEAKED, EXIT_ERROR = 0, 1, 2
OURS = ("fiduciary", "agentgov")
"""The packages whose hashes and signatures are recorded."""
STATEMENT_HEAD = "statement head"
"""The one canary that may be kept: inside ``agent.statement``'s clip."""
COMMITMENTS = {
    ("an agent's bearer token", "sha256 by fiduciary.mcp"): (
        "the bridge hashes an offered token to compare it with the SHA-256 the "
        "configuration holds (F10-13); the hash is kept nowhere"
    ),
}
"""Where a canary may enter a hash: computed to compare, kept nowhere."""


@dataclass(frozen=True)
class Canaries:
    """Each planted value, by name."""

    values: dict[str, str]

    @classmethod
    def make(cls) -> Canaries:
        tag = secrets.token_hex(6)
        return cls(
            {
                "the runtime key's seed": secrets.token_hex(32),
                "an agent's bearer token": f"agent-token-{secrets.token_hex(16)}",
                "a passthrough's answer": f"ROWDATA{tag}",
                "a plan's argument": f"pi_PAYLOAD{tag}",
                "a model call's message": f"TRANSCRIPT{tag}",
                "the model's reply": f"MODELREPLY{tag}",
                STATEMENT_HEAD: f"STATEHEAD{tag}",
                "a statement past its clip": f"STATEBEYOND{tag}",
                "an unpinned tool's name": f"NOSUCHTOOL{tag}",
                "a refused argument": f"REFUSEDARG{tag}",
            }
        )

    def needles(self) -> dict[str, list[bytes]]:
        """Each canary, and the SHA-256 of it, in hex: either found is a leak."""
        return {
            name: [value.encode(), hashlib.sha256(value.encode()).hexdigest().encode()]
            for name, value in self.values.items()
        }


# --------------------------------------------------------------------------
# taint: what fiduciary and AgentGov hash and sign
# --------------------------------------------------------------------------


@dataclass
class Taint:
    hashed: list[tuple[str, bytes]] = field(default_factory=list)
    signed: list[tuple[str, bytes]] = field(default_factory=list)
    families: dict[str, int] = field(default_factory=dict)


def _caller(depth: int) -> str:
    frame = sys._getframe(depth)  # the code that asked for the hash
    while frame is not None:
        name = str(frame.f_globals.get("__name__", ""))
        if name != __name__ and not name.startswith("hashlib"):
            return name
        frame = frame.f_back  # type: ignore[assignment]
    return ""


@contextmanager
def tainted(taint: Taint) -> Iterator[None]:
    """Record every SHA-256's input and every Ed25519 message signed, by a
    module of :data:`OURS`."""
    from agentgov.receipts import signing

    real = hashlib.sha256

    class Recording:
        def __init__(self, data: bytes = b"", **kw: Any) -> None:
            self._who = _caller(2)
            self._inner = real(data, **kw)
            self._data = bytearray(data)

        def update(self, data: bytes) -> None:
            self._data.extend(data)
            self._inner.update(data)

        def _note(self) -> None:
            if self._who.startswith(OURS):
                family = self._who.split(".")[1] if "." in self._who else self._who
                taint.families[family] = taint.families.get(family, 0) + 1
                taint.hashed.append((self._who, bytes(self._data)))

        def digest(self) -> bytes:
            self._note()
            return self._inner.digest()

        def hexdigest(self) -> str:
            self._note()
            return self._inner.hexdigest()

        def copy(self) -> Any:
            return self._inner.copy()

        @property
        def digest_size(self) -> int:
            return self._inner.digest_size

        @property
        def block_size(self) -> int:
            return self._inner.block_size

        name = "sha256"

    sign = signing.Ed25519Signer.sign

    def recorded_sign(self: Any, message: bytes) -> bytes:
        who = _caller(2)
        if who.startswith(OURS):
            taint.signed.append((who, bytes(message)))
        return sign(self, message)

    hashlib.sha256 = Recording  # type: ignore[assignment]
    signing.Ed25519Signer.sign = recorded_sign  # type: ignore[method-assign]
    try:
        yield
    finally:
        hashlib.sha256 = real
        signing.Ed25519Signer.sign = sign  # type: ignore[method-assign]


# --------------------------------------------------------------------------
# the scenario
# --------------------------------------------------------------------------


@dataclass
class Run:
    backend: str
    leaks: list[str] = field(default_factory=list)
    found: dict[str, set[str]] = field(default_factory=dict)
    commitments: set[str] = field(default_factory=set)
    families: dict[str, int] = field(default_factory=dict)
    places: int = 0

    @property
    def clean(self) -> bool:
        return not self.leaks and all(self.found.get(name) for name in KEPT)


KEPT: dict[str, str] = {
    "the runtime key's seed": "file:runtime.key",
    "a passthrough's answer": "client",
    "a plan's argument": "back office",  # the Stripe request's payload, in the outbox
    "a model call's message": "client",
    "the model's reply": "client",
    STATEMENT_HEAD: "fiduciary",
}
"""Where each canary is meant to be found: the audit is seen to look."""


def scenario(
    store: Any,
    tmp: Path,
    canaries: Canaries,
    taint: Taint,
    plant: Callable[[Any, Canaries], None] | None = None,
) -> dict[str, Any]:
    """The session's work, under :func:`tainted`; returns what it kept in
    memory (the client's calls) and the bundle's bytes. ``plant`` is called
    with the session's Fiduciary, under the taint: a test plants a leak."""
    import hashlib as hl
    from types import SimpleNamespace

    from agentgov.interceptor import TokenUsage
    from agentgov.receipts.signing import Ed25519Signer
    from interlock.records import Keyring, RecordLog
    from tests import bound
    from tests import bridge_kit as kit
    from tests import bundle_kit as bk
    from tests import examples as e
    from tests import serve_kit as sk
    from tests.conftest import LiveClock

    from fiduciary.mcp import Agent, HttpBridge
    from fiduciary.sqlite import SqliteStore

    v = canaries.values
    backend = "sqlite" if isinstance(store, SqliteStore) else "postgres"
    seed = bytes.fromhex(v["the runtime key's seed"])
    (tmp / "runtime.key").write_text(v["the runtime key's seed"] + "\n")
    runtime_key = Ed25519Signer(seed)
    clock = LiveClock()
    kept: dict[str, Any] = {"calls": []}
    with tainted(taint):
        built = bound.build(backend, tmp, store, clock, tools=kit.tools(backend))
        world = next(built)
        try:
            mandate = kit.mandate(world)
            world.issue_and_bind(mandate)
            public = runtime_key.public_key()
            records = RecordLog(
                runtime_key,
                path=tmp / "audit-runtime.jsonl",
                keyring=Keyring({public.key_id: public}),
            )
            world.cleanups.append(records.close)

            def lookup(arguments: Any) -> dict[str, Any]:
                answer = {"order_id": arguments["order_id"], "note": v["a passthrough's answer"]}
                kept["calls"].append(answer)
                return answer

            fid = kit.fiduciary(world, handlers={"lookup_order": lookup}, records=records)
            session = fid.session(bound.SCOPE, tenant="acme")
            refund = {"order_id": 7, "amount": "12.50", "payment_intent": v["a plan's argument"]}
            committed = session.call("refund_order", refund)
            assert committed.committed, committed.render()
            assert session.call("lookup_order", {"order_id": 7}).ok
            refused = session.call(
                "adjust_orders", {"first": 1, "second": 3, "amount": v["a refused argument"]}
            )
            assert refused.outcome == "rejected", refused.render()
            assert session.call(v["an unpinned tool's name"], {}).outcome == "rejected"
            from fiduciary.documents import Mandate

            raw = kit.mandate_json(world)
            raw.update(version=2, prev=mandate.digest)
            proposed = Mandate.from_json(raw, signed=False).signed(e.signer("cfo"))
            world.authority.propose(proposed)
            # A statement within agent.statement's 512 characters is kept, as
            # written; a longer one is refused, and kept nowhere (F4-18).
            asked = session.call(
                "fiduciary_request",
                {
                    "kind": "amend",
                    "statement": f"{v[STATEMENT_HEAD]} please",
                    "mandate": proposed.digest,
                },
            )
            assert asked.ticket is not None, asked.render()
            beyond = "x" * 520 + " " + v["a statement past its clip"]
            too_long = session.call(
                "fiduciary_request",
                {"kind": "amend", "statement": beyond, "mandate": proposed.digest},
            )
            assert too_long.outcome == "rejected", too_long.render()

            class Client:
                def __init__(self) -> None:
                    self.messages = self

                def create(self, **kwargs: Any) -> Any:
                    kept["calls"].append(kwargs)
                    kept["calls"].append(v["the model's reply"])
                    return SimpleNamespace(
                        model=kwargs["model"],
                        usage=TokenUsage(input_tokens=10, output_tokens=10),
                        content=[{"type": "text", "text": v["the model's reply"]}],
                    )

            model = session.govern(Client())
            model.messages.create(
                model="claude-opus-5-5",
                max_tokens=64,
                messages=[{"role": "user", "content": v["a model call's message"]}],
            )
            # An agent over HTTP: the bridge holds its token's digest, never the token.
            token = v["an agent's bearer token"]
            bridge = HttpBridge(
                "127.0.0.1",
                0,
                lock=fid.lock,
                agents={
                    "desk": Agent(
                        "desk",
                        hl.sha256(token.encode()).hexdigest(),
                        lambda: fid.session(bound.SCOPE, tenant="acme", bridged=True),
                    )
                },
            )
            bridge.start()
            try:
                client = sk.McpClient(bridge.address, token)
                client.initialize()
                client.close()
            finally:
                bridge.stop()
            session.close()
            if plant is not None:
                plant(fid, canaries)
            kept["bundle"] = bk.export(
                world,
                mandate.mandate_id,
                evidence={
                    "runtime_records": records.records(),
                    "runtime_key": public,
                },
            )
            kept["back office"] = [tuple(r) for r in world.fetch(_outbox_payloads(backend))]
            kept["world"] = world
        finally:
            built.close()
    return kept


def _outbox_payloads(backend: str) -> str:
    table = "_interlock_outbox" if backend == "sqlite" else "interlock.outbox"
    return f"SELECT payload FROM {table}"  # noqa: S608 - a fixed table


# --------------------------------------------------------------------------
# the crawl
# --------------------------------------------------------------------------


def crawl_sqlite(path: Path) -> Iterator[tuple[str, bytes]]:
    with sqlite3.connect(path) as conn:
        tables = [
            str(r[0])
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in tables:
            columns = [str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")]
            for row in conn.execute(f"SELECT * FROM {table}"):  # noqa: S608 - its own table
                for column, value in zip(columns, row, strict=True):
                    yield f"{path.name}:{table}.{column}", _bytes(value)


def crawl_postgres(dsn: str, schema: str) -> Iterator[tuple[str, bytes]]:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        found = conn.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = %s ORDER BY table_name, ordinal_position",
            (schema,),
        ).fetchall()
        tables: dict[str, list[str]] = {}
        for table, column in found:
            tables.setdefault(str(table), []).append(str(column))
        for table, columns in tables.items():
            names = ", ".join(f'"{c}"' for c in columns)
            for row in conn.execute(f'SELECT {names} FROM {schema}."{table}"'):  # noqa: S608
                for column, value in zip(columns, row, strict=True):
                    yield f"{schema}.{table}.{column}", _bytes(value)


def crawl_files(root: Path, skip: Callable[[Path], bool]) -> Iterator[tuple[str, bytes]]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and not skip(path):
            yield f"file:{path.relative_to(root)}", path.read_bytes()


def crawl_bundle(data: bytes) -> Iterator[tuple[str, bytes]]:
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for member in tar.getmembers():
            found = tar.extractfile(member)
            if found is not None:
                yield f"bundle:{member.name}", found.read()


def _bytes(value: object) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value)
    return str(value).encode("utf-8", "replace")


# --------------------------------------------------------------------------
# the audit
# --------------------------------------------------------------------------


def audit(
    store: Any,
    canaries: Canaries,
    *,
    say: Callable[[str], None] = print,
    plant: Callable[[Any, Canaries], None] | None = None,
) -> Run:
    """Run the scenario on ``store``, then hold everything it touched to the canaries."""
    from fiduciary.postgres import PostgresStore
    from fiduciary.sqlite import SqliteStore

    backend = "sqlite" if isinstance(store, SqliteStore) else "postgres"
    run = Run(backend)
    taint = Taint()
    with tempfile.TemporaryDirectory(prefix="fiduciary-audit-") as scratch:
        tmp = Path(scratch)
        if isinstance(store, SqliteStore):  # the store's file lives with the rest
            moved = SqliteStore(tmp / "fiduciary.db")
            store.close()
            store = moved
        kept = scenario(store, tmp, canaries, taint, plant)
        run.families = dict(taint.families)
        places: list[tuple[str, bytes]] = []
        places += [(f"sha256 by {who}", data) for who, data in taint.hashed]
        places += [(f"signed by {who}", data) for who, data in taint.signed]
        if isinstance(store, SqliteStore):
            places += list(crawl_sqlite(tmp / "fiduciary.db"))
        else:
            assert isinstance(store, PostgresStore)
            from tests.conftest import need_postgres

            places += list(crawl_postgres(need_postgres(), store.schema))
        places += list(crawl_sqlite(tmp / "ledger.db"))
        places += list(
            crawl_files(
                tmp,
                lambda p: (
                    p.name in ("fiduciary.db", "ledger.db", "back_office.db", "runtime.key")
                    or p.name.endswith((".db-wal", ".db-shm", ".lock"))
                ),
            )
        )
        places += list(crawl_bundle(kept["bundle"]))
        run.places = len(places)
        needles = canaries.needles()
        for where, data in places:
            for name, forms in needles.items():
                if any(form in data for form in forms):
                    if name == STATEMENT_HEAD and _in_statement(where, data, canaries):
                        run.found.setdefault(name, set()).add("fiduciary")
                        continue
                    if (name, where) in COMMITMENTS:
                        run.commitments.add(COMMITMENTS[name, where])
                        continue
                    run.leaks.append(f"{name} in {where}")
        # Where each is kept: the audit is seen to look.
        key_file = (tmp / "runtime.key").read_bytes()
        if canaries.values["the runtime key's seed"].encode() in key_file:
            run.found.setdefault("the runtime key's seed", set()).add("file:runtime.key")
        client = repr(kept["calls"]).encode()
        for name in ("a passthrough's answer", "a model call's message", "the model's reply"):
            if canaries.values[name].encode() in client:
                run.found.setdefault(name, set()).add("client")
        office = repr(kept["back office"]).encode()
        if canaries.values["a plan's argument"].encode() in office:
            run.found.setdefault("a plan's argument", set()).add("back office")
        store.close()
    say(f"{backend}: {run.places} places read; {sum(run.families.values())} hashes recorded")
    return run


def _in_statement(where: str, data: bytes, canaries: Canaries) -> bool:
    """The statement's head, held where ``agent.statement`` is: in a ticket
    (or the record, the bundle member or the hash input that carries one),
    clipped, with nothing past the clip beside it."""
    if canaries.values["a statement past its clip"].encode() in data:
        return False
    return b'"statement"' in data or b"statement" in where.encode()


def report(run: Run, say: Callable[[str], None] = print) -> None:
    say(f"{run.backend}: {'clean' if run.clean else 'LEAKED'}")
    for leak in run.leaks:
        say(f"  leak: {leak}")
    for name in sorted(KEPT):
        held = sorted(run.found.get(name, ()))
        say(f"  {name}: kept in {', '.join(held) or 'NOWHERE (the audit did not see it)'}")
    for commitment in sorted(run.commitments):
        say(f"  commitment: {commitment}")
    for family, count in sorted(run.families.items()):
        say(f"  hashes by {family}: {count}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--dsn", help="also audit on PostgreSQL, on this server")
    args = parser.parse_args()
    if args.dsn:  # the test kit builds the back office there; it reads this on import
        import os

        os.environ["FIDUCIARY_TEST_POSTGRES_DSN"] = args.dsn
    from fiduciary.sqlite import SqliteStore

    runs = []
    try:
        with tempfile.TemporaryDirectory() as scratch:
            runs.append(audit(SqliteStore(Path(scratch) / "first.db"), Canaries.make()))
        if args.dsn:
            import psycopg

            from fiduciary.postgres import PostgresStore, install, roles

            schema = f"fid_audit_{uuid.uuid4().hex[:8]}"
            with psycopg.connect(args.dsn, autocommit=True) as admin:
                install(admin, schema=schema)
            try:
                runs.append(audit(PostgresStore(args.dsn, schema=schema), Canaries.make()))
            finally:
                with psycopg.connect(args.dsn, autocommit=True) as admin:
                    admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
                    for role in roles(schema):
                        admin.execute(f"DROP OWNED BY {role}")
                        admin.execute(f"DROP ROLE IF EXISTS {role}")
    except Exception as exc:
        import traceback

        traceback.print_exc()
        print(f"the audit could not run: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    for run in runs:
        report(run)
    return EXIT_CLEAN if all(run.clean for run in runs) else EXIT_LEAKED


if __name__ == "__main__":
    sys.exit(main())

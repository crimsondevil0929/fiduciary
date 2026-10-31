"""The ``fiduciary`` command (``docs/FIDUCIARY_SPEC.md`` §10.6, Phases 1 to 4).

Exit status: 0 on success; 1 when something is refused or does not verify;
2 on a usage, configuration or input error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

from agentgov.receipts.signing import Signer

from fiduciary import __version__, fidu1
from fiduciary.documents import (
    Approval,
    Charter,
    Document,
    Mandate,
    Record,
    Ticket,
    Warrant,
    loads,
    suspension_digest,
)
from fiduciary.documents.ticket import (
    AmendEvidence,
    DeadEvidence,
    ExtensionEvidence,
    HeldEvidence,
    RefusedEvidence,
    UndoEvidence,
)
from fiduciary.exceptions import FiduciaryError

__all__ = ["main", "main_entry", "render_ticket"]

OK, REFUSED, USAGE = 0, 1, 2


_OPENED: list[Callable[[], None]] = []
"""What this invocation opened, closed when it returns."""


def _opened(thing: Any) -> Any:
    """Close ``thing`` when the command returns."""
    if thing is not None:
        _OPENED.append(thing.close)
    return thing


class _ExitError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _fail(message: str, status: int = USAGE) -> NoReturn:
    raise _ExitError(status, message)


def _out(text: str) -> None:
    sys.stdout.write(text + "\n")


def _err(text: str) -> None:
    sys.stderr.write(text + "\n")


# --------------------------------------------------------------------------
# configuration, documents, signers
# --------------------------------------------------------------------------


def _config(args: argparse.Namespace) -> Any:
    from fiduciary.config import ConfigError, load_config

    path = Path(args.config)
    if not path.exists():
        _fail(f"{path}: no such configuration (give --config)")
    try:
        return load_config(path)
    except ConfigError as exc:
        _fail(str(exc))


def _read(path: str, *, signed: bool | None = None) -> Document:
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        _fail(f"{path}: {exc}")
    try:
        return loads(data, signed=signed)
    except FiduciaryError as exc:
        _fail(f"{path}: {exc}")


def _write(document: Document | dict[str, Any], out: str | None) -> None:
    text = (
        document.pretty()
        if isinstance(document, Document)
        else json.dumps(document, indent=2, sort_keys=True)
    )
    if out is None:
        _out(text)
        return
    target = Path(out)
    if target.exists():
        _fail(f"{out} exists: a document is never overwritten")
    target.write_text(text + "\n", encoding="utf-8")
    _err(f"wrote {out}")


def _signer(args: argparse.Namespace) -> Signer:
    from fiduciary.keys import open_signer

    signers: Any = {}
    if args.signer is not None:
        signers = _config(args).signers
    try:
        return open_signer(signers, key=args.key, signer=args.signer)
    except (OSError, ValueError, FiduciaryError) as exc:
        _fail(f"cannot open the key: {exc}")


def _signing(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--key", help="an Ed25519 key file (fiduciary keygen)")
    parser.add_argument("--signer", help="a [signers.<name>] of the configuration")


def _now() -> datetime:
    return datetime.now(UTC)


def _service(args: argparse.Namespace) -> Any:
    """The configuration's store, log and authority, the log anchored when a ledger is set."""
    from fiduciary.authority import Authority
    from fiduciary.log import Log

    config = _config(args)
    try:
        signer = config.service()
        store = _opened(config.open_store())
        ledger = _opened(config.open_ledger())
    except (OSError, ValueError, FiduciaryError) as exc:
        _fail(str(exc))
    log = Log(store, signer, ledger=ledger)
    return config, log, Authority(log, pinned=config.pinned())


def _binder(args: argparse.Namespace, *, read_only: bool = False) -> Any:
    """A :class:`~fiduciary.binding.Binder` of the configured deployment: the
    store, the ledger (read-only when ``read_only``, and the log unanchored,
    for commands that write nothing), ``interlock`` and ``[binding]``."""
    from fiduciary.authority import Authority
    from fiduciary.binding import Binder
    from fiduciary.log import Log

    config = _config(args)
    if config.ledger is None:
        _fail("binding needs the deployment's AgentGov ledger: set ledger")
    try:
        signer = config.service()
        store = _opened(config.open_store())
        governor = _opened(config.open_ledger(read_only=read_only))
        deployment = config.deployment(governor)
    except (OSError, ValueError, FiduciaryError) as exc:
        _fail(str(exc))
    log = Log(store, signer, ledger=None if read_only else governor)
    authority = Authority(log, pinned=config.pinned())
    return _opened(Binder(authority, governor, deployment=deployment, chains=config.chains))


def _records(action: Callable[[], Sequence[Record]], nothing: str = "nothing recorded") -> int:
    try:
        records = action()
    except FiduciaryError as exc:
        _fail(str(exc), REFUSED)
    for record in records:
        _out(f"record {record.seq}: {record.kind} {record.hash}")
    if not records:
        _out(nothing)
    return OK


def _resolve(target: str, args: argparse.Namespace) -> Document:
    """A document from a file, or from the store by its digest."""
    if fidu1.HASH.fullmatch(target) and not Path(target).exists():
        config = _config(args)
        found = _opened(config.open_store()).document(target)
        if found is None:
            _fail(f"the store holds no document {target}", REFUSED)
        return loads(found[1], signed=True)
    return _read(target)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def _keygen(args: argparse.Namespace) -> int:
    from fiduciary.keys import generate_key

    try:
        signer = generate_key(args.path)
    except (OSError, ValueError, FiduciaryError) as exc:
        _fail(f"{args.path}: {exc}")
    _out(signer.public_key().spec())  # type: ignore[attr-defined]
    return OK


def _install(args: argparse.Namespace) -> int:
    from fiduciary.config import is_dsn
    from fiduciary.log import ensure_anchor_scope

    config = _config(args)
    if is_dsn(config.database):
        import psycopg

        from fiduciary.postgres import install, roles

        try:
            with psycopg.connect(config.database, autocommit=True) as conn:
                install(conn, schema=config.schema)
        except (psycopg.Error, FiduciaryError) as exc:
            _fail(f"cannot install: {exc}", REFUSED)
        _out(f"installed schema {config.schema}; roles {', '.join(roles(config.schema))}")
    else:
        config.open_store().close()
        _out(f"installed {config.database}")
    ledger = _opened(config.open_ledger())
    if ledger is not None:
        opened = ensure_anchor_scope(ledger)
        _out(
            "opened the ledger's anchor scope fiduciary"
            if opened
            else "the ledger's anchor scope exists"
        )
    return OK


def _sign(args: argparse.Namespace) -> int:
    draft = _read(args.file, signed=False)
    signed = draft.signed(_signer(args))
    _write(signed, args.out)
    _err(f"{signed.TYPE} {signed.digest}")
    return OK


def _charter_init(args: argparse.Namespace) -> int:
    principals = []
    for spec in args.principal:
        name, sep, key = spec.partition("=")
        if not sep:
            _fail(f"--principal is NAME=ed25519:<hex>, not {spec!r}")
        principals.append({"name": name, "key": key})
    try:
        quorums = [int(q) for q in args.quorums.split(",")]
    except ValueError:
        _fail("--quorums is CHARTER,ISSUE,REVOKE")
    if len(quorums) != 3:
        _fail("--quorums is CHARTER,ISSUE,REVOKE")
    draft = {
        "v": "FIDU1",
        "type": "charter",
        "charter_id": args.charter_id or str(uuid.uuid4()),
        "version": 1,
        "prev": None,
        "deployment": {"ledger": args.ledger, "label": args.label},
        "principals": sorted(principals, key=lambda p: p["name"].encode("utf-16-be")),
        "quorums": {"charter": quorums[0], "issue": quorums[1], "revoke": quorums[2]},
        "break_glass": sorted(args.break_glass),
        "issued_at": fidu1.format_instant(_now()),
    }
    try:
        charter = Charter.from_json(draft, signed=False)
    except FiduciaryError as exc:
        _fail(str(exc))
    _write(charter, args.out)
    _err(
        f"charter draft {charter.charter_id}: sign it with `fiduciary sign` as one of its "
        f"principals"
    )
    return OK


def _show(args: argparse.Namespace) -> int:
    document = _resolve(args.target, args)
    _out(document.pretty())
    signer = document.signature.key_id if document.signature else "unsigned"
    _err(f"{document.TYPE} {document.digest} (signed by key {signer})")
    return OK


def _propose(args: argparse.Namespace, kind: type[Document]) -> int:
    document = _read(args.file, signed=True)
    if not isinstance(document, kind):
        _fail(f"{args.file} is a {document.TYPE}, not a {kind.TYPE}")
    _, _, authority = _service(args)
    try:
        digest = authority.propose(document)
    except FiduciaryError as exc:
        _fail(str(exc), REFUSED)
    _out(digest)
    return OK


def _decided(action: Callable[[], Any]) -> int:
    try:
        record = action()
    except FiduciaryError as exc:
        _fail(str(exc), REFUSED)
    _out(f"record {record.seq}: {record.kind} {record.hash}")
    return OK


def _charter_adopt(args: argparse.Namespace) -> int:
    _, _, authority = _service(args)
    return _decided(lambda: authority.adopt_charter(args.digest))


def _charter_suspend(args: argparse.Namespace) -> int:
    _, _, authority = _service(args)
    return _decided(lambda: authority.suspend(args.principal))


def _mandate_issue(args: argparse.Namespace) -> int:
    _, _, authority = _service(args)
    return _decided(lambda: authority.issue_mandate(args.digest))


def _mandate_compile(args: argparse.Namespace) -> int:
    document = _resolve(args.target, args)
    if not isinstance(document, Mandate):
        _fail(f"{args.target} is no mandate")
    binder = _binder(args, read_only=True)
    try:
        compiled = binder.preview(document)
    except FiduciaryError as exc:
        _fail(str(exc), REFUSED)
    _out(json.dumps(compiled.description, indent=2, sort_keys=True))
    _err(
        f"compiled {compiled.digest} for mandate {document.mandate_id} version "
        f"{document.version}: {len(compiled.checkers)} checkers, {len(compiled.windows)} windows"
    )
    return OK


def _mandate_bind(args: argparse.Namespace) -> int:
    binder = _binder(args)
    try:
        record = binder.bind(args.digest)
    except FiduciaryError as exc:
        _fail(str(exc), REFUSED)
    _out(f"record {record.seq}: {record.kind} {record.hash}")
    _err(
        f"bound mandate {record.content['mandate_id']} version {record.content['version']}: "
        f"compiled {record.content['compiled']}, {record.content['delegated']} delegated"
    )
    return OK


def _mandate_derive(args: argparse.Namespace) -> int:
    draft = _read(args.file, signed=False)
    if not isinstance(draft, Mandate):
        _fail(f"{args.file} is no mandate")
    binder = _binder(args)
    try:
        records = list(binder.derive(draft))
        if args.bind:
            records.append(binder.bind(str(records[0].content["mandate"])))
    except FiduciaryError as exc:
        _fail(str(exc), REFUSED)
    return _records(lambda: records)


def _mandate_amend(args: argparse.Namespace) -> int:
    document = _resolve(args.digest, args)
    if not isinstance(document, Mandate):
        _fail(f"{args.digest} is no mandate")
    if document.prev is None:
        _fail(
            f"mandate {document.mandate_id} version 1 is no amendment: fiduciary mandate issue",
        )
    binder = _binder(args)
    return _records(lambda: binder.issue(document.digest))


def _mandate_revoke(args: argparse.Namespace) -> int:
    binder = _binder(args)
    return _records(lambda: binder.revoke(args.digest))


def _mandate_expire(args: argparse.Namespace) -> int:
    binder = _binder(args)
    return _records(binder.expire, "no lineage in force has outlived its term")


def _mandate_draft(args: argparse.Namespace) -> int:
    charter = _read(args.charter)
    if not isinstance(charter, Charter):
        _fail(f"{args.charter} is no charter")
    if args.parent is not None:
        parent = _read(args.parent)
        if not isinstance(parent, Mandate):
            _fail(f"{args.parent} is no mandate")
        if parent.delegation is None or parent.delegation.max_depth < 1:
            _fail(f"{args.parent} allows no derivation: a child of it would not refine it", REFUSED)
        draft = parent.body()
        draft.update(
            {
                "mandate_id": str(uuid.uuid4()),
                "version": 1,
                "prev": None,
                "parent": parent.ref.to_json(),
                "issuance": "derived",
                "subject": {
                    "scope": args.scope,
                    "funded_from": parent.subject.scope,
                    "label": args.label,
                },
                "delegation": None,
            }
        )
        if parent.delegation is not None:
            draft["budget"] = {
                **draft["budget"],
                "envelope": format(parent.delegation.envelope, "f"),
            }
    else:
        draft = _skeleton(charter, args.scope, args.funded_from or "", args.label)
    draft["charter"] = {
        "charter_id": charter.charter_id,
        "version": charter.version,
        "digest": charter.digest,
    }
    draft["issued_at"] = fidu1.format_instant(_now())
    try:
        mandate = Mandate.from_json(draft, signed=False)
    except FiduciaryError as exc:
        _fail(f"the draft does not parse: {exc}")
    _write(mandate, args.out)
    _err("mandate draft: edit its bounds, then `fiduciary mandate check` and `fiduciary sign`")
    return OK


def _skeleton(charter: Charter, scope: str, funded_from: str, label: str) -> dict[str, Any]:
    """A root mandate allowing almost nothing: every bound at its tightest, to widen by hand."""
    if not funded_from:
        _fail("a root mandate is funded from a scope: give --funded-from")
    quorum = charter.issue_quorum
    quorums = dict.fromkeys(
        ("amend", "grant", "reset", "release", "requeue", "compensate", "compensate_late"), quorum
    )
    quorums.update(
        reset_safety=len(charter.principals),
        revoke=charter.revoke_quorum,
        reduce=charter.revoke_quorum,
        accept_repair=quorum,
    )
    quorums["amend"] = max(quorums["amend"], charter.issue_quorum)
    now = _now()
    return {
        "v": "FIDU1",
        "type": "mandate",
        "mandate_id": str(uuid.uuid4()),
        "version": 1,
        "prev": None,
        "parent": None,
        "issuance": "quorum",
        "subject": {"scope": scope, "funded_from": funded_from, "label": label},
        "term": {
            "not_before": fidu1.format_instant(now),
            "not_after": fidu1.format_instant(now + timedelta(days=30)),
        },
        "roster": {"principals": list(charter.names), "quorums": quorums},
        "budget": {
            "envelope": "1.00000000",
            "per_call": "0.10000000",
            "models": ["claude-sonnet-5-5"],
            "max_output_tokens": 1024,
        },
        "governance": {
            "max_calls": 10,
            "seconds": 60,
            "fleet_velocity": True,
            "cognitive": {
                "max_identical_repeats": 3,
                "similarity_threshold": "0.7",
                "max_similar_streak": 3,
                "result_similarity_threshold": "0.3",
                "max_cycle_period": 4,
                "min_cycle_repeats": 3,
                "min_novelty_ratio": "0.02",
                "exempt_tools": [],
            },
        },
        "data": {
            "tables": {"example": ["update"]},
            "tenants": [],
            "declared_tenants": True,
            "max_rows": 1,
            "max_tenants": 1,
            "drawdown": [],
            "stated_footprint": None,
            "agreements": [],
            "facts": [],
        },
        "outbound": {
            "sinks": {},
            "max_per_plan": 0,
            "per_sink": {},
            "caps": [],
            "recipients": [],
            "tenants": None,
        },
        "windows": [],
        "tools": {"statements": "bound", "bindings": []},
        "recovery": None,
        "extension": None,
        "escalation": {"max_open": 1, "ttl_seconds": 3600, "auto": []},
        "delegation": None,
    }


def _mandate_check(args: argparse.Namespace) -> int:
    from fiduciary.refines import delegation_problems

    mandate = _read(args.file)
    if not isinstance(mandate, Mandate):
        _fail(f"{args.file} is no mandate")
    problems: list[str] = []
    if args.charter is not None:
        charter = _read(args.charter)
        if not isinstance(charter, Charter):
            _fail(f"{args.charter} is no charter")
        problems += [f"charter: {p}" for p in mandate.charter_problems(charter)]
    if args.parent is not None:
        parent = _read(args.parent)
        if not isinstance(parent, Mandate):
            _fail(f"{args.parent} is no mandate")
        problems += [str(p) for p in delegation_problems(mandate, parent)]
    elif mandate.parent is not None:
        _err("the mandate names a parent: give --parent to check it refines it")
    for problem in problems:
        _out(problem)
    if problems:
        return REFUSED
    _out(
        f"mandate {mandate.mandate_id} version {mandate.version}: well formed"
        + (", refines its parent" if args.parent else "")
    )
    return OK


def _mandate_diff(args: argparse.Namespace) -> int:
    from fiduciary.refines import refines

    first, second = _read(args.first), _read(args.second)
    if not isinstance(first, Mandate) or not isinstance(second, Mandate):
        _fail("diff compares two mandates")
    wider = refines(second, first)
    narrower = refines(first, second)
    _out(
        f"{args.second} is wider than {args.first} at:"
        if wider
        else f"{args.second} refines {args.first}"
    )
    for problem in wider:
        _out(f"  {problem}")
    _out(
        f"{args.first} is wider than {args.second} at:"
        if narrower
        else f"{args.first} refines {args.second}"
    )
    for problem in narrower:
        _out(f"  {problem}")
    return OK


def _tool_digest(args: argparse.Namespace) -> int:
    from fiduciary.config import ConfigError, load_tool

    try:
        document = load_tool(args.target)
    except ConfigError as exc:
        _fail(str(exc))
    _out(document.digest)
    _err(f'pin it in a mandate as {{"name": "{document.name}", "digest": "{document.digest}"}}')
    return OK


def _approve(args: argparse.Namespace) -> int:
    document = _read(args.file, signed=True)
    resolution = None
    if args.suspend is not None:
        if not isinstance(document, Charter):
            _fail("--suspend names a principal of the charter in force: give that charter")
        principal = document.principal(args.suspend)
        if principal is None:
            _fail(f"{args.suspend!r} is no principal of that charter")
        subject = {
            "type": "suspension",
            "digest": suspension_digest(document.charter_id, principal.name, principal.key),
        }
        charter_ref = {"charter_id": document.charter_id, "version": document.version}
        what = f"suspend {principal.name}'s key {principal.key}"
    elif isinstance(document, Charter):
        version = document.version if document.version == 1 else document.version - 1
        subject = {"type": "charter", "digest": document.digest}
        charter_ref = {"charter_id": document.charter_id, "version": version}
        what = f"adopt charter {document.charter_id} version {document.version}"
    elif isinstance(document, Mandate):
        subject = {"type": "revocation" if args.revoke else "mandate", "digest": document.digest}
        charter_ref = {
            "charter_id": document.charter.charter_id,
            "version": document.charter.version,
        }
        what = (
            "revoke" if args.revoke else "issue"
        ) + f" mandate {document.mandate_id} version {document.version}"
    elif isinstance(document, Ticket):
        charter_ref = _charter_in_force(args)
        resolution, shown = _ticket_resolution(document, args)
        subject = {"type": "ticket", "digest": document.digest}
        what = f"ticket {document.ticket_id} ({document.ticket_class}): {shown}"
    else:
        _fail(f"{args.file} is a {document.TYPE}; approvals decide charters, mandates and tickets")
    decision = "decline" if args.decline else "approve"
    # What you see is what you sign (G8, F7-3): the document, from the bytes read, then its digest.
    if isinstance(document, Ticket):
        _err(render_ticket(document))
    else:
        _err(document.pretty())
    _err(f"{decision}: {what}; digest {subject['digest']}")
    if not args.yes:
        if not sys.stdin.isatty():
            _fail("refusing to sign without reading: pass --yes after reading the document")
        if input("sign? [y/N] ").strip().lower() != "y":
            _err("nothing signed")
            return REFUSED
    now = _now()
    draft = {
        "v": "FIDU1",
        "type": "approval",
        "approval_id": str(uuid.uuid4()),
        "charter": charter_ref,
        "principal": args.principal,
        "subject": subject,
        "resolution": resolution,
        "decision": decision,
        "issued_at": fidu1.format_instant(now),
        "not_after": fidu1.format_instant(now + timedelta(seconds=args.ttl)),
    }
    try:
        approval = Approval.from_json(draft, signed=False).signed(_signer(args))
    except FiduciaryError as exc:
        _fail(str(exc))
    _write(approval, args.out)
    if args.submit:
        config = _config(args)
        submitted = _opened(config.open_store()).submit(approval.canonical())
        _err(f"submitted as intake {submitted}")
    return OK


def _submit(args: argparse.Namespace) -> int:
    approval = _read(args.file, signed=True)
    if not isinstance(approval, Approval):
        _fail(f"{args.file} is no approval")
    config = _config(args)
    try:
        submitted = _opened(config.open_store()).submit(approval.canonical())
    except FiduciaryError as exc:
        _fail(str(exc), REFUSED)
    _out(f"intake {submitted}")
    return OK


def _verify(args: argparse.Namespace) -> int:
    from fiduciary.verify import verify_store

    config = _config(args)
    if args.service_key is not None:
        try:
            key = fidu1.key_of(args.service_key)
        except FiduciaryError as exc:
            _fail(f"--service-key: {exc}")
    else:
        try:
            key = config.service().public_key()
        except (OSError, ValueError, FiduciaryError) as exc:
            _fail(f"cannot read the service key: {exc}")
    store = _opened(config.open_store())
    ledger = _opened(config.open_ledger(read_only=True))
    entries = None
    if ledger is not None:
        ledger.refresh()
        entries = ledger.audit_trail()
    skew = config.skew_seconds if args.skew is None else args.skew
    report = verify_store(
        store,
        service_key=key,
        pinned=config.pinned(),
        ledger=entries,
        skew_seconds=skew if entries is not None else None,
    )
    for problem in report.problems:
        _out(f"FAIL {problem}")
    anchored = f", {report.anchored} anchored" if entries is not None else ", no ledger given"
    status = "PASS" if report.ok else "FAIL"
    _out(f"{status}: {report.records} records, {report.documents} documents{anchored}")
    return OK if report.ok else REFUSED


def _charter_in_force(args: argparse.Namespace) -> dict[str, Any]:
    """The charter an approval names: ``--charter``'s file, which needs no
    database credential, or else the one the configured store has in force."""
    if args.charter is not None:
        found = _read(args.charter, signed=True)
        if not isinstance(found, Charter):
            _fail(f"{args.charter} is a {found.TYPE}, not a charter")
        return {"charter_id": found.charter_id, "version": found.version}
    from fiduciary.log import LOG_ID
    from fiduciary.store import State

    try:
        store = _opened(_config(args).open_store())
    except (OSError, ValueError, FiduciaryError) as exc:
        _fail(f"{exc}; or name the charter in force with --charter")
    state = State.of([Record.loads(r.data) for r in store.records(LOG_ID)])
    if state.charter is None:
        _fail("the store holds no adopted charter: name it with --charter")
    version, digest = state.charter
    charter_raw = store.document(digest)
    assert charter_raw is not None
    found = loads(charter_raw[1], signed=True)
    assert isinstance(found, Charter)
    return {"charter_id": found.charter_id, "version": version}


def _ticket_resolution(ticket: Ticket, args: argparse.Namespace) -> tuple[str, str]:
    """The resolution digest an approval of ``ticket`` names, and how it is
    shown: an option by action or digest, or a narrowing of one read from a
    file and rendered (F4-20, G8). A digest alone is never signed."""
    from fiduciary.documents import Resolution

    chosen = args.resolution
    if chosen is None:
        if len(ticket.options) == 1 or args.decline:
            option = ticket.options[0]
            return option.digest, f"{option.resolution.action} {option.digest}"
        _fail("approving a ticket names its resolution: --resolution ACTION|DIGEST|FILE")
    for option in ticket.options:
        if chosen in (option.digest, option.resolution.action):
            return option.digest, f"{option.resolution.action} {option.digest}"
    if not Path(chosen).is_file():
        _fail(f"{chosen!r} is no option of the ticket, nor a file holding a narrowing of one")
    try:
        narrowed = Resolution.read(fidu1.parse(Path(chosen).read_bytes()))
    except (OSError, FiduciaryError) as exc:
        _fail(f"{chosen}: {exc}")
    if ticket.option_for(narrowed) is None:
        _fail(f"{chosen} narrows no option of the ticket (F4-20)")
    shown = json.dumps(narrowed.to_json(), sort_keys=True)
    return narrowed.digest, f"the narrowing {shown} {narrowed.digest}"


def render_ticket(ticket: Ticket) -> str:
    """Render ``ticket`` from its canonical bytes in a fixed template, with
    ``agent.statement`` quarantined apart as the agent's unverified claim (F7-3, G8)."""
    canonical = ticket.canonical()
    reparsed = Ticket.loads(canonical, signed=ticket.signature is not None)
    assert reparsed.digest == ticket.digest
    lines = [
        f"Ticket:   {reparsed.ticket_id}",
        f"Digest:   {reparsed.digest}",
        f"Class:    {reparsed.ticket_class}",
        (
            f"Mandate:  {reparsed.mandate.mandate_id} version {reparsed.mandate.version} "
            f"({reparsed.mandate.digest})"
        ),
        f"Raised:   {fidu1.format_instant(reparsed.raised_at)}",
        f"Expires:  {fidu1.format_instant(reparsed.not_after)}",
        (
            f"Observed: ledger #{reparsed.observed.ledger.sequence} "
            f"({reparsed.observed.ledger.head[:16]}), "
            f"log #{reparsed.observed.log.seq} ({reparsed.observed.log.hash[:16]})"
        ),
        "",
        f"Evidence ({reparsed.ticket_class}):",
    ]
    ev = reparsed.evidence
    if isinstance(ev, RefusedEvidence):
        lines.append(f"  refused receipt: {ev.receipt.receipt_id} ({ev.receipt.digest[:16]})")
        lines.append(f"  blocked by:      {', '.join(ev.blocked_by)}")
        lines.append(f"  proposal hash:   {ev.proposal}")
        lines.append(f"  kept effects:    {', '.join(ev.kept)}")
        for d in ev.dropped:
            lines.append(f"  dropped effect:  {d.effect_id} ({d.cause})")
        lines.append(f"  exhaustive:      {ev.exhaustive} (trials: {ev.trials})")
    elif isinstance(ev, HeldEvidence):
        if ev.halt is not None:
            lines.append(
                f"  halting scope:   {ev.halt.scope} (spent_out={ev.halt.spent_out}, "
                f"reason={ev.halt.reason!r})"
            )
        else:
            lines.append("  halting scope:   (cleared)")
        for m in ev.messages:
            lines.append(
                f"  held message:    {m.message_id} sink={m.sink} op={m.operation} "
                f"head={m.head[:16]} payload={m.payload_hash[:16]}"
            )
    elif isinstance(ev, DeadEvidence):
        dm = ev.message
        lines.append(
            f"  dead message:    {dm.message_id} outcome={dm.outcome} "
            f"attempts={dm.attempts} requeues={dm.requeues} head={dm.head[:16]}"
        )
    elif isinstance(ev, UndoEvidence):
        lines.append(f"  plan_id:         {ev.plan_id} (late={ev.late})")
        for um in ev.messages:
            lines.append(
                f"  original:        {um.message_id} comp_op={um.operation} "
                f"not_after={fidu1.format_instant(um.not_after)} head={um.head[:16]}"
            )
    elif isinstance(ev, ExtensionEvidence):
        q = ev.quote
        lines.append(
            f"  quote:           {q.quote_id} ({q.record_log}#{q.record_seq} {q.record_hash[:16]})"
        )
        lines.append(
            f"  amounts:         needed={q.needed} requested={q.requested} "
            f"spent={q.spent} available={q.available} estimate={q.estimate}"
        )
        lines.append(f"  quote expires:   {fidu1.format_instant(q.expires_at)}")
        lines.append(
            f"  progress:        committed={ev.progress.committed} rows={ev.progress.rows}"
        )
    elif isinstance(ev, AmendEvidence):
        lines.append(f"  proposed:        version {ev.proposed.version} ({ev.proposed.digest})")
        for item in ev.wider:
            lines.append(f"  wider:           {item.path}: {item.message}")
        for item in ev.narrower:
            lines.append(f"  narrower:        {item.path}: {item.message}")

    lines.append("")
    lines.append("Options:")
    for opt in reparsed.options:
        lines.append(
            f"  - {opt.resolution.action} (quorum={opt.quorum}, digest={opt.digest}): "
            f"{json.dumps(opt.resolution.to_json(), sort_keys=True)}"
        )

    lines.append("")
    if reparsed.statement is None:
        lines.append("Agent statement (unverified claim): (none)")
    else:
        lines.append(
            "--- AGENT STATEMENT (UNVERIFIED CLAIM — QUARANTINED; DO NOT TRUST AS EVIDENCE) ---"
        )
        lines.append(reparsed.statement)
        lines.append("--- END UNVERIFIED AGENT STATEMENT ---")
    return "\n".join(lines)


def _store_and_state(args: argparse.Namespace) -> tuple[Any, Any]:
    from fiduciary.log import LOG_ID
    from fiduciary.store import State

    config = _config(args)
    try:
        store = _opened(config.open_store())
    except (OSError, ValueError, FiduciaryError) as exc:
        _fail(str(exc))
    state = State.of([Record.loads(r.data) for r in store.records(LOG_ID)])
    return store, state


def _tickets_list(args: argparse.Namespace) -> int:
    _, state = _store_and_state(args)
    if args.all:
        rows = sorted(state.tickets.values(), key=lambda r: r.seq)
        if args.mandate is not None:
            rows = [r for r in rows if r.mandate_id == args.mandate]
    else:
        rows = state.open_tickets(args.mandate)
    if not rows:
        _out("no tickets" if args.all else "no open tickets")
        return OK
    for row in rows:
        status = state.ticket_status(row.ticket_id) or "open"
        _out(f"{row.ticket_id}  {status}  {row.ticket_class}  {row.mandate_id}  {row.digest}")
    return OK


def _tickets_show(args: argparse.Namespace) -> int:
    target = args.target
    if Path(target).exists():
        doc = _read(target, signed=True)
    else:
        store, state = _store_and_state(args)
        row = state.tickets.get(target)
        digest = row.digest if row is not None else target
        found = store.document(digest)
        if found is None:
            _fail(f"the store holds no ticket {target}", REFUSED)
        doc = loads(found[1], signed=True)
    if not isinstance(doc, Ticket):
        _fail(f"{target} is a {doc.TYPE}, not a ticket")
    _out(render_ticket(doc))
    signer = doc.signature.key_id if doc.signature else "unsigned"
    _err(f"ticket {doc.digest} (signed by key {signer})")
    return OK


def _warrant_show(args: argparse.Namespace) -> int:
    target = args.target
    if Path(target).exists():
        doc = _read(target, signed=True)
    else:
        store, state = _store_and_state(args)
        digest = state.warrants_by_id.get(target, target)
        found = store.document(digest)
        if found is None:
            _fail(f"the store holds no warrant {target}", REFUSED)
        doc = loads(found[1], signed=True)
    if not isinstance(doc, Warrant):
        _fail(f"{target} is a {doc.TYPE}, not a warrant")
    _out(doc.pretty())
    signer = doc.signature.key_id if doc.signature else "unsigned"
    _err(f"warrant {doc.digest} (signed by key {signer})")
    return OK


def _resolve_cmd(args: argparse.Namespace) -> int:
    from fiduciary.executor import Executor

    config = _config(args)
    binder = _binder(args)
    try:
        exec_signer = config.executor()
        run_signer = config.runtime()
        operator_log = config.operators_log()
        keyring = config.operator_keyring()
    except (OSError, ValueError, FiduciaryError) as exc:
        _fail(str(exc), REFUSED)
    executor = Executor(  # reads both ILOK1 logs without claiming them
        binder,
        operator_log=operator_log,
        executor_signer=exec_signer,
        operator_keyring=keyring,
        runtime_records=config.runtime_records,
        runtime_signer=run_signer,
    )
    return _records(executor.resolve, "no open execution intents")


def _bridge(args: argparse.Namespace) -> tuple[Any, Any]:
    """Fiduciary from the configuration, and the session the agent gets."""
    from fiduciary.bridge import Fiduciary
    from fiduciary.config import ConfigError

    path = Path(args.config)
    if not path.exists():
        _fail(f"{path}: no such configuration (give --config)")
    try:
        fid = _opened(Fiduciary.from_config(path, bridged=True))
    except (ConfigError, OSError, ValueError) as exc:
        _fail(str(exc))
    session = fid.session(
        args.mandate, tenant=args.tenant, trajectory=args.trajectory, bridged=True
    )
    _OPENED.insert(1, session.close)  # closed before the parts it records into
    return fid, session


def _mcp_serve(args: argparse.Namespace) -> int:
    from fiduciary.mcp import HttpBridge, McpServer, serve_stdio

    fid, session = _bridge(args)
    if args.http is None:
        serve_stdio(
            McpServer(session, lock=fid.lock),
            sys.stdin.buffer,
            sys.stdout.buffer,
            service=fid.serve_once,
            lock=fid.lock,
        )
        return OK
    from fiduciary.config import _listen

    where = _listen(args.http)
    if where is None:
        _fail("--http is HOST:PORT")
    tls = None
    if args.tls_cert is not None or args.tls_key is not None:
        import ssl

        if args.tls_cert is None or args.tls_key is None:
            _fail("TLS needs --tls-cert and --tls-key")
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(args.tls_cert, args.tls_key)
    bridge = HttpBridge(where[0], where[1], tls=tls, service=fid.serve_once, lock=fid.lock)
    token = bridge.add(session)
    host, port = bridge.address
    if args.token_file is not None:
        target = Path(args.token_file)
        target.touch(mode=0o600, exist_ok=False)
        target.write_text(token + "\n", encoding="utf-8")
        _err(f"session {session.session_id}: token written to {target}")
    else:
        _err(f"session {session.session_id}: bearer token {token}")
    scheme = "https" if tls is not None else "http"
    _err(f"serving {scheme}://{host}:{port}/mcp")
    import signal

    signal.signal(signal.SIGTERM, lambda *_: bridge.stop())
    try:
        bridge.serve_forever()
    except KeyboardInterrupt:
        bridge.stop()
    return OK


def _mcp_proxy(args: argparse.Namespace) -> int:
    from fiduciary.mcp import McpServer, Upstream, proxy_handlers, serve_stdio

    command = list(args.upstream)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        _fail("give the upstream server's command after --")
    fid, session = _bridge(args)
    upstream = Upstream(command)
    _OPENED.insert(0, upstream.close)
    for name, handler in proxy_handlers(fid, upstream).items():
        fid.register(name, handler)
    serve_stdio(
        McpServer(session, lock=fid.lock),
        sys.stdin.buffer,
        sys.stdout.buffer,
        service=fid.serve_once,
        lock=fid.lock,
    )
    return OK


def _serve(args: argparse.Namespace) -> int:
    """``fiduciary serve``: the supervised service, until ``SIGTERM`` or
    ``SIGINT``; ``SIGHUP`` opens its keys again."""
    import asyncio
    import logging
    import threading

    from fiduciary.config import ConfigError
    from fiduciary.service import build_service

    path = Path(args.config)
    if not path.exists():
        _fail(f"{path}: no such configuration (give --config)")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        supervisor = build_service(
            str(path), node=args.node, bridge=args.bridge, metrics=args.metrics
        )
    except (ConfigError, OSError, ValueError, FiduciaryError) as exc:
        _fail(str(exc))

    def announce() -> None:
        while not supervisor.wait_finished(0.05):
            port = supervisor.metrics_port
            if port is not None:
                _err(f"fiduciary serve: metrics on http://127.0.0.1:{port}/metrics")
                return

    threading.Thread(target=announce, daemon=True).start()
    try:
        asyncio.run(supervisor.run(handle_signals=True))
    except Exception as exc:  # a node already running, a database out of reach
        _fail(f"fiduciary serve stopped: {type(exc).__name__}: {exc}", REFUSED)
    return OK


def _export_bundle(args: argparse.Namespace) -> int:
    from agentgov.receipts import ReceiptLog
    from interlock.records import Keyring, RecordLog

    from fiduciary.bundle import BundleError, Evidence, export_bundle
    from fiduciary.bundle.export import spec
    from fiduciary.config import ConfigError, _outbox
    from fiduciary.keys import open_signer

    config = _config(args)
    target = Path(args.out)
    if target.exists():
        _fail(f"{args.out} exists: a bundle is never overwritten")
    if config.ledger is None:
        _fail("a bundle needs the deployment's AgentGov ledger: set ledger")
    if args.ledger_key is None and args.ledger_signer is None:
        _fail("the ledger checkpoint is signed by the ledger's checkpoint key: give --ledger-key")
    opened: list[Callable[[], None]] = []
    try:
        ledger_signer = open_signer(config.signers, key=args.ledger_key, signer=args.ledger_signer)
        service = config.service()
        store = _opened(config.open_store())
        governor = _opened(config.open_ledger(read_only=True))
        deployment = config.deployment(governor)
        receipts = receipt_keys = None
        if config.receipts_log is not None:
            signer = config.receipts()
            receipts = _opened(ReceiptLog("fiduciary-receipts", signer, path=config.receipts_log))
            receipt_keys = [signer.public_key()]
        keyring = config.operator_keyring()
        executor = config.executor().public_key()
        operator_keys = {"executor": spec(executor)}
        if config.interlock is not None and config.interlock_config().operators is not None:
            operators = config.interlock_config().operators
            assert operators is not None
            operator_keys = dict(operators.keys)
        operator_records: tuple[Any, ...] = ()
        log = config.operators_log()
        if log is not None and log.exists():
            operator_records = RecordLog.load(log, keyring or Keyring({executor.key_id: executor}))
        runtime_key = config.runtime().public_key()
        runtime_records: tuple[Any, ...] = ()
        if config.runtime_records is not None and config.runtime_records.exists():
            runtime_records = RecordLog.load(config.runtime_records, runtime_key)
        outbox = None
        if config.executor_outbox is not None:
            outbox = _outbox(config.executor_outbox, opened)
        data = export_bundle(
            store,
            args.mandate,
            deployment=deployment,
            evidence=Evidence(
                ledger=governor,
                ledger_signer=ledger_signer,
                receipts=receipts,
                receipt_keys=receipt_keys or (),
                operator_records=operator_records,
                operator_keys=operator_keys,
                runtime_records=runtime_records,
                runtime_key=runtime_key,
                chains=config.chains,
                outbox=outbox,
                service=service,
            ),
            from_seq=args.from_seq,
            to_seq=args.to_seq,
            sealed=args.sealed,
        )
    except (ConfigError, BundleError, OSError, ValueError) as exc:
        _fail(str(exc), REFUSED if isinstance(exc, BundleError) else USAGE)
    finally:
        for close in reversed(opened):
            close()
    target.write_bytes(data)
    _err(f"wrote {args.out}: {len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()}")
    return OK


def _verify_bundle(args: argparse.Namespace) -> int:
    """§9.3: exit 0 passes; 1 usage; 2 unreadable; 3 to 9 the first failing
    check's class. Needs no database and no network (F9-3)."""
    from fiduciary.bundle import BundleError, TrustedKeys, verify_bundle

    if not fidu1.HASH.fullmatch(args.charter):
        _err("fiduciary: --charter is the pinned digest: 64 lowercase hex characters")
        return 1
    kind = _report_kind(args.report) if args.report is not None else None
    if args.report is not None and kind is None:
        _err("fiduciary: --report writes a .html or a .md file")
        return 1
    try:
        data = Path(args.bundle).read_bytes()
        keys = None
        if args.keys is not None:
            keys = TrustedKeys.loads(Path(args.keys).read_bytes())
        witness = fidu1.key_of(args.witness) if args.witness is not None else None
    except (OSError, FiduciaryError) as exc:
        _err(f"fiduciary: {exc}")
        return 2 if isinstance(exc, OSError) else 1
    try:
        report = verify_bundle(
            data, charter=args.charter, keys=keys, witness=witness, skew_seconds=args.skew
        )
    except BundleError as exc:
        _err(f"fiduciary: {exc}")
        return 2
    if args.json or args.report is not None:
        from fiduciary import report as reports
        from fiduciary.attest import attest

        pinned = sorted(keys.raw) if keys is not None else []
        attested = attest(
            data,
            report,
            charter=args.charter,
            pinned=pinned,
            witness=witness is not None,
            skew_seconds=args.skew,
        )
        if args.report is not None:
            page = reports.payload({Path(args.bundle).name: attested})
            text = reports.html(page) if kind == "html" else reports.markdown(page)
            try:
                Path(args.report).write_text(text, encoding="utf-8")
            except OSError as exc:
                _err(f"fiduciary: --report: {exc}")
                return 1
            _err(f"wrote {args.report}")
        if args.json:
            _out(json.dumps(attested, indent=2, sort_keys=True))
            return report.exit_code
    _out(report.render())
    _out("PASS" if report.passed else f"FAIL: exit {report.exit_code}")
    return report.exit_code


def _report_kind(path: str) -> str | None:
    suffix = Path(path).suffix.lower()
    if suffix in (".html", ".htm"):
        return "html"
    if suffix in (".md", ".markdown"):
        return "md"
    return None


# --------------------------------------------------------------------------
# the demo
# --------------------------------------------------------------------------


def _demo(args: argparse.Namespace) -> int:
    """§10.6: the whole stack, offline, in one command. 0 when every act
    showed what it shows; 1 when one did not; 2 on a usage error."""
    import tempfile

    from agentgov.exceptions import SignerUnavailableError
    from agentgov.receipts.signing import Ed25519Signer

    from fiduciary import demo

    if args.report is not None and not Path(args.report).is_dir():
        if _report_kind(args.report) is None:
            _fail("--report is a directory, or a .html or .md file")
    try:
        Ed25519Signer.generate()
    except SignerUnavailableError:
        _fail(
            "the demo signs as every party of its deployment does, which needs the "
            "cryptography package interlock-fiduciary installs: reinstall it with its "
            "dependencies"
        )
    live = None
    if args.live:
        live = _live_model(args)
    if args.out is not None:
        root = Path(args.out).resolve()
        if root.exists() and any(root.iterdir()):
            _fail(f"{root} is not empty: the demo writes a fresh deployment there")
    else:
        root = Path(tempfile.mkdtemp(prefix="fiduciary-demo-"))
    say = (lambda _: None) if args.quiet else _out
    say(f"fiduciary demo: a deployment in {root}")
    say("  an input gateway (Cedar) and the stack (AgentGov, Interlock, Fiduciary), same calls")
    try:
        outcome = demo.run(root, live=live, engine=args.gateway, say=say, domain=args.domain)
    except ImportError as exc:
        _fail(f"{exc}: pip install 'interlock-fiduciary[demo]' for the Cedar engine")
    data = outcome.payload
    if args.report is not None:
        from fiduciary import report as reports

        target = Path(args.report)
        if target.is_dir():
            target = target / "dashboard.html"
        kind = _report_kind(str(target))
        target.write_text(
            reports.html(data) if kind == "html" else reports.markdown(data), encoding="utf-8"
        )
        say(f"wrote {target}")
    say("")
    say(f"dashboard   {outcome.dashboard}")
    say(f"bundles     {root / 'before.fidb'}, {root / 'after.fidb'}")
    say(
        f"verify      fiduciary verify-bundle {root / 'after.fidb'} --charter "
        f"$(cat {root / 'charter.digest'}) --keys {root / 'pinned-keys.json'}"
    )
    say(f"explore     fiduciary --config {root / 'fiduciary.toml'} tickets list --all")
    for page in data.get("domains", []):
        profile = page["demo"]["domain"]
        say(f"{profile['key']:<11} {root / profile['key']} ({profile['industry']})")
    if data["demo"]["model"]:
        model = data["demo"]["model"]
        say(f"live        {model['served']}: {model['turns']} turn(s), ${model['metered']} metered")
    url = outcome.dashboard.resolve().as_uri()
    server = None
    if args.serve is not None:
        server, url = _serve_dashboard(root, args.serve)
        say(f"serving     {url} (Ctrl-C stops)")
    if args.open:
        import webbrowser

        webbrowser.open(url)
    if server is not None:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    return OK if outcome.held else REFUSED


def _live_model(args: argparse.Namespace) -> Any:
    """The model for act 1: ``BENCHMARK_API_KEY`` from the environment or
    ``--env-file``, never ``ANTHROPIC_API_KEY`` (spending money is deliberate)."""
    import importlib
    import os
    from decimal import Decimal, InvalidOperation

    from fiduciary.demo import Live
    from fiduciary.demo.world import PRICING

    key = os.environ.get("BENCHMARK_API_KEY")
    if not key and args.env_file is not None:
        try:
            lines = Path(args.env_file).read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            _fail(f"--env-file: {exc}")
        for line in lines:
            name, sep, value = line.strip().removeprefix("export ").partition("=")
            if sep and name.strip() == "BENCHMARK_API_KEY":
                key = value.strip().strip("'\"") or None
    if not key:
        _fail(
            "--live needs BENCHMARK_API_KEY (in the environment, or --env-file); it never "
            "reads ANTHROPIC_API_KEY: spending money is deliberate"
        )
    if args.model not in PRICING:
        _fail(f"no rate is configured for {args.model}: it is never called unmetered")
    try:
        cap = Decimal(args.max_cost)
    except InvalidOperation:
        _fail("--max-cost is a dollar amount")
    try:
        anthropic: Any = importlib.import_module("anthropic")
    except ImportError:
        _fail("--live needs the anthropic package: pip install anthropic")
    client = anthropic.Anthropic(api_key=key, max_retries=3, timeout=180.0)
    return Live(client, args.model, effort=args.effort, max_cost=cap)


def _serve_dashboard(root: Path, where: str) -> tuple[Any, str]:
    """A loopback HTTP server over the demo's directory."""
    import functools
    import http.server

    host, _, port = (where or "127.0.0.1:8740").rpartition(":")
    host = host or "127.0.0.1"
    if host not in ("127.0.0.1", "localhost", "::1"):
        _fail("--serve listens on loopback only: 127.0.0.1:<port>")
    try:
        number = int(port)
    except ValueError:
        _fail("--serve is HOST:PORT")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    try:
        server = http.server.ThreadingHTTPServer((host, number), handler)
    except OSError as exc:
        _fail(f"--serve: {exc}")
    return server, f"http://{host}:{server.server_address[1]}/dashboard.html"


# --------------------------------------------------------------------------
# the parser
# --------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fiduciary",
        description="Mandates, warrants and compliance proofs, over interlock and AgentGov.",
    )
    parser.add_argument("--version", action="version", version=f"fiduciary {__version__}")
    parser.add_argument(
        "--config", default="fiduciary.toml", help="the configuration (fiduciary.toml)"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    keygen = commands.add_parser("keygen", help="write a new Ed25519 key; print its public half")
    keygen.add_argument("path")
    keygen.set_defaults(handler=_keygen)

    install = commands.add_parser("install", help="create the store, and the ledger's anchor scope")
    install.set_defaults(handler=_install)

    sign = commands.add_parser("sign", help="sign a draft document")
    sign.add_argument("file")
    sign.add_argument("--out")
    _signing(sign)
    sign.set_defaults(handler=_sign)

    charter = commands.add_parser("charter", help="the root of authority").add_subparsers(
        dest="action", required=True
    )
    init = charter.add_parser("init", help="write a charter draft")
    init.add_argument("--principal", action="append", required=True, metavar="NAME=KEY")
    init.add_argument("--quorums", required=True, metavar="CHARTER,ISSUE,REVOKE")
    init.add_argument("--ledger", required=True, help="the entry_hash of AgentGov entry 1")
    init.add_argument("--label", required=True)
    init.add_argument("--break-glass", action="append", default=[], metavar="KEY_ID")
    init.add_argument("--charter-id")
    init.add_argument("--out")
    init.set_defaults(handler=_charter_init)
    show = charter.add_parser("show", help="show a charter, from a file or the store")
    show.add_argument("target", metavar="FILE|DIGEST")
    show.set_defaults(handler=_show)
    propose = charter.add_parser(
        "propose", help="store a signed charter so approvals can reference it"
    )
    propose.add_argument("file")
    propose.set_defaults(handler=lambda a: _propose(a, Charter))
    adopt = charter.add_parser("adopt", help="adopt a charter version approved by its quorum")
    adopt.add_argument("digest")
    adopt.set_defaults(handler=_charter_adopt)
    suspend = charter.add_parser(
        "suspend", help="suspend a principal's key, once its revocation quorum approves"
    )
    suspend.add_argument("principal")
    suspend.set_defaults(handler=_charter_suspend)

    mandate = commands.add_parser("mandate", help="mandates").add_subparsers(
        dest="action", required=True
    )
    draft = mandate.add_parser("draft", help="write a mandate draft")
    draft.add_argument("--charter", required=True, help="the charter in force")
    draft.add_argument("--scope", required=True)
    draft.add_argument("--funded-from")
    draft.add_argument("--label", required=True)
    draft.add_argument("--parent", help="derive a child of this mandate")
    draft.add_argument("--out")
    draft.set_defaults(handler=_mandate_draft)
    check = mandate.add_parser(
        "check", help="check that a mandate is well formed and refines its parent"
    )
    check.add_argument("file")
    check.add_argument("--parent")
    check.add_argument("--charter")
    check.set_defaults(handler=_mandate_check)
    diff = mandate.add_parser(
        "diff", help="show where each of two mandates is wider than the other"
    )
    diff.add_argument("first")
    diff.add_argument("second")
    diff.set_defaults(handler=_mandate_diff)
    show = mandate.add_parser("show", help="show a mandate, from a file or the store")
    show.add_argument("target", metavar="FILE|DIGEST")
    show.set_defaults(handler=_show)
    propose = mandate.add_parser(
        "propose", help="store a signed mandate so approvals can reference it"
    )
    propose.add_argument("file")
    propose.set_defaults(handler=lambda a: _propose(a, Mandate))
    issue = mandate.add_parser(
        "issue", help="issue a mandate approved by its quorum, or a derived child"
    )
    issue.add_argument("digest")
    issue.set_defaults(handler=_mandate_issue)
    compile_ = mandate.add_parser(
        "compile", help="print what a mandate compiles to against the deployment, and its digest"
    )
    compile_.add_argument("target", metavar="FILE|DIGEST")
    compile_.set_defaults(handler=_mandate_compile)
    bind = mandate.add_parser(
        "bind", help="bind an issued mandate to the deployment, or rebind its lineage to it"
    )
    bind.add_argument("digest")
    bind.set_defaults(handler=_mandate_bind)
    derive = mandate.add_parser(
        "derive", help="sign a derived child with the service key and issue it (no approvals)"
    )
    derive.add_argument("file", help="the unsigned draft (fiduciary mandate draft --parent)")
    derive.add_argument("--bind", action="store_true", help="and bind it")
    derive.set_defaults(handler=_mandate_derive)
    amend = mandate.add_parser(
        "amend",
        help="issue an approved amendment; rebase or suspend descendants, rebind what is bound",
    )
    amend.add_argument("digest", metavar="DIGEST|FILE")
    amend.set_defaults(handler=_mandate_amend)
    revoke = mandate.add_parser(
        "revoke", help="revoke a mandate and its descendants, on a revocation quorum's approvals"
    )
    revoke.add_argument("digest")
    revoke.set_defaults(handler=_mandate_revoke)
    expire = mandate.add_parser("expire", help="expire every lineage whose term has ended")
    expire.set_defaults(handler=_mandate_expire)

    tool = commands.add_parser("tool", help="tool bindings").add_subparsers(
        dest="action", required=True
    )
    digest = tool.add_parser("digest", help="print a binding's digest, for pinning in a mandate")
    digest.add_argument("target", metavar="FILE|MODULE:NAME")
    digest.set_defaults(handler=_tool_digest)

    approve = commands.add_parser(
        "approve",
        help="sign an approval: of a charter, a mandate, a ticket's option, a revocation, or "
        "a suspension",
    )
    approve.add_argument("file", help="the document being approved")
    approve.add_argument("--principal", required=True)
    approve.add_argument("--decline", action="store_true")
    approve.add_argument("--revoke", action="store_true", help="approve revoking the mandate")
    approve.add_argument(
        "--resolution",
        metavar="ACTION|DIGEST|FILE",
        help="a ticket's option, by action or digest, or a file holding a narrowing of one",
    )
    approve.add_argument(
        "--charter",
        metavar="FILE",
        help="the charter in force, for a ticket: no database is read then",
    )
    approve.add_argument(
        "--suspend", metavar="PRINCIPAL", help="approve suspending a principal's key"
    )
    approve.add_argument("--ttl", type=int, default=3600, help="seconds the approval stays valid")
    approve.add_argument("--yes", action="store_true", help="sign without the confirmation prompt")
    approve.add_argument("--out")
    approve.add_argument("--submit", action="store_true", help="put it into the store's intake")
    _signing(approve)
    approve.set_defaults(handler=_approve)

    submit = commands.add_parser("submit", help="put a signed approval into the intake")
    submit.add_argument("file")
    submit.set_defaults(handler=_submit)

    tickets = commands.add_parser("tickets", help="escalated tickets").add_subparsers(
        dest="action", required=True
    )
    t_list = tickets.add_parser("list", help="list open tickets (or all with --all)")
    t_list.add_argument("--mandate", help="filter by mandate_id")
    t_list.add_argument("--all", action="store_true", help="include terminal tickets")
    t_list.set_defaults(handler=_tickets_list)
    t_show = tickets.add_parser(
        "show",
        help="show a ticket, rendered from its signed bytes, with the agent's statement apart",
    )
    t_show.add_argument("target", metavar="FILE|DIGEST|TICKET_ID")
    t_show.set_defaults(handler=_tickets_show)

    warrant = commands.add_parser("warrant", help="formed warrants").add_subparsers(
        dest="action", required=True
    )
    w_show = warrant.add_parser("show", help="show a warrant, from a file or the store")
    w_show.add_argument("target", metavar="FILE|DIGEST|WARRANT_ID")
    w_show.set_defaults(handler=_warrant_show)

    resolve = commands.add_parser(
        "resolve", help="resolve interrupted execution intents from what the target systems hold"
    )
    resolve.set_defaults(handler=_resolve_cmd)

    mcp = commands.add_parser("mcp", help="the MCP bridge").add_subparsers(
        dest="mcp_command", required=True
    )
    for name, helping in (
        ("serve", "serve one session over stdio, or Streamable HTTP with --http"),
        ("proxy", "front an upstream MCP server: only the tools the mandate pins"),
    ):
        sub = mcp.add_parser(name, help=helping)
        sub.add_argument("--mandate", required=True, help="the lineage, or its subject scope")
        sub.add_argument("--tenant", help="the session's tenant")
        sub.add_argument("--trajectory", help="the session's trajectory")
        if name == "serve":
            sub.add_argument("--http", help="HOST:PORT: Streamable HTTP, on loopback or TLS")
            sub.add_argument("--token-file", help="write the session's bearer token here")
            sub.add_argument("--tls-cert", help="the listener's certificate chain (PEM)")
            sub.add_argument("--tls-key", help="the listener's private key (PEM)")
            sub.set_defaults(handler=_mcp_serve)
        else:
            sub.add_argument("upstream", nargs=argparse.REMAINDER, help="-- <command>")
            sub.set_defaults(handler=_mcp_proxy)

    serve = commands.add_parser(
        "serve",
        help="run the service: writer and bridge, triggers, settler, executor and metrics, "
        "alone or as a node of a cluster (§10.7)",
    )
    serve.add_argument("--node", help="this node's name, in a cluster ([cluster])")
    serve.add_argument("--bridge", help="HOST:PORT for the bridge, over [bridge] http")
    serve.add_argument("--metrics", help="HOST:PORT for /metrics and /healthz, over [metrics]")
    serve.set_defaults(handler=_serve)

    export = commands.add_parser("export-bundle", help="write a lineage's audit bundle")
    export.add_argument("--mandate", required=True, help="the lineage's mandate_id")
    export.add_argument("--from", dest="from_seq", type=int, help="the range's first record")
    export.add_argument("--to", dest="to_seq", type=int, help="the range's last record")
    export.add_argument("--out", required=True, help="the bundle file to write (.fidb)")
    export.add_argument("--ledger-key", help="the ledger's checkpoint key file")
    export.add_argument("--ledger-signer", help="the ledger's checkpoint key, a [signers.<name>]")
    export.add_argument(
        "--sealed",
        action="store_true",
        help="one party's bundle: other lineages' records as their place and hash only (fidb/2)",
    )
    export.set_defaults(handler=_export_bundle)

    check = commands.add_parser("verify-bundle", help="verify a bundle offline, V1 to V12")
    check.add_argument("bundle")
    check.add_argument("--charter", required=True, help="the pinned digest of charter version 1")
    check.add_argument("--keys", help="the public keys to pin, in keys.json format")
    check.add_argument("--witness", help="a ledger witness's key, ed25519:<hex>")
    check.add_argument("--skew", type=int, default=5, help="seconds of clock skew (V12)")
    check.add_argument(
        "--json",
        action="store_true",
        help="print the attestation as JSON (for a SIEM or GRC system); same exit code",
    )
    check.add_argument(
        "--report", metavar="PATH", help="also write the attestation report: a .html or .md file"
    )
    check.set_defaults(handler=_verify_bundle)

    demo = commands.add_parser(
        "demo",
        help="run the whole stack offline in seven acts, beside an input gateway, and write "
        "its dashboard (--domain picks the industry)",
    )
    demo.add_argument("--out", help="the directory to write the deployment into (new or empty)")
    demo.add_argument(
        "--domain",
        default="fintech",
        choices=("fintech", "healthcare", "sre", "supply-chain", "saas", "all"),
        help="the industry: fintech (the default), a domain pack on the same engine, or all "
        "of them, each in its own subdirectory, behind one dashboard",
    )
    demo.add_argument(
        "--report", metavar="DIR|FILE", help="also write the dashboard (.html) or report (.md)"
    )
    demo.add_argument(
        "--serve",
        nargs="?",
        const="127.0.0.1:8740",
        metavar="HOST:PORT",
        help="serve the dashboard on loopback (127.0.0.1:8740)",
    )
    demo.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    demo.add_argument(
        "--gateway",
        default="auto",
        choices=("auto", "cedarpy", "builtin"),
        help="the gateway's Cedar engine: cedarpy when installed, or the built-in evaluator",
    )
    demo.add_argument(
        "--live",
        action="store_true",
        help="a Claude model works fintech's act 1 and each pack's act 2 (BENCHMARK_API_KEY; "
        "costs money)",
    )
    demo.add_argument("--model", default="claude-opus-5-5")
    demo.add_argument("--effort", default="medium", choices=("low", "medium", "high"))
    demo.add_argument(
        "--max-cost",
        default="0.50",
        help="stop each live act once its metered spend reaches this",
    )
    demo.add_argument("--env-file", help="read BENCHMARK_API_KEY from this file")
    demo.add_argument("--quiet", action="store_true", help="print nothing but errors")
    demo.set_defaults(handler=_demo)

    verify = commands.add_parser("verify", help="verify the store, its log and its anchors")
    verify.add_argument("--service-key", help="ed25519:<hex>; the configured key's by default")
    verify.add_argument(
        "--skew",
        type=int,
        help="seconds an instant may stray from its anchor's ledger time; [bridge] skew_seconds",
    )
    verify.set_defaults(handler=_verify)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        words = sys.argv[1:] if argv is None else argv
        if exc.code == USAGE and "verify-bundle" in words:
            return 1  # §9.3: a usage error is 1; 2 is a bundle that could not be read
        raise
    try:
        status: int = args.handler(args)
    except _ExitError as exc:
        _err(f"fiduciary: {exc}")
        return exc.status
    except FiduciaryError as exc:
        _err(f"fiduciary: {exc}")
        return REFUSED
    finally:
        while _OPENED:
            _OPENED.pop()()
    return status


def main_entry() -> NoReturn:
    sys.exit(main())

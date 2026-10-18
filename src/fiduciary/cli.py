"""The ``fiduciary`` command (``docs/FIDUCIARY_SPEC.md`` §10.6, Phase 1).

Exit status: 0 on success; 1 when something is refused or does not verify;
2 on a usage, configuration or input error.
"""

from __future__ import annotations

import argparse
import importlib
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
    ToolBinding,
    load,
    loads,
    suspension_digest,
)
from fiduciary.exceptions import FiduciaryError

__all__ = ["main", "main_entry"]

OK, REFUSED, USAGE = 0, 1, 2


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
        store = config.open_store()
        ledger = config.open_ledger()
    except (OSError, ValueError, FiduciaryError) as exc:
        _fail(str(exc))
    log = Log(store, signer, ledger=ledger)
    return config, log, Authority(log, pinned=config.pinned())


def _resolve(target: str, args: argparse.Namespace) -> Document:
    """A document from a file, or from the store by its digest."""
    if fidu1.HASH.fullmatch(target) and not Path(target).exists():
        config = _config(args)
        found = config.open_store().document(target)
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
    ledger = config.open_ledger()
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


def _mandate_draft(args: argparse.Namespace) -> int:
    charter = _read(args.charter)
    if not isinstance(charter, Charter):
        _fail(f"{args.charter} is no charter")
    if args.parent is not None:
        parent = _read(args.parent)
        if not isinstance(parent, Mandate):
            _fail(f"{args.parent} is no mandate")
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
    target: str = args.target
    if Path(target).exists() or ":" not in target:
        document = _read(target)
    else:
        module, _, attribute = target.partition(":")
        try:
            value = getattr(importlib.import_module(module), attribute)
        except (ImportError, AttributeError) as exc:
            _fail(f"{target}: {exc}")
        if isinstance(value, ToolBinding):
            document = value
        else:
            try:
                document = load(value, signed=isinstance(value, dict) and "sig" in value)
            except FiduciaryError as exc:
                _fail(f"{target}: {exc}")
    if not isinstance(document, ToolBinding):
        _fail(f"{target} is no tool binding")
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
    else:
        _fail(
            f"{args.file} is a {document.TYPE}; approvals decide charters and mandates in this "
            f"release"
        )
    decision = "decline" if args.decline else "approve"
    # What you see is what you sign (G8): the document, from the bytes read, then its digest.
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
        submitted = config.open_store().submit(approval.canonical())
        _err(f"submitted as intake {submitted}")
    return OK


def _submit(args: argparse.Namespace) -> int:
    approval = _read(args.file, signed=True)
    if not isinstance(approval, Approval):
        _fail(f"{args.file} is no approval")
    config = _config(args)
    try:
        submitted = config.open_store().submit(approval.canonical())
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
    store = config.open_store()
    ledger = config.open_ledger()
    entries = None
    if ledger is not None:
        ledger.refresh()
        entries = ledger.audit_trail()
    report = verify_store(store, service_key=key, pinned=config.pinned(), ledger=entries)
    for problem in report.problems:
        _out(f"FAIL {problem}")
    anchored = f", {report.anchored} anchored" if entries is not None else ", no ledger given"
    status = "PASS" if report.ok else "FAIL"
    _out(f"{status}: {report.records} records, {report.documents} documents{anchored}")
    return OK if report.ok else REFUSED


# --------------------------------------------------------------------------
# the parser
# --------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fiduciary", description="Signed mandates and quorum approvals."
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
    show = charter.add_parser("show", help="a charter, from a file or the store")
    show.add_argument("target", metavar="FILE|DIGEST")
    show.set_defaults(handler=_show)
    propose = charter.add_parser("propose", help="store a signed charter for approvals to name")
    propose.add_argument("file")
    propose.set_defaults(handler=lambda a: _propose(a, Charter))
    adopt = charter.add_parser("adopt", help="adopt a charter version its quorum approved")
    adopt.add_argument("digest")
    adopt.set_defaults(handler=_charter_adopt)
    suspend = charter.add_parser(
        "suspend", help="suspend a principal's key its revocation quorum approved"
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
    check = mandate.add_parser("check", help="a mandate well formed, and refining its parent")
    check.add_argument("file")
    check.add_argument("--parent")
    check.add_argument("--charter")
    check.set_defaults(handler=_mandate_check)
    diff = mandate.add_parser("diff", help="where each of two mandates is wider than the other")
    diff.add_argument("first")
    diff.add_argument("second")
    diff.set_defaults(handler=_mandate_diff)
    show = mandate.add_parser("show", help="a mandate, from a file or the store")
    show.add_argument("target", metavar="FILE|DIGEST")
    show.set_defaults(handler=_show)
    propose = mandate.add_parser("propose", help="store a signed mandate for approvals to name")
    propose.add_argument("file")
    propose.set_defaults(handler=lambda a: _propose(a, Mandate))
    issue = mandate.add_parser(
        "issue", help="issue a mandate its quorum approved, or a derived child"
    )
    issue.add_argument("digest")
    issue.set_defaults(handler=_mandate_issue)

    tool = commands.add_parser("tool", help="tool bindings").add_subparsers(
        dest="action", required=True
    )
    digest = tool.add_parser("digest", help="a binding's digest, to pin in a mandate")
    digest.add_argument("target", metavar="FILE|MODULE:NAME")
    digest.set_defaults(handler=_tool_digest)

    approve = commands.add_parser("approve", help="sign an approval of a charter or mandate")
    approve.add_argument("file", help="the document decided on")
    approve.add_argument("--principal", required=True)
    approve.add_argument("--decline", action="store_true")
    approve.add_argument("--revoke", action="store_true", help="approve revoking the mandate")
    approve.add_argument(
        "--suspend", metavar="PRINCIPAL", help="approve suspending a principal's key"
    )
    approve.add_argument("--ttl", type=int, default=3600, help="seconds the approval holds")
    approve.add_argument("--yes", action="store_true", help="sign without asking, having read it")
    approve.add_argument("--out")
    approve.add_argument("--submit", action="store_true", help="put it into the store's intake")
    _signing(approve)
    approve.set_defaults(handler=_approve)

    submit = commands.add_parser("submit", help="put a signed approval into the intake")
    submit.add_argument("file")
    submit.set_defaults(handler=_submit)

    verify = commands.add_parser("verify", help="verify the store, its log and its anchors")
    verify.add_argument("--service-key", help="ed25519:<hex>; the configured key's by default")
    verify.set_defaults(handler=_verify)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        status: int = args.handler(args)
    except _ExitError as exc:
        _err(f"fiduciary: {exc}")
        return exc.status
    except FiduciaryError as exc:
        _err(f"fiduciary: {exc}")
        return REFUSED
    return status


def main_entry() -> NoReturn:
    sys.exit(main())

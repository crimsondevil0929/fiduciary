"""``fiduciary conform`` (1.22.0): the chatbot conformance lab's commands.

    fiduciary conform laws [--on DATE] [--laws LIST]
    fiduciary conform run (--endpoint URL | --stand-in NAME | --replay FILE) --out DIR --key FILE
    fiduciary conform verify DIR [--key ed25519:<hex>]
    fiduciary conform demo [--out DIR]
    fiduciary conform stand-in NAME [--port N]

``run`` exits 0 when no clause in force on ``--on`` was missed, 1 when one was, and 3 when the
run could not be made (an endpoint that fails, a recording that does not fit). ``verify`` exits
0 when the folder checks out and 1 when it does not.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any, Final

from fiduciary.conform import evidence, laws, standins
from fiduciary.conform import suite as suites
from fiduciary.conform.targets import (
    ConformError,
    HttpTarget,
    OpenAITarget,
    ReplayTarget,
    StandInTarget,
    Target,
    converse,
    recording,
)
from fiduciary.exceptions import MalformedError

__all__ = ["add", "demo", "run"]

PASSED, MISSED, USAGE, BROKEN = 0, 1, 2, 3
_MARK: Final = {"pass": "met", "fail": "MISSED", "open": "open"}


def _say(text: str = "") -> None:
    print(text)


def _today() -> date:
    return datetime.now(UTC).date()


def _date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{text!r} is not a date (YYYY-MM-DD)") from exc


def _laws(text: str) -> tuple[str, ...]:
    try:
        return laws.jurisdictions(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def recorded(name: str) -> dict[str, Any]:
    """A stand-in's packaged recording (``scripts/record_conform.py`` makes them)."""
    found: dict[str, Any] = json.loads(
        (files("fiduciary.conform") / "recordings" / f"{name}.json").read_text("utf-8")
    )
    return found


def run(
    target: Target,
    out: Path,
    key: Path,
    jurisdictions: tuple[str, ...],
    on: date,
    mode: str = "live",
) -> dict[str, Any]:
    """Run the suite against ``target``, sign the report with the key at ``key`` (made if it
    is not there), write the evidence folder into ``out``: the signed report."""
    suite = suites.suite()
    catalog = laws.catalog()
    made = recording(target, converse(target, suite))
    signer = evidence.load_key(key) if key.exists() else evidence.new_key(key)
    signed = evidence.report(made, suite, catalog, jurisdictions, on, mode, signer)
    evidence.write(out, signed, made, suite, catalog, signer)
    return signed


def _print(signed: dict[str, Any], out: Path, every: bool = False) -> int:
    catalog = laws.catalog()
    clauses = {c["id"]: c for c in catalog["clauses"]}
    on = signed["laws"]["on"]
    now = [v for v in signed["clauses"] if v["in_force"]]
    tally = {word: sum(v["verdict"] == word for v in now) for word in ("pass", "fail", "open")}
    _say(f"{signed['target']['label']}")
    _say(
        f"  {len(now)} {'clause' if len(now) == 1 else 'clauses'} in force on {on}: "
        f"{tally['pass']} met, {tally['fail']} missed, "
        f"{tally['open']} open; {len(signed['clauses']) - len(now)} more not in force that day"
    )
    _say(f"  test clock {'on' if signed['target']['hooks'] else 'off: the time checks are open'}")
    for verdict in signed["clauses"]:
        if verdict["verdict"] == "pass" and not every:
            continue
        clause = clauses[verdict["id"]]
        when = ""
        if not verdict["in_force"]:
            ended = clause["end"] is not None and clause["end"] < on
            when = f"  (ended {clause['end']})" if ended else f"  (from {clause['start']})"
        _say(f"  {_MARK[verdict['verdict']]:<7} {verdict['id']:<24} {clause['duty']}{when}")
    _say(f"  evidence {out}  (check it: fiduciary conform verify {out})")
    return MISSED if tally["fail"] else PASSED


def _run(args: argparse.Namespace) -> int:
    target: Target
    try:
        if args.replay:
            target = ReplayTarget(json.loads(Path(args.replay).read_text(encoding="utf-8")))
        elif args.stand_in:
            target = StandInTarget(args.stand_in, hooks=not args.no_hooks)
        elif args.kind == "http":
            target = HttpTarget(args.endpoint, args.api_key_env, args.hooks, args.label)
        else:
            target = OpenAITarget(
                args.endpoint, args.model, args.api_key_env, args.hooks, args.label
            )
        signed = run(
            target,
            args.out,
            args.key,
            args.laws,
            args.on or _today(),
            "replay" if args.replay else "live",
        )
    except (ConformError, FileExistsError, ValueError, OSError, KeyError) as exc:
        print(f"fiduciary conform: {exc}", file=sys.stderr)
        return BROKEN
    return _print(signed, args.out, args.all)


def _verify(args: argparse.Namespace) -> int:
    found = evidence.verify(args.folder, args.key)
    for note in found.notes:
        _say(f"note: {note}")
    for problem in found.problems:
        _say(f"FAIL: {problem}")
    if found.ok:
        _say(f"PASS: {args.folder} is the run its report says, judged again from its transcripts")
    return PASSED if found.ok else MISSED


def demo(out: Path, on: date | None = None) -> dict[str, dict[str, Any]]:
    """Replay the three stand-ins' recordings into ``out``, signed with a key made there, and
    verify each folder: each stand-in's report."""
    out.mkdir(parents=True, exist_ok=True)
    key = out / "demo.key"
    reports = {}
    for name in standins.BOTS:
        folder = out / name
        reports[name] = run(
            ReplayTarget(recorded(name)),
            folder,
            key,
            laws.jurisdictions("all"),
            on or _today(),
            "replay",
        )
        found = evidence.verify(folder, (folder / "key.pub").read_text(encoding="ascii").strip())
        if not found.ok:  # pragma: no cover - a packaged recording that no longer verifies
            raise ConformError(f"{folder} does not verify: {'; '.join(found.problems)}")
    return reports


def _demo(args: argparse.Namespace) -> int:
    try:
        reports = demo(args.out, args.on)
    except (ConformError, FileExistsError, MalformedError) as exc:
        print(f"fiduciary conform: {exc}", file=sys.stderr)
        return BROKEN
    for name, signed in reports.items():
        _print(signed, args.out / name)
        _say()
    _say("Three stand-ins replayed from their recordings: no model ran, no money was spent.")
    return PASSED


def _laws_list(args: argparse.Namespace) -> int:
    catalog = laws.catalog()
    on = args.on or _today()
    by_law = {law["key"]: law for law in catalog["laws"]}
    for law in catalog["laws"]:
        if law["jurisdiction"] not in args.laws:
            continue
        _say(f"{law['title']}  ({law['cite']})")
        _say(f"  {law['enacted']}")
        _say(f"  enforcement: {law['enforcement']}")
        _say(f"  text: {law['source']}")
        for clause in catalog["clauses"]:
            if by_law[clause["law"]]["key"] != law["key"]:
                continue
            state = "in force" if laws.in_force(clause, on) else "not in force"
            span = f"from {clause['start']}" + (f" to {clause['end']}" if clause["end"] else "")
            _say(f"  - {clause['cite']}: {clause['duty']} [{state} on {on}; {span}]")
        _say()
    return PASSED


def _stand_in(args: argparse.Namespace) -> int:
    server, thread = standins.serve(args.name, args.port)
    port = server.server_address[1]
    _say(f"stand-in {args.name} on http://127.0.0.1:{port}/v1 (Ctrl-C stops it)")
    _say(f"  fiduciary conform run --endpoint http://127.0.0.1:{port}/v1 --hooks \\")
    _say("    --out conform-run --key conform.key")
    try:
        thread.join()
    except KeyboardInterrupt:
        server.shutdown()
    return PASSED


def add(commands: Any) -> None:
    """Add ``conform`` to fiduciary's subcommands."""
    conform = commands.add_parser(
        "conform",
        help="test a chatbot against the US state chatbot laws and EU AI Act article 50: "
        "scripted conversations, signed evidence",
    ).add_subparsers(dest="conform_command", required=True, metavar="COMMAND")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--laws", type=_laws, default=laws.jurisdictions("all"), help="all, or e.g. us-ca,us-ny,eu"
    )
    common.add_argument("--on", type=_date, help="judge the laws as in force on this date")

    listing = conform.add_parser("laws", parents=[common], help="list the clauses checked")
    listing.set_defaults(handler=_laws_list)

    running = conform.add_parser(
        "run", parents=[common], help="run the scripted conversations and sign the evidence"
    )
    where = running.add_mutually_exclusive_group(required=True)
    where.add_argument("--endpoint", help="the chat endpoint users' replies come from")
    where.add_argument("--stand-in", choices=tuple(standins.BOTS), help="a stand-in, in process")
    where.add_argument("--replay", type=Path, metavar="FILE", help="a run's recording.json")
    running.add_argument(
        "--kind", choices=("openai", "http"), default="openai", help="the endpoint's shape"
    )
    running.add_argument("--model", help="the model name an OpenAI-style endpoint expects")
    running.add_argument(
        "--api-key-env", metavar="NAME", help="the environment variable holding its key"
    )
    running.add_argument(
        "--hooks",
        action="store_true",
        help="send the test clock and the account's age (the endpoint must honour them)",
    )
    running.add_argument(
        "--no-hooks", action="store_true", help="a stand-in without the test clock"
    )
    running.add_argument("--label", default="chat endpoint", help="what the report calls it")
    running.add_argument("--all", action="store_true", help="list met clauses too")
    running.add_argument("--out", type=Path, required=True, help="the evidence folder")
    running.add_argument(
        "--key", type=Path, required=True, help="the signing key file (made if missing)"
    )
    running.set_defaults(handler=_run)

    verifying = conform.add_parser("verify", help="check an evidence folder offline")
    verifying.add_argument("folder", type=Path)
    verifying.add_argument("--key", help="ed25519:<hex>, pinned from elsewhere")
    verifying.set_defaults(handler=_verify)

    demoing = conform.add_parser(
        "demo", help="replay three stand-in chatbots from their recordings, at no cost"
    )
    demoing.add_argument("--out", type=Path, default=Path("conform-demo"))
    demoing.add_argument("--on", type=_date, help="judge the laws as in force on this date")
    demoing.set_defaults(handler=_demo)

    serving = conform.add_parser(
        "stand-in", help="serve a stand-in chatbot on 127.0.0.1, OpenAI-style"
    )
    serving.add_argument("name", choices=tuple(standins.BOTS))
    serving.add_argument("--port", type=int, default=8790)
    serving.set_defaults(handler=_stand_in)

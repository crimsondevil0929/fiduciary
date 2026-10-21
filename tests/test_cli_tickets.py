"""The ``tickets``, ``warrant`` and ticket ``approve`` commands, and how each
class of ticket renders (§10.6, F7-3, G8), on SQLite and PostgreSQL."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from fiduciary.cli import main, render_ticket
from fiduciary.documents import Approval, Ticket
from fiduciary.sqlite import SqliteStore
from tests import examples as e
from tests.conftest import need_postgres
from tests.test_tamper_escalation import Escalated, escalated_fixture  # noqa: F401
from tests.test_tickets_warrants import (
    _amend_ticket_json,
    _dead_ticket_json,
    _held_ticket_json,
    _refused_ticket_json,
    _undo_ticket_json,
)


def test_every_class_renders_its_evidence_in_a_fixed_template() -> None:
    cases: list[tuple[dict[str, Any], str]] = [
        (_refused_ticket_json(), "refused receipt:"),
        (_held_ticket_json(spent_out=True), "halting scope:   support-agent (spent_out=True"),
        (_held_ticket_json(with_halt=False), "halting scope:   (cleared)"),
        (_dead_ticket_json(), "dead message:"),
        (_undo_ticket_json(late=True), "(late=True)"),
        (e.ticket_json(), "quote expires:"),
        (_amend_ticket_json(), "proposed:        version 2"),
    ]
    for raw, line in cases:
        ticket = Ticket.from_json(raw, signed=False).signed(e.SERVICE)
        shown = render_ticket(ticket)
        assert line in shown, (raw["class"], shown)
        assert f"Digest:   {ticket.digest}" in shown
        for option in ticket.options:
            assert option.digest in shown
    quiet = e.ticket_json()
    quiet["agent"] = {"statement": None}
    assert "Agent statement (unverified claim): (none)" in render_ticket(
        Ticket.from_json(quiet, signed=False).signed(e.SERVICE)
    )


@pytest.fixture
def cli(escalated: Escalated, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Escalated:
    """The command line's configuration, naming the escalated store."""
    monkeypatch.chdir(tmp_path)
    store = escalated.store
    if isinstance(store, SqliteStore):
        database, schema = str(store.path), ""
    else:
        database, schema = need_postgres(), f'schema = "{store.schema}"\n'  # type: ignore[attr-defined]
    Path("service.key").write_text(hashlib.sha256(b"fiduciary-test/service").hexdigest() + "\n")
    Path("service.key").chmod(0o600)
    Path("cfo.key").write_text(hashlib.sha256(b"fiduciary-test/cfo").hexdigest() + "\n")
    Path("cfo.key").chmod(0o600)
    Path("fiduciary.toml").write_text(
        f'database = "{database}"\n{schema}'
        f'[charter]\ndigest = "{e.charter().digest}"\n[service]\nkey = "service.key"\n'
    )
    Path("charter.json").write_text(e.charter().pretty())
    Path("ticket.json").write_text(escalated.ticket.pretty())
    Path("warrant.json").write_text(escalated.warrant.pretty())
    return escalated


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    status = main(["--config", "fiduciary.toml", *args])
    out, err = capsys.readouterr()
    return status, out, err


def test_tickets_and_warrants_are_shown_from_the_store_or_a_file(
    cli: Escalated, capsys: pytest.CaptureFixture[str]
) -> None:
    ticket, warrant = cli.ticket, cli.warrant
    assert run(capsys, "tickets", "list")[1] == "no open tickets\n"
    status, out, _ = run(capsys, "tickets", "list", "--all", "--mandate", ticket.mandate.mandate_id)
    assert status == 0 and f"{ticket.ticket_id}  warranted  extension" in out
    assert run(capsys, "tickets", "list", "--all", "--mandate", "nobody")[1] == "no tickets\n"
    for target in (ticket.ticket_id, ticket.digest, "ticket.json"):
        status, out, err = run(capsys, "tickets", "show", target)
        assert status == 0 and f"Digest:   {ticket.digest}" in out and ticket.digest in err
    assert run(capsys, "tickets", "show", "f" * 64)[0] == 1
    status, _, err = run(capsys, "tickets", "show", "warrant.json")
    assert status == 2 and "is a warrant, not a ticket" in err

    for target in (warrant.warrant_id, warrant.digest, "warrant.json"):
        status, out, err = run(capsys, "warrant", "show", target)
        assert status == 0 and warrant.warrant_id in out and warrant.digest in err
    assert run(capsys, "warrant", "show", "f" * 64)[0] == 1
    status, _, err = run(capsys, "warrant", "show", "ticket.json")
    assert status == 2 and "is a ticket, not a warrant" in err


def test_a_ticket_approval_names_one_resolution_it_shows(
    cli: Escalated, capsys: pytest.CaptureFixture[str]
) -> None:
    ticket = cli.ticket
    signing = ["approve", "ticket.json", "--principal", "cfo", "--key", "cfo.key", "--yes"]
    status, _, err = run(capsys, *signing)
    assert status == 2 and "names its resolution" in err
    # The charter in force, read from the store when no file names it.
    status, out, _ = run(capsys, *signing, "--resolution", "grant")
    approval = Approval.loads(out.encode())
    assert status == 0 and (approval.charter_id, approval.charter_version) == (e.CHARTER_ID, 1)
    status, out, _ = run(capsys, *signing, "--decline")
    declined = Approval.loads(out.encode())
    assert declined.decision == "decline" and declined.resolution == ticket.options[0].digest
    status, _, err = run(capsys, *signing, "--charter", "ticket.json", "--resolution", "grant")
    assert status == 2 and "is a ticket, not a charter" in err
    Path("broken.json").write_text(json.dumps({"action": "grant"}))
    status, _, err = run(capsys, *signing, "--resolution", "broken.json")
    assert status == 2 and "broken.json:" in err
    Path("fiduciary.toml").write_text(
        'database = "missing-dir/x.db"\n[service]\nkey = "service.key"\n'
    )
    status, _, err = run(capsys, *signing, "--resolution", "grant")
    assert status == 2 and "--charter" in err

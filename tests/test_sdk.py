"""Solo deployments, shadow mode and governed tools in your own process (1.14.0).

Each test stands a solo deployment up with ``fiduciary init --solo`` beside a system (a
SQLite file, a directory, a REST API), governs Python functions with ``@gov.tool``, and
reads what happened back from the deployment's own records: the escrow chain the shadow
report reads, and the audit bundle the offline verifier checks.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import Any, Literal

import pytest

import fiduciary
from fiduciary import cli
from fiduciary.documents import Charter, Mandate
from fiduciary.exceptions import FiduciaryError
from fiduciary.init import Target, init
from fiduciary.sdk import Governor, find_config, input_schema
from fiduciary.shadow import render, report


def _tickets(path: Path) -> Path:
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(
            "CREATE TABLE tickets (id INTEGER PRIMARY KEY, status TEXT NOT NULL, owner TEXT);"
            "INSERT INTO tickets VALUES (1, 'open', NULL), (2, 'open', NULL), (3, 'open', NULL);"
        )
        conn.commit()
    return path


def _statuses(path: Path) -> list[str]:
    with closing(sqlite3.connect(path)) as conn:
        return [str(r[0]) for r in conn.execute("SELECT status FROM tickets ORDER BY id")]


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project directory with a SQLite file of three open tickets, as the working
    directory."""
    root = tmp_path / "helpdesk"
    root.mkdir()
    _tickets(root / "app.db")
    monkeypatch.chdir(root)
    monkeypatch.delenv("FIDUCIARY_CONFIG", raising=False)
    return root


def _solo(root: Path, *, shadow: bool) -> Path:
    init(
        root / ".fiduciary",
        target=Target("sqlite", database=str(root / "app.db")),
        solo=True,
        shadow=shadow,
    )
    return root / ".fiduciary"


def _govern(gov: Governor) -> tuple[Any, Any]:
    @gov.tool
    def close_ticket(db: fiduciary.Sql, ticket_id: int) -> str:
        """Close one ticket."""
        db.execute("UPDATE tickets SET status = 'closed' WHERE id = :id", {"id": ticket_id})
        return f"ticket {ticket_id} closed"

    @gov.tool
    def close_all(db: fiduciary.Sql) -> str:
        """Close every open ticket."""
        (row,) = db.query("SELECT count(*) AS n FROM tickets WHERE status = 'open'")
        db.execute("UPDATE tickets SET status = 'closed' WHERE status = 'open'")
        return f"closed {row['n']}"

    return close_ticket, close_all


# --------------------------------------------------------------------------
# init --solo
# --------------------------------------------------------------------------


def test_a_solo_deployment_has_one_principal_who_decides_alone(project: Path) -> None:
    root = _solo(project, shadow=True)
    charter = Charter.loads((root / "charter.json").read_bytes())
    mandate = Mandate.loads((root / "mandate.json").read_bytes())
    assert charter.names == ("owner",)
    assert (charter.charter_quorum, charter.issue_quorum, charter.revoke_quorum) == (1, 1, 1)
    assert mandate.roster.principals == ("owner",)
    assert set(mandate.roster.quorums.values()) == {1}
    assert mandate.shadow and mandate.tools.statements == "any"
    assert (mandate.not_after - mandate.not_before).days >= 365
    # named for the project, not for its hidden directory
    assert mandate.subject.scope == "helpdesk-agent"
    # nothing in it belongs in version control
    assert (root / ".gitignore").read_text().splitlines()[-1] == "*"
    readme = (root / "README.md").read_text()
    assert "fiduciary.open()" in readme and "shadow report" in readme
    assert "solo add-principals" in readme


def test_init_refuses_a_solo_deployment_of_several(project: Path) -> None:
    with pytest.raises(ValueError, match="one principal"):
        init(project / ".fiduciary", solo=True, principals=("a", "b"))


def test_the_command_writes_a_solo_deployment_into_dot_fiduciary(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["init", "--solo", "--shadow", "--substrate", "sqlite", "--database", "app.db"]
    assert cli.main(argv) == 0
    said = capsys.readouterr().out
    assert (project / ".fiduciary" / "fiduciary.toml").is_file()
    for line in ("shadow      ", "enforce     ", "sdk         ", "grow        "):
        assert line in said
    assert find_config() == (project / ".fiduciary" / "fiduciary.toml").resolve()
    # without --solo there is no default directory
    assert cli.main(["init", "--substrate", "sqlite", "--database", "app.db"]) == 2
    assert "give --out" in capsys.readouterr().err


# --------------------------------------------------------------------------
# shadow mode, the SDK, and the report
# --------------------------------------------------------------------------


def test_shadow_mode_lets_every_plan_through_and_records_what_would_have_refused_it(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _solo(project, shadow=True)
    with fiduciary.open() as gov:
        close_ticket, close_all = _govern(gov)
        assert gov.shadow and gov.scope == "helpdesk-agent"
        assert close_ticket(ticket_id=1) == "ticket 1 closed"
        assert close_all() == "closed 2"  # two rows: the mandate allows one
        assert _statuses(project / "app.db") == ["closed", "closed", "closed"]
        (seen,) = gov.observed
        assert seen.tool == "close_all" and seen.changes == 2
        assert seen.would_refuse == ("blast_radius",)
        assert seen.messages == ("plan would mutate 2 rows, limit is 1",)
    lineages = report(project / ".fiduciary" / "chains")
    (lineage,) = lineages
    summary = lineage.to_json()
    assert (summary["plans"], summary["committed"], summary["would_have_refused"]) == (2, 2, 1)
    assert summary["checks"] == {"blast_radius": {"plans": 1, "tools": {"close_all": 1}}}
    assert [(p["tool"], p["changes"]) for p in summary["largest"]] == [
        ("close_all", 2),
        ("close_ticket", 1),
    ]
    text = render(lineages)
    assert "2 plans staged: 2 committed, 0 refused, 0 put back." in text
    assert "blast_radius: 1 plan, from close_all (1)" in text
    assert "2 rows: close_all" in text
    # the command reads the same chain, finding the deployment from here
    assert cli.main(["shadow", "report", "--json"]) == 0
    (printed,) = json.loads(capsys.readouterr().out)
    assert printed["would_have_refused"] == 1


def test_an_enforced_mandate_refuses_in_words_the_agent_reads(project: Path) -> None:
    _solo(project, shadow=False)
    with fiduciary.open() as gov:
        close_ticket, close_all = _govern(gov)
        assert not gov.shadow
        with pytest.raises(fiduciary.Refused) as refused:
            close_all()
        assert refused.value.outcome == "refused"
        assert str(refused.value).startswith("The plan was refused and rolled back")
        assert "blast_radius: the plan changed 2-9 rows; the limit is 1" in str(refused.value)
        assert refused.value.result is not None
        assert refused.value.result.blocked_by == ("blast_radius",)
        assert _statuses(project / "app.db") == ["open", "open", "open"]
        assert close_ticket(ticket_id=2) == "ticket 2 closed"
        assert _statuses(project / "app.db") == ["open", "closed", "open"]
        assert gov.observed == []


def test_a_call_is_checked_and_a_failed_one_changes_nothing(project: Path) -> None:
    _solo(project, shadow=False)
    with fiduciary.open() as gov:
        close_ticket, _ = _govern(gov)
        with pytest.raises(fiduciary.Refused) as wrong:
            close_ticket(ticket_id="two")
        assert wrong.value.outcome == "rejected"
        assert "ticket_id" in str(wrong.value)

        @gov.tool
        def half_done(db: fiduciary.Sql, ticket_id: int) -> str:
            """Close a ticket, then fail."""
            db.execute("UPDATE tickets SET status = 'closed' WHERE id = :id", {"id": ticket_id})
            raise RuntimeError("the tool's own bug")

        with pytest.raises(RuntimeError, match="own bug"):
            half_done(ticket_id=3)

        @gov.tool
        def peek(db: fiduciary.Sql) -> int:
            """Count open tickets."""
            (row,) = db.query("SELECT count(*) AS n FROM tickets WHERE status = 'open'")
            return int(row["n"])

        assert peek() == 3  # wrote nothing: nothing to judge, nothing recorded

        @gov.tool
        def drop_it(db: fiduciary.Sql) -> str:
            """Try a statement the stage refuses."""
            db.execute("DELETE FROM sqlite_master", table="tickets")
            return "unreachable"

        with pytest.raises(fiduciary.Refused) as stage_refused:
            drop_it()
        assert stage_refused.value.outcome == "error"
    assert _statuses(project / "app.db") == ["open", "open", "open"]
    (lineage,) = report(project / ".fiduciary" / "chains")
    assert lineage.plans == []  # the bad call, the raise and the read left no plan


def test_a_tools_schema_is_read_from_its_signature() -> None:
    def tool(
        db: object, name: str, count: int, ratio: float, on: bool, tags: list[str], note: str = ""
    ) -> None:
        """Doc."""

    schema = input_schema(tool)
    assert schema["required"] == ["name", "count", "ratio", "on", "tags"]
    assert schema["properties"]["tags"] == {"type": "array", "items": {"type": "string"}}
    assert schema["additionalProperties"] is False

    def untyped(db: object, thing) -> None:  # type: ignore[no-untyped-def]
        """Doc."""

    with pytest.raises(TypeError, match="annotate it"):
        input_schema(untyped)


def test_open_names_what_it_cannot_find(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FIDUCIARY_CONFIG", raising=False)
    with pytest.raises(FileNotFoundError, match="fiduciary init --solo"):
        fiduciary.open()


# --------------------------------------------------------------------------
# growing up: enforce, add principals, and the audit bundle
# --------------------------------------------------------------------------


def test_a_solo_deployment_enforces_then_grows_to_two_of_three(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _solo(project, shadow=True)
    with fiduciary.open() as gov:
        _, close_all = _govern(gov)
        close_all()  # observed, committed
    assert cli.main(["solo", "enforce"]) == 0
    assert "version 2" in capsys.readouterr().out
    _reopen(project)
    with fiduciary.open() as gov:
        _, close_all = _govern(gov)
        with pytest.raises(fiduciary.Refused):
            close_all()
    assert cli.main(["solo", "enforce"]) == 1  # already enforced
    assert cli.main(["solo", "add-principals", "alice", "bob"]) == 0
    said = capsys.readouterr().out
    assert "version 3" in said and "under charter version 2" in said
    for name in ("alice", "bob"):
        key = root / "keys" / f"{name}.key"
        assert stat.S_IMODE(os.stat(key).st_mode) == 0o600
    grown = Charter.loads((root / "charter-v2.json").read_bytes())
    assert grown.names == ("alice", "bob", "owner") and grown.issue_quorum == 2
    # from now on no one key changes the mandate
    assert cli.main(["solo", "enforce"]) == 1
    assert "takes their approvals" in capsys.readouterr().err
    # the audit bundle verifies offline against the first charter, and says which code
    # made each plan
    mandate = json.loads((root / "mandate.json").read_text())["mandate_id"]
    bundle = project / "lineage.fidb"
    argv = ["--config", str(root / "fiduciary.toml"), "export-bundle", "--mandate", mandate]
    ledger_key = str(root / "keys" / "ledger-checkpoints.key")
    assert cli.main([*argv, "--ledger-key", ledger_key, "--out", str(bundle)]) == 0
    capsys.readouterr()
    charter = (root / "charter.digest").read_text().strip()
    keys = str(root / "pinned-keys.json")
    assert cli.main(["verify-bundle", str(bundle), "--charter", charter, "--keys", keys]) == 0
    said = capsys.readouterr().out
    assert "in-process tools made plans the mandate admits without a pinned binding" in said
    assert "close_all (" in said


def _reopen(project: Path) -> None:
    with closing(sqlite3.connect(project / "app.db")) as conn:
        conn.execute("UPDATE tickets SET status = 'open'")
        conn.commit()


# --------------------------------------------------------------------------
# a directory, and a REST API
# --------------------------------------------------------------------------


def test_a_workspace_tool_writes_through_its_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "notes"
    (project / "docs").mkdir(parents=True)
    (project / "docs" / "todo.md").write_text("- ship\n")
    (project / "scripts").mkdir()
    (project / "scripts" / "deploy.sh").write_text("#!/bin/sh\n")
    monkeypatch.chdir(project)
    init(project / ".fiduciary", target=Target("filesystem", directory=project), solo=True)
    with fiduciary.open() as gov:

        @gov.tool
        def add_item(ws: fiduciary.Workspace, item: str) -> list[str]:
            """Add one item to the list."""
            ws.append("docs/todo.md", f"- {item}\n")
            text = ws.read("docs/todo.md")
            assert text is not None
            return text.splitlines()

        @gov.tool
        def remove_script(ws: fiduciary.Workspace) -> str:
            """Remove the deploy script."""
            ws.delete("scripts/deploy.sh")
            return "removed"

        @gov.tool
        def read_key(ws: fiduciary.Workspace) -> str | None:
            """Read the owner's key."""
            return ws.read(".fiduciary/keys/owner.key")

        assert add_item(item="test") == ["- ship", "- test"]
        assert (project / "docs" / "todo.md").read_text() == "- ship\n- test\n"
        with pytest.raises(fiduciary.Refused) as refused:
            remove_script()
        assert "kind_allowlist" in str(refused.value) or "no_delete" in str(refused.value)
        assert (project / "scripts" / "deploy.sh").exists()
        with pytest.raises(fiduciary.Refused, match="nothing changed"):
            read_key()  # the deployment's own directory is not the workspace's
    mandate = Mandate.loads((project / ".fiduciary" / "mandate.json").read_bytes())
    assert mandate.data.vocabulary == "resource"  # a directory is bounded in its own words


def test_a_rest_tool_predicts_and_the_api_is_read_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from interlock.sim.rest import Collection, LoopbackRestServer

    server = LoopbackRestServer((Collection("tickets", filters=("account_id",)),))
    server.start()
    try:
        server.seed("tickets", {"id": "t-1", "account_id": "acme", "status": "open"})
        monkeypatch.chdir(tmp_path)
        init(
            tmp_path / ".fiduciary",
            target=Target(
                "rest",
                server=server.url,
                resources=("tickets=/tickets",),
                tenant_field="account_id",
                managed=("updated_at", "updated_by", "version", "last_request"),
            ),
            solo=True,
        )
        with fiduciary.open() as gov:

            @gov.tool
            def solve(api: fiduciary.Rest, ticket_id: str) -> str:
                """Mark one ticket solved."""
                api.patch("tickets", ticket_id, {"status": "solved"})
                return f"{ticket_id} solved"

            assert solve(ticket_id="t-1") == "t-1 solved"
        found = server.get("tickets", "t-1")
        assert found is not None and found["status"] == "solved"
    finally:
        server.stop()


def test_a_deployment_that_serves_its_agent_out_of_process_is_not_opened_here(
    project: Path,
) -> None:
    root = _solo(project, shadow=False)
    config = root / "fiduciary.toml"
    config.write_text(
        config.read_text().replace('in_process = "tool-calls-only"', 'in_process = "no"')
    )
    with pytest.raises(FiduciaryError, match="out of process"):
        fiduciary.open()


def test_every_schema_shape_a_signature_can_say() -> None:
    def tool(
        db: object,
        mode: Literal["fast", "slow"],
        limit: int | None,
        extra: dict[str, int],
        flags: list[bool],
    ) -> None:
        """Doc."""

    found = input_schema(tool)["properties"]
    assert found["mode"] == {"type": "string", "enum": ["fast", "slow"]}
    assert found["limit"] == {"type": ["integer", "null"]}
    assert found["extra"] == {"type": "object"}
    assert found["flags"] == {"type": "array", "items": {"type": "boolean"}}

    def varargs(db: object, *rest: str) -> None:
        """Doc."""

    with pytest.raises(TypeError, match="keyword arguments only"):
        input_schema(varargs)

    def unknown(db: object, when: bytes) -> None:
        """Doc."""

    with pytest.raises(TypeError, match="give the tool an input_schema"):
        input_schema(unknown)


def test_a_tool_is_named_described_and_governed_once(project: Path) -> None:
    _solo(project, shadow=False)
    with fiduciary.open() as gov:

        @gov.tool(
            name="reopen",
            description="Reopen one ticket.",
            input_schema={
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "integer"}},
            },
        )
        def anything(db: fiduciary.Sql, id: int) -> int:
            return db.execute("UPDATE tickets SET status = 'open' WHERE id = :id", {"id": id})

        assert anything.name == "reopen" and repr(anything) == "<governed tool reopen>"
        assert anything(id=1) == 1
        with pytest.raises(ValueError, match="already governed"):
            gov.tool(name="reopen", description="Again.")(lambda db: None)
        with pytest.raises(ValueError, match="lowercase identifier"):
            gov.tool(name="Reopen", description="Bad.")(lambda db: None)

        def undocumented(db: fiduciary.Sql) -> None:
            pass

        with pytest.raises(ValueError, match="docstring or a description"):
            gov.tool(undocumented)

        def handleless() -> None:
            """No handle."""

        with pytest.raises(TypeError, match="first parameter is the handle"):
            gov.tool(handleless)

        @gov.tool
        def bad_sql(db: fiduciary.Sql) -> None:
            """Statements execute() does not take."""
            with pytest.raises(ValueError, match="one INSERT, UPDATE or DELETE"):
                db.execute("SELECT 1")
            with pytest.raises(ValueError, match="pass table="):
                db.execute("UPDATE (SELECT 1) SET x = 1")

        assert bad_sql() is None
        text, error = anything.call_for_agent({"id": "x"})
        assert error and "refused" in text
        value, error = anything.call_for_agent({"id": 2})
        assert (value, error) == ("1", False)


def test_a_workspace_handle_writes_replaces_moves_and_lists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "site"
    (project / "docs").mkdir(parents=True)
    (project / "docs" / "a.md").write_text("old line\n")
    monkeypatch.chdir(project)
    init(project / ".fiduciary", target=Target("filesystem", directory=project), solo=True)
    with fiduciary.open() as gov:

        @gov.tool
        def edit(ws: fiduciary.Workspace) -> list[str]:
            """Rewrite the docs."""
            ws.replace("docs/a.md", "old", "new")
            return ws.paths()

        assert edit() == ["docs/a.md"]

        @gov.tool
        def rename(ws: fiduciary.Workspace) -> str:
            """Rename the docs page."""
            ws.move("docs/a.md", "docs/b.md")
            return "renamed"

        # a move is a deletion and a creation: two files, one of them deleted
        with pytest.raises(fiduciary.Refused) as refused:
            rename()
        assert "change_limit" in str(refused.value) and "no_delete" in str(refused.value)

        @gov.tool
        def fresh(ws: fiduciary.Workspace, text: str) -> bytes | None:
            """Write a new file."""
            ws.write("docs/c.md", text)
            return ws.read_bytes("docs/c.md")

        assert fresh(text="hello\n") == b"hello\n"
    assert (project / "docs" / "a.md").read_text() == "new line\n"
    assert not (project / "docs" / "b.md").exists()
    assert (project / "docs" / "c.md").read_text() == "hello\n"


def test_a_rest_handle_creates_and_pins_a_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from interlock.sim.rest import Collection, LoopbackRestServer

    server = LoopbackRestServer((Collection("tickets", filters=("account_id",)),))
    server.start()
    try:
        server.seed("tickets", {"id": "t-1", "account_id": "acme", "status": "open"})
        monkeypatch.chdir(tmp_path)
        init(
            tmp_path / ".fiduciary",
            target=Target(
                "rest",
                server=server.url,
                resources=("tickets=/tickets",),
                tenant_field="account_id",
                managed=("updated_at", "updated_by", "version", "last_request"),
            ),
            solo=True,
            shadow=True,
        )
        import urllib.request

        with urllib.request.urlopen(f"{server.url}/tickets/t-1") as reply:  # noqa: S310
            version = str(reply.headers["ETag"])  # what a tool reads before it writes
        with fiduciary.open() as gov:

            @gov.tool
            def file_ticket(api: fiduciary.Rest, subject: str) -> str:
                """Open one ticket."""
                api.create("tickets", {"account_id": "acme", "status": "new", "subject": subject})
                return "filed"

            @gov.tool
            def pinned(api: fiduciary.Rest, version: str) -> str:
                """Solve t-1 if no one changed it."""
                api.expect_version("tickets", "t-1", version)
                api.patch("tickets", "t-1", {"status": "solved"})
                return "solved"

            assert file_ticket(subject="printer") == "filed"
            assert pinned(version=version) == "solved"
        found = server.get("tickets", "t-1")
        assert found is not None and found["status"] == "solved"
    finally:
        server.stop()

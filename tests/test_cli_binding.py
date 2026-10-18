"""``fiduciary mandate compile``, ``bind``, ``derive``, ``amend``, ``revoke`` and
``expire``, end to end on SQLite and PostgreSQL, against a deployment that
``interlock.toml`` and ``[binding]`` describe."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from agentgov import BudgetManager

from fiduciary import fidu1
from fiduciary.documents import Charter, Mandate
from tests import examples as e
from tests.test_cli import _write_config, run

LOOKUP = e.lookup()
"""The lookup tool, as ``[binding] tools`` names a binding in code: ``module:name``."""

INTERLOCK_TOML = """\
substrate = "sqlite"

[[tables]]
name = "orders"
columns = ["id", "tenant", "total", "status"]
tenant_column = "tenant"

[[tables]]
name = "refunds"
columns = ["id", "order_id", "tenant", "amount"]
tenant_column = "tenant"

[[tables]]
name = "items"
columns = ["id", "tenant", "sku", "qty"]
tenant_column = "tenant"

[[sinks]]
name = "stripe"
cost_per_call = "0.01"
idempotency = "header"
max_payload_bytes = 65536
operations = [{ name = "refunds.create" }, { name = "charges.create" }]

[[sinks]]
name = "mail"
idempotency = "header"
max_payload_bytes = 65536
operations = [{ name = "send" }]
"""

BINDING = """
interlock = "interlock.toml"

[binding]
tools = ["refund.tool.json", "tests.test_cli_binding:LOOKUP"]
directives = ["stop.txt"]
chains = "chains"
"""

Capsys = pytest.CaptureFixture[str]


def _ok(capsys: Capsys, *args: str) -> tuple[str, str]:
    status, out, err = run(capsys, *args)
    assert status == 0, err
    return out, err


def _deploy(home: dict[str, Any], capsys: Capsys, *, binding: str = BINDING) -> Charter:
    """A ledger with ``support`` funded, a charter naming its first entry,
    adopted, and the deployment the example mandate pins: ``interlock.toml``,
    the two tools and the directive."""
    governor = BudgetManager.open_sqlite("ledger.db")
    governor.open_root("support", "1000")
    genesis = governor.entry(1).entry_hash
    governor.close()
    keys = {name: _ok(capsys, "keygen", f"{name}.key")[0].strip() for name in _NAMES}
    principals = [f"--principal={n}={keys[n]}" for n in ("alice", "bob", "carol")]
    _ok(capsys, "charter", "init", *principals, "--quorums", "2,2,1", "--ledger", genesis,
        "--label", "demo", "--out", "charter.draft.json")  # fmt: skip
    _ok(capsys, "sign", "charter.draft.json", "--key", "alice.key", "--out", "charter.json")
    charter = Charter.loads(Path("charter.json").read_bytes())
    _write_config(home, charter.digest)
    top, table, rest = binding.partition("[binding]")
    written = Path("fiduciary.toml").read_text()
    Path("fiduciary.toml").write_text(top.lstrip() + written + "\n" + table + rest)
    Path("interlock.toml").write_text(INTERLOCK_TOML)
    Path("refund.tool.json").write_text(e.tool().pretty())
    Path("stop.txt").write_bytes(b"Stop retrying.")
    _ok(capsys, "install")
    _ok(capsys, "charter", "propose", "charter.json")
    for principal in ("alice", "bob"):
        _approve(capsys, "charter.json", principal)
    _ok(capsys, "charter", "adopt", charter.digest)
    return charter


_NAMES = ("alice", "bob", "carol", "service")


def _approve(capsys: Capsys, file: str, principal: str, *flags: str) -> None:
    _ok(capsys, "approve", file, "--principal", principal, "--key", f"{principal}.key", "--yes",
        "--submit", *flags)  # fmt: skip


def _mandate(
    capsys: Capsys,
    charter: Charter,
    name: str,
    *,
    prev: Mandate | None = None,
    days: float = 30,
    edit: Any = None,
) -> Mandate:
    """The example mandate under ``charter``, its roster the command line's
    principals and its term from now, signed by bob into ``<name>.json``; or,
    with ``prev``, that mandate's next version, ``edit`` applied."""
    now = datetime.now(UTC)
    if prev is None:
        raw = e.mandate_json(
            mandate_id=str(uuid.uuid4()),
            charter={"charter_id": charter.charter_id, "version": 1, "digest": charter.digest},
            term={
                "not_before": fidu1.format_instant(now - timedelta(minutes=1)),
                "not_after": fidu1.format_instant(now + timedelta(days=days)),
            },
        )
        raw["roster"]["principals"] = ["alice", "bob", "carol"]
    else:
        raw = {**prev.body(), "version": prev.version + 1, "prev": prev.digest}
    raw["issued_at"] = fidu1.format_instant(now)
    if edit is not None:
        edit(raw)
    Path(f"{name}.draft.json").write_text(Mandate.from_json(raw, signed=False).dumps())
    _ok(capsys, "sign", f"{name}.draft.json", "--key", "bob.key", "--out", f"{name}.json")
    return Mandate.loads(Path(f"{name}.json").read_bytes())


def _issued(capsys: Capsys, mandate: Mandate, name: str) -> None:
    _ok(capsys, "mandate", "propose", f"{name}.json")
    for principal in ("alice", "bob"):
        _approve(capsys, f"{name}.json", principal)
    _ok(capsys, "mandate", "issue", mandate.digest)


def _child_draft(capsys: Capsys, parent: str) -> None:
    scope = ("--scope", "support-agent/triage", "--label", "Triage")
    _ok(capsys, "mandate", "draft", "--charter", "charter.json", "--parent", f"{parent}.json",
        *scope, "--out", "child.draft.json")  # fmt: skip


def _kinds(out: str) -> list[str]:
    return [line.split()[2] for line in out.splitlines() if line.startswith("record ")]


def _ledger() -> BudgetManager:
    governor = BudgetManager.open_sqlite("ledger.db", read_only=True)
    governor.refresh()
    return governor


def test_compile_bind_derive_amend_revoke(home: dict[str, Any], capsys: Capsys) -> None:
    charter = _deploy(home, capsys)
    parent = _mandate(capsys, charter, "m")

    # compile: a mandate not yet issued, then by its digest once issued; one digest
    out, err = _ok(capsys, "mandate", "compile", "m.json")
    description = json.loads(out)
    assert description["mandate"]["digest"] == parent.digest
    compiled = err.split()[1]
    assert fidu1.HASH.fullmatch(compiled) and "checkers" in err
    _issued(capsys, parent, "m")
    assert _ok(capsys, "mandate", "compile", parent.digest)[1].split()[1] == compiled

    # bind: the envelope delegated, the compiled digest recorded
    out, err = _ok(capsys, "mandate", "bind", parent.digest)
    assert _kinds(out) == ["mandate.bound"]
    assert f"compiled {compiled}" in err and "250.00000000 delegated" in err
    with _ledger() as governor:
        assert governor.available("support-agent") == 250

    # derive: a child the service signs, compiled beneath its parent, issued and bound
    _child_draft(capsys, "m")
    out, err = _ok(capsys, "mandate", "compile", "child.draft.json")
    assert json.loads(out)["lineage"][0]["digest"] == parent.digest
    out, _ = _ok(capsys, "mandate", "derive", "child.draft.json", "--bind")
    assert _kinds(out) == ["mandate.issued", "mandate.bound"]
    with _ledger() as governor:
        assert governor.available("support-agent/triage") == 50

    # an amendment the child no longer refines: issue refuses it, amend suspends the child
    amended = _mandate(
        capsys,
        charter,
        "m2",
        prev=parent,
        edit=lambda r: r["budget"].update(max_output_tokens=2048),
    )
    _ok(capsys, "mandate", "propose", "m2.json")
    for principal in ("alice", "bob"):
        _approve(capsys, "m2.json", principal)
    status, _, err = run(capsys, "mandate", "issue", amended.digest)
    assert status == 1 and "fiduciary mandate amend" in err
    out, _ = _ok(capsys, "mandate", "amend", "m2.json")
    assert _kinds(out) == ["mandate.issued", "mandate.suspended", "mandate.bound"]
    with _ledger() as governor:
        assert governor.halted_by("support-agent/triage") is not None
        assert governor.halted_by("support-agent") is None

    # revoke, on carol's revocation approval: the lineage ends and its scope halts
    _approve(capsys, "m2.json", "carol", "--revoke")
    out, _ = _ok(capsys, "mandate", "revoke", amended.digest)
    assert _kinds(out) == ["mandate.revoked"]
    with _ledger() as governor:
        assert governor.halted_by("support-agent") is not None
    status, _, err = run(capsys, "mandate", "bind", amended.digest)
    assert status == 1 and "revoked" in err

    assert _ok(capsys, "mandate", "expire")[0] == "no lineage in force has outlived its term\n"
    out, _ = _ok(capsys, "verify")
    assert out.startswith("PASS")


def test_expire_ends_a_lineage_and_its_descendants(
    home: dict[str, Any], capsys: Capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    charter = _deploy(home, capsys)
    parent = _mandate(capsys, charter, "m", days=1)
    _issued(capsys, parent, "m")
    _ok(capsys, "mandate", "bind", parent.digest)
    _child_draft(capsys, "m")
    _ok(capsys, "mandate", "derive", "child.draft.json", "--bind")

    import fiduciary.log

    class Later(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> Any:
            return datetime.now(tz) + timedelta(days=2)

    monkeypatch.setattr(fiduciary.log, "datetime", Later)
    out, _ = _ok(capsys, "mandate", "expire")
    assert _kinds(out) == ["mandate.expired", "mandate.expired"]
    with _ledger() as governor:
        for scope in ("support-agent", "support-agent/triage"):
            assert governor.halted_by(scope) is not None
    assert _ok(capsys, "mandate", "expire")[0].startswith("no lineage")


def test_amend_rebinds_a_changed_envelope(home: dict[str, Any], capsys: Capsys) -> None:
    charter = _deploy(home, capsys)
    parent = _mandate(capsys, charter, "m")
    _issued(capsys, parent, "m")
    _ok(capsys, "mandate", "bind", parent.digest)
    wider = _mandate(
        capsys,
        charter,
        "m2",
        prev=parent,
        edit=lambda r: r["budget"].update(envelope="300.00000000"),
    )
    _ok(capsys, "mandate", "propose", "m2.json")
    for principal in ("alice", "bob"):
        _approve(capsys, "m2.json", principal)
    out, _ = _ok(capsys, "mandate", "amend", wider.digest)
    assert _kinds(out) == ["mandate.issued", "mandate.bound"]
    with _ledger() as governor:
        assert governor.available("support-agent/ext-1") == 50


def test_binding_commands_refuse(home: dict[str, Any], capsys: Capsys) -> None:
    charter = _deploy(home, capsys)
    parent = _mandate(capsys, charter, "m")
    status, _, err = run(capsys, "mandate", "bind", parent.digest)
    assert status == 1 and "no issued mandate" in err
    status, _, err = run(capsys, "mandate", "amend", "m.json")
    assert status == 2 and "no amendment" in err
    status, _, err = run(capsys, "mandate", "compile", "charter.json")
    assert status == 2 and "no mandate" in err
    status, _, err = run(capsys, "mandate", "amend", "charter.json")
    assert status == 2 and "no mandate" in err
    status, _, err = run(capsys, "mandate", "derive", "m.json")
    assert status == 2  # a derived draft is unsigned: the service signs it
    status, _, err = run(capsys, "mandate", "derive", "charter.draft.json")
    assert status == 2 and "no mandate" in err
    _issued(capsys, parent, "m")
    Path("orphan.draft.json").write_text(
        Mandate.from_json(
            e.child_json(of=e.mandate(), charter=parent.charter.to_json()), signed=False
        ).dumps()
    )
    status, _, err = run(capsys, "mandate", "compile", "orphan.draft.json")
    assert status == 1 and "no issued version in force" in err
    status, _, err = run(capsys, "mandate", "derive", "orphan.draft.json")
    assert status == 1
    _ok(capsys, "mandate", "draft", "--charter", "charter.json", "--scope", "x", "--funded-from",
        "support", "--label", "X", "--out", "root.draft.json")  # fmt: skip
    status, _, err = run(capsys, "mandate", "derive", "root.draft.json")
    assert status == 1 and "issuance is 'derived'" in err
    status, _, err = run(capsys, "mandate", "revoke", parent.digest)
    assert status == 1  # no revocation approval
    status, _, err = run(capsys, "mandate", "compile", "f" * 64)
    assert status == 1 and "holds no document" in err


def test_the_ledger_must_fund_the_lineage(home: dict[str, Any], capsys: Capsys) -> None:
    charter = _deploy(home, capsys)
    parent = _mandate(
        capsys, charter, "m", edit=lambda r: r["subject"].update(funded_from="elsewhere")
    )
    status, _, err = run(capsys, "mandate", "compile", "m.json")
    assert status == 1 and "no scope 'elsewhere'" in err
    assert parent.subject.funded_from == "elsewhere"


@pytest.mark.parametrize(
    ("binding", "message", "status"),
    [
        ("", "interlock names no deployment", 2),
        ('interlock = "missing.toml"\n', "missing.toml", 2),
        ('interlock = "interlock.toml"\n[binding]\ntools = ["nope.json"]\n', "nope.json", 2),
        (
            'interlock = "interlock.toml"\n[binding]\ntools = ["charter.json"]\n',
            "is no tool binding",
            2,
        ),
        (
            'interlock = "interlock.toml"\n[binding]\ndirectives = ["gone.txt"]\n',
            "directive",
            2,
        ),
        (
            'interlock = "interlock.toml"\n[binding]\ndirectives = ["binary.txt"]\n',
            "directive",
            2,
        ),
        ('interlock = "interlock.toml"\n', "CompileError", 1),
    ],
)
def test_a_deployment_the_files_do_not_describe(
    home: dict[str, Any], capsys: Capsys, binding: str, message: str, status: int
) -> None:
    charter = _deploy(home, capsys, binding=binding)
    Path("binary.txt").write_bytes(b"\xff\xfe")
    _mandate(capsys, charter, "m")
    found, _, err = run(capsys, "mandate", "compile", "m.json")
    if message == "CompileError":
        # No tools and no directive: the deployment cannot enforce the mandate.
        assert found == 1 and "refund_order" in err
    else:
        assert found == status and message in err, err


def test_binding_needs_a_ledger(home: dict[str, Any], capsys: Capsys) -> None:
    charter = _deploy(home, capsys)
    _mandate(capsys, charter, "m")
    text = Path("fiduciary.toml").read_text().replace('ledger = "ledger.db"\n', "")
    Path("fiduciary.toml").write_text(text)
    status, _, err = run(capsys, "mandate", "compile", "m.json")
    assert status == 2 and "set ledger" in err


def test_an_empty_ledger_has_no_first_entry(home: dict[str, Any], capsys: Capsys) -> None:
    charter = _deploy(home, capsys)
    _mandate(capsys, charter, "m")
    BudgetManager.open_sqlite("empty.db").close()
    text = Path("fiduciary.toml").read_text().replace("ledger.db", "empty.db")
    Path("fiduciary.toml").write_text(text)
    status, _, err = run(capsys, "mandate", "compile", "m.json")
    assert status == 2 and "the ledger is empty" in err

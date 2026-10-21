"""The ``fiduciary`` command, end to end on SQLite and PostgreSQL, and its refusals."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from fiduciary.cli import main
from fiduciary.documents import Approval, Charter, Mandate, ToolBinding, loads
from tests import examples as e

Run = Any


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    status = main(["--config", "fiduciary.toml", *args])
    out, err = capsys.readouterr()
    return status, out, err


def _write_config(home: dict[str, Any], digest: str) -> None:
    schema = f'schema = "{home["schema"]}"\n' if home["schema"] else ""
    Path("fiduciary.toml").write_text(
        f'database = "{home["database"]}"\n{schema}ledger = "ledger.db"\n\n'
        f'[charter]\nfile = "charter.json"\ndigest = "{digest}"\n\n[service]\nkey = "service.key"\n'
    )


def _setup(home: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> str:
    keys = {}
    for name in ("alice", "bob", "carol", "service"):
        status, out, _ = run(capsys, "keygen", f"{name}.key")
        assert status == 0
        keys[name] = out.strip()
    status, _, _ = run(
        capsys,
        "charter",
        "init",
        "--principal",
        f"bob={keys['bob']}",
        "--principal",
        f"alice={keys['alice']}",
        "--principal",
        f"carol={keys['carol']}",
        "--quorums",
        "2,2,1",
        "--ledger",
        e.LEDGER,
        "--label",
        "demo",
        "--out",
        "charter.draft.json",
    )
    assert status == 0
    status, _, err = run(
        capsys, "sign", "charter.draft.json", "--key", "alice.key", "--out", "charter.json"
    )
    assert status == 0
    digest = Charter.loads(Path("charter.json").read_bytes()).digest
    assert digest in err
    _write_config(home, digest)
    status, out, _ = run(capsys, "install")
    assert status == 0 and "anchor scope" in out
    return digest


def test_the_whole_phase_1_flow(home: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    digest = _setup(home, capsys)
    assert run(capsys, "install")[1].endswith("the ledger's anchor scope exists\n")
    status, out, _ = run(capsys, "charter", "propose", "charter.json")
    assert (status, out.strip()) == (0, digest)
    for principal in ("alice", "bob"):
        status, _, err = run(
            capsys,
            "approve",
            "charter.json",
            "--principal",
            principal,
            "--key",
            f"{principal}.key",
            "--yes",
            "--submit",
            "--out",
            f"{principal}.json",
        )
        assert status == 0 and "submitted as intake" in err and "adopt charter" in err
    status, out, _ = run(capsys, "charter", "adopt", digest)
    assert status == 0 and "charter.adopted" in out
    status, out, err = run(capsys, "charter", "show", digest)
    assert status == 0 and json.loads(out)["type"] == "charter" and digest in err

    status, _, _ = run(
        capsys,
        "mandate",
        "draft",
        "--charter",
        "charter.json",
        "--scope",
        "agent",
        "--funded-from",
        "support",
        "--label",
        "Agent",
        "--out",
        "m.draft.json",
    )
    assert status == 0
    status, out, _ = run(capsys, "mandate", "check", "m.draft.json", "--charter", "charter.json")
    assert status == 0 and "well formed" in out
    Path("leaf.json").write_text(Path("m.draft.json").read_text())
    assert run(capsys, "sign", "leaf.json", "--key", "bob.key", "--out", "leaf-signed.json")[0] == 0
    status, _, err = run(
        capsys,
        "mandate",
        "draft",
        "--charter",
        "charter.json",
        "--scope",
        "x",
        "--label",
        "x",
        "--parent",
        "leaf-signed.json",
    )
    assert status == 1 and "allows no derivation" in err
    widened = json.loads(Path("m.draft.json").read_text())
    widened["delegation"] = {"max_depth": 1, "max_children": 2, "envelope": "0.50000000"}
    Path("m.draft.json").unlink()
    Path("m.draft.json").write_text(json.dumps(widened))
    assert run(capsys, "sign", "m.draft.json", "--key", "bob.key", "--out", "m.json")[0] == 0
    mandate = Mandate.loads(Path("m.json").read_bytes())
    assert run(capsys, "mandate", "propose", "m.json")[0] == 0
    status, _, err = run(capsys, "mandate", "issue", mandate.digest)
    assert status == 1 and "needs 2 approvals; 0 count" in err
    for principal in ("alice", "carol"):
        assert (
            run(
                capsys,
                "approve",
                "m.json",
                "--principal",
                principal,
                "--key",
                f"{principal}.key",
                "--yes",
                "--out",
                f"m-{principal}.json",
            )[0]
            == 0
        )
        status, out, _ = run(capsys, "submit", f"m-{principal}.json")
        assert status == 0 and out.startswith("intake ")
    status, out, _ = run(capsys, "mandate", "issue", mandate.digest)
    assert status == 0 and "mandate.issued" in out

    status, _, _ = run(
        capsys,
        "mandate",
        "draft",
        "--charter",
        "charter.json",
        "--scope",
        "agent/child",
        "--label",
        "Child",
        "--parent",
        "m.json",
        "--out",
        "child.draft.json",
    )
    assert status == 0
    child = Mandate.loads(Path("child.draft.json").read_bytes(), signed=False)
    assert child.issuance == "derived" and child.parent is not None
    status, out, _ = run(capsys, "mandate", "check", "child.draft.json", "--parent", "m.json")
    assert status == 0 and "refines its parent" in out
    assert (
        run(capsys, "sign", "child.draft.json", "--key", "service.key", "--out", "child.json")[0]
        == 0
    )
    child = Mandate.loads(Path("child.json").read_bytes())
    assert run(capsys, "mandate", "propose", "child.json")[0] == 0
    assert run(capsys, "mandate", "issue", child.digest)[0] == 0

    status, out, _ = run(capsys, "mandate", "diff", "m.json", "child.json")
    assert (
        status == 0
        and "child.json refines m.json" in out
        and "m.json is wider than child.json at:" in out
    )
    status, out, _ = run(capsys, "mandate", "show", "m.json")
    assert status == 0 and json.loads(out)["mandate_id"] == mandate.mandate_id

    status, out, _ = run(capsys, "verify")
    assert status == 0 and out.startswith("PASS")
    status, out, _ = run(capsys, "verify", "--service-key", _public("service.key"))
    assert status == 0


def _public(path: str) -> str:
    from fiduciary.keys import load_key

    return load_key(path).public_key().spec()  # type: ignore[attr-defined, no-any-return]


def test_verify_fails_on_a_wrong_service_key(
    home: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    digest = _setup(home, capsys)
    run(capsys, "charter", "propose", "charter.json")
    run(
        capsys,
        "approve",
        "charter.json",
        "--principal",
        "alice",
        "--key",
        "alice.key",
        "--yes",
        "--submit",
    )
    run(capsys, "charter", "adopt", digest)  # one approval: refused, but the approval is recorded
    status, out, _ = run(capsys, "verify", "--service-key", _public("alice.key"))
    assert status == 1 and "FAIL" in out
    status, _, err = run(capsys, "verify", "--service-key", "ed25519:00")
    assert status == 2 and "--service-key" in err


def test_approve_suspend_revoke_and_decline(
    home: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    _setup(home, capsys)
    status, _, err = run(
        capsys,
        "approve",
        "charter.json",
        "--principal",
        "alice",
        "--key",
        "alice.key",
        "--yes",
        "--suspend",
        "carol",
        "--out",
        "s.json",
    )
    assert status == 0 and "suspend carol's key" in err
    assert Approval.loads(Path("s.json").read_bytes()).subject.type == "suspension"
    status, _, err = run(
        capsys,
        "approve",
        "charter.json",
        "--principal",
        "alice",
        "--key",
        "alice.key",
        "--yes",
        "--suspend",
        "nobody",
    )
    assert status == 2 and "no principal" in err
    Path("m.json").write_text(e.mandate().dumps())
    status, out, _ = run(
        capsys,
        "approve",
        "m.json",
        "--principal",
        "alice",
        "--key",
        "alice.key",
        "--yes",
        "--revoke",
        "--decline",
    )
    approval = loads(out)
    assert (
        isinstance(approval, Approval)
        and approval.subject.type == "revocation"
        and approval.decision == "decline"
    )
    Path("tool.json").write_text(e.tool().dumps())
    status, _, err = run(
        capsys, "approve", "tool.json", "--principal", "alice", "--key", "alice.key", "--yes"
    )
    assert status == 2 and "approvals decide charters, mandates and tickets" in err
    status, _, err = run(
        capsys,
        "approve",
        "m.json",
        "--principal",
        "alice",
        "--key",
        "alice.key",
        "--yes",
        "--suspend",
        "x",
    )
    assert status == 2 and "--suspend names a principal" in err


def test_approve_refuses_to_sign_unread(
    home: dict[str, Any], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(home, capsys)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    status, _, err = run(
        capsys, "approve", "charter.json", "--principal", "alice", "--key", "alice.key"
    )
    assert status == 2 and "refusing to sign without reading" in err
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    status, _, err = run(
        capsys, "approve", "charter.json", "--principal", "alice", "--key", "alice.key"
    )
    assert status == 1 and "nothing signed" in err
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    status, out, _ = run(
        capsys, "approve", "charter.json", "--principal", "alice", "--key", "alice.key"
    )
    assert status == 0 and isinstance(loads(out), Approval)


def test_tool_digest(home: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    Path("tool.json").write_text(e.tool().dumps())
    status, out, err = run(capsys, "tool", "digest", "tool.json")
    assert (status, out.strip()) == (0, e.tool().digest) and '"name": "refund_order"' in err
    status, out, _ = run(capsys, "tool", "digest", "tests.examples:tool_json")
    assert status == 2  # a function, not a binding
    status, out, _ = run(capsys, "tool", "digest", "tests.test_cli:BINDING")
    assert (status, out.strip()) == (0, e.tool().digest)
    status, out, _ = run(capsys, "tool", "digest", "tests.test_cli:DRAFT")
    assert status == 0 and out.strip() == ToolDraft.digest
    status, _, err = run(capsys, "tool", "digest", "tests.nowhere:x")
    assert status == 2
    Path("charter.json").write_text(e.charter().dumps())
    status, _, err = run(capsys, "tool", "digest", "charter.json")
    assert status == 2 and "no tool binding" in err
    status, _, err = run(capsys, "tool", "digest", "tests.test_cli:NOT_A_DOCUMENT")
    assert status == 2


BINDING = e.tool()
DRAFT = e.tool_json()
NOT_A_DOCUMENT = {"v": "FIDU1", "type": "tool"}


class ToolDraft:
    digest = ToolBinding.from_json(e.tool_json(), signed=False).digest


@pytest.mark.parametrize(
    ("args", "fragment"),
    [
        (
            (
                "charter",
                "init",
                "--principal",
                "nokey",
                "--quorums",
                "1,1,1",
                "--ledger",
                e.LEDGER,
                "--label",
                "x",
            ),
            "NAME=ed25519",
        ),
        (
            (
                "charter",
                "init",
                "--principal",
                f"a={e.key('cfo')}",
                "--quorums",
                "1,1",
                "--ledger",
                e.LEDGER,
                "--label",
                "x",
            ),
            "CHARTER,ISSUE,REVOKE",
        ),
        (
            (
                "charter",
                "init",
                "--principal",
                f"a={e.key('cfo')}",
                "--quorums",
                "a,b,c",
                "--ledger",
                e.LEDGER,
                "--label",
                "x",
            ),
            "CHARTER,ISSUE,REVOKE",
        ),
        (
            (
                "charter",
                "init",
                "--principal",
                f"a={e.key('cfo')}",
                "--quorums",
                "2,1,1",
                "--ledger",
                e.LEDGER,
                "--label",
                "x",
            ),
            "quorums",
        ),
        (("sign", "missing.json", "--key", "k"), "No such file"),
        (("mandate", "check", "charter.json"), "is no mandate"),
        (("mandate", "diff", "charter.json", "charter.json"), "two mandates"),
        (
            ("mandate", "draft", "--charter", "charter.json", "--scope", "s", "--label", "l"),
            "--funded-from",
        ),
        (
            ("mandate", "draft", "--charter", "m.json", "--scope", "s", "--label", "l"),
            "is no charter",
        ),
        (
            (
                "mandate",
                "draft",
                "--charter",
                "charter.json",
                "--scope",
                "s",
                "--label",
                "l",
                "--parent",
                "charter.json",
            ),
            "is no mandate",
        ),
        (("mandate", "check", "m.json", "--charter", "m.json"), "is no charter"),
        (("mandate", "check", "m.json", "--parent", "charter.json"), "is no mandate"),
        (("submit", "charter.json"), "no approval"),
        (("verify",), "no such configuration"),
        (("sign", "garbage.json", "--key", "x"), "not valid JSON"),
        (("sign", "draft.json"), "--key"),
    ],
)
def test_usage_errors_exit_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    args: tuple[str, ...],
    fragment: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    Path("charter.json").write_text(e.charter().dumps())
    Path("m.json").write_text(e.mandate().dumps())
    Path("garbage.json").write_text("{")
    Path("draft.json").write_text(Charter.from_json(e.charter_json(), signed=False).dumps())
    status, _, err = run(capsys, *args)
    assert status == 2, err
    assert fragment in err


def test_check_names_what_does_not_refine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    Path("m.json").write_text(e.mandate().dumps())
    Path("charter.json").write_text(e.charter().dumps())
    wide = Mandate.from_json(
        e.child_json(budget={**e.mandate_json()["budget"], "per_call": "9.00000000"}), signed=False
    )
    Path("wide.json").write_text(wide.dumps())
    status, out, _ = run(capsys, "mandate", "check", "wide.json", "--parent", "m.json")
    assert status == 1 and "budget.per_call" in out
    status, _, err = run(capsys, "mandate", "check", "wide.json")
    assert status == 0 and "give --parent" in err
    other = Charter.from_json(e.charter_json(charter_id=str(uuid.uuid4())), signed=False)
    Path("other.json").write_text(other.dumps())
    status, out, _ = run(capsys, "mandate", "check", "m.json", "--charter", "other.json")
    assert status == 1 and "charter:" in out


def test_documents_are_never_overwritten(
    home: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    _setup(home, capsys)
    status, _, err = run(
        capsys, "sign", "charter.draft.json", "--key", "alice.key", "--out", "charter.json"
    )
    assert status == 2 and "never overwritten" in err
    status, _, err = run(capsys, "keygen", "alice.key")
    assert status == 2


def test_refusals_exit_1(home: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    _setup(home, capsys)
    status, _, err = run(capsys, "charter", "adopt", "a" * 64)
    assert status == 1 and "holds no document" in err
    status, _, err = run(capsys, "charter", "show", "b" * 64)
    assert status == 1 and "holds no document" in err
    status, _, err = run(capsys, "charter", "suspend", "alice")
    assert status == 1 and "no charter is adopted" in err
    status, _, err = run(capsys, "mandate", "propose", "charter.json")
    assert status == 2 and "not a mandate" in err
    Path("unsigned.json").write_text(Charter.from_json(e.charter_json(), signed=False).dumps())
    status, _, err = run(capsys, "charter", "propose", "unsigned.json")
    assert status == 2

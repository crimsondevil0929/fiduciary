"""A solo owner's passkey (1.17.0): registered in the charter, then the only key that signs.

The owner's key file is moved out of ``keys/`` once the passkey is in force, so an agent
that reads files can no longer sign anything here. Each later change (enforcing the
mandate, growing to three principals) is signed by the passkey: through the one-shot page
on ``http://localhost``, which a stand-in browser answers here as a passkey would, and the
audit bundle verifies offline, in Python and in the browser's checks alike.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import sqlite3
import subprocess
import urllib.request
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

import fiduciary
from fiduciary import cli, passkeys, solo
from fiduciary.bridge import Fiduciary
from fiduciary.documents import Charter
from fiduciary.init import Target, init
from fiduciary.passkeys import BrowserPasskey, PasskeyError, policy_for
from fiduciary.webauthn import PasskeyKey, Policy, SoftPasskey

ORIGIN = "http://localhost:8740"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "helpdesk"
    root.mkdir()
    with closing(sqlite3.connect(root / "app.db")) as conn:
        conn.executescript(
            "CREATE TABLE tickets (id INTEGER PRIMARY KEY, status TEXT NOT NULL);"
            "INSERT INTO tickets VALUES (1, 'open'), (2, 'open');"
        )
        conn.commit()
    monkeypatch.chdir(root)
    monkeypatch.delenv("FIDUCIARY_CONFIG", raising=False)
    init(
        root / ".fiduciary",
        target=Target("sqlite", database=str(root / "app.db")),
        solo=True,
        shadow=True,
    )
    return root


def _fid(project: Path) -> Fiduciary:
    return Fiduciary.from_config(project / ".fiduciary" / "fiduciary.toml")


def _browser(passkey: SoftPasskey, origin: str = ORIGIN) -> Any:
    """A stand-in browser: it opens the page, reads what it asks, answers as the passkey."""

    def open_page(url: str) -> bool:
        page = urllib.request.urlopen(url, timeout=5).read().decode("utf-8")  # noqa: S310
        request = json.loads(re.search(r"var R = (\{.*?\});", page).group(1))  # type: ignore[union-attr]
        challenge = base64.urlsafe_b64decode(request["challenge"] + "==")
        if request["mode"] == "create":
            body = {
                "point": passkey.point.hex(),
                "credential": "Y3JlZGVudGlhbC1pZC0x",
                "origin": origin,
            }
        else:
            made = passkey.assert_(challenge, origin)
            body = {
                "authenticator_data": made.authenticator_data.hex(),
                "client_data_json": made.client_data_json.hex(),
                "signature": made.signature.hex(),
            }
        post = urllib.request.Request(  # noqa: S310
            url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Origin": url.rstrip("/")},
            method="POST",
        )
        urllib.request.urlopen(post, timeout=5).read()  # noqa: S310
        return True

    return open_page


def test_a_passkey_replaces_the_owners_key_file_and_signs_every_change_after(
    project: Path,
) -> None:
    root = project / ".fiduciary"
    passkey = SoftPasskey(seed=11)
    fid = _fid(project)
    try:
        made = passkeys.create(
            "owner", "http://localhost:8741", opener=_browser(passkey, "http://localhost:8741")
        )
        assert made.point == passkey.point
        policy = policy_for(made.origin, credential=made.credential)
        done = passkeys.register(
            fid,
            root,
            "owner",
            made.point,
            policy,
            passkey=BrowserPasskey(
                "owner",
                PasskeyKey(made.point, policy),
                opener=_browser(passkey, "http://localhost:8741"),
            ),
        )
        assert done.adopted and done.charter_version == 2
        assert done.retired is not None and not (root / "keys" / "owner.key").exists()
        charter = Charter.loads((root / "charter-v2.json").read_bytes())
        (owner,) = charter.principals
        assert owner.key == passkey.spec and owner.webauthn == policy
        # the old key file signs nothing now
        with pytest.raises(solo.SoloError, match="not the one the charter holds"):
            solo.enforce(
                fid, root, passkey=SoftPasskey(seed=12).signer(policy, "http://localhost:8741")
            )
        # the page, answered by the passkey, signs the next mandate version
        key = PasskeyKey(passkey.point, policy)
        browser = BrowserPasskey(
            "owner",
            key,
            what="the mandate's next version",
            opener=_browser(passkey, "http://localhost:8741"),
        )
        grown = solo.enforce(fid, root, passkey=browser)
        assert grown.version == 2 and grown.charter_version == 2
        with pytest.raises(PasskeyError, match="already"):
            passkeys.register(
                fid, root, "owner", passkey.point, policy, signer=browser, passkey=browser
            )
    finally:
        fid.close()
    with fiduciary.open() as gov:

        @gov.tool
        def close_all(db: fiduciary.Sql) -> str:
            """Close every open ticket."""
            db.execute("UPDATE tickets SET status = 'closed' WHERE status = 'open'")
            return "closed"

        with pytest.raises(fiduciary.Refused):
            close_all()  # enforced now, by a version the passkey signed
    _bundle_verifies(project)


def _bundle_verifies(project: Path) -> None:
    root = project / ".fiduciary"
    mandate = json.loads((root / "mandate.json").read_text())["mandate_id"]
    bundle = project / "lineage.fidb"
    argv = ["--config", str(root / "fiduciary.toml"), "export-bundle", "--mandate", mandate]
    ledger_key = str(root / "keys" / "ledger-checkpoints.key")
    assert cli.main([*argv, "--ledger-key", ledger_key, "--out", str(bundle)]) == 0
    charter = (root / "charter.digest").read_text().strip()
    keys = str(root / "pinned-keys.json")
    assert cli.main(["verify-bundle", str(bundle), "--charter", charter, "--keys", keys]) == 0
    node = shutil.which("node")
    if node is None:
        return
    harness = Path(__file__).resolve().parent / "js" / "verify_vectors.cjs"
    listing = project / "bundles.json"
    listing.write_text(json.dumps([{"name": "passkey", "file": bundle.name, "keys": None}]))
    done = subprocess.run(  # noqa: S603
        [node, str(harness), str(listing)], capture_output=True, text=True, check=False, timeout=120
    )
    assert done.returncode == 0, done.stderr
    checks = json.loads(done.stdout)["passkey"]["checks"]
    assert checks.get("passkeys") == "pass", json.loads(done.stdout)["passkey"]["failing"]


def test_a_policy_is_made_from_the_origin_a_passkey_was_made_on() -> None:
    assert policy_for(ORIGIN) == Policy("localhost", (ORIGIN,))
    assert policy_for(ORIGIN, any_port=True).origins == ("http://localhost:*",)
    company = policy_for("https://approvals.example.com", any_port=True)
    assert company.rp_id == "approvals.example.com" and company.origins == (
        "https://approvals.example.com",
    )
    with pytest.raises(PasskeyError):
        policy_for("http://127.0.0.1:8740")


def test_a_page_that_gets_no_answer_signs_nothing(project: Path) -> None:
    passkey = SoftPasskey(seed=13)
    key = PasskeyKey(passkey.point, Policy("localhost", ("http://localhost:*",)))
    silent = BrowserPasskey("owner", key, opener=lambda url: None, wait=timedelta(seconds=0.2))
    with pytest.raises(PasskeyError, match="no passkey answered"):
        silent.assertion(b"x" * 82)
    elsewhere = PasskeyKey(passkey.point, Policy("example.com", ("https://example.com",)))
    with pytest.raises(PasskeyError, match="sign on that page"):
        BrowserPasskey("owner", elsewhere).assertion(b"x")
    os.environ.pop("FIDUCIARY_CONFIG", None)


# --------------------------------------------------------------------------
# the console: a principal of three registers a passkey, then approves with it
# --------------------------------------------------------------------------


def test_the_console_registers_a_passkey_and_takes_its_approval(tmp_path: Path) -> None:
    from fiduciary.authority import Authority
    from fiduciary.config import load_config
    from fiduciary.control import ConfiguredDeployment, ControlError
    from fiduciary.demo.sandbox import SandboxDeployment
    from fiduciary.keys import load_key
    from fiduciary.log import Log

    built = SandboxDeployment("fintech", tmp_path / "held")
    ticket = built.call({"preset": "held"})["ticket"]
    root = built.built().world.root
    built.close()
    desk = ConfiguredDeployment("d", root / "fiduciary.toml", scratch=tmp_path, accepts=True)
    passkey, intruder = SoftPasskey(seed=21), SoftPasskey(seed=22)
    asked = {
        "principal": "cfo",
        "point": passkey.point.hex(),
        "credential": "Y3JlZGVudGlhbC1pZC0x",
        "origin": ORIGIN,
    }
    draft = desk.passkey_draft(asked)
    assert draft["key_id"] == passkey.key_id and draft["passkey"]["origins"] == [ORIGIN]

    def answer(key: SoftPasskey, signing_input: str) -> dict[str, str]:
        made = key.assert_(bytes.fromhex(signing_input), ORIGIN)
        return {
            "authenticator_data": made.authenticator_data.hex(),
            "client_data_json": made.client_data_json.hex(),
            "signature": made.signature.hex(),
        }

    sent = {"principal": "cfo", "charter": draft["charter"], "key_id": draft["key_id"]}
    with pytest.raises(ControlError, match="does not verify"):
        desk.passkey_register({**sent, "assertion": answer(intruder, draft["signing_input"])})
    done = desk.passkey_register({**sent, "assertion": answer(passkey, draft["signing_input"])})
    assert done["charter_version"] == 2 and not done["adopted"], "three principals: a quorum"
    assert (root / "keys" / "cfo.key").exists(), "kept until the version is in force"
    # the other principals approve the version, and the writer adopts it
    fid = Fiduciary.from_config(root / "fiduciary.toml")
    try:
        charter = fid._authority.decide.charter(fid.log.state())
        digest = Charter.loads((root / "charter-v2.json").read_bytes()).digest
        for other in ("head-ops", "security"):
            passkeys.approve(
                fid, charter, other, load_key(root / "keys" / f"{other}.key"), "charter", digest
            )
        fid._authority.adopt_charter(digest)
    finally:
        fid.close()
    summary = desk.summary()
    (cfo,) = [p for p in summary["principals"] if p["name"] == "cfo"]
    assert cfo["passkey"]["rp_id"] == "localhost" and summary["passkeys"]
    # an approval of the held ticket, signed by touch
    vote = desk.draft({"ticket_id": ticket, "principal": "cfo", "option": "release"})
    assert vote["alg"] == "webauthn-es256" and vote["passkey"]["credential"]
    assert "passkey" in vote["command"]
    old_file = load_key(root / "keys" / "cfo.key")
    with pytest.raises(ControlError):
        desk.accept(
            {
                "approval": vote["approval"],
                "key_id": vote["key_id"],
                "signature": old_file.sign(bytes.fromhex(vote["signing_input"])).hex(),
            }
        )
    queued = desk.accept(
        {
            "approval": vote["approval"],
            "key_id": vote["key_id"],
            "assertion": answer(passkey, vote["signing_input"]),
        }
    )
    assert queued["intake"] > 0
    config = load_config(root / "fiduciary.toml")
    with config.open_store() as store:
        log = Log(store, config.service(), sealer=config.sealer())
        kinds = [r.kind for r in Authority(log, pinned=config.pinned()).process_intake()]
    assert "approval.recorded" in kinds
    view = desk.ticket(ticket)
    assert [v["principal"] for v in view["votes"]] == ["cfo"]
    desk.close()


def test_registration_refuses_what_it_cannot_make_a_principals_key(project: Path) -> None:
    root = project / ".fiduciary"
    passkey, other = SoftPasskey(seed=51), SoftPasskey(seed=52)
    policy = Policy("localhost", ("http://localhost:8745",))
    fid = _fid(project)
    try:
        with pytest.raises(PasskeyError, match="no principal"):
            passkeys.draft_charter(fid, "mallory", passkey.point, policy)
        with pytest.raises(PasskeyError, match="not the one it names"):
            passkeys.register(
                fid, root, "owner", passkey.point, policy, passkey=other.signer(policy)
            )
        draft = passkeys.draft_charter(fid, "owner", passkey.point, policy)
        unnamed = draft.signed(passkey.signer(policy))
        with pytest.raises(PasskeyError, match="makes no passkey"):
            passkeys.complete(fid, root, "mallory", unnamed)
        with pytest.raises(PasskeyError, match="no principal"):
            passkeys.signer_for(fid, root, "mallory")
        (root / "keys" / "owner.key").rename(root / "keys" / "elsewhere.key")
        with pytest.raises(PasskeyError, match="is not at"):
            passkeys.signer_for(fid, root, "owner")
        (root / "keys" / "owner.key").write_text("0" * 64)
        with pytest.raises(PasskeyError, match="not the key the charter holds"):
            passkeys.signer_for(fid, root, "owner")
        (root / "keys" / "owner.key").unlink()
        (root / "keys" / "elsewhere.key").rename(root / "keys" / "owner.key")
        done = passkeys.register(
            fid, root, "owner", passkey.point, policy, passkey=passkey.signer(policy)
        )
        assert done.adopted
        found = passkeys.signer_for(fid, root, "owner")
        assert isinstance(found, BrowserPasskey) and found.key_id == passkey.key_id
        with pytest.raises(PasskeyError, match="not the one the charter holds"):
            passkeys.signer_for(fid, root, "owner", passkey=other.signer(policy))
        with pytest.raises(PasskeyError, match=r"another principal's key|already"):
            passkeys.draft_charter(fid, "owner", passkey.point, policy)
    finally:
        fid.close()
    with pytest.raises(PasskeyError, match="its policy"):
        BrowserPasskey("owner", PasskeyKey(passkey.point))
    with pytest.raises(PasskeyError):
        policy_for("http://localhost:*")

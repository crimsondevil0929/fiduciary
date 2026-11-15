"""Secret stores for ``fiduciary protect --move-secrets`` (1.20.0). The commands each store
runs are checked here against stand-ins; ``scripts/check_custody.py`` runs the real stores in
CI (the Secret Service on Linux, the Credential Manager on Windows)."""

from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from fiduciary import custody
from fiduciary.custody import CustodyError, FileStore, Keychain, SecretService


def test_the_file_store_is_for_this_user_alone(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    with pytest.raises(CustodyError, match="holds no"):
        store.get("a/b/C")
    store.put("a/b/C", "value-1")
    store.put("a/b/D", "value-2")
    assert store.get("a/b/C") == "value-1"
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700
    store.delete("a/b/C")
    store.delete("never/there")
    with pytest.raises(CustodyError):
        store.get("a/b/C")
    assert "only you can read" in store.where
    store.path.write_text("{")
    with pytest.raises(CustodyError, match="not JSON"):
        store.get("a/b/D")
    store.path.write_text("[]")
    with pytest.raises(CustodyError):
        store.get("a/b/D")


def test_the_secret_service_takes_the_value_on_its_standard_input() -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []
    kept: dict[str, str] = {}

    def secret_tool(argv: list[str], **options: Any) -> Any:
        calls.append((argv, options))
        account = argv[argv.index("account") + 1]
        if argv[1] == "store":
            kept[account] = options["input"]
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1] == "lookup":
            if account in kept:
                return subprocess.CompletedProcess(argv, 0, kept[account], "")
            return subprocess.CompletedProcess(argv, 1, "", "")
        kept.pop(account, None)
        return subprocess.CompletedProcess(argv, 0, "", "")

    store = SecretService(secret_tool)
    store.put("cursor/notes/TOKEN", "s3cret value")
    assert "s3cret value" not in " ".join(calls[0][0]), "never on a command line"
    assert calls[0][1]["input"] == "s3cret value"
    assert store.get("cursor/notes/TOKEN") == "s3cret value"
    store.delete("cursor/notes/TOKEN")
    with pytest.raises(CustodyError, match="holds no"):
        store.get("cursor/notes/TOKEN")


def test_which_store_answers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def quiet(argv: list[str], **_: Any) -> Any:
        return subprocess.CompletedProcess(argv, 1, "", "")

    def no_service(argv: list[str], **_: Any) -> Any:
        return subprocess.CompletedProcess(argv, 1, "", "Cannot autolaunch D-Bus")

    def broken(argv: list[str], **_: Any) -> Any:
        raise OSError("no such program")

    monkeypatch.setattr(sys, "platform", "linux")
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    assert SecretService.available(quiet), "nothing found, nothing said: a service answered"
    assert not SecretService.available(no_service)
    assert not SecretService.available(broken)
    assert not Keychain.available()
    assert isinstance(custody.store_for("auto", tmp_path, runner=quiet), SecretService)
    assert isinstance(custody.store_for("auto", tmp_path, runner=no_service), FileStore)
    assert isinstance(custody.store_for("secret-service", tmp_path, runner=quiet), SecretService)
    with pytest.raises(CustodyError, match="secret-tool"):
        custody.store_for("secret-service", tmp_path, runner=no_service)
    with pytest.raises(CustodyError, match="Keychain"):
        custody.store_for("keychain", tmp_path)
    with pytest.raises(CustodyError, match="Windows"):
        custody.store_for("credential-manager", tmp_path)
    with pytest.raises(CustodyError, match="no secret store"):
        custody.store_for("vault", tmp_path)
    with pytest.raises(CustodyError, match="no secret store"):
        custody.store_named("vault", tmp_path)
    assert isinstance(custody.store_for("file", tmp_path), FileStore)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert isinstance(custody.store_for("auto", tmp_path), Keychain)
    assert isinstance(custody.store_named("keychain", tmp_path), Keychain)
    assert isinstance(custody.store_named("secret-service", tmp_path), SecretService)
    assert isinstance(custody.store_named("file", tmp_path), FileStore)
    monkeypatch.setattr(sys, "platform", "freebsd")
    assert not SecretService.available(quiet)
    monkeypatch.undo()


def test_the_keychain_files_each_value_under_one_service() -> None:
    calls: list[list[str]] = []

    def security(argv: list[str], **_: Any) -> Any:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "value\n", "")

    chain = Keychain(security)
    chain.put("a/b/C", "value")
    assert chain.get("a/b/C") == "value"
    assert all(custody.SERVICE in argv for argv in calls)

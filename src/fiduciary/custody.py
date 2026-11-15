"""Where ``fiduciary protect --move-secrets`` keeps a server's secrets (1.20.0).

A protected server's secrets (the values of its ``env``, and a remote server's headers) leave the
client's JSON file, which every program that reads files can read, an agent among them. They go to
the system's own secret store, and the proxy reads them back when it starts the server:

- **macOS:** the Keychain, through ``security``.
- **Linux:** the Secret Service (GNOME Keyring, KWallet), through ``secret-tool``. The value goes
  on its standard input, never on a command line.
- **Windows:** the Credential Manager, through its own API (``CredWriteW``, ``CredReadW``), with no
  program run at all.
- **Anywhere else, or when none answers:** a file only this user can read
  (``<protect root>/custody/secrets.json``, mode 600), outside the client's file and the
  workspace. Any program running as this user can still read it; a secret store is better, and
  ``protect`` says which one it used.

None of them keeps a value from another program of the same user that asks the store for it.
They keep it out of files an agent reads in passing.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final, Protocol

from fiduciary.exceptions import FiduciaryError

__all__ = [
    "SERVICE",
    "STORES",
    "CredentialManager",
    "CustodyError",
    "FileStore",
    "Keychain",
    "MemoryStore",
    "SecretService",
    "Store",
    "store_for",
    "store_named",
]

SERVICE: Final = "fiduciary-protect"
"""The service every secret is filed under, in whichever store."""
STORES: Final = ("keychain", "secret-service", "credential-manager", "file")
"""The stores by name, as ``--secret-store`` and a deployment's ``protect.json`` name them."""

Runner = Callable[..., Any]


class CustodyError(FiduciaryError):
    """A secret store that is not here, or did not answer."""


class Store(Protocol):
    """A place a secret is kept, under an account name."""

    name: str
    where: str
    """Where the values went, in plain words: ``protect`` prints it."""

    def put(self, account: str, value: str) -> None: ...

    def get(self, account: str) -> str: ...

    def delete(self, account: str) -> None: ...


class Keychain:
    """The macOS Keychain, through ``security``.

    A value passes on ``security``'s command line for a moment, where another process of the
    same user could read it, as it could read the JSON file it came from.
    """

    name = "keychain"
    where = "the macOS Keychain"

    def __init__(self, runner: Runner = subprocess.run) -> None:
        self._run = runner

    @staticmethod
    def available() -> bool:
        return sys.platform == "darwin" and shutil.which("security") is not None

    def put(self, account: str, value: str) -> None:
        self._run(
            ["security", "add-generic-password", "-U", "-s", SERVICE, "-a", account, "-w", value],
            check=True,
            capture_output=True,
        )

    def get(self, account: str) -> str:
        done = self._run(
            ["security", "find-generic-password", "-s", SERVICE, "-a", account, "-w"],
            check=True,
            capture_output=True,
            text=True,
        )
        return str(done.stdout).rstrip("\n")

    def delete(self, account: str) -> None:
        with contextlib.suppress(subprocess.CalledProcessError):
            self._run(
                ["security", "delete-generic-password", "-s", SERVICE, "-a", account],
                check=True,
                capture_output=True,
            )


class SecretService:
    """The Linux Secret Service (GNOME Keyring, KWallet), through ``secret-tool`` (libsecret).
    The value is written to its standard input, so no command line holds it."""

    name = "secret-service"
    where = "the Secret Service (secret-tool)"

    def __init__(self, runner: Runner = subprocess.run) -> None:
        self._run = runner

    @classmethod
    def available(cls, runner: Runner = subprocess.run) -> bool:
        """Whether ``secret-tool`` is here and a Secret Service answers it: a lookup of an
        account nobody stored finds nothing and says nothing; with no service, it says why."""
        if not sys.platform.startswith("linux") or shutil.which("secret-tool") is None:
            return False
        try:
            done = runner(
                ["secret-tool", "lookup", "service", SERVICE, "account", "fiduciary-probe"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return done.returncode == 0 or not str(done.stderr).strip()

    def put(self, account: str, value: str) -> None:
        self._run(
            [
                "secret-tool",
                "store",
                f"--label=fiduciary protect: {account}",
                "service",
                SERVICE,
                "account",
                account,
            ],
            input=value,
            text=True,
            check=True,
            capture_output=True,
        )

    def get(self, account: str) -> str:
        done = self._run(
            ["secret-tool", "lookup", "service", SERVICE, "account", account],
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode != 0:
            raise CustodyError(f"the Secret Service holds no {account}")
        return str(done.stdout)  # secret-tool adds a newline only for a terminal

    def delete(self, account: str) -> None:
        self._run(
            ["secret-tool", "clear", "service", SERVICE, "account", account],
            capture_output=True,
            check=False,
        )


class CredentialManager:  # pragma: no cover - Windows only: scripts/check_custody.py, in CI
    """The Windows Credential Manager, through its API (``advapi32``): a generic credential
    named ``fiduciary-protect/<account>``, kept for this user on this machine. No program is
    run, so no command line holds the value."""

    name = "credential-manager"
    where = "the Windows Credential Manager"
    _GENERIC: Final = 1  # CRED_TYPE_GENERIC
    _LOCAL_MACHINE: Final = 2  # CRED_PERSIST_LOCAL_MACHINE
    _NOT_FOUND: Final = 1168  # ERROR_NOT_FOUND

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class Credential(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        self._ctypes: Any = ctypes
        self._credential = Credential
        api = self._ctypes.WinDLL("advapi32", use_last_error=True)
        self._write = api.CredWriteW
        self._write.argtypes = [ctypes.POINTER(Credential), wintypes.DWORD]
        self._write.restype = wintypes.BOOL
        self._read = api.CredReadW
        self._read.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(Credential)),
        ]
        self._read.restype = wintypes.BOOL
        self._free = api.CredFree
        self._free.argtypes = [ctypes.c_void_p]
        self._free.restype = None
        self._delete = api.CredDeleteW
        self._delete.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._delete.restype = wintypes.BOOL

    @staticmethod
    def available() -> bool:
        return os.name == "nt"

    @staticmethod
    def _target(account: str) -> str:
        return f"{SERVICE}/{account}"

    def put(self, account: str, value: str) -> None:
        ctypes = self._ctypes
        blob = value.encode("utf-8")
        buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        credential = self._credential()
        credential.Type = self._GENERIC
        credential.TargetName = self._target(account)
        credential.UserName = account
        credential.CredentialBlobSize = len(blob)
        credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = self._LOCAL_MACHINE
        if not self._write(ctypes.byref(credential), 0):
            error = ctypes.get_last_error()
            raise CustodyError(f"the Credential Manager did not keep {account} (error {error})")

    def get(self, account: str) -> str:
        ctypes = self._ctypes
        found = ctypes.POINTER(self._credential)()
        if not self._read(self._target(account), self._GENERIC, 0, ctypes.byref(found)):
            raise CustodyError(f"the Credential Manager holds no {account}")
        try:
            size = found.contents.CredentialBlobSize
            return bytes(found.contents.CredentialBlob[:size]).decode("utf-8")
        finally:
            self._free(found)

    def delete(self, account: str) -> None:
        if not self._delete(self._target(account), self._GENERIC, 0):
            error = self._ctypes.get_last_error()
            if error != self._NOT_FOUND:
                raise CustodyError(f"the Credential Manager did not delete {account} ({error})")


class MemoryStore:
    """Secrets held in this process's memory alone (1.20.0): ``fiduciary ci``'s proxy, which
    is given its job's secrets on its standard input and keeps them nowhere else. A deployment
    whose ``protect.json`` names it can be served only by the process that protected it."""

    name = "memory"
    where = "the CI proxy's memory"

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def put(self, account: str, value: str) -> None:
        self._values[account] = value

    def get(self, account: str) -> str:
        if account not in self._values:
            raise CustodyError(f"{account} was held by a process that has ended")
        return self._values[account]

    def delete(self, account: str) -> None:
        self._values.pop(account, None)


class FileStore:
    """The fallback: a JSON file only this user can read, under the protect root
    (``custody/secrets.json``, mode 600, in a directory of mode 700). It keeps a secret out of
    the client's file and the workspace; any program running as this user can read it."""

    name = "file"

    def __init__(self, root: Path) -> None:
        self.path = root / "custody" / "secrets.json"
        self.where = f"a file only you can read ({self.path})"

    def _read(self) -> dict[str, str]:
        try:
            found = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except ValueError as exc:
            raise CustodyError(f"{self.path} is not JSON: {exc}") from exc
        return {str(k): str(v) for k, v in found.items()} if isinstance(found, dict) else {}

    def _write(self, values: dict[str, str]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.path.with_name(f"secrets.{os.getpid()}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as out:
            json.dump(values, out, indent=2, sort_keys=True)
        temporary.replace(self.path)

    def put(self, account: str, value: str) -> None:
        values = self._read()
        values[account] = value
        self._write(values)

    def get(self, account: str) -> str:
        values = self._read()
        if account not in values:
            raise CustodyError(f"{self.path} holds no {account}")
        return values[account]

    def delete(self, account: str) -> None:
        values = self._read()
        if values.pop(account, None) is not None:
            self._write(values)


def store_named(name: str, root: Path, *, runner: Runner = subprocess.run) -> Store:
    """The store ``name`` (one of :data:`STORES`): what a deployment's ``protect.json`` says
    its secrets went to. ``root`` is the protect root, for the file store.

    :raises CustodyError: For a name no store has.
    """
    if name == "keychain":
        return Keychain(runner)
    if name == "secret-service":
        return SecretService(runner)
    if name == "credential-manager":  # pragma: no cover - Windows only
        return CredentialManager()
    if name == "file":
        return FileStore(root)
    if name == "memory":
        raise CustodyError("its secrets were held in the memory of the process that protected it")
    raise CustodyError(f"no secret store is named {name!r}: {', '.join(STORES)}")


def store_for(name: str, root: Path, *, runner: Runner = subprocess.run) -> Store:
    """The store ``--secret-store`` asks for. ``auto`` is this system's own store when it
    answers (the Keychain, the Secret Service, the Credential Manager), else the file.

    :raises CustodyError: When the named store is not here.
    """
    if name == "auto":
        if Keychain.available():
            return Keychain(runner)
        if SecretService.available(runner):
            return SecretService(runner)
        if CredentialManager.available():  # pragma: no cover - Windows only
            return CredentialManager()
        return FileStore(root)
    here: dict[str, Callable[[], bool]] = {
        "keychain": Keychain.available,
        "secret-service": lambda: SecretService.available(runner),
        "credential-manager": CredentialManager.available,
        "file": lambda: True,
    }
    if name not in here:
        raise CustodyError(f"no secret store is named {name!r}: auto, {', '.join(STORES)}")
    if not here[name]():
        raise CustodyError(
            {
                "keychain": "--secret-store keychain needs the macOS Keychain (security)",
                "secret-service": "--secret-store secret-service needs secret-tool and a "
                "running Secret Service (GNOME Keyring or KWallet)",
                "credential-manager": "--secret-store credential-manager needs Windows",
            }[name]
        )
    return store_named(name, root, runner=runner)

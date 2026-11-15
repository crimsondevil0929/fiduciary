"""Put a value into a real secret store, read it back, and delete it (1.20.0).

CI runs it on the stores no developer machine here has: the Secret Service on Linux (under a
D-Bus session with GNOME Keyring unlocked) and the Credential Manager on Windows. It imports
nothing of fiduciary but :mod:`fiduciary.custody`, which needs only the standard library, so it
runs where the rest of the package does not (fiduciary itself runs on macOS and Linux).

    python scripts/check_custody.py secret-service
    python scripts/check_custody.py credential-manager
"""

from __future__ import annotations

import secrets
import sys
import tempfile
from pathlib import Path


def main(argv: list[str]) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from fiduciary import custody

    name = argv[1] if len(argv) > 1 else "auto"
    with tempfile.TemporaryDirectory() as root:
        store = custody.store_for(name, Path(root))
        if name != "auto" and store.name != name:
            print(f"asked for {name}, got {store.name}")
            return 1
        account = f"check/custody/{secrets.token_hex(4)}"
        value = "value " + secrets.token_urlsafe(24) + " ünïcode"
        store.put(account, value)
        try:
            found = store.get(account)
        finally:
            store.delete(account)
        if found != value:
            print(f"{store.where}: read back {found!r}, put {value!r}")
            return 1
        try:
            store.get(account)
        except custody.CustodyError:
            pass
        else:
            print(f"{store.where}: still holds {account} after it was deleted")
            return 1
        store.delete(account)  # deleting what is gone is not an error
    print(f"ok: {store.where} kept a value, gave it back exactly, and deleted it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

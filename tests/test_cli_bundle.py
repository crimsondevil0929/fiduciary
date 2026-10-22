"""``fiduciary export-bundle`` and ``fiduciary verify-bundle`` (§9, §10), and
``fiduciary verify --skew``: a configured deployment exports its lineage,
and the bundle verifies offline with the exit codes §9.3 names."""

from __future__ import annotations

import io
import json
import tarfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from fiduciary import cli
from fiduciary.store import Store
from tests import bound
from tests import bundle_kit as bk
from tests.conftest import LiveClock
from tests.test_cli_mcp import _configure, _key


@pytest.fixture
def world(store: Store, tmp_path: Path) -> Iterator[bound.World]:
    yield from bk.world(store, tmp_path)


@pytest.fixture
def exported(world: bound.World) -> tuple[Path, Path, str]:
    """A configured deployment's lineage, bound, exported by the CLI: the
    configuration, the bundle, and the lineage."""
    config = _configure(world)
    (mandate_id,) = world.log.state().lineages
    bundle = world.tmp / "lineage.fidb"
    ledger_key = _key(world.tmp / "ledger.key", "ledger-checkpoints")
    status = cli.main(
        [
            "--config",
            str(config),
            "export-bundle",
            "--mandate",
            mandate_id,
            "--out",
            str(bundle),
            "--ledger-key",
            str(ledger_key),
        ]
    )
    assert status == 0
    return config, bundle, mandate_id


def _keys_of(bundle: Path, path: Path) -> Path:
    """The auditor's pin: here, what the bundle states (in practice, the
    deployment's published keys)."""
    with tarfile.open(bundle) as tar:
        member = tar.extractfile("keys.json")
        assert member is not None
        stated = json.loads(member.read())
    stated.pop("principals")
    path.write_text(json.dumps(stated))
    return path


def test_export_bundle_writes_a_bundle_verify_bundle_passes(
    world: bound.World, exported: tuple[Path, Path, str], capsys: pytest.CaptureFixture[str]
) -> None:
    config, bundle, mandate_id = exported
    data = bundle.read_bytes()
    keys = _keys_of(bundle, world.tmp / "pinned.json")
    argv = ["verify-bundle", str(bundle), "--charter", world.charter.digest, "--keys", str(keys)]
    assert cli.main(argv) == 0
    out = capsys.readouterr().out
    assert "V7 completeness: ok" in out and out.rstrip().endswith("PASS")
    # Without --keys, it passes under the bundle's own and says so.
    assert cli.main(["verify-bundle", str(bundle), "--charter", world.charter.digest]) == 0
    assert "pin them with --keys" in capsys.readouterr().out
    # Never overwritten.
    again = [
        "--config", str(config), "export-bundle", "--mandate", mandate_id, "--out", str(bundle),
        "--ledger-key", str(world.tmp / "ledger.key"),
    ]  # fmt: skip
    assert cli.main(again) == cli.USAGE
    assert "never overwritten" in capsys.readouterr().err
    assert bundle.read_bytes() == data


def test_export_bundle_refuses_without_the_ledger_key_or_a_lineage(
    world: bound.World, exported: tuple[Path, Path, str], capsys: pytest.CaptureFixture[str]
) -> None:
    config, _, mandate_id = exported
    base = ["--config", str(config), "export-bundle", "--out", str(world.tmp / "b.fidb")]
    assert cli.main([*base, "--mandate", mandate_id]) == cli.USAGE
    assert "--ledger-key" in capsys.readouterr().err
    key = str(world.tmp / "ledger.key")
    unknown = "00000000-0000-4000-8000-000000000000"
    assert cli.main([*base, "--mandate", unknown, "--ledger-key", key]) == cli.REFUSED
    assert not (world.tmp / "b.fidb").exists()


def test_verify_bundle_exit_codes(
    world: bound.World, exported: tuple[Path, Path, str], capsys: pytest.CaptureFixture[str]
) -> None:
    """1 usage; 2 unreadable; 3 to 9 the first failing check's class, every
    failing check printed."""
    _, bundle, _ = exported
    pinned = world.charter.digest
    assert cli.main(["verify-bundle", str(bundle), "--charter", "not-a-digest"]) == 1
    assert cli.main(["verify-bundle", str(bundle)]) == 1  # --charter is required
    missing = world.tmp / "missing.fidb"
    assert cli.main(["verify-bundle", str(missing), "--charter", pinned]) == 2
    junk = world.tmp / "junk.fidb"
    junk.write_bytes(b"not a tar at all" * 64)
    assert cli.main(["verify-bundle", str(junk), "--charter", pinned]) == 2
    capsys.readouterr()

    # One member's bytes changed, another member deleted: every failure printed.
    with tarfile.open(bundle) as tar:
        members = {m.name: tar.extractfile(m).read() for m in tar.getmembers()}  # type: ignore[union-attr]
    members["log.jsonl"] = members["log.jsonl"].replace(b'"seq":1,', b'"seq":2,', 1)
    del members["deployment.json"]
    from fiduciary.bundle.format import pack

    tampered = world.tmp / "tampered.fidb"
    tampered.write_bytes(pack(members))
    assert cli.main(["verify-bundle", str(tampered), "--charter", pinned]) == 3
    out = capsys.readouterr().out
    assert "member log.jsonl does not match its manifest digest" in out
    assert "member deployment.json is listed and missing" in out
    assert "V9 records: FAILED" in out  # later checks still run and print
    assert out.rstrip().endswith("FAIL: exit 3")

    # The wrong charter pinned: V2, exit 5.
    assert cli.main(["verify-bundle", str(bundle), "--charter", "0" * 64]) == 5


def test_verify_bundle_holds_a_witness_to_its_cosignature(
    world: bound.World, exported: tuple[Path, Path, str], capsys: pytest.CaptureFixture[str]
) -> None:
    from agentgov.receipts.signing import Ed25519Signer, verifier_of

    from fiduciary.bundle.export import spec

    _, bundle, _ = exported
    witness = spec(verifier_of(Ed25519Signer(bytes(range(32)))))
    argv = ["verify-bundle", str(bundle), "--charter", world.charter.digest, "--witness", witness]
    assert cli.main(argv) == 8
    assert "no cosignature of the checkpoint by the witness" in capsys.readouterr().out
    assert cli.main([*argv[:-1], "ed25519:zz"]) == 1


def test_verify_takes_its_skew_from_the_flag_or_the_bridge(
    store: Store, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Records written a minute ahead of the ledger: ``verify`` names them
    under the default skew, and passes under ``--skew`` or ``[bridge]
    skew_seconds`` of two minutes."""
    for world in bk.world(store, tmp_path):
        assert isinstance(world.clock, LiveClock)
        world.clock.advance(minutes=1)
        config = _configure(world)
        assert cli.main(["--config", str(config), "verify"]) == cli.REFUSED
        assert "ledger" in capsys.readouterr().out
        assert cli.main(["--config", str(config), "verify", "--skew", "120"]) == 0
        text = config.read_text().replace(
            "[bridge.handlers]", "[bridge]\nskew_seconds = 120\n[bridge.handlers]"
        )
        config.write_text(text)
        assert cli.main(["--config", str(config), "verify"]) == 0
        assert cli.main(["--config", str(config), "verify", "--skew", "5"]) == cli.REFUSED


def test_a_bundle_of_the_cli_verifies_in_process(
    world: bound.World, exported: tuple[Path, Path, str]
) -> None:
    _, bundle, _ = exported
    data = bundle.read_bytes()
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        names = tar.getnames()
    assert names == sorted(names) and "manifest.json" in names
    report = bk.verify(world, data, keys=None)
    assert report.passed, report.render()

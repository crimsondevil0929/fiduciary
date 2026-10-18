"""``fiduciary.toml`` and the signers it names."""

from __future__ import annotations

from pathlib import Path

import pytest
from agentgov.receipts.kms_aws import AwsKmsSigner

from fiduciary.config import Config, ConfigError, load_config
from fiduciary.keys import SignerSpec, generate_key, load_key, open_signer, parse_signers
from fiduciary.sqlite import SqliteStore
from tests import examples as e
from tests.fakes import FakeKms, KeyService


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "fiduciary.toml"
    path.write_text(text)
    return path


def test_a_configuration_reads_with_paths_beside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FIDUCIARY_DATABASE", raising=False)
    monkeypatch.delenv("FIDUCIARY_LEDGER", raising=False)
    path = _write(
        tmp_path,
        f'database = "f.db"\nledger = "l.db"\n[charter]\nfile = "c.json"\ndigest = "{"a" * 64}"\n[service]\nkey = "s.key"\n',
    )
    config = load_config(path)
    assert config.database == str(tmp_path / "f.db")
    assert config.ledger == str(tmp_path / "l.db")
    assert config.charter_file == tmp_path / "c.json"
    assert config.service_key == tmp_path / "s.key"
    assert config.pinned() == "a" * 64
    store = config.open_store()
    assert isinstance(store, SqliteStore)
    store.close()
    governor = config.open_ledger()
    assert governor is not None
    governor.close()


def test_the_environment_overrides_the_database_and_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FIDUCIARY_DATABASE", "postgresql://u@h/db")
    monkeypatch.setenv("FIDUCIARY_LEDGER", "postgresql://u@h/ledger")
    config = load_config(_write(tmp_path, 'database = "f.db"\n'))
    assert config.database == "postgresql://u@h/db" and config.ledger == "postgresql://u@h/ledger"


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ('database = "f.db"\nbogus = 1\n', "unknown key"),
        ("", "database is required"),
        ('database = "f.db"\nledger = 3\n', "ledger is"),
        ('database = "f.db"\n[charter]\ndigest = "short"\n', "64 lowercase hex"),
        ('database = "f.db"\n[charter]\nwho = "x"\n', "unknown key"),
        ('database = "f.db"\ncharter = 3\n', "is a table"),
        ('database = "f.db"\n[service]\nsigner = "nope"\n', "names no"),
        (
            'database = "f.db"\n[service]\nkey = "k"\nsigner = "s"\n[signers.s]\ntype = "aws-kms"\nkey_id = "k"\n',
            "not both",
        ),
        ('database = "f.db"\nschema = 3\n', "schema is a name"),
        ('database = "f.db"\n[charter]\nfile = ""\n', "is a path"),
        ('database = "f.db"\nsigners = 3\n', "tables"),
        ("not toml [", "Expected"),
    ],
)
def test_a_configuration_that_says_what_it_may_not_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str, match: str
) -> None:
    monkeypatch.delenv("FIDUCIARY_DATABASE", raising=False)
    monkeypatch.delenv("FIDUCIARY_LEDGER", raising=False)
    with pytest.raises(ConfigError, match=match):
        load_config(_write(tmp_path, text))


def test_a_configuration_without_its_root_or_service_says_so() -> None:
    config = Config(database="x.db")
    with pytest.raises(ConfigError, match="pins no digest"):
        config.pinned()
    with pytest.raises(ConfigError, match="names no key"):
        config.service()
    assert config.open_ledger() is None


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        ({"bad name!": {"type": "http"}}, "a signer is a table"),
        ({"s": "x"}, "a signer is a table"),
        ({"s": {"type": "gcp"}}, "type is"),
        ({"s": {"type": "http", "url": "https://k", "key": "k", "extra": 1}}, "unknown key"),
        ({"s": {"type": "http", "url": "https://k"}}, "key is required"),
        ({"s": {"type": "aws-kms"}}, "key_id is required"),
    ],
)
def test_signers_are_checked(raw: object, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        parse_signers(raw)


def test_a_signer_opens_from_a_file_an_http_service_or_kms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = generate_key(tmp_path / "k.key")
    assert load_key(tmp_path / "k.key").key_id == key.key_id
    with pytest.raises(FileExistsError):
        generate_key(tmp_path / "k.key")
    with KeyService() as service:
        service.add("svc", e.signer("service"))
        monkeypatch.setenv("TOKEN", "t")
        signers = parse_signers(
            {
                "svc": {
                    "type": "http",
                    "url": service.url,
                    "key": "svc",
                    "token_env": "TOKEN",
                    "timeout_seconds": 2,
                    "version": 1,
                }
            }
        )
        remote = open_signer(signers, key=None, signer="svc")
        assert remote.key_id == e.signer("service").key_id
    kms = FakeKms(e.signer("cfo"))
    import agentgov.receipts.kms_aws as kms_module

    spec = SignerSpec("cfo", "aws-kms", {"type": "aws-kms", "key_id": "alias/cfo"})
    monkeypatch.setattr(kms_module.AwsKmsSigner, "__init__", _with_client(kms))
    signer = spec.open()
    assert isinstance(signer, AwsKmsSigner) and signer.key_id == e.signer("cfo").key_id
    with pytest.raises(ValueError, match="one"):
        open_signer({}, key=None, signer=None)
    with pytest.raises(ValueError, match="no \\[signers.x\\]"):
        open_signer({}, key=None, signer="x")
    assert parse_signers(None) == {}


def _with_client(kms: FakeKms) -> object:
    original = AwsKmsSigner.__init__

    def init(
        self: AwsKmsSigner, key_id: str, *, client: object = None, region: str | None = None
    ) -> None:
        original(self, key_id, client=kms, region=region)

    return init

"""``fiduciary.toml`` and the signers it names."""

from __future__ import annotations

from decimal import Decimal
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
        'database = "f.db"\nledger = "l.db"\n[charter]\nfile = "c.json"\n'
        f'digest = "{"a" * 64}"\n[service]\nkey = "s.key"\n',
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
            'database = "f.db"\n[service]\nkey = "k"\nsigner = "s"\n'
            '[signers.s]\ntype = "aws-kms"\nkey_id = "k"\n',
            "not both",
        ),
        ('database = "f.db"\nschema = 3\n', "schema is a name"),
        ('database = "f.db"\n[charter]\nfile = ""\n', "is a path"),
        ('database = "f.db"\nsigners = 3\n', "tables"),
        ("not toml [", "Expected"),
        ('database = "f.db"\nbinding = 3\n', "\\[binding\\] is a table"),
        ('database = "f.db"\n[binding]\nwho = 1\n', "unknown key"),
        ('database = "f.db"\n[binding]\ntools = "t.json"\n', "a list of paths"),
        ('database = "f.db"\n[binding]\ndirectives = [""]\n', "a list of paths"),
        ('database = "f.db"\n[binding]\nchains = 3\n', "is a path"),
        ('database = "f.db"\ninterlock = ""\n', "interlock is a path"),
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
    with pytest.raises(ValueError, match=r"no \[signers\.x\]"):
        open_signer({}, key=None, signer="x")
    assert parse_signers(None) == {}


def _with_client(kms: FakeKms) -> object:
    original = AwsKmsSigner.__init__

    def init(
        self: AwsKmsSigner, key_id: str, *, client: object = None, region: str | None = None
    ) -> None:
        original(self, key_id, client=kms, region=region)

    return init


def test_binding_reads_paths_beside_it_and_keeps_module_names(tmp_path: Path) -> None:
    (tmp_path / "t.json").write_text("{}")
    path = _write(
        tmp_path,
        'database = "f.db"\ninterlock = "i.toml"\n[binding]\n'
        'tools = ["t.json", "acme.tools:refund"]\ndirectives = ["d.txt"]\nchains = "c"\n',
    )
    config = load_config(path)
    assert config.interlock == tmp_path / "i.toml"
    assert config.tools == (str(tmp_path / "t.json"), "acme.tools:refund")
    assert config.directives == (tmp_path / "d.txt",)
    assert config.chains == tmp_path / "c"


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ('in_process = "yes"\n', "in_process"),
        ('[bridge]\nhttp = "nowhere"\n', "HOST:PORT"),
        ("[bridge]\nhttp = 8765\n", "HOST:PORT"),
        ("[bridge]\nskew_seconds = 900\n", "skew_seconds"),
        ("[bridge]\nskew_seconds = true\n", "skew_seconds"),
        ('[bridge.handlers]\nlookup = "no-colon"\n', "module:name"),
        ('[bridge]\nhandlers = "x"\n', "module:name"),
        ('[bridge.pricing.m]\ninput = "4"\noutput = "20"\n', "cache_write"),
        (
            '[bridge.pricing.m]\ninput = "4"\noutput = "x"\ncache_read = "1"\ncache_write = "1"\n',
            "decimal",
        ),
        ("[bridge]\npricing = 3\n", "pricing"),
        ("[executor]\noutbox = 3\n", "outbox"),
        ('[receipts]\nsigner = "nope"\n', "names no"),
        ('[receipts]\nkey = "r.key"\nsigner = "nope"\n', "names no"),
        ("[bridge]\nother = 1\n", "unknown key"),
    ],
)
def test_the_bridge_keys_are_checked(tmp_path: Path, text: str, match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        load_config(_write(tmp_path, 'database = "f.db"\n' + text))


def test_the_bridge_keys_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FIDUCIARY_DATABASE", raising=False)
    key = tmp_path / "r.key"
    key.write_text("ab" * 32 + "\n")
    key.chmod(0o600)
    config = load_config(
        _write(
            tmp_path,
            'database = "f.db"\nin_process = "tool-calls-only"\n'
            '[executor]\noutbox = "back.db"\n'
            '[receipts]\nlog = "receipts.jsonl"\nkey = "r.key"\n'
            '[bridge]\nhttp = "[::1]:0"\nskew_seconds = 9\n'
            '[bridge.handlers]\nlookup_order = "tests.bridge_kit:lookup"\n'
            'bad = "tests.bridge_kit:ADJUST"\n'
            '[bridge.pricing.claude-opus-5-5]\ninput = "4"\noutput = "20"\n'
            'cache_read = "0.20"\ncache_write = "5"\n',
        )
    )
    assert config.in_process == "tool-calls-only" and config.skew_seconds == 9
    assert config.bridge_http == "[::1]:0" and config.executor_outbox == str(tmp_path / "back.db")
    assert config.receipts_log == tmp_path / "receipts.jsonl"
    assert config.receipts().public_key() is not None
    price = config.pricing()["claude-opus-5-5"]
    assert (price.input_usd_per_mtok, price.cache_write_usd_per_mtok) == (Decimal(4), Decimal(5))
    with pytest.raises(ConfigError, match=r"bad: tests\.bridge_kit:ADJUST is not callable"):
        config.load_handlers()
    missing = Config(database="f.db", handlers={"x": "no.such.module:x"})
    with pytest.raises(ConfigError, match=r"no\.such\.module"):
        missing.load_handlers()
    assert Config(database="f.db", service_key=key).receipts() is not None
    assert Config(database="f.db").operators_log() is None
    assert Config(database="f.db").operator_keyring() is None
    with pytest.raises(ConfigError, match="names no deployment"):
        Config(database="f.db").interlock_config()
    with pytest.raises(ConfigError, match=r"interlock\.toml"):
        Config(database="f.db", interlock=tmp_path / "interlock.toml").interlock_config()


def test_the_bridge_needs_its_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fiduciary.bridge import Fiduciary

    monkeypatch.delenv("FIDUCIARY_LEDGER", raising=False)
    monkeypatch.delenv("FIDUCIARY_DATABASE", raising=False)
    with pytest.raises(ConfigError, match="AgentGov ledger"):
        Fiduciary.from_config(_write(tmp_path, 'database = "f.db"\n'))
    path = _write(tmp_path, 'database = "f.db"\nledger = "l.db"\n')
    with pytest.raises(ConfigError, match="no key and no signer"):
        Fiduciary.from_config(path)
    key = tmp_path / "s.key"
    key.write_text("cd" * 32 + "\n")
    key.chmod(0o600)
    path = _write(tmp_path, 'database = "f.db"\nledger = "l.db"\n[service]\nkey = "s.key"\n')
    with pytest.raises(ConfigError):  # no deployment: what it opened is closed again
        Fiduciary.from_config(path)
    from agentgov import BudgetManager

    BudgetManager.open_sqlite(str(tmp_path / "l.db")).close()  # the ledger's writer is free


def test_an_outbox_that_does_not_open_is_a_configuration_error(tmp_path: Path) -> None:
    from fiduciary.config import _outbox

    opened: list[object] = []
    with pytest.raises(ConfigError, match="outbox"):
        _outbox(str(tmp_path / "no" / "such" / "dir.db"), opened)  # type: ignore[arg-type]
    with pytest.raises(ConfigError, match="outbox"):
        _outbox("postgresql://nobody@127.0.0.1:1/none", opened)  # type: ignore[arg-type]
    assert opened == []

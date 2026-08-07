import argparse
import pathlib

import pytest
import yaml

from loom.cli import main as cli_main
from loom.cli.main import config_get, config_list, config_set
from loom.config.database_config import DatabaseConfig
from loom.config.root_config import RootConfig


def test_default_dsn():
    config = DatabaseConfig()
    assert config.dsn == 'postgresql+psycopg://loom@localhost:5432/loom'


def test_dsn_includes_password_when_set():
    config = DatabaseConfig(password='secret')
    assert config.dsn == 'postgresql+psycopg://loom:secret@localhost:5432/loom'


def test_root_config_has_database_section(tmp_path):
    root = RootConfig(config_path=tmp_path / 'config.yaml')
    assert root.database.database == 'loom'


def test_dsn_excluded_from_model_dump():
    config = DatabaseConfig(password='secret')
    assert 'dsn' not in config.model_dump()


@pytest.mark.asyncio
async def test_config_set_password_end_to_end(
    tmp_path: pathlib.Path, capsys, monkeypatch
):
    """Regression test for the config-set/save/get password workflow.

    Covers points 1-5 of the finding: setting a SecretStr field via the CLI
    function must not crash, the on-disk file must hold the real password,
    display commands must mask it, a second save must not clobber it, and
    the dsn read path must still see the real value.
    """
    # Prevent rich from soft-wrapping the printed YAML at the default
    # width-80 fallback it uses when stdout isn't a tty (capsys), which
    # would otherwise corrupt line-based/yaml.safe_load assertions below.
    monkeypatch.setattr(cli_main.console, 'width', 400)

    config_path = tmp_path / 'e2e-test.yaml'
    config = RootConfig(config_path=config_path)

    # (1) config_set must succeed without raising.
    set_args = argparse.Namespace(key='database.password', value='sup3rs3cret')
    exit_code = await config_set(config, set_args)
    assert exit_code == 0

    # The in-memory value is a real SecretStr, not a bare string.
    assert config.database.password.get_secret_value() == 'sup3rs3cret'

    # (3) The on-disk file holds the REAL password in plaintext.
    on_disk = yaml.safe_load(config_path.read_text())
    assert on_disk['database']['password'] == 'sup3rs3cret'

    # Each CLI invocation is a fresh process that reloads from disk, so
    # mirror that here rather than reusing the in-memory `config` object
    # (which never went through `model_validate` and would trip
    # `exclude_unset` in `config_get`).
    reloaded_for_display = RootConfig.load(config_path)

    # (2) config_get masks the password in displayed output.
    capsys.readouterr()
    get_args = argparse.Namespace(key='database.password')
    exit_code = await config_get(reloaded_for_display, get_args)
    assert exit_code == 0
    get_output = capsys.readouterr().out
    assert 'sup3rs3cret' not in get_output
    assert '**********' in get_output

    # config_list also masks the password, and -- because it must call
    # `model_dump(mode='json')` rather than a bare `model_dump()` -- its
    # output is free of PyYAML's unsafe `!!python/object` tags (which is how
    # a raw `SecretStr`/`pathlib.Path` would otherwise leak or break
    # round-tripping) and is itself `yaml.safe_load`-able.
    capsys.readouterr()
    exit_code = await config_list(reloaded_for_display, argparse.Namespace())
    assert exit_code == 0
    list_output = capsys.readouterr().out
    assert 'sup3rs3cret' not in list_output
    assert '**********' in list_output
    assert '!!python/object' not in list_output
    safely_loaded = yaml.safe_load(list_output)
    assert safely_loaded['database']['password'] == '**********'

    # (4) A second save() (as happens on every CLI invocation) must not
    # overwrite the real on-disk password with the masked placeholder.
    reloaded = RootConfig.load(config_path)
    reloaded.save()
    on_disk_again = yaml.safe_load(config_path.read_text())
    assert on_disk_again['database']['password'] == 'sup3rs3cret'

    # (5) The dsn read path is unaffected and still embeds the real secret.
    assert reloaded.database.dsn == (
        'postgresql+psycopg://loom:sup3rs3cret@localhost:5432/loom'
    )

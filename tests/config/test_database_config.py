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

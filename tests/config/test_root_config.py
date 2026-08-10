import stat

from loom.config import RootConfig


def test_save_restricts_config_file_to_owner_only(tmp_path):
    """The config carries cleartext secrets once saved; it must not be
    group/world readable, same as any credentials file."""
    config_path = tmp_path / 'config.yaml'
    config = RootConfig(config_path=config_path)

    config.save()

    mode = stat.S_IMODE(config_path.stat().st_mode)
    assert mode == 0o600

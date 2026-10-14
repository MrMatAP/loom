import pathlib
from typing import Self

import yaml
from pydantic import BaseModel, Field, computed_field

from loom import __version__


class ConfigFile(BaseModel):
    """
    Base class for a top-level configuration model backed by one YAML file.

    There is deliberately no single root configuration: `UserConfig` (`loom`),
    `AdminConfig` (`loom-admin`), `ApiServerConfig` and `McpServerConfig` each live
    in their own file, so a process only ever has access to the secrets of
    its own trust domain (see docs/adr/0002-split-cli-and-config-by-trust-
    domain.md).
    """

    config_path: pathlib.Path = Field(
        description='The configuration file backing this object'
    )

    @computed_field
    @property
    def version(self) -> str:
        """The current version of loom."""
        return __version__

    def save(self) -> None:
        """
        Save the current in-memory configuration to disk.

        The file may carry cleartext secrets (database password, cached CLI
        session tokens) once `reveal_secrets` is set below, so it's
        restricted to owner-only access same as any credentials file.
        """
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with self.config_path.open('w') as f:
            yaml.dump(
                self.model_dump(
                    mode='json',
                    exclude_none=True,
                    exclude_computed_fields=True,
                    context={'reveal_secrets': True},
                ),
                f,
            )
        self.config_path.chmod(0o600)

    @classmethod
    def load(cls, config_path: pathlib.Path) -> Self:
        """
        Load the configuration from disk, or return an all-defaults
        instance bound to `config_path` if the file doesn't exist yet.
        """
        if config_path.exists():
            with config_path.open() as f:
                data = yaml.safe_load(f) or {}
            data['config_path'] = config_path
            return cls.model_validate(data)
        return cls(config_path=config_path)

import pathlib
from typing import Any

import yaml
from pydantic import BaseModel, Field, computed_field

from loom import __version__

from .base import RootConfigAware


class RootConfig(BaseModel):
    """
    Configuration of the kube-eng cluster
    """

    config_path: pathlib.Path = Field(
        description='The configuration file backing this object'
    )

    @computed_field
    @property
    def version(self) -> str:
        """
        The current version of kube-eng

        Returns:
            The current version of kube-eng
        """
        return __version__

    def save(self) -> None:
        """
        Save the current in-memory configuration to disk.
        Returns:
            Nothing
        """
        yaml.dump(
            self.model_dump(
                mode='json', exclude_none=True, exclude_computed_fields=True
            ),
            self.config_path.open('w'),
        )

    @classmethod
    def load(cls, config_path: pathlib.Path) -> 'RootConfig':
        """
        Load the configuration from disk.
        Args:
            config_path (Path): Path to the configuration file.

        Returns:
            An initialised Config object.
        """
        if config_path.exists():
            return cls.model_validate(yaml.safe_load(config_path.open()))
        else:
            return cls(config_path=config_path)

    def model_post_init(self, context: Any, /) -> None:
        """
        Propagate a reference to this root configuration instance down the
        hierarchy. Pydantic invokes this method to let us know that the
        instance is fully initialised.

        Massaging of initial, unset defaults within the hierarchy must occur
        here because individual model_post_init methods within the hierarchy
        execute before this sets a reference to the root config.

        Args:
            context (): Undocumented parameter, appears to always be None
        """
        super().model_post_init(context)
        for field in dict(self).values():
            if issubclass(type(field), RootConfigAware):
                field.propagate_root_config(self)

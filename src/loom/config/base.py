from pydantic import BaseModel

import loom.config


class RootConfigAware(BaseModel):
    """
    The common base class for all configuration models except for the RootConfig
    itself. It defines a reference back to the root configuration instance so
    configuration classes further down in the hierarchy have access to all other
    configuration no matter where it is.

    We accept the slight uglyness for this to be a dunder attribute we set from
    outside __init__. This is required for pydantic to not complain about it.
    """

    _root_config: loom.config.RootConfig = None

    def propagate_root_config(self, root_config: loom.config.RootConfig) -> None:
        """
        Evaluate all attributes of this instance. If the field has a type that
        inherits from this class then recursively invoke ourselves.
        Args:
            root_config (RootConfig): The root configuration instance
        """
        self._root_config = root_config
        for attr in dict(self).values():
            if issubclass(type(attr), RootConfigAware):
                attr.propagate_root_config(root_config)

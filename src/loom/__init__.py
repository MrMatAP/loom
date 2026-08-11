import importlib.metadata
import os
import pathlib

try:
    __version__ = importlib.metadata.version('loom')
except importlib.metadata.PackageNotFoundError:
    # You have not yet installed this as a package, likely because you're hacking on it in some IDE
    __version__ = '0.0.0.dev0'

# Kept for anything already importing the pre-computed constant; prefer
# `default_config_path()` below, which reads `LOOM_CONFIG_PATH` at call
# time rather than baking in whatever `HOME` happened to resolve to at
# import time -- the console-script entry points (`loom-catalog-api`,
# `loom-catalog-mcp`) build their FastAPI `app` at module import, so a
# container's entrypoint must be able to set `LOOM_CONFIG_PATH` before that
# import runs, not depend on `HOME` matching wherever it wrote the file.
__default_config_path__ = pathlib.Path.home() / '.loom'


def default_config_path() -> pathlib.Path:
    """Where a `loom` process looks for its config file absent `--config`:
    `LOOM_CONFIG_PATH` if set, else `~/.loom`."""
    override = os.environ.get('LOOM_CONFIG_PATH')
    return pathlib.Path(override) if override else pathlib.Path.home() / '.loom'

import importlib.metadata
import os
import pathlib

try:
    __version__ = importlib.metadata.version('loom')
except importlib.metadata.PackageNotFoundError:
    # You have not yet installed this as a package, likely because you're hacking on it in some IDE
    __version__ = '0.0.0.dev0'

_CONFIG_DIR = pathlib.Path('.config') / 'loom'


def _config_path(env_var: str, default_name: str) -> pathlib.Path:
    override = os.environ.get(env_var)
    return (
        pathlib.Path(override)
        if override
        else pathlib.Path.home() / _CONFIG_DIR / default_name
    )


# The three configuration files are read at call time rather than import time:
# the API server's console-script entry points build their FastAPI `app` at
# startup, so a container's entrypoint must be able to set the env var before
# that, not depend on `HOME` matching wherever it wrote the file.


def default_config_path() -> pathlib.Path:
    """Where `loom` (the user CLI) looks for its config absent `--config`:
    `LOOM_CONFIG_PATH` if set, else `~/.config/loom/config.yaml`."""
    return _config_path('LOOM_CONFIG_PATH', 'config.yaml')


def default_admin_config_path() -> pathlib.Path:
    """Where `loom-admin` looks for its config absent `--config`:
    `LOOM_ADMIN_CONFIG_PATH` if set, else `~/.config/loom/admin.yaml`."""
    return _config_path('LOOM_ADMIN_CONFIG_PATH', 'admin.yaml')


def default_api_config_path() -> pathlib.Path:
    """Where the Catalog API server looks for its config:
    `LOOM_API_CONFIG_PATH` if set, else `~/.config/loom/api.yaml`."""
    return _config_path('LOOM_API_CONFIG_PATH', 'api.yaml')


def default_mcp_config_path() -> pathlib.Path:
    """Where the Catalog MCP server looks for its config:
    `LOOM_MCP_CONFIG_PATH` if set, else `~/.config/loom/mcp.yaml`."""
    return _config_path('LOOM_MCP_CONFIG_PATH', 'mcp.yaml')

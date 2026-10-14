#!/bin/sh
# Materialize this container's config file from environment variables, then
# exec the real command (loom-catalog-api / loom-catalog-mcp / `loom-admin db
# upgrade` / an interactive `loom ...` invocation).
#
# Which file depends on the command's trust domain (see
# docs/adr/0002-split-cli-and-config-by-trust-domain.md):
#   loom-admin               -> $LOOM_ADMIN_CONFIG_PATH (AdminConfig)
#   loom                     -> nothing is materialized; users log in
#   loom-catalog-api         -> $LOOM_API_CONFIG_PATH (ApiServerConfig)
#   loom-catalog-mcp         -> $LOOM_MCP_CONFIG_PATH (McpServerConfig)
#
# Why not bake config into the image or mount a config.yaml Secret
# directly at the config path: saving a config chmod(0o600)s the file it
# just wrote -- that fails outright against a read-only-mounted Secret
# volume, and the CLIs save on every invocation, so there's no way to avoid
# the write. Sourcing secrets as plain environment variables
# (`secretKeyRef` in Kubernetes, `env_file`/`environment` in Compose)
# instead and writing them into a path *this container* owns -- ideally a
# small `emptyDir` so it survives a read-only root filesystem -- sidesteps
# the problem entirely rather than trying to make `save()` tolerate a
# read-only mount.
set -eu

ROLE="${1:-}"
case "$ROLE" in
  loom)
    exec "$@"
    ;;
  loom-admin)
    CONFIG_PATH="${LOOM_ADMIN_CONFIG_PATH:-$HOME/.config/loom/admin.yaml}"
    ;;
  loom-catalog-mcp)
    CONFIG_PATH="${LOOM_MCP_CONFIG_PATH:-$HOME/.config/loom/mcp.yaml}"
    ;;
  *)
    CONFIG_PATH="${LOOM_API_CONFIG_PATH:-$HOME/.config/loom/api.yaml}"
    ;;
esac

# Fail with a specific, actionable message rather than letting the write
# below surface a bare `[Errno 13] Permission denied` -- the most likely
# cause is the config path pointing outside the writable `config` emptyDir
# the Kubernetes manifests mount (or that mount missing entirely), not a
# real permissions bug. Probes the directory with a throwaway file rather
# than $CONFIG_PATH itself -- leaving an empty file at $CONFIG_PATH would
# make the next load treat it as an existing-but-empty config.
CONFIG_DIR="$(dirname "$CONFIG_PATH")"
mkdir -p "$CONFIG_DIR"
PROBE="$CONFIG_DIR/.entrypoint-write-test"
if ! (: >"$PROBE") 2>/dev/null; then
  echo "entrypoint.sh: cannot write to $CONFIG_DIR (for $CONFIG_PATH)." >&2
  echo "This needs a writable directory -- on a read-only root filesystem" >&2
  echo "(the Kubernetes manifests under deploy/kubernetes/ set one), mount" >&2
  echo "an emptyDir at its parent directory. See docker/entrypoint.sh." >&2
  exit 1
fi
rm -f "$PROBE"

# Every role here (API, MCP, `loom-admin db upgrade`) talks to Postgres, and
# an unset password isn't a usable default -- it silently produces a
# passwordless DSN that only fails once something makes a real query,
# *after* /healthz has already reported ready. Fail at startup instead,
# where it's obvious. Set LOOM_ALLOW_NO_DB_PASSWORD=1 for the rare
# genuinely-passwordless (trust-auth) Postgres setup.
if [ -z "${LOOM_DB_PASSWORD:-}" ] && [ "${LOOM_ALLOW_NO_DB_PASSWORD:-}" != '1' ]; then
  echo "entrypoint.sh: LOOM_DB_PASSWORD is unset/empty. Refusing to start" >&2
  echo "with no database password configured -- check the Secret this" >&2
  echo "container's LOOM_DB_PASSWORD env var reads from (see" >&2
  echo "deploy/kubernetes/secret.example.yaml). Set" >&2
  echo "LOOM_ALLOW_NO_DB_PASSWORD=1 to proceed anyway (trust-auth Postgres)." >&2
  exit 1
fi

if [ "$ROLE" = 'loom-admin' ]; then
  set_if_present() {
    key="$1"
    value="$2"
    if [ -n "$value" ]; then
      loom-admin --config "$CONFIG_PATH" config set "$key" "$value" >/dev/null
    fi
  }
  set_if_present database.host "${LOOM_DB_HOST:-}"
  set_if_present database.port "${LOOM_DB_PORT:-}"
  set_if_present database.database "${LOOM_DB_NAME:-}"
  set_if_present database.username "${LOOM_DB_USERNAME:-}"
  set_if_present database.password "${LOOM_DB_PASSWORD:-}"
  set_if_present auth.audience "${LOOM_AUTH_AUDIENCE:-}"
  set_if_present auth.discovery_url "${LOOM_AUTH_DISCOVERY_URL:-}"
else
  if [ "$ROLE" = 'loom-catalog-mcp' ]; then
    SERVER=mcp
    export LOOM_MCP_CONFIG_PATH="$CONFIG_PATH"
  else
    SERVER=api
    export LOOM_API_CONFIG_PATH="$CONFIG_PATH"
  fi
  loom-admin "$SERVER" from-env
fi

exec "$@"

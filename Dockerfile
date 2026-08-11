# syntax=docker/dockerfile:1
#
# Builds one image serving three roles, selected at `docker run`/Kubernetes
# `command` time rather than by building three images:
#   loom-catalog-api   REST API   (port 8000)
#   loom-catalog-mcp   MCP server (port 8100)
#   loom               CLI, e.g. `loom db upgrade` as a migration Job
# All three come from the same `loom` package (see pyproject.toml's
# [project.scripts]), so one reproducible build covers every role.
#
# Reproducibility: dependency resolution is pinned by uv.lock (`uv sync
# --frozen` fails closed if pyproject.toml and uv.lock have drifted rather
# than silently re-resolving); the base image tag tracks the latest 3.14.x
# patch, which is the same trade-off ci.yml's `python-version: 3.14.2`
# makes. For a fully pinned supply chain, replace `python:3.14-slim` below
# with a digest pin (`python:3.14-slim@sha256:...`).

FROM ghcr.io/astral-sh/uv:0.12.2 AS uv

FROM python:3.14-slim AS builder
COPY --from=uv /uv /uvx /usr/local/bin/
WORKDIR /src

# Dependency layer cached separately from application code -- an
# src/-only change doesn't re-resolve or re-download dependencies.
COPY pyproject.toml uv.lock ./
COPY README.md LICENSE ./
COPY src ./src

# ARG, not ENV: only needs to be visible to this RUN, not baked into the
# final image's environment (the version is already frozen into the
# installed package's metadata by then).
ARG VERSION=0.0.0.dev0
RUN MRMAT_VERSION=${VERSION} uv sync --frozen --no-dev --no-editable

FROM python:3.14-slim AS runtime
# Fixed UID/GID (not --system's arbitrary allocation) so Kubernetes
# manifests can pin securityContext.runAsUser/runAsGroup deterministically.
RUN groupadd --gid 1000 loom \
    && useradd --uid 1000 --gid loom --home-dir /home/loom --create-home loom
COPY --from=builder /src/.venv /opt/venv
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENV PATH="/opt/venv/bin:${PATH}" \
    HOME=/home/loom \
    PYTHONUNBUFFERED=1

USER loom
WORKDIR /home/loom
EXPOSE 8000 8100

ENTRYPOINT ["entrypoint.sh"]
CMD ["loom-catalog-api"]

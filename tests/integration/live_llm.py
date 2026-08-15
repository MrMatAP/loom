"""Env-var gating shared by the `live_llm` integration suite.

Exercises a real OpenAI-compatible LLM server (LM Studio, vLLM, ollama's
OpenAI-compatible surface, ...) over the network -- mirrors `live.py`'s
`live_idp` gating and `live_db.py`'s `live_db` gating, but for a live model
server instead of a live Keycloak/Postgres instance. Self-skips (not a
pytest collection arg) whenever nothing answers at `LOOM_LLM_BASE_URL`
(default `http://localhost:1234`, LM Studio's default), so `pytest` stays
green without one running. Also self-skips if the `live-llm` dependency
group (`langchain`, `langchain-openai`) isn't installed -- see
`pyproject.toml`.

Run explicitly with:

    uv sync --group live-llm
    pytest tests/integration/ -m live_llm
"""

import os

import httpx

BASE_URL_ENV_VAR = 'LOOM_LLM_BASE_URL'
DEFAULT_BASE_URL = 'http://localhost:1234'


def live_llm_base_url() -> str:
    """The live server's origin -- no `/v1` suffix. What the OpenAI-
    compatible protocol path under that origin looks like is a calling
    client's concern, not something the Catalog's `ModelEndpoint.base_url`
    pins down (see `test_live_llm.py`'s module docstring)."""
    return os.environ.get(BASE_URL_ENV_VAR, DEFAULT_BASE_URL).rstrip('/')


def probe_live_llm(base_url: str) -> tuple[str | None, str | None]:
    """One request against the server's OpenAI-compatible `/v1/models`
    endpoint -- both the reachability check and the model-discovery call,
    deliberately combined rather than run as two separate probes: the
    model id it returns is what the suite actually needs, and there's no
    id-independent notion of "reachable" worth checking on its own.
    Returns `(model_id, None)` on success, `(None, skip_reason)`
    otherwise -- "nothing is listening" and "a server is up but has no
    model loaded" are distinct, both useful skip reasons to see in a
    report rather than collapsed into one generic skip."""
    try:
        response = httpx.get(f'{base_url}/v1/models', timeout=3.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        return None, f'no live LLM server reachable at {base_url}: {exc}'
    models = response.json().get('data', [])
    if not models:
        return None, f'LLM server at {base_url} is up but has no model loaded'
    return models[0]['id'], None

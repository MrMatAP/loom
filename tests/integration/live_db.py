"""Env-var gating shared by the `live_db` integration suite.

Mirrors `live.py`'s `live_idp` gating, but for a real Postgres instance
instead of a real Keycloak instance. Self-skips (not a pytest collection
arg) whenever `LOOM_DB_HOST` isn't set, so `pytest` stays green without a
live database. Run explicitly with:

    pytest tests/integration/ -m live_db

Reuses `IT_PREFIX` from `live.py` for anything this suite creates, so a run
is trivially recognizable (and safe to hand-delete) against whatever else
lives in the same database. Every fixture that creates a row tears it down
at the end of its scope regardless.
"""

from .live import requires_env_vars

DB_HOST_ENV_VAR = 'LOOM_DB_HOST'

requires_live_db = requires_env_vars(DB_HOST_ENV_VAR)

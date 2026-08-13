"""Content Engine configuration - CE-1 scope only.

The only thing CE-1 needs configured is where the SQLite database file
lives. No credentials, no general settings system - those are explicitly
out of scope for CE-1 (see the Architecture Freeze Review: credential
storage is deferred to CE-9a, when it's actually needed).
"""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "seoulkit-content-engine" / "content_engine.db"
_ENV_VAR = "CONTENT_ENGINE_DB_PATH"


def resolve_db_path() -> Path:
    override = os.environ.get(_ENV_VAR)
    return Path(override) if override else _DEFAULT_DB_PATH

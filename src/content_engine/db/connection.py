"""Thin SQLite connection helper - CE-1 scope only.

`get_connection()` does exactly two things: open the database file
(creating its parent directory if needed) and apply `schema.sql`. It does
not know about `Job`, does not offer `insert`/`get`/`update` methods, and
is not a persistence-abstraction layer - that's explicitly deferred past
CE-1 (see the Architecture Freeze Review). Callers that need to read or
write rows use `sqlite3` directly, e.g. as `tests/test_ce_jobs_models.py`
does for its round-trip test.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA_PATH.read_text())
    return conn

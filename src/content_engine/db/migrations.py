"""Post-CE-1 `jobs` schema additions - CE-2B scope only.

`db/schema.sql` (CE-1, frozen) keeps defining only the original CE-1 shape
of `jobs` - it is never edited again, on the same principle CE-1/CE-2A
already established (a completed phase's own file is not reopened just
because a later phase needs more columns). Every column added after CE-1
lives here instead, as a small, idempotent `ALTER TABLE` list applied by
`apply_migrations()` right after `schema.sql` runs.

Both statements below exist because CE-2B is the first phase that actually
reads/writes them - `content_package_id` links a job back to the
`ContentPackage` it belongs to (CE-1's own Architecture Freeze Review
explicitly deferred this exact column for exactly this reason: "not decided
now... left to CE-4a [and beyond], once the real query patterns against
`jobs` are known" - CE-2B is where they became known), and `payload` carries
the one thing this project has never had a file format for: a human's
`Stage2ShotOutput`/`ShotQcDecision`/`HumanVoiceTimingInput`/
`HumanSemanticSyncSegment`/`HumanOverlayDecision`/`HumanSfxDecision` values,
supplied at job-creation time rather than invented as a new on-disk
convention (see `jobs/creation.py`/`jobs/artifacts.py`).

`ALTER TABLE ... ADD COLUMN` has no `IF NOT EXISTS` guard in SQLite, so each
statement is tried and a `duplicate column name` `OperationalError` is
swallowed - this makes `apply_migrations()` safe to call on both a
brand-new database (where `schema.sql` already created the un-migrated
shape a moment earlier) and an already-migrated one, with no separate
version-tracking table.
"""

from __future__ import annotations

import sqlite3

_MIGRATIONS = (
    "ALTER TABLE jobs ADD COLUMN content_package_id TEXT REFERENCES content_packages(id)",
    "ALTER TABLE jobs ADD COLUMN payload TEXT",
)


def apply_migrations(conn: sqlite3.Connection) -> None:
    for statement in _MIGRATIONS:
        try:
            conn.execute(statement)
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc):
                raise
    conn.commit()

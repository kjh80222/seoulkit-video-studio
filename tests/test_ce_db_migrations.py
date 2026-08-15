import sqlite3

from content_engine.db.connection import get_connection
from content_engine.db.migrations import apply_migrations


def _columns(conn: sqlite3.Connection) -> list[str]:
    return [row[1] for row in conn.execute("PRAGMA table_info(jobs)")]


def test_apply_migrations_adds_content_package_id_and_payload(tmp_path):
    conn = get_connection(tmp_path / "content_engine.db")

    columns = _columns(conn)

    assert "content_package_id" in columns
    assert "payload" in columns


def test_apply_migrations_is_idempotent(tmp_path):
    conn = get_connection(tmp_path / "content_engine.db")

    apply_migrations(conn)  # must not raise on an already-migrated DB
    apply_migrations(conn)

    assert _columns(conn).count("content_package_id") == 1
    assert _columns(conn).count("payload") == 1


def test_apply_migrations_adds_columns_to_a_pre_ce2b_schema(tmp_path):
    db_path = tmp_path / "content_engine.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            job_type TEXT NOT NULL,
            state TEXT NOT NULL,
            stage TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT
        );
        """
    )
    conn.commit()
    assert "content_package_id" not in _columns(conn)

    apply_migrations(conn)

    assert "content_package_id" in _columns(conn)
    assert "payload" in _columns(conn)


def test_migrated_jobs_table_still_accepts_a_row_with_only_the_original_columns(tmp_path):
    conn = get_connection(tmp_path / "content_engine.db")

    conn.execute(
        "INSERT INTO jobs (id, job_type, state, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("job-1", "stage2_input_build", "pending", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    )
    conn.commit()

    row = conn.execute(
        "SELECT content_package_id, payload FROM jobs WHERE id = ?", ("job-1",)
    ).fetchone()
    assert row == (None, None)

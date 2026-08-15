from pathlib import Path

from content_engine.db.connection import get_connection


def test_get_connection_creates_the_jobs_table(tmp_path):
    conn = get_connection(tmp_path / "content_engine.db")

    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'")
    assert cursor.fetchone() is not None


def test_jobs_table_has_exactly_the_expected_columns(tmp_path):
    conn = get_connection(tmp_path / "content_engine.db")

    columns = [row[1] for row in conn.execute("PRAGMA table_info(jobs)")]

    assert columns == [
        "id", "job_type", "state", "stage", "error_message",
        "created_at", "updated_at", "started_at", "completed_at",
        "content_package_id", "payload",
    ]


def test_get_connection_creates_missing_parent_directories(tmp_path):
    nested_path = tmp_path / "a" / "b" / "content_engine.db"

    get_connection(nested_path)

    assert nested_path.exists()


def test_get_connection_is_idempotent_when_called_twice(tmp_path):
    db_path = tmp_path / "content_engine.db"

    get_connection(db_path)
    conn = get_connection(db_path)  # must not raise on the already-existing table

    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'")
    assert cursor.fetchone() is not None

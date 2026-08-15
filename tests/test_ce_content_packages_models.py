from content_engine.content_packages.models import ContentPackage
from content_engine.db.connection import get_connection


def test_get_connection_creates_the_content_packages_table(tmp_path):
    conn = get_connection(tmp_path / "content_engine.db")

    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='content_packages'")
    assert cursor.fetchone() is not None


def test_content_packages_table_has_exactly_the_expected_columns(tmp_path):
    conn = get_connection(tmp_path / "content_engine.db")

    columns = [row[1] for row in conn.execute("PRAGMA table_info(content_packages)")]

    assert columns == ["id", "topic", "project_dir", "created_at", "updated_at"]


def test_content_package_dataclass_holds_exactly_the_expected_fields():
    package = ContentPackage(
        id="pkg-1",
        topic="서울이 여러 번 파괴되고도 살아남은 역사",
        project_dir="/tmp/somewhere/pkg-1",
        created_at="2026-08-15T00:00:00+00:00",
        updated_at="2026-08-15T00:00:00+00:00",
    )

    assert not hasattr(package, "status")
    assert package.topic == "서울이 여러 번 파괴되고도 살아남은 역사"


def test_content_package_round_trips_through_raw_sql_insert_and_select(tmp_path):
    # No repository/DAO in CE-4a either (same CE-1 Architecture Freeze
    # principle) - this test does its own raw INSERT/SELECT.
    conn = get_connection(tmp_path / "content_engine.db")

    original = ContentPackage(
        id="pkg-42",
        topic="test topic",
        project_dir="/tmp/somewhere/pkg-42",
        created_at="2026-08-15T10:00:00+00:00",
        updated_at="2026-08-15T10:05:00+00:00",
    )

    conn.execute(
        "INSERT INTO content_packages (id, topic, project_dir, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (original.id, original.topic, original.project_dir, original.created_at, original.updated_at),
    )
    conn.commit()

    row = conn.execute(
        "SELECT id, topic, project_dir, created_at, updated_at FROM content_packages WHERE id = ?",
        (original.id,),
    ).fetchone()

    restored = ContentPackage(id=row[0], topic=row[1], project_dir=row[2], created_at=row[3], updated_at=row[4])

    assert restored == original

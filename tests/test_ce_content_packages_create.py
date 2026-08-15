import json
import sqlite3
import subprocess
import uuid
from pathlib import Path

import pytest

from content_engine.content_packages import create as create_module
from content_engine.content_packages.create import WorkspaceAssemblyError, create_content_package
from content_engine.content_packages.workspace import assemble_workspace
from content_engine.db.connection import get_connection
from content_engine.video_studio.adapter import run_preflight


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_ENGINE_PROJECTS_ROOT", str(tmp_path / "projects"))
    return get_connection(tmp_path / "content_engine.db")


def _row(conn: sqlite3.Connection, package_id: str) -> tuple:
    return conn.execute(
        "SELECT id, topic, project_dir, created_at, updated_at FROM content_packages WHERE id = ?", (package_id,)
    ).fetchone()


# --- basic creation ----------------------------------------------------------


def test_create_content_package_inserts_a_row_matching_the_returned_package(conn):
    package = create_content_package(conn, "test topic")

    row = _row(conn, package.id)
    assert row == (package.id, package.topic, package.project_dir, package.created_at, package.updated_at)


def test_create_content_package_creates_the_workspace_on_disk(conn):
    package = create_content_package(conn, "test topic")

    project_dir = Path(package.project_dir)
    assert project_dir.is_dir()
    assert (project_dir / "clips").is_dir()


def test_create_content_package_id_is_a_valid_uuid4(conn):
    package = create_content_package(conn, "test topic")

    parsed = uuid.UUID(package.id)
    assert parsed.version == 4
    assert str(parsed) == package.id


def test_create_content_package_same_topic_twice_yields_two_distinct_packages(conn):
    first = create_content_package(conn, "same topic")
    second = create_content_package(conn, "same topic")

    assert first.id != second.id
    assert first.project_dir != second.project_dir


# --- topic validation ---------------------------------------------------------


def test_create_content_package_rejects_empty_topic(conn):
    with pytest.raises(ValueError):
        create_content_package(conn, "")


def test_create_content_package_rejects_whitespace_only_topic(conn):
    with pytest.raises(ValueError):
        create_content_package(conn, "   ")


def test_create_content_package_strips_leading_and_trailing_whitespace(conn):
    package = create_content_package(conn, "  hello world  ")

    assert package.topic == "hello world"


def test_create_content_package_round_trips_a_korean_topic(conn):
    topic = "서울이 여러 번 파괴되고도 살아남은 역사"

    package = create_content_package(conn, topic)

    assert package.topic == topic
    assert _row(conn, package.id)[1] == topic


# --- partial failure: DB-first ordering ---------------------------------------


def test_db_insert_failure_leaves_no_row_and_no_directory(conn, tmp_path, monkeypatch):
    class _FailingConn:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("simulated DB failure")

    projects_root = tmp_path / "projects"

    with pytest.raises(sqlite3.OperationalError):
        create_content_package(_FailingConn(), "test topic")

    assert conn.execute("SELECT COUNT(*) FROM content_packages").fetchone()[0] == 0
    assert not projects_root.exists() or list(projects_root.iterdir()) == []


# --- partial failure: workspace assembly failure after commit -----------------


def test_workspace_assembly_failure_raises_wrapped_error_but_row_survives(conn, monkeypatch):
    def _boom(project_dir):
        raise OSError("simulated filesystem failure")

    monkeypatch.setattr(create_module, "assemble_workspace", _boom)

    with pytest.raises(WorkspaceAssemblyError) as exc_info:
        create_content_package(conn, "test topic")

    row = _row(conn, exc_info.value.content_package_id)
    assert row is not None
    assert row[2] == exc_info.value.project_dir


def test_workspace_assembly_error_carries_the_exact_package_id_and_project_dir(conn, monkeypatch):
    def _boom(project_dir):
        raise OSError("simulated filesystem failure")

    monkeypatch.setattr(create_module, "assemble_workspace", _boom)

    with pytest.raises(WorkspaceAssemblyError) as exc_info:
        create_content_package(conn, "test topic")

    row = _row(conn, exc_info.value.content_package_id)
    assert exc_info.value.project_dir == row[2]
    assert isinstance(exc_info.value.cause, OSError)


def test_workspace_assembly_error_is_recoverable_by_retrying_assemble_workspace(conn, monkeypatch):
    def _boom(project_dir):
        raise OSError("simulated filesystem failure")

    monkeypatch.setattr(create_module, "assemble_workspace", _boom)
    with pytest.raises(WorkspaceAssemblyError) as exc_info:
        create_content_package(conn, "test topic")
    monkeypatch.undo()

    recovered_project_dir = Path(exc_info.value.project_dir)
    assemble_workspace(recovered_project_dir)  # the real function, un-monkeypatched

    assert recovered_project_dir.is_dir()
    assert (recovered_project_dir / "clips").is_dir()


# --- CE-3 connection: a workspace this module builds is usable by CE-3 -------


def _colored_clip(path: Path, color: str, duration_s: float) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:size=320x240:rate=10:duration={duration_s}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        capture_output=True, text=True, check=True,
    )


def test_a_workspace_created_here_is_usable_by_ce3_run_preflight(conn):
    # CE-4a never writes edit_plan.json/clip_manifest.json/clips - the test
    # supplies them itself, exactly as a human + Stage 1-4 would, to prove
    # the directory shape this module produces doesn't conflict with CE-3.
    package = create_content_package(conn, "ce3 connection check")
    project_dir = Path(package.project_dir)

    _colored_clip(project_dir / "clips" / "shot_1a_flow.mp4", "navy", 2)

    plan = {
        "schema_version": "1.0", "project": "ce4a-check", "format": "MINI", "status": "READY",
        "timing_grade_default": "EXACT",
        "voice": {"audio_file": None, "aligned": False, "alignment_file": None, "total_duration_ms": 2000},
        "segments": [{
            "beat": 1, "shot": "1A", "start_ms": 0, "end_ms": 2000, "timing_grade": "EXACT",
            "source_clip": "clips/shot_1a_flow.mp4", "source_duration_ms": 2000,
            "usable_start_ms": 0, "usable_end_ms": 2000, "key_event_end_ms": 2000, "settle_start_ms": 2000,
            "clip_in_ms": 0, "clip_out_ms": 2000, "camera_behavior": "locked-off", "hold_strategy": "none",
            "hold_ms": 0, "trim_reason": None, "voice_text": "", "status": "OK", "status_note": None,
        }],
        "overlays": [], "subtitles": [],
        "audio_layers": {"voice": "authoritative", "sfx_source": {"mode": "none", "clips": []},
                          "bgm": {"mode": "none", "file": None, "reference_db": None}},
        "warnings": [],
    }
    (project_dir / "edit_plan.json").write_text(json.dumps(plan))
    manifest = {"note": "test fixture", "clips": [{
        "shot": "1A", "file": "clips/shot_1a_flow.mp4", "camera_behavior": "locked-off",
        "source_duration_ms": 2000, "usable_start_ms": 0, "usable_end_ms": 2000,
        "key_event_end_ms": 2000, "settle_start_ms": 2000,
    }]}
    (project_dir / "clips" / "clip_manifest.json").write_text(json.dumps(manifest))

    result = run_preflight(project_dir)

    assert result.ok is True
    assert result.payload["effective_status"] == "READY"

import json
import threading
from pathlib import Path

import pytest

from content_engine.content_packages.create import create_content_package
from content_engine.db.connection import get_connection
from content_engine.jobs.creation import create_job
from content_engine.jobs.transitions import start_job
from content_engine.jobs.worker import run_worker


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_ENGINE_PROJECTS_ROOT", str(tmp_path / "projects"))
    return get_connection(tmp_path / "content_engine.db")


@pytest.fixture
def package(conn):
    return create_content_package(conn, "test topic")


def _write_stage1_table(project_dir: Path) -> None:
    (project_dir / "script").mkdir(parents=True, exist_ok=True)
    data = {
        "beats": [
            {"beat": 1, "narration": "voice text", "visual_purpose": "purpose",
             "screen_number": None, "screen_label": None},
        ],
        "shots": [
            {"shot": "1A", "beat": 1, "shot_type": "wide", "on_screen_text": None},
        ],
    }
    (project_dir / "script" / "stage1_beat_shot_table.json").write_text(json.dumps(data), encoding="utf-8")


def test_run_worker_stops_immediately_when_stop_event_already_set(conn, package):
    job = create_job(conn, package.id, "stage2_input_build")
    stop_event = threading.Event()
    stop_event.set()

    run_worker(conn, stop_event=stop_event)

    row = conn.execute("SELECT state FROM jobs WHERE id = ?", (job.id,)).fetchone()
    assert row[0] == "pending"  # never claimed - the loop body never ran


def test_run_worker_claims_and_dispatches_a_pending_job(conn, package, monkeypatch):
    _write_stage1_table(Path(package.project_dir))
    job = create_job(conn, package.id, "stage2_input_build")
    stop_event = threading.Event()

    import content_engine.jobs.worker as worker_module

    real_dispatch = worker_module.dispatch

    def _dispatch_then_stop(conn, job):
        real_dispatch(conn, job)
        stop_event.set()

    monkeypatch.setattr(worker_module, "dispatch", _dispatch_then_stop)

    run_worker(conn, poll_interval_seconds=0.01, stop_event=stop_event)

    row = conn.execute("SELECT state FROM jobs WHERE id = ?", (job.id,)).fetchone()
    assert row[0] == "complete"


def test_run_worker_waits_poll_interval_when_no_pending_jobs(conn):
    stop_event = threading.Event()
    calls = []
    real_wait = stop_event.wait

    def _wait(timeout):
        calls.append(timeout)
        stop_event.set()
        return real_wait(0)

    stop_event.wait = _wait

    run_worker(conn, poll_interval_seconds=1.23, stop_event=stop_event)

    assert calls == [1.23]


def test_run_worker_processes_multiple_jobs_before_stopping(conn, package, monkeypatch):
    _write_stage1_table(Path(package.project_dir))
    first = create_job(conn, package.id, "stage2_input_build")
    second = create_job(conn, package.id, "stage2_input_build")  # will fail: file already exists after first
    stop_event = threading.Event()

    import content_engine.jobs.worker as worker_module

    real_dispatch = worker_module.dispatch
    calls = {"count": 0}

    def _counting_dispatch(conn, job):
        real_dispatch(conn, job)
        calls["count"] += 1
        if calls["count"] >= 2:
            stop_event.set()

    monkeypatch.setattr(worker_module, "dispatch", _counting_dispatch)

    run_worker(conn, poll_interval_seconds=0.01, stop_event=stop_event)

    first_state = conn.execute("SELECT state FROM jobs WHERE id = ?", (first.id,)).fetchone()[0]
    second_state = conn.execute("SELECT state FROM jobs WHERE id = ?", (second.id,)).fetchone()[0]
    assert first_state == "complete"
    assert second_state == "failed"  # stage2_input.json already exists (FileExistsError)


def test_run_worker_recovers_stale_processing_jobs_on_startup(conn, package):
    stale_job = create_job(conn, package.id, "stage2_input_build")
    start_job(conn, stale_job.id)
    conn.execute(
        "UPDATE jobs SET updated_at = ? WHERE id = ?", ("2000-01-01T00:00:00+00:00", stale_job.id)
    )
    conn.commit()

    fresh_job = create_job(conn, package.id, "stage3_input_build")
    start_job(conn, fresh_job.id)

    stop_event = threading.Event()
    stop_event.set()  # only the startup recovery step should run

    run_worker(conn, stop_event=stop_event, stale_after_seconds=3600)

    stale_row = conn.execute(
        "SELECT state, error_message FROM jobs WHERE id = ?", (stale_job.id,)
    ).fetchone()
    assert stale_row[0] == "failed"
    assert "crash" in stale_row[1]

    fresh_row = conn.execute("SELECT state FROM jobs WHERE id = ?", (fresh_job.id,)).fetchone()
    assert fresh_row[0] == "processing"

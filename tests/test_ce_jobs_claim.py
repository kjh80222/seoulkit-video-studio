import json
import subprocess
from pathlib import Path

import pytest

from content_engine.content_packages.create import create_content_package
from content_engine.db.connection import get_connection
from content_engine.jobs.claim import claim_next_runnable_job
from content_engine.jobs.creation import create_job
from content_engine.jobs.models import JobState
from content_engine.jobs.transitions import InvalidJobTransitionError, start_job


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_ENGINE_PROJECTS_ROOT", str(tmp_path / "projects"))
    return get_connection(tmp_path / "content_engine.db")


@pytest.fixture
def package(conn):
    return create_content_package(conn, "test topic")


def _write_ready_plan(project_dir: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=navy:size=320x240:rate=10:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(project_dir / "clips" / "shot_1a_flow.mp4"),
        ],
        capture_output=True, text=True, check=True,
    )
    plan = {
        "schema_version": "1.0", "project": "claim-test", "format": "MINI", "status": "READY",
        "timing_grade_default": "EXACT",
        "voice": {"audio_file": None, "aligned": False, "total_duration_ms": 2000},
        "segments": [{
            "beat": 1, "shot": "1A", "start_ms": 0, "end_ms": 2000, "timing_grade": "EXACT",
            "source_clip": "clips/shot_1a_flow.mp4", "source_duration_ms": 2000,
            "usable_start_ms": 0, "usable_end_ms": 2000, "key_event_end_ms": 1000, "settle_start_ms": 1500,
            "clip_in_ms": 0, "clip_out_ms": 2000, "camera_behavior": "locked-off",
            "hold_strategy": "none", "hold_ms": 0, "trim_reason": None,
            "voice_text": "text", "status": "OK", "status_note": None,
        }],
        "overlays": [], "subtitles": [],
        "audio_layers": {"voice": "authoritative", "sfx_source": {"mode": "none", "clips": []},
                          "bgm": {"mode": "none", "file": None, "reference_db": None}},
        "warnings": [],
    }
    (project_dir / "edit_plan.json").write_text(json.dumps(plan))


def test_claim_returns_none_when_no_pending_jobs(conn):
    assert claim_next_runnable_job(conn) is None


def test_claim_claims_the_oldest_pending_non_gated_job(conn, package):
    job = create_job(conn, package.id, "stage2_input_build")

    claimed = claim_next_runnable_job(conn)

    assert claimed.id == job.id
    assert claimed.state == JobState.PROCESSING


def test_claim_leaves_a_second_pending_job_untouched(conn, package):
    first = create_job(conn, package.id, "stage2_input_build")
    second = create_job(conn, package.id, "stage3_input_build")

    claimed = claim_next_runnable_job(conn)

    assert claimed.id == first.id
    remaining_state = conn.execute("SELECT state FROM jobs WHERE id = ?", (second.id,)).fetchone()[0]
    assert remaining_state == "pending"


def test_claim_skips_gated_job_when_flow_clips_not_ready(conn, package):
    # empty project_dir: no edit_plan.json at all -> CE-5 readiness reports not ready
    job = create_job(conn, package.id, "preview_render")

    claimed = claim_next_runnable_job(conn)

    assert claimed is None
    row_state = conn.execute("SELECT state FROM jobs WHERE id = ?", (job.id,)).fetchone()[0]
    assert row_state == "pending"


def test_claim_claims_gated_job_once_flow_clips_ready(conn, package):
    _write_ready_plan(Path(package.project_dir))
    job = create_job(conn, package.id, "preview_render")

    claimed = claim_next_runnable_job(conn)

    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.state == JobState.PROCESSING


def test_claim_skips_a_not_ready_gated_job_and_claims_the_next_one(conn, package):
    not_ready = create_job(conn, package.id, "preview_render")
    other_package = create_content_package(conn, "other topic")
    non_gated = create_job(conn, other_package.id, "stage2_input_build")

    claimed = claim_next_runnable_job(conn)

    assert claimed.id == non_gated.id
    row_state = conn.execute("SELECT state FROM jobs WHERE id = ?", (not_ready.id,)).fetchone()[0]
    assert row_state == "pending"


def test_claim_moves_to_next_candidate_if_start_job_loses_the_race(conn, package, monkeypatch):
    lost = create_job(conn, package.id, "stage2_input_build")
    won = create_job(conn, package.id, "stage3_input_build")

    import content_engine.jobs.claim as claim_module

    real_start_job = claim_module.start_job

    def _flaky_start_job(conn, job_id):
        if job_id == lost.id:
            raise InvalidJobTransitionError(job_id, JobState.PENDING, JobState.PROCESSING)
        return real_start_job(conn, job_id)

    monkeypatch.setattr(claim_module, "start_job", _flaky_start_job)

    claimed = claim_next_runnable_job(conn)

    assert claimed.id == won.id


def test_claim_propagates_readiness_infrastructure_failure(conn, package, monkeypatch):
    create_job(conn, package.id, "preview_render")

    import content_engine.jobs.claim as claim_module

    def _boom(project_dir):
        raise RuntimeError("preflight could not run")

    monkeypatch.setattr(claim_module, "check_asset_readiness", _boom)

    with pytest.raises(RuntimeError):
        claim_next_runnable_job(conn)


def test_claim_does_not_move_already_processing_job(conn, package):
    job = create_job(conn, package.id, "stage2_input_build")
    start_job(conn, job.id)

    claimed = claim_next_runnable_job(conn)

    assert claimed is None

import json
import subprocess
from pathlib import Path

import pytest

from content_engine.content_packages.create import create_content_package
from content_engine.db.connection import get_connection
from content_engine.jobs.artifacts import (
    edit_plan_human_input_to_payload,
    qc_decisions_to_payload,
    stage2_outputs_to_payload,
)
from content_engine.jobs.claim import claim_next_runnable_job
from content_engine.jobs.creation import create_job
from content_engine.jobs.dispatch import dispatch
from content_engine.jobs.transitions import start_job
from content_engine.video_planning.models import (
    HumanSemanticSyncSegment,
    HumanVoiceTimingInput,
    ShotQcDecision,
    Stage2ShotOutput,
)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_ENGINE_PROJECTS_ROOT", str(tmp_path / "projects"))
    return get_connection(tmp_path / "content_engine.db")


@pytest.fixture
def package(conn):
    return create_content_package(conn, "test topic")


def _job_row(conn, job_id):
    return conn.execute("SELECT state, error_message FROM jobs WHERE id = ?", (job_id,)).fetchone()


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


def _write_keyframe(project_dir: Path) -> None:
    (project_dir / "keyframes").mkdir(parents=True, exist_ok=True)
    (project_dir / "keyframes" / "shot_1a_keyframe.png").write_bytes(b"fake png")


def _write_voice_audio(project_dir: Path) -> None:
    (project_dir / "voice").mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "2",
            "-c:a", "aac", str(project_dir / "voice" / "final.m4a"),
        ],
        capture_output=True, text=True, check=True,
    )


def _write_flow_clip(project_dir: Path) -> None:
    # duration=3 (not 2) deliberately leaves margin below the usable_end_ms=2000
    # fixtures used throughout this file - real ffprobe-measured duration has a
    # small rounding variance (CE-4e's own tests tolerate +-100ms), so the
    # timing-invariant check (usable_end_ms <= source_duration_ms) needs real
    # headroom, not an exact 2000/2000 match.
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=navy:size=320x240:rate=10:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(project_dir / "clips" / "shot_1a_flow.mp4"),
        ],
        capture_output=True, text=True, check=True,
    )


def _claim_and_run(conn, job_id):
    start_job(conn, job_id)
    row = conn.execute(
        "SELECT id, job_type, state, stage, error_message, created_at, updated_at, "
        "started_at, completed_at, content_package_id, payload FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    from content_engine.jobs.models import Job, JobState
    job = Job(
        id=row[0], job_type=row[1], state=JobState(row[2]), stage=row[3], error_message=row[4],
        created_at=row[5], updated_at=row[6], started_at=row[7], completed_at=row[8],
        content_package_id=row[9], payload=row[10],
    )
    dispatch(conn, job)


# --- full happy-path pipeline: exercises all six handlers end to end -------


def test_full_pipeline_dispatch_produces_every_artifact_and_completes_every_job(conn, package):
    project_dir = Path(package.project_dir)
    _write_stage1_table(project_dir)

    job = create_job(conn, package.id, "stage2_input_build")
    _claim_and_run(conn, job.id)
    assert _job_row(conn, job.id) == ("complete", None)
    assert (project_dir / "stage2_input.json").is_file()

    _write_keyframe(project_dir)
    stage2_outputs = [
        Stage2ShotOutput(shot="1A", beat=1, approved_keyframe_path="keyframes/shot_1a_keyframe.png",
                          story_function="func", continuity="cont")
    ]
    job = create_job(conn, package.id, "stage3_input_build", payload=stage2_outputs_to_payload(stage2_outputs))
    _claim_and_run(conn, job.id)
    assert _job_row(conn, job.id) == ("complete", None)
    assert (project_dir / "stage3_input.json").is_file()

    _write_flow_clip(project_dir)
    decisions = [
        ShotQcDecision(shot="1A", usable_start_ms=0, usable_end_ms=2000, key_event_end_ms=1000,
                        settle_start_ms=1500, camera_behavior="locked-off", qc_passed=True)
    ]
    job = create_job(conn, package.id, "clip_manifest_build", payload=qc_decisions_to_payload(decisions))
    _claim_and_run(conn, job.id)
    assert _job_row(conn, job.id) == ("complete", None)
    assert (project_dir / "clips" / "clip_manifest.json").is_file()

    _write_voice_audio(project_dir)
    voice_input = HumanVoiceTimingInput(timing_grade="EXACT", audio_file="voice/final.m4a", total_duration_ms=None)
    semantic_sync = [HumanSemanticSyncSegment(shot="1A", start_ms=0, end_ms=2000, clip_in_ms=0, clip_out_ms=2000)]
    payload = edit_plan_human_input_to_payload(voice_input, semantic_sync, [], [])
    job = create_job(conn, package.id, "edit_plan_build", payload=payload)
    _claim_and_run(conn, job.id)
    assert _job_row(conn, job.id) == ("complete", None)
    assert (project_dir / "edit_plan.json").is_file()

    create_job(conn, package.id, "preview_render")
    claimed = claim_next_runnable_job(conn)
    assert claimed is not None and claimed.job_type == "preview_render"
    dispatch(conn, claimed)
    assert _job_row(conn, claimed.id) == ("complete", None)

    create_job(conn, package.id, "final_render")
    claimed = claim_next_runnable_job(conn)
    assert claimed is not None and claimed.job_type == "final_render"
    dispatch(conn, claimed)
    assert _job_row(conn, claimed.id) == ("complete", None)


# --- per-handler failure cases -----------------------------------------------


def test_stage2_input_build_fails_when_stage1_file_missing(conn, package):
    job = create_job(conn, package.id, "stage2_input_build")

    _claim_and_run(conn, job.id)

    state, error_message = _job_row(conn, job.id)
    assert state == "failed"
    assert "stage1_beat_shot_table" in error_message


def test_stage3_input_build_fails_on_malformed_payload(conn, package):
    _write_stage1_table(Path(package.project_dir))
    prereq = create_job(conn, package.id, "stage2_input_build")
    _claim_and_run(conn, prereq.id)

    job = create_job(conn, package.id, "stage3_input_build", payload={"not_the_right_key": []})

    _claim_and_run(conn, job.id)

    state, error_message = _job_row(conn, job.id)
    assert state == "failed"
    assert error_message  # non-empty - the raw KeyError message is preserved


def test_clip_manifest_build_fails_when_flow_clip_missing(conn, package):
    project_dir = Path(package.project_dir)
    _write_stage1_table(project_dir)
    prereq = create_job(conn, package.id, "stage2_input_build")
    _claim_and_run(conn, prereq.id)

    _write_keyframe(project_dir)
    stage2_outputs = [
        Stage2ShotOutput(shot="1A", beat=1, approved_keyframe_path="keyframes/shot_1a_keyframe.png",
                          story_function="func", continuity="cont")
    ]
    prereq = create_job(conn, package.id, "stage3_input_build", payload=stage2_outputs_to_payload(stage2_outputs))
    _claim_and_run(conn, prereq.id)

    # no _write_flow_clip() here - the expected Flow clip is never placed on disk
    job = create_job(conn, package.id, "clip_manifest_build", payload=qc_decisions_to_payload([]))

    _claim_and_run(conn, job.id)

    state, error_message = _job_row(conn, job.id)
    assert state == "failed"
    assert "shot_1a_flow.mp4" in error_message


def test_edit_plan_build_fails_on_semantic_sync_identity_mismatch(conn, package):
    project_dir = Path(package.project_dir)
    _write_stage1_table(project_dir)
    prereq = create_job(conn, package.id, "stage2_input_build")
    _claim_and_run(conn, prereq.id)

    _write_keyframe(project_dir)
    stage2_outputs = [
        Stage2ShotOutput(shot="1A", beat=1, approved_keyframe_path="keyframes/shot_1a_keyframe.png",
                          story_function="func", continuity="cont")
    ]
    prereq = create_job(conn, package.id, "stage3_input_build", payload=stage2_outputs_to_payload(stage2_outputs))
    _claim_and_run(conn, prereq.id)

    _write_flow_clip(project_dir)
    decisions = [
        ShotQcDecision(shot="1A", usable_start_ms=0, usable_end_ms=2000, key_event_end_ms=1000,
                        settle_start_ms=1500, camera_behavior="locked-off", qc_passed=True)
    ]
    prereq = create_job(conn, package.id, "clip_manifest_build", payload=qc_decisions_to_payload(decisions))
    _claim_and_run(conn, prereq.id)

    voice_input = HumanVoiceTimingInput(timing_grade="ESTIMATED", audio_file=None, total_duration_ms=2000)
    # semantic_sync references a shot ("9Z") that does not exist in stage3_input.json
    semantic_sync = [HumanSemanticSyncSegment(shot="9Z", start_ms=0, end_ms=2000, clip_in_ms=0, clip_out_ms=2000)]
    payload = edit_plan_human_input_to_payload(voice_input, semantic_sync, [], [])
    job = create_job(conn, package.id, "edit_plan_build", payload=payload)

    _claim_and_run(conn, job.id)

    state, error_message = _job_row(conn, job.id)
    assert state == "failed"
    assert "9Z" in error_message


def test_render_job_gate_rejected_is_recorded_as_failed_with_issues(conn, package):
    project_dir = Path(package.project_dir)
    plan = {
        "schema_version": "1.0", "project": "dispatch-test", "format": "MINI", "status": "READY",
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
    # edit_plan.json references a clip file that is never placed on disk
    (project_dir / "edit_plan.json").write_text(json.dumps(plan))

    job = create_job(conn, package.id, "final_render")
    _claim_and_run(conn, job.id)

    state, error_message = _job_row(conn, job.id)
    assert state == "failed"
    assert "gate_rejected" in error_message
    assert "MISSING_CLIP" in error_message

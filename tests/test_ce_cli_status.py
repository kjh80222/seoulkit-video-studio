import json
import subprocess
from pathlib import Path

import pytest

from content_engine.cli.main import main
from content_engine.cli.status import derive_status
from content_engine.content_packages.create import create_content_package
from content_engine.db.connection import get_connection
from content_engine.jobs.artifacts import (
    edit_plan_human_input_to_payload,
    qc_decisions_to_payload,
    stage2_outputs_to_payload,
)
from content_engine.jobs.creation import create_job
from content_engine.jobs.transitions import fail_job, start_job
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
    return create_content_package(conn, "status test")


def _write_stage1_table(project_dir: Path) -> None:
    (project_dir / "script").mkdir(parents=True, exist_ok=True)
    data = {
        "beats": [{"beat": 1, "narration": "voice text", "visual_purpose": "purpose",
                   "screen_number": None, "screen_label": None}],
        "shots": [{"shot": "1A", "beat": 1, "shot_type": "wide", "on_screen_text": None}],
    }
    (project_dir / "script" / "stage1_beat_shot_table.json").write_text(json.dumps(data), encoding="utf-8")


def _write_keyframe(project_dir: Path) -> None:
    (project_dir / "keyframes").mkdir(parents=True, exist_ok=True)
    (project_dir / "keyframes" / "shot_1a_keyframe.png").write_bytes(b"fake png")


def _write_flow_clip(project_dir: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=navy:size=320x240:rate=10:duration=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(project_dir / "clips" / "shot_1a_flow.mp4")],
        capture_output=True, text=True, check=True,
    )


def _write_voice_audio(project_dir: Path) -> None:
    (project_dir / "voice").mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "2",
         "-c:a", "aac", str(project_dir / "voice" / "final.m4a")],
        capture_output=True, text=True, check=True,
    )


def _run(conn, job_id):
    from content_engine.jobs.dispatch import dispatch
    from content_engine.jobs.models import Job, JobState
    start_job(conn, job_id)
    row = conn.execute(
        "SELECT id, job_type, state, stage, error_message, created_at, updated_at, "
        "started_at, completed_at, content_package_id, payload FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    job = Job(
        id=row[0], job_type=row[1], state=JobState(row[2]), stage=row[3], error_message=row[4],
        created_at=row[5], updated_at=row[6], started_at=row[7], completed_at=row[8],
        content_package_id=row[9], payload=row[10],
    )
    dispatch(conn, job)


# --- Stage 1 / build-step branches -------------------------------------


def test_status_awaiting_stage1_input(conn, package):
    status = derive_status(conn, package.id)
    assert status.status == "AWAITING_STAGE1_INPUT"


def test_status_awaiting_stage2_input_build_no_job(conn, package):
    _write_stage1_table(Path(package.project_dir))
    status = derive_status(conn, package.id)
    assert status.status == "AWAITING_STAGE2_INPUT_BUILD"


def test_status_stage2_input_build_failed(conn, package):
    # stage1 file exists (so precedence moves past AWAITING_STAGE1_INPUT)
    # but is malformed, so the job itself fails.
    project_dir = Path(package.project_dir)
    (project_dir / "script").mkdir(parents=True, exist_ok=True)
    (project_dir / "script" / "stage1_beat_shot_table.json").write_text("{ not valid json", encoding="utf-8")

    job = create_job(conn, package.id, "stage2_input_build")
    _run(conn, job.id)

    status = derive_status(conn, package.id)
    assert status.status == "STAGE2_INPUT_BUILD_FAILED"
    assert status.detail  # non-empty - the raw exception message is preserved


def test_status_stage2_input_build_running(conn, package):
    _write_stage1_table(Path(package.project_dir))
    job = create_job(conn, package.id, "stage2_input_build")
    start_job(conn, job.id)  # claimed but not dispatched yet

    status = derive_status(conn, package.id)
    assert status.status == "STAGE2_INPUT_BUILD_RUNNING"


def test_status_awaiting_stage3_input_build_no_job(conn, package):
    project_dir = Path(package.project_dir)
    _write_stage1_table(project_dir)
    job = create_job(conn, package.id, "stage2_input_build")
    _run(conn, job.id)

    status = derive_status(conn, package.id)
    assert status.status == "AWAITING_STAGE3_INPUT_BUILD"


def test_status_awaiting_clip_manifest_build_no_job(conn, package):
    project_dir = Path(package.project_dir)
    _write_stage1_table(project_dir)
    _run(conn, create_job(conn, package.id, "stage2_input_build").id)
    _write_keyframe(project_dir)
    outputs = [Stage2ShotOutput(shot="1A", beat=1, approved_keyframe_path="keyframes/shot_1a_keyframe.png",
                                 story_function="f", continuity="c")]
    _run(conn, create_job(conn, package.id, "stage3_input_build",
                           payload=stage2_outputs_to_payload(outputs)).id)

    status = derive_status(conn, package.id)
    assert status.status == "AWAITING_CLIP_MANIFEST_BUILD"


def test_status_awaiting_edit_plan_build_no_job(conn, package):
    project_dir = Path(package.project_dir)
    _write_stage1_table(project_dir)
    _run(conn, create_job(conn, package.id, "stage2_input_build").id)
    _write_keyframe(project_dir)
    outputs = [Stage2ShotOutput(shot="1A", beat=1, approved_keyframe_path="keyframes/shot_1a_keyframe.png",
                                 story_function="f", continuity="c")]
    _run(conn, create_job(conn, package.id, "stage3_input_build",
                           payload=stage2_outputs_to_payload(outputs)).id)
    _write_flow_clip(project_dir)
    decisions = [ShotQcDecision(shot="1A", usable_start_ms=0, usable_end_ms=2000, key_event_end_ms=1000,
                                 settle_start_ms=1500, camera_behavior="locked-off", qc_passed=True)]
    _run(conn, create_job(conn, package.id, "clip_manifest_build",
                           payload=qc_decisions_to_payload(decisions)).id)

    status = derive_status(conn, package.id)
    assert status.status == "AWAITING_EDIT_PLAN_BUILD"


def _to_edit_plan_stage(conn, package_id, project_dir):
    _write_stage1_table(project_dir)
    _run(conn, create_job(conn, package_id, "stage2_input_build").id)
    _write_keyframe(project_dir)
    outputs = [Stage2ShotOutput(shot="1A", beat=1, approved_keyframe_path="keyframes/shot_1a_keyframe.png",
                                 story_function="f", continuity="c")]
    _run(conn, create_job(conn, package_id, "stage3_input_build",
                           payload=stage2_outputs_to_payload(outputs)).id)
    _write_flow_clip(project_dir)
    decisions = [ShotQcDecision(shot="1A", usable_start_ms=0, usable_end_ms=2000, key_event_end_ms=1000,
                                 settle_start_ms=1500, camera_behavior="locked-off", qc_passed=True)]
    _run(conn, create_job(conn, package_id, "clip_manifest_build",
                           payload=qc_decisions_to_payload(decisions)).id)
    _write_voice_audio(project_dir)
    voice_input = HumanVoiceTimingInput(timing_grade="EXACT", audio_file="voice/final.m4a", total_duration_ms=None)
    semantic_sync = [HumanSemanticSyncSegment(shot="1A", start_ms=0, end_ms=2000, clip_in_ms=0, clip_out_ms=2000)]
    payload = edit_plan_human_input_to_payload(voice_input, semantic_sync, [], [])
    _run(conn, create_job(conn, package_id, "edit_plan_build", payload=payload).id)


def test_status_ready_for_preview(conn, package):
    project_dir = Path(package.project_dir)
    _to_edit_plan_stage(conn, package.id, project_dir)

    status = derive_status(conn, package.id)
    assert status.status == "READY_FOR_PREVIEW"


def test_status_preview_running(conn, package):
    project_dir = Path(package.project_dir)
    _to_edit_plan_stage(conn, package.id, project_dir)
    job = create_job(conn, package.id, "preview_render")
    start_job(conn, job.id)

    status = derive_status(conn, package.id)
    assert status.status == "PREVIEW_RUNNING"


def test_status_preview_failed(conn, package):
    project_dir = Path(package.project_dir)
    _to_edit_plan_stage(conn, package.id, project_dir)
    job = create_job(conn, package.id, "preview_render")
    start_job(conn, job.id)
    fail_job(conn, job.id, error_message="simulated failure")

    status = derive_status(conn, package.id)
    assert status.status == "PREVIEW_FAILED"
    assert status.detail == "simulated failure"


def test_status_ready_for_final_approval(conn, package):
    project_dir = Path(package.project_dir)
    _to_edit_plan_stage(conn, package.id, project_dir)
    _run(conn, create_job(conn, package.id, "preview_render").id)

    status = derive_status(conn, package.id)
    assert status.status == "READY_FOR_FINAL_APPROVAL"


def test_status_done(conn, package):
    project_dir = Path(package.project_dir)
    _to_edit_plan_stage(conn, package.id, project_dir)
    _run(conn, create_job(conn, package.id, "preview_render").id)
    _run(conn, create_job(conn, package.id, "final_render").id)

    status = derive_status(conn, package.id)
    assert status.status == "DONE"


# --- CLI wiring -----------------------------------------------------------


def test_cli_status_project_not_found(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_ENGINE_DB_PATH", str(tmp_path / "content_engine.db"))
    monkeypatch.setenv("CONTENT_ENGINE_PROJECTS_ROOT", str(tmp_path / "projects"))

    exit_code = main(["status", "no-such-project"])

    assert exit_code == 3
    assert "Error" in capsys.readouterr().out


def test_cli_status_json_output(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_ENGINE_DB_PATH", str(tmp_path / "content_engine.db"))
    monkeypatch.setenv("CONTENT_ENGINE_PROJECTS_ROOT", str(tmp_path / "projects"))
    main(["create", "topic", "--json"])
    project_id = json.loads(capsys.readouterr().out)["content_package_id"]

    exit_code = main(["status", project_id, "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "AWAITING_STAGE1_INPUT"

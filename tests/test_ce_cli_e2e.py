import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run_cli(args: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "content_engine.cli.main", *args],
        capture_output=True, text=True, env=env,
    )


@pytest.fixture
def env(tmp_path):
    e = dict(os.environ)
    e["CONTENT_ENGINE_DB_PATH"] = str(tmp_path / "content_engine.db")
    e["CONTENT_ENGINE_PROJECTS_ROOT"] = str(tmp_path / "projects")
    return e


def _write_flow_clip(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=navy:size=320x240:rate=10:duration=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, text=True, check=True,
    )


def _write_voice_audio(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "2",
         "-c:a", "aac", str(path)],
        capture_output=True, text=True, check=True,
    )


def test_full_operator_workflow_via_cli_subprocess_reaches_done(env, tmp_path):
    create = _run_cli(["create", "e2e topic", "--json"], env)
    assert create.returncode == 0, create.stdout + create.stderr
    project = json.loads(create.stdout)
    project_id = project["content_package_id"]
    project_dir = Path(project["project_dir"])

    # human step: Stage 1 file placement
    (project_dir / "script").mkdir(parents=True, exist_ok=True)
    (project_dir / "script" / "stage1_beat_shot_table.json").write_text(json.dumps({
        "beats": [{"beat": 1, "narration": "voice text", "visual_purpose": "purpose",
                   "screen_number": None, "screen_label": None}],
        "shots": [{"shot": "1A", "beat": 1, "shot_type": "wide", "on_screen_text": None}],
    }))

    assert _run_cli(["submit", project_id, "stage2_input_build"], env).returncode == 0
    assert _run_cli(["worker", "run", "--once"], env).returncode == 0
    assert (project_dir / "stage2_input.json").is_file()

    # human step: Stage 2 keyframe + explicit Stage2ShotOutput decision
    (project_dir / "keyframes").mkdir(parents=True, exist_ok=True)
    (project_dir / "keyframes" / "shot_1a_keyframe.png").write_bytes(b"fake png")
    stage3_payload = tmp_path / "stage3_payload.json"
    stage3_payload.write_text(json.dumps({"stage2_outputs": [
        {"shot": "1A", "beat": 1, "approved_keyframe_path": "keyframes/shot_1a_keyframe.png",
         "story_function": "func", "continuity": "cont"}
    ]}))
    assert _run_cli(["submit", project_id, "stage3_input_build", str(stage3_payload)], env).returncode == 0
    assert _run_cli(["worker", "run", "--once"], env).returncode == 0
    assert (project_dir / "stage3_input.json").is_file()

    # human step: Flow clip + explicit QC decision
    _write_flow_clip(project_dir / "clips" / "shot_1a_flow.mp4")
    qc_payload = tmp_path / "qc_payload.json"
    qc_payload.write_text(json.dumps({"decisions": [
        {"shot": "1A", "usable_start_ms": 0, "usable_end_ms": 2000, "key_event_end_ms": 1000,
         "settle_start_ms": 1500, "camera_behavior": "locked-off", "qc_passed": True}
    ]}))
    assert _run_cli(["submit", project_id, "clip_manifest_build", str(qc_payload)], env).returncode == 0
    assert _run_cli(["worker", "run", "--once"], env).returncode == 0
    assert (project_dir / "clips" / "clip_manifest.json").is_file()

    # human step: Voice file + explicit Semantic Sync decision
    _write_voice_audio(project_dir / "voice" / "final.m4a")
    edit_plan_payload = tmp_path / "edit_plan_payload.json"
    edit_plan_payload.write_text(json.dumps({
        "voice_input": {"timing_grade": "EXACT", "audio_file": "voice/final.m4a", "total_duration_ms": None},
        "semantic_sync": [{"shot": "1A", "start_ms": 0, "end_ms": 2000, "clip_in_ms": 0, "clip_out_ms": 2000}],
        "overlay_decisions": [], "sfx_decisions": [],
    }))
    assert _run_cli(["submit", project_id, "edit_plan_build", str(edit_plan_payload)], env).returncode == 0
    assert _run_cli(["worker", "run", "--once"], env).returncode == 0
    assert (project_dir / "edit_plan.json").is_file()

    # list shows the project
    listing = _run_cli(["list", "--json"], env)
    assert listing.returncode == 0
    assert any(p["content_package_id"] == project_id for p in json.loads(listing.stdout)["projects"])

    assert _run_cli(["preview", project_id], env).returncode == 0
    assert _run_cli(["worker", "run", "--once"], env).returncode == 0

    assert _run_cli(["final", project_id], env).returncode == 0
    assert _run_cli(["worker", "run", "--once"], env).returncode == 0

    status = _run_cli(["status", project_id, "--json"], env)
    assert status.returncode == 0
    assert json.loads(status.stdout)["status"] == "DONE"


def test_preview_requested_before_edit_plan_exists_leaves_worker_blocked(env):
    # Requesting preview before any build step has run is allowed (CE-6/CE-11
    # never enforce ordering on job creation) - the job simply cannot be
    # claimed, since CE-5 readiness reports not-ready (edit_plan.json itself
    # is missing). `status` still reports the true root-cause blocker
    # (Stage 1 input), which is earlier in the pipeline than the render
    # step and does not contradict the worker's BLOCKED signal.
    create = _run_cli(["create", "blocked topic", "--json"], env)
    project_id = json.loads(create.stdout)["content_package_id"]

    assert _run_cli(["preview", project_id], env).returncode == 0

    worker = _run_cli(["worker", "run", "--once"], env)
    assert worker.returncode == 1  # BLOCKED
    assert "blocked on human input" in worker.stdout

    status = _run_cli(["status", project_id, "--json"], env)
    assert json.loads(status.stdout)["status"] == "AWAITING_STAGE1_INPUT"

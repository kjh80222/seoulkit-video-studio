import json
import subprocess
import sys
import venv
from pathlib import Path

import pytest

from content_engine.video_studio.adapter import (
    AdapterFailureCategory,
    _classify,
    _run_cli,
    run_preflight,
    run_preview,
    run_render,
)

# --- shared project-fixture helpers (self-contained, no import from other
# test modules - matches this repo's per-file convention) -------------------


def _colored_clip(path: Path, color: str, duration_s: float) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:size=320x240:rate=10:duration={duration_s}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        capture_output=True, text=True, check=True,
    )


def _tone(path: Path, duration_s: float) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=800:duration={duration_s}",
         "-af", "volume=-15dB", "-ar", "44100", "-ac", "2", str(path)],
        capture_output=True, text=True, check=True,
    )


def _base_plan(*, warnings: list[dict]) -> dict:
    return {
        "schema_version": "1.0",
        "project": "ce3-adapter-test",
        "format": "MINI",
        "status": "READY",
        "timing_grade_default": "EXACT",
        "voice": {
            "audio_file": "clips/audio/voice_final.wav",
            "aligned": True,
            "alignment_file": None,
            "total_duration_ms": 4000,
        },
        "segments": [
            {
                "beat": 1, "shot": "1A", "start_ms": 0, "end_ms": 2000,
                "timing_grade": "EXACT", "source_clip": "clips/shot_1a.mp4",
                "source_duration_ms": 3000, "usable_start_ms": 0, "usable_end_ms": 3000,
                "key_event_end_ms": 2000, "settle_start_ms": 2000,
                "clip_in_ms": 0, "clip_out_ms": 2000,
                "camera_behavior": "locked-off", "hold_strategy": "none", "hold_ms": 0,
                "trim_reason": None, "voice_text": "one", "status": "OK", "status_note": None,
            },
            {
                "beat": 1, "shot": "1B", "start_ms": 2000, "end_ms": 4000,
                "timing_grade": "EXACT", "source_clip": "clips/shot_1b.mp4",
                "source_duration_ms": 3000, "usable_start_ms": 0, "usable_end_ms": 3000,
                "key_event_end_ms": 2000, "settle_start_ms": 2000,
                "clip_in_ms": 0, "clip_out_ms": 2000,
                "camera_behavior": "locked-off", "hold_strategy": "none", "hold_ms": 0,
                "trim_reason": None, "voice_text": "two", "status": "OK", "status_note": None,
            },
        ],
        "overlays": [],
        "subtitles": [],
        "audio_layers": {
            "voice": "authoritative",
            "sfx_source": {"mode": "none", "clips": []},
            "bgm": {"mode": "none", "file": None, "reference_db": None},
        },
        "warnings": warnings,
    }


def _write_plan(project_dir: Path, plan: dict) -> None:
    (project_dir / "edit_plan.json").write_text(json.dumps(plan))


def _write_clip_manifest(project_dir: Path) -> None:
    manifest = {
        "note": "test fixture",
        "clips": [
            {"shot": "1A", "file": "clips/shot_1a.mp4", "camera_behavior": "locked-off",
             "source_duration_ms": 3000, "usable_start_ms": 0, "usable_end_ms": 3000,
             "key_event_end_ms": 2000, "settle_start_ms": 2000},
            {"shot": "1B", "file": "clips/shot_1b.mp4", "camera_behavior": "locked-off",
             "source_duration_ms": 3000, "usable_start_ms": 0, "usable_end_ms": 3000,
             "key_event_end_ms": 2000, "settle_start_ms": 2000},
        ],
    }
    (project_dir / "clips" / "clip_manifest.json").write_text(json.dumps(manifest))


@pytest.fixture
def demo_project(tmp_path):
    project_dir = tmp_path / "project"
    (project_dir / "clips" / "audio").mkdir(parents=True)

    _colored_clip(project_dir / "clips" / "shot_1a.mp4", "navy", 3)
    _colored_clip(project_dir / "clips" / "shot_1b.mp4", "darkgreen", 3)
    _tone(project_dir / "clips" / "audio" / "voice_final.wav", 4)

    _write_plan(project_dir, _base_plan(warnings=[]))
    _write_clip_manifest(project_dir)
    return project_dir


@pytest.fixture(scope="session")
def bare_python_without_seoulkit_studio(tmp_path_factory):
    """A real venv that never had `seoulkit_studio` installed into it - used
    to reproduce the actual "not installed" failure shape empirically
    (confirmed during CE-3 investigation: a normal exit=1, empty stdout,
    plain-text ModuleNotFoundError on stderr - not a Python-level
    FileNotFoundError in the calling process), rather than assuming it."""
    venv_dir = tmp_path_factory.mktemp("bare-venv")
    venv.create(venv_dir, with_pip=False)
    return venv_dir / "bin" / "python"


VALID_PREFLIGHT_PAYLOAD = json.dumps({
    "command": "preflight", "effective_status": "READY", "plan_status_as_declared": "READY",
    "studio_execution_result": "OK", "load_error": None, "issues": [],
})
VALID_RENDER_PAYLOAD = json.dumps({
    "command": "render", "ok": True, "gate_error": None, "effective_status": "READY",
    "plan_status_as_declared": "READY", "studio_execution_result": "OK",
    "output_path": "output/final_v001.mp4", "report_path": "output/final_v001_report.json",
    "log_path": "output/final_v001.log", "render_version": "v001", "issues": [],
})
VALID_USAGE_ERROR_PAYLOAD = json.dumps({"error": "project directory not found: /nope", "exit_code": 4})


# --- group 1: _classify() for `preflight` (6 cases) -------------------------


@pytest.mark.parametrize(
    "exit_code,stdout,expected_category",
    [
        (0, VALID_PREFLIGHT_PAYLOAD, None),
        (1, VALID_PREFLIGHT_PAYLOAD, None),
        (2, VALID_PREFLIGHT_PAYLOAD, None),
        (4, VALID_USAGE_ERROR_PAYLOAD, AdapterFailureCategory.USAGE_ERROR),
        (2, "", AdapterFailureCategory.ADAPTER_INVOCATION_ERROR),  # argparse collision, preflight side
        (0, "not json", AdapterFailureCategory.ADAPTER_INVOCATION_ERROR),
    ],
    ids=["exit0-ok", "exit1-ok", "exit2-ok", "exit4-usage-error", "exit2-argparse-collision", "malformed-json"],
)
def test_classify_preflight(exit_code, stdout, expected_category):
    category, payload = _classify("preflight", exit_code, stdout)

    assert category == expected_category
    if expected_category is None:
        assert payload["effective_status"] is not None
    elif expected_category == AdapterFailureCategory.ADAPTER_INVOCATION_ERROR:
        assert payload is None
    else:
        assert payload is not None


# --- group 2: _classify() for `preview`/`render` (7 cases x 2 = 14) ---------


@pytest.mark.parametrize("command", ["preview", "render"])
@pytest.mark.parametrize(
    "exit_code,stdout,expected_category",
    [
        (0, VALID_RENDER_PAYLOAD, None),
        (1, VALID_RENDER_PAYLOAD, AdapterFailureCategory.GATE_REJECTED),
        (2, VALID_RENDER_PAYLOAD, AdapterFailureCategory.GATE_REJECTED),
        (3, VALID_RENDER_PAYLOAD, AdapterFailureCategory.EXECUTION_FAILED),
        (4, VALID_USAGE_ERROR_PAYLOAD, AdapterFailureCategory.USAGE_ERROR),
        (2, "", AdapterFailureCategory.ADAPTER_INVOCATION_ERROR),  # argparse collision, render side
        (0, "not json", AdapterFailureCategory.ADAPTER_INVOCATION_ERROR),
    ],
    ids=["exit0-ok", "exit1-gate", "exit2-gate", "exit3-exec-failed", "exit4-usage-error",
         "exit2-argparse-collision", "malformed-json"],
)
def test_classify_preview_and_render(command, exit_code, stdout, expected_category):
    category, payload = _classify(command, exit_code, stdout)

    assert category == expected_category
    if expected_category == AdapterFailureCategory.ADAPTER_INVOCATION_ERROR:
        assert payload is None
    else:
        assert payload is not None


# --- group 3: minimal payload-shape validation (3 cases) --------------------


def test_classify_rejects_a_json_array_payload():
    category, payload = _classify("preflight", 0, "[1, 2, 3]")

    assert category == AdapterFailureCategory.ADAPTER_INVOCATION_ERROR
    assert payload is None


def test_classify_rejects_a_dict_missing_effective_status():
    category, payload = _classify("preflight", 0, json.dumps({"foo": "bar"}))

    assert category == AdapterFailureCategory.ADAPTER_INVOCATION_ERROR
    assert payload is None


def test_classify_rejects_an_exit_four_payload_missing_error_keys():
    category, payload = _classify("preflight", 4, json.dumps({"foo": "bar"}))

    assert category == AdapterFailureCategory.ADAPTER_INVOCATION_ERROR
    assert payload is None


# --- group 4: _run_cli() against the real subprocess (6 cases) -------------


def test_run_cli_converts_a_stray_os_error_to_adapter_invocation_error(monkeypatch, tmp_path):
    def _raise(*a, **k):
        raise OSError("no such file or directory")

    monkeypatch.setattr("content_engine.video_studio.adapter.subprocess.run", _raise)

    result = _run_cli("preflight", tmp_path)

    assert result.ok is False
    assert result.category == AdapterFailureCategory.ADAPTER_INVOCATION_ERROR
    assert result.exit_code is None


def test_real_preflight_succeeds_on_a_ready_project(demo_project):
    result = run_preflight(demo_project)

    assert result.ok is True
    assert result.category is None
    assert result.payload["effective_status"] == "READY"


def test_real_render_succeeds_and_exposes_output_path(demo_project):
    result = run_render(demo_project)

    assert result.ok is True
    # demo_project is already absolute (tmp_path), so the pipeline's own
    # output_path comes back absolute too - the adapter must not rewrite it.
    assert result.payload["output_path"] == str(demo_project / "output" / "final_v001.mp4")
    assert (demo_project / "output" / "final_v001.mp4").exists()


def test_real_render_is_gate_rejected_when_not_ready(demo_project):
    _write_plan(demo_project, _base_plan(warnings=[
        {"code": "SOME_BLOCKING_THING", "message": "...", "severity": "blocking", "segment_ref": None}
    ]))

    result = run_render(demo_project)

    assert result.ok is False
    assert result.category == AdapterFailureCategory.GATE_REJECTED


def test_real_invocation_without_seoulkit_studio_installed_is_adapter_invocation_error(
    bare_python_without_seoulkit_studio, demo_project,
):
    argv = [str(bare_python_without_seoulkit_studio), "-m", "seoulkit_studio.cli.main",
            "preflight", str(demo_project), "--json"]
    completed = subprocess.run(argv, capture_output=True, text=True)

    category, payload = _classify("preflight", completed.returncode, completed.stdout)

    assert completed.stdout == ""
    assert category == AdapterFailureCategory.ADAPTER_INVOCATION_ERROR
    assert payload is None


def test_real_argparse_missing_argument_collision_is_adapter_invocation_error(demo_project):
    # Deliberately omit the required `project_dir` positional so argparse
    # itself rejects the call (real exit code 2, empty stdout) - the exact
    # collision with NOT_READY=2 this module was built to defend against.
    argv = [sys.executable, "-m", "seoulkit_studio.cli.main", "preflight", "--json"]
    completed = subprocess.run(argv, capture_output=True, text=True)

    category, payload = _classify("preflight", completed.returncode, completed.stdout)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert category == AdapterFailureCategory.ADAPTER_INVOCATION_ERROR
    assert payload is None


# --- group 5: public function wiring (3 cases) ------------------------------


@pytest.mark.parametrize(
    "public_func,expected_command", [(run_preflight, "preflight"), (run_preview, "preview"), (run_render, "render")]
)
def test_public_function_invokes_run_cli_with_the_right_command(monkeypatch, tmp_path, public_func, expected_command):
    captured = {}

    def _fake_run_cli(command, project_dir):
        captured["command"] = command
        captured["project_dir"] = project_dir
        return "sentinel"

    monkeypatch.setattr(
        f"content_engine.video_studio.adapter._run_cli", _fake_run_cli,
    )

    result = public_func(tmp_path)

    assert captured["command"] == expected_command
    assert captured["project_dir"] == tmp_path
    assert result == "sentinel"

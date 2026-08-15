import json
from pathlib import Path

import pytest

from content_engine.content_packages import readiness as readiness_module
from content_engine.content_packages.readiness import check_asset_readiness
from content_engine.video_studio.adapter import AdapterFailureCategory, AdapterResult


def _base_plan(
    *,
    voice_audio_file: str | None = "clips/audio/voice_final.wav",
    bgm_mode: str = "selected",
    bgm_file: str | None = "clips/audio/bgm/track.mp3",
    sfx_file: str | None = "clips/audio/sfx/amb.wav",
    clip_1a: str = "clips/shot_1a.mp4",
    clip_1b: str = "clips/shot_1b.mp4",
) -> dict:
    sfx_clips = [{"shot": "1A", "file": sfx_file, "action": "adopted"}] if sfx_file else []
    return {
        "schema_version": "1.0",
        "project": "ce5-readiness-test",
        "format": "MINI",
        "status": "READY",
        "timing_grade_default": "EXACT",
        "voice": {
            "audio_file": voice_audio_file,
            "aligned": True,
            "alignment_file": None,
            "total_duration_ms": 4000,
        },
        "segments": [
            {
                "beat": 1, "shot": "1A", "start_ms": 0, "end_ms": 2000,
                "timing_grade": "EXACT", "source_clip": clip_1a,
                "source_duration_ms": 3000, "usable_start_ms": 0, "usable_end_ms": 3000,
                "key_event_end_ms": 2000, "settle_start_ms": 2000,
                "clip_in_ms": 0, "clip_out_ms": 2000,
                "camera_behavior": "locked-off", "hold_strategy": "none", "hold_ms": 0,
                "trim_reason": None, "voice_text": "one", "status": "OK", "status_note": None,
            },
            {
                "beat": 1, "shot": "1B", "start_ms": 2000, "end_ms": 4000,
                "timing_grade": "EXACT", "source_clip": clip_1b,
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
            "sfx_source": {"mode": "stage3_optional" if sfx_file else "none", "clips": sfx_clips},
            "bgm": {"mode": bgm_mode, "file": bgm_file, "reference_db": None},
        },
        "warnings": [],
    }


def _write_plan(project_dir: Path, plan: dict) -> None:
    (project_dir / "edit_plan.json").write_text(json.dumps(plan))


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


@pytest.fixture
def project_dir(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "clips").mkdir()
    return project_dir


# --- edit_plan.json missing/invalid --------------------------------------


def test_missing_edit_plan_json_is_not_ready(project_dir):
    result = check_asset_readiness(project_dir)

    assert result.ready is False
    assert len(result.missing) == 1
    assert "edit_plan.json" in result.missing[0]


# --- MISSING_CLIP is the only blocking condition --------------------------


def test_one_missing_clip_is_not_ready(project_dir):
    _write_plan(project_dir, _base_plan())
    _touch(project_dir / "clips" / "audio" / "voice_final.wav")
    _touch(project_dir / "clips" / "audio" / "bgm" / "track.mp3")
    _touch(project_dir / "clips" / "audio" / "sfx" / "amb.wav")
    _touch(project_dir / "clips" / "shot_1a.mp4")
    # shot_1b.mp4 deliberately not created

    result = check_asset_readiness(project_dir)

    assert result.ready is False
    assert len(result.missing) == 1
    assert "shot_1b.mp4" in result.missing[0]


def test_multiple_missing_clips_are_all_reported(project_dir):
    _write_plan(project_dir, _base_plan())
    _touch(project_dir / "clips" / "audio" / "voice_final.wav")
    _touch(project_dir / "clips" / "audio" / "bgm" / "track.mp3")
    _touch(project_dir / "clips" / "audio" / "sfx" / "amb.wav")
    # neither shot_1a.mp4 nor shot_1b.mp4 created

    result = check_asset_readiness(project_dir)

    assert result.ready is False
    assert len(result.missing) == 2


# --- Voice/BGM/SFX/clip_manifest are explicitly NOT CE-5's concern --------


def test_missing_voice_alone_is_still_ready(project_dir):
    _write_plan(project_dir, _base_plan())
    _touch(project_dir / "clips" / "audio" / "bgm" / "track.mp3")
    _touch(project_dir / "clips" / "audio" / "sfx" / "amb.wav")
    _touch(project_dir / "clips" / "shot_1a.mp4")
    _touch(project_dir / "clips" / "shot_1b.mp4")
    # voice_final.wav deliberately not created

    result = check_asset_readiness(project_dir)

    assert result.ready is True
    assert result.missing == []


def test_missing_bgm_alone_is_still_ready(project_dir):
    _write_plan(project_dir, _base_plan())
    _touch(project_dir / "clips" / "audio" / "voice_final.wav")
    _touch(project_dir / "clips" / "audio" / "sfx" / "amb.wav")
    _touch(project_dir / "clips" / "shot_1a.mp4")
    _touch(project_dir / "clips" / "shot_1b.mp4")
    # bgm/track.mp3 deliberately not created

    result = check_asset_readiness(project_dir)

    assert result.ready is True
    assert result.missing == []


def test_missing_sfx_alone_is_still_ready(project_dir):
    _write_plan(project_dir, _base_plan())
    _touch(project_dir / "clips" / "audio" / "voice_final.wav")
    _touch(project_dir / "clips" / "audio" / "bgm" / "track.mp3")
    _touch(project_dir / "clips" / "shot_1a.mp4")
    _touch(project_dir / "clips" / "shot_1b.mp4")
    # sfx/amb.wav deliberately not created

    result = check_asset_readiness(project_dir)

    assert result.ready is True
    assert result.missing == []


def test_missing_clip_manifest_alone_is_still_ready(project_dir):
    _write_plan(project_dir, _base_plan())
    _touch(project_dir / "clips" / "audio" / "voice_final.wav")
    _touch(project_dir / "clips" / "audio" / "bgm" / "track.mp3")
    _touch(project_dir / "clips" / "audio" / "sfx" / "amb.wav")
    _touch(project_dir / "clips" / "shot_1a.mp4")
    _touch(project_dir / "clips" / "shot_1b.mp4")
    # clips/clip_manifest.json deliberately not created

    result = check_asset_readiness(project_dir)

    assert result.ready is True
    assert result.missing == []


def test_voice_bgm_sfx_and_clip_manifest_all_missing_together_is_still_ready(project_dir):
    # The scope-narrowing this phase exists to prove: none of these four
    # combined outweighs the presence of every required Flow clip.
    _write_plan(project_dir, _base_plan())
    _touch(project_dir / "clips" / "shot_1a.mp4")
    _touch(project_dir / "clips" / "shot_1b.mp4")
    # voice, bgm, sfx, clip_manifest.json all deliberately absent

    result = check_asset_readiness(project_dir)

    assert result.ready is True
    assert result.missing == []


# --- fully clean project ----------------------------------------------------


def test_everything_present_is_ready(project_dir):
    _write_plan(project_dir, _base_plan())
    _touch(project_dir / "clips" / "audio" / "voice_final.wav")
    _touch(project_dir / "clips" / "audio" / "bgm" / "track.mp3")
    _touch(project_dir / "clips" / "audio" / "sfx" / "amb.wav")
    _touch(project_dir / "clips" / "shot_1a.mp4")
    _touch(project_dir / "clips" / "shot_1b.mp4")
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

    result = check_asset_readiness(project_dir)

    assert result.ready is True
    assert result.missing == []


# --- idempotency --------------------------------------------------------------


def test_check_asset_readiness_is_idempotent(project_dir):
    _write_plan(project_dir, _base_plan())
    _touch(project_dir / "clips" / "audio" / "voice_final.wav")
    _touch(project_dir / "clips" / "audio" / "bgm" / "track.mp3")
    _touch(project_dir / "clips" / "audio" / "sfx" / "amb.wav")
    _touch(project_dir / "clips" / "shot_1a.mp4")
    # shot_1b.mp4 left missing

    first = check_asset_readiness(project_dir)
    second = check_asset_readiness(project_dir)

    assert first == second


# --- adapter failure is not swallowed into ready=False ----------------------


def test_adapter_failure_raises_runtime_error_with_diagnostics(project_dir, monkeypatch):
    failure = AdapterResult(
        command="preflight", ok=False, category=AdapterFailureCategory.ADAPTER_INVOCATION_ERROR,
        exit_code=2, payload=None, stdout="", stderr="ModuleNotFoundError: No module named 'seoulkit_studio'",
    )
    monkeypatch.setattr(readiness_module, "run_preflight", lambda project_dir: failure)

    with pytest.raises(RuntimeError) as exc_info:
        check_asset_readiness(project_dir)

    message = str(exc_info.value)
    assert "ADAPTER_INVOCATION_ERROR" in message
    assert "exit_code=2" in message
    assert "ModuleNotFoundError" in message

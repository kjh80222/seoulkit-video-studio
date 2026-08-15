import json

import pytest

from content_engine.video_planning.clip_qc import (
    ClipQcIdentityMismatchError,
    MissingClipFileError,
    QcNotPassedError,
    TimingInvariantError,
    build_clip_manifest,
    check_timing_invariants,
    measure_clip,
    measure_clips,
    write_clip_manifest,
)
from content_engine.video_planning.models import MeasuredClipFacts, ShotQcDecision, Stage3InputPackage, Stage3PlannedShot


def _stage3_shot(shot="1A", filename="clips/shot_1a_flow.mp4") -> Stage3PlannedShot:
    return Stage3PlannedShot(
        beat=1, shot=shot, visual_purpose="purpose", voice_text="voice text",
        approved_keyframe_path=f"keyframes/shot_{shot.lower()}_keyframe.png",
        story_function="story", continuity="continuity", expected_clip_filename=filename,
    )


def _stage3_input(shots: list[Stage3PlannedShot]) -> Stage3InputPackage:
    return Stage3InputPackage(schema_version="1.0", topic="test topic", shots=shots)


def _decision(shot="1A", usable_start_ms=0, usable_end_ms=5800, key_event_end_ms=4200,
              settle_start_ms=4800, camera_behavior="movement", qc_passed=True) -> ShotQcDecision:
    return ShotQcDecision(
        shot=shot, usable_start_ms=usable_start_ms, usable_end_ms=usable_end_ms,
        key_event_end_ms=key_event_end_ms, settle_start_ms=settle_start_ms,
        camera_behavior=camera_behavior, qc_passed=qc_passed,
    )


def _facts(shot="1A", source_duration_ms=6000, has_audio_stream=False) -> MeasuredClipFacts:
    return MeasuredClipFacts(shot=shot, source_duration_ms=source_duration_ms, has_audio_stream=has_audio_stream)


# --- measure_clip() ----------------------------------------------------------


def test_measure_clip_reports_duration_and_audio_presence(tmp_path, make_synthetic_video):
    clip_path = make_synthetic_video(tmp_path / "clip.mp4", duration_ms=2000, with_audio=True)

    facts = measure_clip("1A", clip_path)

    assert facts.shot == "1A"
    assert 1900 <= facts.source_duration_ms <= 2100
    assert facts.has_audio_stream is True


def test_measure_clip_reports_no_audio_stream_when_absent(tmp_path, make_synthetic_video):
    clip_path = make_synthetic_video(tmp_path / "clip.mp4", duration_ms=2000, with_audio=False)

    facts = measure_clip("1A", clip_path)

    assert facts.has_audio_stream is False


# --- measure_clips() ----------------------------------------------------------


def test_measure_clips_raises_on_missing_clip_file(tmp_path):
    stage3_input = _stage3_input([_stage3_shot(shot="1A")])

    with pytest.raises(MissingClipFileError) as exc_info:
        measure_clips(tmp_path, stage3_input)

    assert exc_info.value.missing_paths == ["clips/shot_1a_flow.mp4"]


def test_measure_clips_reports_every_missing_clip_together(tmp_path):
    stage3_input = _stage3_input([
        _stage3_shot(shot="1A", filename="clips/shot_1a_flow.mp4"),
        _stage3_shot(shot="1B", filename="clips/shot_1b_flow.mp4"),
    ])

    with pytest.raises(MissingClipFileError) as exc_info:
        measure_clips(tmp_path, stage3_input)

    assert set(exc_info.value.missing_paths) == {"clips/shot_1a_flow.mp4", "clips/shot_1b_flow.mp4"}


def test_measure_clips_succeeds_and_preserves_stage3_input_order(tmp_path, make_synthetic_video):
    (tmp_path / "clips").mkdir()
    make_synthetic_video(tmp_path / "clips" / "shot_1b_flow.mp4", duration_ms=1500)
    make_synthetic_video(tmp_path / "clips" / "shot_1a_flow.mp4", duration_ms=1500)
    stage3_input = _stage3_input([
        _stage3_shot(shot="1A", filename="clips/shot_1a_flow.mp4"),
        _stage3_shot(shot="1B", filename="clips/shot_1b_flow.mp4"),
    ])

    measured = measure_clips(tmp_path, stage3_input)

    assert [m.shot for m in measured] == ["1A", "1B"]


# --- check_timing_invariants() ------------------------------------------------


def test_check_timing_invariants_usable_end_exceeds_source_duration():
    violations = check_timing_invariants("1A", _decision(usable_end_ms=7000), source_duration_ms=6000)

    assert len(violations) == 1
    assert "usable_end_ms" in violations[0]
    assert "source_duration_ms" in violations[0]


def test_check_timing_invariants_usable_start_not_less_than_usable_end():
    violations = check_timing_invariants(
        "1A", _decision(usable_start_ms=5800, usable_end_ms=5800), source_duration_ms=6000
    )

    assert any("usable_start_ms" in v and "usable_end_ms" in v for v in violations)


def test_check_timing_invariants_key_event_end_outside_usable_range():
    violations = check_timing_invariants(
        "1A", _decision(usable_start_ms=1000, usable_end_ms=5000, key_event_end_ms=500), source_duration_ms=6000
    )

    assert any("key_event_end_ms" in v for v in violations)


def test_check_timing_invariants_settle_start_outside_usable_range():
    violations = check_timing_invariants(
        "1A", _decision(usable_start_ms=1000, usable_end_ms=5000, settle_start_ms=5500), source_duration_ms=6000
    )

    assert any("settle_start_ms" in v for v in violations)


def test_check_timing_invariants_key_event_after_settle_start():
    violations = check_timing_invariants(
        "1A", _decision(key_event_end_ms=4900, settle_start_ms=4800, usable_end_ms=5800), source_duration_ms=6000
    )

    assert any("key_event_end_ms" in v and "settle_start_ms" in v for v in violations)


def test_check_timing_invariants_allows_null_key_event_and_settle():
    violations = check_timing_invariants(
        "1A", _decision(key_event_end_ms=None, settle_start_ms=None), source_duration_ms=6000
    )

    assert violations == []


def test_check_timing_invariants_passes_for_valid_values():
    violations = check_timing_invariants("1A", _decision(), source_duration_ms=6000)

    assert violations == []


# --- build_clip_manifest() ----------------------------------------------------


def test_build_clip_manifest_merges_matching_shots_correctly():
    stage3_input = _stage3_input([_stage3_shot(shot="1A", filename="clips/shot_1a_flow.mp4")])
    measured = [_facts(shot="1A", source_duration_ms=6000, has_audio_stream=True)]
    decisions = [_decision(shot="1A")]

    manifest = build_clip_manifest(stage3_input, measured, decisions)

    assert manifest.sfx_contract_version == 1
    assert len(manifest.clips) == 1
    entry = manifest.clips[0]
    assert entry.shot == "1A"
    assert entry.file == "clips/shot_1a_flow.mp4"
    assert entry.source_duration_ms == 6000
    assert entry.camera_behavior == "movement"
    assert entry.optional_source_audio.available is True
    assert entry.optional_source_audio.file is None


def test_build_clip_manifest_raises_on_missing_decision():
    stage3_input = _stage3_input([_stage3_shot(shot="1A"), _stage3_shot(shot="1B", filename="clips/shot_1b_flow.mp4")])
    measured = [_facts(shot="1A"), _facts(shot="1B")]
    decisions = [_decision(shot="1A")]

    with pytest.raises(ClipQcIdentityMismatchError) as exc_info:
        build_clip_manifest(stage3_input, measured, decisions)

    assert exc_info.value.missing == ["1B"]


def test_build_clip_manifest_raises_on_extra_decision():
    stage3_input = _stage3_input([_stage3_shot(shot="1A")])
    measured = [_facts(shot="1A")]
    decisions = [_decision(shot="1A"), _decision(shot="1B")]

    with pytest.raises(ClipQcIdentityMismatchError) as exc_info:
        build_clip_manifest(stage3_input, measured, decisions)

    assert exc_info.value.extra == ["1B"]


def test_build_clip_manifest_raises_on_duplicate_decision():
    stage3_input = _stage3_input([_stage3_shot(shot="1A")])
    measured = [_facts(shot="1A")]
    decisions = [_decision(shot="1A"), _decision(shot="1A")]

    with pytest.raises(ClipQcIdentityMismatchError) as exc_info:
        build_clip_manifest(stage3_input, measured, decisions)

    assert exc_info.value.duplicate == ["1A"]


def test_build_clip_manifest_raises_timing_invariant_error_collecting_all_violations():
    stage3_input = _stage3_input([
        _stage3_shot(shot="1A", filename="clips/shot_1a_flow.mp4"),
        _stage3_shot(shot="1B", filename="clips/shot_1b_flow.mp4"),
    ])
    measured = [_facts(shot="1A", source_duration_ms=6000), _facts(shot="1B", source_duration_ms=6000)]
    decisions = [
        _decision(shot="1A", usable_end_ms=7000),  # exceeds source_duration_ms
        _decision(shot="1B", usable_start_ms=5000, usable_end_ms=4000,
                   key_event_end_ms=None, settle_start_ms=None),  # start >= end
    ]

    with pytest.raises(TimingInvariantError) as exc_info:
        build_clip_manifest(stage3_input, measured, decisions)

    assert len(exc_info.value.violations) == 2


def test_build_clip_manifest_raises_qc_not_passed_error_listing_failed_shots():
    stage3_input = _stage3_input([
        _stage3_shot(shot="1A", filename="clips/shot_1a_flow.mp4"),
        _stage3_shot(shot="1B", filename="clips/shot_1b_flow.mp4"),
    ])
    measured = [_facts(shot="1A"), _facts(shot="1B")]
    decisions = [_decision(shot="1A", qc_passed=True), _decision(shot="1B", qc_passed=False)]

    with pytest.raises(QcNotPassedError) as exc_info:
        build_clip_manifest(stage3_input, measured, decisions)

    assert exc_info.value.failed_shots == ["1B"]


def test_build_clip_manifest_optional_source_audio_file_is_always_none_regardless_of_availability():
    stage3_input = _stage3_input([
        _stage3_shot(shot="1A", filename="clips/shot_1a_flow.mp4"),
        _stage3_shot(shot="1B", filename="clips/shot_1b_flow.mp4"),
    ])
    measured = [
        _facts(shot="1A", has_audio_stream=True),
        _facts(shot="1B", has_audio_stream=False),
    ]
    decisions = [_decision(shot="1A"), _decision(shot="1B")]

    manifest = build_clip_manifest(stage3_input, measured, decisions)

    assert all(c.optional_source_audio.file is None for c in manifest.clips)
    assert manifest.clips[0].optional_source_audio.available is True
    assert manifest.clips[1].optional_source_audio.available is False


# --- write_clip_manifest() ----------------------------------------------------


def test_write_clip_manifest_creates_file_with_exact_expected_keys(tmp_path):
    (tmp_path / "clips").mkdir()
    stage3_input = _stage3_input([_stage3_shot(shot="1A")])
    manifest = build_clip_manifest(stage3_input, [_facts(shot="1A")], [_decision(shot="1A")])

    write_clip_manifest(tmp_path, manifest)

    data = json.loads((tmp_path / "clips" / "clip_manifest.json").read_text(encoding="utf-8"))
    assert set(data.keys()) == {"sfx_contract_version", "clips"}
    assert data["sfx_contract_version"] == 1
    assert set(data["clips"][0].keys()) == {
        "shot", "file", "camera_behavior", "source_duration_ms", "usable_start_ms",
        "usable_end_ms", "key_event_end_ms", "settle_start_ms", "optional_source_audio",
    }
    assert set(data["clips"][0]["optional_source_audio"].keys()) == {"available", "file"}


def test_write_clip_manifest_rejects_an_existing_file_without_modifying_it(tmp_path):
    (tmp_path / "clips").mkdir()
    original = tmp_path / "clips" / "clip_manifest.json"
    original.write_text('{"already": "here"}', encoding="utf-8")
    before = original.read_bytes()
    stage3_input = _stage3_input([_stage3_shot(shot="1A")])
    manifest = build_clip_manifest(stage3_input, [_facts(shot="1A")], [_decision(shot="1A")])

    with pytest.raises(FileExistsError):
        write_clip_manifest(tmp_path, manifest)

    assert original.read_bytes() == before


def test_write_clip_manifest_never_touches_source_clip_files(tmp_path, make_synthetic_video):
    (tmp_path / "clips").mkdir()
    clip_path = make_synthetic_video(tmp_path / "clips" / "shot_1a_flow.mp4", duration_ms=1500)
    before = clip_path.read_bytes()
    stage3_input = _stage3_input([_stage3_shot(shot="1A")])
    manifest = build_clip_manifest(stage3_input, [_facts(shot="1A")], [_decision(shot="1A")])

    write_clip_manifest(tmp_path, manifest)

    assert clip_path.read_bytes() == before

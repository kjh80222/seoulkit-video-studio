import json

import pytest

from content_engine.jobs.artifacts import (
    edit_plan_human_input_from_payload,
    edit_plan_human_input_to_payload,
    qc_decisions_from_payload,
    qc_decisions_to_payload,
    read_clip_manifest,
    read_edit_plan,
    read_stage2_input,
    read_stage3_input,
    stage2_outputs_from_payload,
    stage2_outputs_to_payload,
)
from content_engine.video_planning.models import (
    ClipManifest,
    ClipManifestEntry,
    EditPlan,
    EditPlanAudioLayers,
    EditPlanBgm,
    EditPlanOverlay,
    EditPlanSegment,
    EditPlanSfxClip,
    EditPlanSfxSource,
    EditPlanSubtitle,
    EditPlanVoice,
    EditPlanWarning,
    HumanOverlayDecision,
    HumanSemanticSyncSegment,
    HumanSfxDecision,
    HumanVoiceTimingInput,
    OptionalSourceAudio,
    PlannedShot,
    ShotQcDecision,
    Stage2ShotOutput,
)
from content_engine.video_planning.stage2_input import build_stage2_input, write_stage2_input
from content_engine.video_planning.stage3_input import build_stage3_input, write_stage3_input
from content_engine.video_planning.clip_qc import write_clip_manifest
from content_engine.video_planning.edit_plan import write_edit_plan


# --- stage2_input.json ---------------------------------------------------


def test_read_stage2_input_round_trips_write_stage2_input(tmp_path):
    shots = [
        PlannedShot(
            beat=1, shot="1A", shot_type="wide", visual_purpose="purpose",
            screen_number="1953", screen_label=None, on_screen_text="1953", voice_text="voice text",
        )
    ]
    package = build_stage2_input("test topic", shots)
    write_stage2_input(tmp_path, package)

    restored = read_stage2_input(tmp_path)

    assert restored == package


def test_read_stage2_input_missing_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_stage2_input(tmp_path)


# --- stage3_input.json ----------------------------------------------------


def test_read_stage3_input_round_trips_write_stage3_input(tmp_path):
    (tmp_path / "keyframes").mkdir()
    (tmp_path / "keyframes" / "shot_1a_keyframe.png").write_bytes(b"fake png")

    stage2_input = build_stage2_input("test topic", [
        PlannedShot(
            beat=1, shot="1A", shot_type="wide", visual_purpose="purpose",
            screen_number=None, screen_label=None, on_screen_text=None, voice_text="voice text",
        )
    ])
    outputs = [
        Stage2ShotOutput(
            shot="1A", beat=1, approved_keyframe_path="keyframes/shot_1a_keyframe.png",
            story_function="func", continuity="cont",
        )
    ]
    package = build_stage3_input(stage2_input, outputs)
    write_stage3_input(tmp_path, package)

    restored = read_stage3_input(tmp_path)

    assert restored == package


def test_read_stage3_input_missing_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_stage3_input(tmp_path)


# --- clips/clip_manifest.json ----------------------------------------------


def test_read_clip_manifest_round_trips_write_clip_manifest(tmp_path):
    (tmp_path / "clips").mkdir()
    manifest = ClipManifest(
        sfx_contract_version=1,
        clips=[
            ClipManifestEntry(
                shot="1A", file="clips/shot_1a_flow.mp4", camera_behavior="locked-off",
                source_duration_ms=2000, usable_start_ms=0, usable_end_ms=2000,
                key_event_end_ms=1000, settle_start_ms=1500,
                optional_source_audio=OptionalSourceAudio(available=True, file=None),
            ),
        ],
    )
    write_clip_manifest(tmp_path, manifest)

    restored = read_clip_manifest(tmp_path)

    assert restored == manifest


def test_read_clip_manifest_missing_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_clip_manifest(tmp_path)


# --- edit_plan.json ---------------------------------------------------------


def _edit_plan() -> EditPlan:
    return EditPlan(
        project="test project",
        status="READY",
        timing_grade_default="EXACT",
        voice=EditPlanVoice(audio_file=None, aligned=True, total_duration_ms=2000),
        segments=[
            EditPlanSegment(
                beat=1, shot="1A", start_ms=0, end_ms=2000, timing_grade="EXACT",
                source_clip="clips/shot_1a_flow.mp4", source_duration_ms=2000,
                usable_start_ms=0, usable_end_ms=2000, key_event_end_ms=1000, settle_start_ms=1500,
                clip_in_ms=0, clip_out_ms=2000, camera_behavior="locked-off",
                hold_strategy="none", hold_ms=0, trim_reason=None,
                voice_text="voice text", status="OK", status_note=None,
            )
        ],
        overlays=[
            EditPlanOverlay(
                beat=1, text="1953", type="giant_number", start_ms=0, end_ms=2000,
                position_preset="top-left", position_override=None, source="stage1_beat_table",
            )
        ],
        subtitles=[
            EditPlanSubtitle(
                beat=1, start_ms=0, end_ms=2000, lines=["voice text"],
                position_preset="bottom-center", timing_grade="EXACT",
            )
        ],
        audio_layers=EditPlanAudioLayers(
            voice="authoritative",
            sfx_source=EditPlanSfxSource(
                mode="stage3_optional",
                clips=[
                    EditPlanSfxClip(shot="1A", file="clips/audio/sfx1.wav", action="adopted"),
                    EditPlanSfxClip(shot="1B", file="", action="discarded"),
                ],
            ),
            bgm=EditPlanBgm(mode="none", file=None, reference_db=None),
        ),
        warnings=[
            EditPlanWarning(code="SUBTITLE_TEXT_OVERFLOW", message="msg", severity="warning", segment_ref="shot=1A")
        ],
    )


def test_read_edit_plan_round_trips_write_edit_plan(tmp_path):
    plan = _edit_plan()
    write_edit_plan(tmp_path, plan)

    restored = read_edit_plan(tmp_path)

    assert restored == plan


def test_read_edit_plan_missing_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_edit_plan(tmp_path)


# --- jobs.payload (de)serializers -------------------------------------------


def test_stage2_outputs_payload_round_trips_through_json():
    outputs = [
        Stage2ShotOutput(shot="1A", beat=1, approved_keyframe_path="keyframes/a.png",
                          story_function="func", continuity="cont"),
    ]

    payload = json.loads(json.dumps(stage2_outputs_to_payload(outputs)))
    restored = stage2_outputs_from_payload(payload)

    assert restored == outputs


def test_qc_decisions_payload_round_trips_through_json():
    decisions = [
        ShotQcDecision(
            shot="1A", usable_start_ms=0, usable_end_ms=2000, key_event_end_ms=1000,
            settle_start_ms=1500, camera_behavior="locked-off", qc_passed=True,
        ),
    ]

    payload = json.loads(json.dumps(qc_decisions_to_payload(decisions)))
    restored = qc_decisions_from_payload(payload)

    assert restored == decisions


def test_edit_plan_human_input_payload_round_trips_through_json():
    voice_input = HumanVoiceTimingInput(timing_grade="EXACT", audio_file="voice/final.wav", total_duration_ms=None)
    semantic_sync = [HumanSemanticSyncSegment(shot="1A", start_ms=0, end_ms=2000, clip_in_ms=0, clip_out_ms=2000)]
    overlay_decisions = [HumanOverlayDecision(shot="1A", position_preset="top-left", start_ms=0, end_ms=2000)]
    sfx_decisions = [HumanSfxDecision(shot="1A", action="adopted", file="clips/audio/sfx1.wav")]

    payload = json.loads(json.dumps(
        edit_plan_human_input_to_payload(voice_input, semantic_sync, overlay_decisions, sfx_decisions)
    ))
    restored_voice, restored_sync, restored_overlay, restored_sfx = edit_plan_human_input_from_payload(payload)

    assert restored_voice == voice_input
    assert restored_sync == semantic_sync
    assert restored_overlay == overlay_decisions
    assert restored_sfx == sfx_decisions

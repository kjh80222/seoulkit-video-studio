import json

import pytest

from content_engine.video_planning.edit_plan import (
    ClipSelectionExceedsRequiredDurationError,
    EditPlanIdentityMismatchError,
    InvalidSemanticSyncTimingError,
    InvalidSfxDecisionError,
    MissingSfxFileError,
    MissingVoiceFileError,
    OverlayIdentityMismatchError,
    SfxIdentityMismatchError,
    VoiceInputInconsistentError,
    build_edit_plan,
    build_subtitle_cues,
    build_subtitle_lines,
    compute_clip_fit,
    measure_voice_duration,
    split_into_clauses,
    write_edit_plan,
)
from content_engine.video_planning.models import (
    ClipManifest,
    ClipManifestEntry,
    HumanOverlayDecision,
    HumanSemanticSyncSegment,
    HumanSfxDecision,
    HumanVoiceTimingInput,
    OptionalSourceAudio,
    PlannedShot,
    Stage2InputPackage,
    Stage3InputPackage,
    Stage3PlannedShot,
)


def _planned_shot(shot="1A", beat=1, screen_number=None, screen_label=None) -> PlannedShot:
    return PlannedShot(
        beat=beat, shot=shot, shot_type="wide", visual_purpose="purpose",
        screen_number=screen_number, screen_label=screen_label, on_screen_text=None, voice_text="voice text",
    )


def _stage2_input(shots) -> Stage2InputPackage:
    return Stage2InputPackage(schema_version="1.0", topic="topic", shots=shots)


def _stage3_shot(shot="1A", beat=1, filename=None, voice_text="voice text") -> Stage3PlannedShot:
    return Stage3PlannedShot(
        beat=beat, shot=shot, visual_purpose="purpose", voice_text=voice_text,
        approved_keyframe_path=f"keyframes/shot_{shot.lower()}_keyframe.png",
        story_function="story", continuity="continuity",
        expected_clip_filename=filename or f"clips/shot_{shot.lower()}_flow.mp4",
    )


def _stage3_input(shots) -> Stage3InputPackage:
    return Stage3InputPackage(schema_version="1.0", topic="topic", shots=shots)


def _manifest_entry(
    shot="1A", camera_behavior="movement", source_duration_ms=6000,
    usable_start_ms=0, usable_end_ms=5800, key_event_end_ms=4200, settle_start_ms=4800, available=False,
) -> ClipManifestEntry:
    return ClipManifestEntry(
        shot=shot, file=f"clips/shot_{shot.lower()}_flow.mp4", camera_behavior=camera_behavior,
        source_duration_ms=source_duration_ms, usable_start_ms=usable_start_ms, usable_end_ms=usable_end_ms,
        key_event_end_ms=key_event_end_ms, settle_start_ms=settle_start_ms,
        optional_source_audio=OptionalSourceAudio(available=available, file=None),
    )


def _clip_manifest(clips) -> ClipManifest:
    return ClipManifest(sfx_contract_version=1, clips=clips)


def _voice_input(grade="ESTIMATED", audio_file=None, total_duration_ms=4800) -> HumanVoiceTimingInput:
    return HumanVoiceTimingInput(timing_grade=grade, audio_file=audio_file, total_duration_ms=total_duration_ms)


def _sync(shot="1A", start_ms=0, end_ms=4800, clip_in_ms=0, clip_out_ms=4800) -> HumanSemanticSyncSegment:
    return HumanSemanticSyncSegment(
        shot=shot, start_ms=start_ms, end_ms=end_ms, clip_in_ms=clip_in_ms, clip_out_ms=clip_out_ms,
    )


def _overlay_decision(shot="1A", position_preset="top-right", start_ms=0, end_ms=2400) -> HumanOverlayDecision:
    return HumanOverlayDecision(shot=shot, position_preset=position_preset, start_ms=start_ms, end_ms=end_ms)


def _sfx_decision(shot="1A", action="discarded", file=None) -> HumanSfxDecision:
    return HumanSfxDecision(shot=shot, action=action, file=file)


def _build(tmp_path, *, shots=("1A",), manifest_kwargs=None, voice_input=None, sync=None,
           overlay_decisions=(), sfx_decisions=(), screen_numbers=None):
    screen_numbers = screen_numbers or {}
    stage2_input = _stage2_input([_planned_shot(shot=s, screen_number=screen_numbers.get(s)) for s in shots])
    stage3_input = _stage3_input([_stage3_shot(shot=s) for s in shots])
    manifest_kwargs = manifest_kwargs or {}
    clip_manifest = _clip_manifest([_manifest_entry(shot=s, **manifest_kwargs) for s in shots])
    voice_input = voice_input or _voice_input()
    sync = sync if sync is not None else [_sync(shot=s) for s in shots]
    return build_edit_plan(
        "test-project", stage2_input, stage3_input, clip_manifest, voice_input,
        sync, list(overlay_decisions), list(sfx_decisions), tmp_path,
    )


# --- voice input consistency / measurement ------------------------------------


def test_voice_input_estimated_requires_no_audio_file(tmp_path):
    with pytest.raises(VoiceInputInconsistentError):
        _build(tmp_path, voice_input=_voice_input(grade="ESTIMATED", audio_file="voice.wav"))


def test_voice_input_audio_based_requires_audio_file(tmp_path):
    with pytest.raises(VoiceInputInconsistentError):
        _build(tmp_path, voice_input=_voice_input(grade="AUDIO_BASED", audio_file=None))


def test_voice_input_exact_requires_audio_file(tmp_path):
    with pytest.raises(VoiceInputInconsistentError):
        _build(tmp_path, voice_input=_voice_input(grade="EXACT", audio_file=None))


def test_missing_voice_file_raises(tmp_path):
    with pytest.raises(MissingVoiceFileError):
        _build(tmp_path, voice_input=_voice_input(grade="AUDIO_BASED", audio_file="nope.wav", total_duration_ms=None))


def test_aligned_true_only_for_exact(tmp_path, make_synthetic_video):
    make_synthetic_video(tmp_path / "voice.mp4", duration_ms=4800, with_audio=True)
    plan_exact = _build(tmp_path, voice_input=_voice_input(grade="EXACT", audio_file="voice.mp4", total_duration_ms=None))
    assert plan_exact.voice.aligned is True

    plan_audio_based = _build(
        tmp_path, voice_input=_voice_input(grade="AUDIO_BASED", audio_file="voice.mp4", total_duration_ms=None),
    )
    assert plan_audio_based.voice.aligned is False


def test_total_duration_ms_measured_via_ffprobe_for_audio_based(tmp_path, make_synthetic_video):
    make_synthetic_video(tmp_path / "voice.mp4", duration_ms=2000, with_audio=True)
    plan = _build(tmp_path, voice_input=_voice_input(grade="AUDIO_BASED", audio_file="voice.mp4", total_duration_ms=None))
    assert 1900 <= plan.voice.total_duration_ms <= 2100


def test_total_duration_ms_uses_human_value_for_estimated(tmp_path):
    plan = _build(tmp_path, voice_input=_voice_input(grade="ESTIMATED", total_duration_ms=9999))
    assert plan.voice.total_duration_ms == 9999


# --- identity checks: stage3_input / clip_manifest / semantic_sync -----------


def test_raises_on_missing_clip_manifest_shot(tmp_path):
    stage2_input = _stage2_input([_planned_shot(shot="1A"), _planned_shot(shot="1B")])
    stage3_input = _stage3_input([_stage3_shot(shot="1A"), _stage3_shot(shot="1B")])
    clip_manifest = _clip_manifest([_manifest_entry(shot="1A")])
    sync = [_sync(shot="1A"), _sync(shot="1B")]

    with pytest.raises(EditPlanIdentityMismatchError) as exc_info:
        build_edit_plan("p", stage2_input, stage3_input, clip_manifest, _voice_input(), sync, [], [], tmp_path)

    assert exc_info.value.missing_clip_manifest == ["1B"]


def test_raises_on_extra_clip_manifest_shot(tmp_path):
    stage2_input = _stage2_input([_planned_shot(shot="1A")])
    stage3_input = _stage3_input([_stage3_shot(shot="1A")])
    clip_manifest = _clip_manifest([_manifest_entry(shot="1A"), _manifest_entry(shot="1B")])
    sync = [_sync(shot="1A")]

    with pytest.raises(EditPlanIdentityMismatchError) as exc_info:
        build_edit_plan("p", stage2_input, stage3_input, clip_manifest, _voice_input(), sync, [], [], tmp_path)

    assert exc_info.value.extra_clip_manifest == ["1B"]


def test_raises_on_missing_semantic_sync_shot(tmp_path):
    with pytest.raises(EditPlanIdentityMismatchError) as exc_info:
        _build(tmp_path, shots=("1A", "1B"), sync=[_sync(shot="1A")])

    assert exc_info.value.missing_semantic_sync == ["1B"]


def test_raises_on_extra_semantic_sync_shot(tmp_path):
    with pytest.raises(EditPlanIdentityMismatchError) as exc_info:
        _build(tmp_path, shots=("1A",), sync=[_sync(shot="1A"), _sync(shot="1B")])

    assert exc_info.value.extra_semantic_sync == ["1B"]


def test_raises_on_duplicate_semantic_sync_shot(tmp_path):
    with pytest.raises(EditPlanIdentityMismatchError) as exc_info:
        _build(tmp_path, shots=("1A",), sync=[_sync(shot="1A"), _sync(shot="1A")])

    assert exc_info.value.duplicate_semantic_sync == ["1A"]


# --- overlay identity ----------------------------------------------------------


def test_raises_on_missing_overlay_decision(tmp_path):
    with pytest.raises(OverlayIdentityMismatchError) as exc_info:
        _build(tmp_path, screen_numbers={"1A": "1953"}, overlay_decisions=[])

    assert exc_info.value.missing == ["1A"]


def test_raises_on_extra_overlay_decision(tmp_path):
    with pytest.raises(OverlayIdentityMismatchError) as exc_info:
        _build(tmp_path, overlay_decisions=[_overlay_decision(shot="1A")])  # no screen_number/label at all

    assert exc_info.value.extra == ["1A"]


def test_no_overlay_decision_needed_when_no_screen_text(tmp_path):
    plan = _build(tmp_path, overlay_decisions=[])
    assert plan.overlays == []


# --- SFX identity ----------------------------------------------------------


def test_raises_on_missing_sfx_decision(tmp_path):
    with pytest.raises(SfxIdentityMismatchError) as exc_info:
        _build(tmp_path, manifest_kwargs={"available": True}, sfx_decisions=[])

    assert exc_info.value.missing == ["1A"]


def test_raises_on_extra_sfx_decision(tmp_path):
    with pytest.raises(SfxIdentityMismatchError) as exc_info:
        _build(tmp_path, sfx_decisions=[_sfx_decision(shot="1A")])  # no available candidate

    assert exc_info.value.extra == ["1A"]


def test_raises_on_duplicate_sfx_decision(tmp_path):
    with pytest.raises(SfxIdentityMismatchError) as exc_info:
        _build(
            tmp_path, manifest_kwargs={"available": True},
            sfx_decisions=[_sfx_decision(shot="1A"), _sfx_decision(shot="1A")],
        )

    assert exc_info.value.duplicate == ["1A"]


def test_raises_on_adopted_sfx_without_file(tmp_path):
    with pytest.raises(InvalidSfxDecisionError):
        _build(
            tmp_path, manifest_kwargs={"available": True},
            sfx_decisions=[_sfx_decision(shot="1A", action="adopted", file=None)],
        )


def test_raises_on_missing_adopted_sfx_file(tmp_path):
    with pytest.raises(MissingSfxFileError):
        _build(
            tmp_path, manifest_kwargs={"available": True},
            sfx_decisions=[_sfx_decision(shot="1A", action="adopted", file="nope.wav")],
        )


# --- semantic-sync timing validation -------------------------------------------


def test_raises_on_invalid_semantic_sync_timing(tmp_path):
    with pytest.raises(InvalidSemanticSyncTimingError):
        _build(tmp_path, sync=[_sync(start_ms=5000, end_ms=4000)])


def test_raises_when_clip_selection_exceeds_required_duration(tmp_path):
    with pytest.raises(ClipSelectionExceedsRequiredDurationError):
        _build(tmp_path, sync=[_sync(start_ms=0, end_ms=4000, clip_in_ms=0, clip_out_ms=5000)])


def test_collects_all_clip_selection_overflow_violations(tmp_path):
    sync = [
        _sync(shot="1A", start_ms=0, end_ms=4000, clip_in_ms=0, clip_out_ms=5000),
        _sync(shot="1B", start_ms=0, end_ms=4000, clip_in_ms=0, clip_out_ms=6000),
    ]
    with pytest.raises(ClipSelectionExceedsRequiredDurationError) as exc_info:
        _build(tmp_path, shots=("1A", "1B"), sync=sync)

    assert len(exc_info.value.violations) == 2


# --- compute_clip_fit() branch table --------------------------------------------


def test_compute_clip_fit_exact_match_movement_none():
    r = compute_clip_fit("1A", 4800, 0, 4800, "movement", key_event_end_ms=1000, settle_start_ms=4000)
    assert r.hold_strategy == "none" and r.hold_ms == 0 and r.status == "OK"


def test_compute_clip_fit_exact_match_locked_off_within_key_event_none():
    r = compute_clip_fit("1A", 4800, 0, 4800, "locked-off", key_event_end_ms=6000, settle_start_ms=None)
    assert r.hold_strategy == "none" and r.status == "OK"


def test_compute_clip_fit_exact_match_locked_off_beyond_key_event_source_hold():
    r = compute_clip_fit("1A", 5000, 0, 5000, "locked-off", key_event_end_ms=3000, settle_start_ms=None)
    assert r.hold_strategy == "source_hold" and r.hold_ms == 0 and r.status == "OK"


def test_compute_clip_fit_exact_match_locked_off_null_key_event_review_required():
    r = compute_clip_fit("1A", 5000, 0, 5000, "locked-off", key_event_end_ms=None, settle_start_ms=None)
    assert r.hold_strategy == "none" and r.status == "REVIEW_REQUIRED"


def test_compute_clip_fit_shortage_settle_frame_hold_ok():
    r = compute_clip_fit("1A", 5000, 0, 4000, "movement", key_event_end_ms=3000, settle_start_ms=3500)
    assert r.hold_strategy == "settle_frame_hold" and r.hold_ms == 1000 and r.status == "OK"


def test_compute_clip_fit_shortage_null_settle_start_review_required():
    r = compute_clip_fit("1A", 5000, 0, 4000, "movement", key_event_end_ms=3000, settle_start_ms=None)
    assert r.hold_strategy == "none" and r.status == "REVIEW_REQUIRED"


def test_compute_clip_fit_shortage_clip_out_before_settle_start_review_required():
    r = compute_clip_fit("1A", 5000, 0, 4000, "movement", key_event_end_ms=3000, settle_start_ms=4500)
    assert r.hold_strategy == "none" and r.status == "REVIEW_REQUIRED"


def test_compute_clip_fit_shortage_exceeds_max_hold_review_required():
    r = compute_clip_fit("1A", 6000, 0, 4000, "movement", key_event_end_ms=3000, settle_start_ms=3500,
                          max_settle_frame_hold_ms=1500)
    assert r.hold_strategy == "settle_frame_hold" and r.hold_ms == 2000 and r.status == "REVIEW_REQUIRED"


# --- project status --------------------------------------------------------


def test_status_ready_when_exact_and_all_ok(tmp_path, make_synthetic_video):
    make_synthetic_video(tmp_path / "voice.mp4", duration_ms=4800, with_audio=True)
    plan = _build(tmp_path, voice_input=_voice_input(grade="EXACT", audio_file="voice.mp4", total_duration_ms=None))
    assert plan.status == "READY"


def test_status_review_required_when_not_exact(tmp_path):
    plan = _build(tmp_path, voice_input=_voice_input(grade="ESTIMATED"))
    assert plan.status == "REVIEW_REQUIRED"


def test_status_review_required_when_any_segment_not_ok(tmp_path, make_synthetic_video):
    make_synthetic_video(tmp_path / "voice.mp4", duration_ms=4800, with_audio=True)
    plan = _build(
        tmp_path, voice_input=_voice_input(grade="EXACT", audio_file="voice.mp4", total_duration_ms=None),
        manifest_kwargs={"camera_behavior": "locked-off", "key_event_end_ms": None},
    )
    assert plan.status == "REVIEW_REQUIRED"
    assert plan.segments[0].status == "REVIEW_REQUIRED"


# --- subtitles --------------------------------------------------------------


def test_build_subtitle_lines_wraps_within_two_lines():
    lines, overflowed = build_subtitle_lines("a short line of narration text")
    assert len(lines) <= 2
    assert overflowed is False


def test_build_subtitle_lines_flags_overflow_and_still_returns_two_lines():
    # Legacy helper - still truncates on its own. Only build_subtitle_cues()
    # (used by build_edit_plan()) is required to never lose text.
    long_text = " ".join(["word"] * 40)
    lines, overflowed = build_subtitle_lines(long_text)
    assert overflowed is True
    assert len(lines) == 2


# --- split_into_clauses() -----------------------------------------------------


def test_split_into_clauses_splits_on_sentence_and_clause_punctuation():
    text = "By 1953, Seoul had changed hands four times, and large parts of the capital lay in ruins."
    clauses = split_into_clauses(text)
    assert clauses == [
        "By 1953,",
        "Seoul had changed hands four times,",
        "and large parts of the capital lay in ruins.",
    ]


def test_split_into_clauses_splits_on_em_dash():
    text = "The city didn't just recover — it reinvented itself completely."
    clauses = split_into_clauses(text)
    assert clauses == ["The city didn't just recover —", "it reinvented itself completely."]


def test_split_into_clauses_no_punctuation_returns_single_clause():
    text = " ".join(["word"] * 40)
    assert split_into_clauses(text) == [text]


def test_split_into_clauses_preserves_every_word():
    text = "With the fighting over, displaced families slowly returned to a city with almost no working infrastructure."
    clauses = split_into_clauses(text)
    assert " ".join(clauses).split() == text.split()


def test_split_into_clauses_empty_text_returns_empty_list():
    assert split_into_clauses("") == []
    assert split_into_clauses("   ") == []


# --- build_subtitle_cues() ----------------------------------------------------


def test_build_subtitle_cues_single_clause_short_text_single_cue():
    # No clause punctuation inside -> one clause -> one cue spanning the window.
    cues = build_subtitle_cues("Seoul lay in ruins", 0, 3000)
    assert len(cues) == 1
    lines, start_ms, end_ms = cues[0]
    assert len(lines) <= 2
    assert start_ms == 0 and end_ms == 3000


def test_build_subtitle_cues_multiple_clauses_become_separate_sequential_cues():
    # Two comma-separated clauses -> two cues, each its own on-screen block,
    # sequential and covering the full window.
    cues = build_subtitle_cues("By 1953, Seoul lay in ruins.", 0, 3000)
    assert len(cues) == 2
    assert cues[0][1] == 0
    assert cues[-1][2] == 3000


def test_build_subtitle_cues_never_drops_or_reorders_words():
    text = (
        "With the fighting over, displaced families slowly returned to a city with almost no "
        "working infrastructure. The government launched a massive rebuilding campaign, clearing "
        "rubble and reconstructing the city block by block."
    )
    cues = build_subtitle_cues(text, 0, 11705)
    reconstructed = " ".join(word for lines, _, _ in cues for line in lines for word in line.split())
    assert reconstructed.split() == text.split()


def test_build_subtitle_cues_produces_multiple_cues_for_a_long_beat():
    long_beat_text = (
        "Today, Seoul is one of the world's largest, densest capitals, its skyline carrying "
        "almost no memory of the rubble it once was. The city didn't just recover — it "
        "reinvented itself completely."
    )
    cues = build_subtitle_cues(long_beat_text, 0, 11857)
    assert len(cues) > 1


def test_build_subtitle_cues_all_cues_max_two_lines():
    text = " ".join(["averyveryverylongword"] * 15) + "."
    cues = build_subtitle_cues(text, 0, 20000)
    assert len(cues) > 1
    for lines, _, _ in cues:
        assert len(lines) <= 2


def test_build_subtitle_cues_pathological_no_punctuation_preserves_all_words():
    long_text = " ".join(["word"] * 40)
    cues = build_subtitle_cues(long_text, 0, 20000)
    assert len(cues) > 1
    reconstructed = " ".join(word for lines, _, _ in cues for line in lines for word in line.split())
    assert reconstructed.split() == long_text.split()


def test_build_subtitle_cues_are_sequential_non_overlapping_within_window():
    text = (
        "Three years of fighting had torn through the same streets again and again, until an "
        "armistice finally silenced the guns in July 1953."
    )
    start_ms, end_ms = 5835, 13894
    cues = build_subtitle_cues(text, start_ms, end_ms)
    assert cues[0][1] == start_ms
    assert cues[-1][2] == end_ms
    for (_, s, e) in cues:
        assert s < e
    for (_, _, prev_end), (_, next_start, _) in zip(cues, cues[1:]):
        assert prev_end == next_start


def test_build_subtitle_cues_raises_on_invalid_window():
    with pytest.raises(ValueError):
        build_subtitle_cues("some text", 5000, 4000)


def test_build_subtitle_cues_empty_text_returns_no_cues():
    assert build_subtitle_cues("", 0, 1000) == []


# --- build_edit_plan() subtitle integration ------------------------------------


def test_subtitles_generated_per_beat_not_per_shot_no_duplication(tmp_path):
    beat_text = "By 1953, Seoul had changed hands four times, and large parts of the capital lay in ruins."
    stage2_input = _stage2_input([_planned_shot(shot="1A"), _planned_shot(shot="1B")])
    stage3_input = _stage3_input([
        _stage3_shot(shot="1A", voice_text=beat_text),
        _stage3_shot(shot="1B", voice_text=beat_text),
    ])
    clip_manifest = _clip_manifest([_manifest_entry(shot="1A"), _manifest_entry(shot="1B")])
    sync = [
        _sync(shot="1A", start_ms=0, end_ms=3000, clip_in_ms=0, clip_out_ms=3000),
        _sync(shot="1B", start_ms=3000, end_ms=5800, clip_in_ms=0, clip_out_ms=2800),
    ]
    plan = build_edit_plan(
        "p", stage2_input, stage3_input, clip_manifest, _voice_input(), sync, [], [], tmp_path,
    )
    reconstructed = " ".join(word for sub in plan.subtitles for line in sub.lines for word in line.split())
    # The Beat's narration appears exactly once across all cues, not once per shot.
    assert reconstructed.split() == beat_text.split()
    assert plan.subtitles[0].start_ms == 0
    assert plan.subtitles[-1].end_ms == 5800


def test_no_subtitle_overflow_warning_for_normal_or_pathological_narration(tmp_path):
    long_text = " ".join(["word"] * 40)
    stage2_input = _stage2_input([_planned_shot()])
    stage3_input = _stage3_input([_stage3_shot(voice_text=long_text)])
    clip_manifest = _clip_manifest([_manifest_entry()])
    plan = build_edit_plan(
        "p", stage2_input, stage3_input, clip_manifest, _voice_input(), [_sync()], [], [], tmp_path,
    )
    assert not any(w.code == "SUBTITLE_TEXT_OVERFLOW" for w in plan.warnings)
    reconstructed = " ".join(word for sub in plan.subtitles for line in sub.lines for word in line.split())
    assert reconstructed.split() == long_text.split()


def test_subtitle_cues_all_within_two_lines_via_build_edit_plan(tmp_path):
    long_text = (
        "Today, Seoul is one of the world's largest, densest capitals, its skyline carrying "
        "almost no memory of the rubble it once was. The city didn't just recover — it "
        "reinvented itself completely."
    )
    stage2_input = _stage2_input([_planned_shot()])
    stage3_input = _stage3_input([_stage3_shot(voice_text=long_text)])
    clip_manifest = _clip_manifest([_manifest_entry()])
    plan = build_edit_plan(
        "p", stage2_input, stage3_input, clip_manifest, _voice_input(), [_sync()], [], [], tmp_path,
    )
    assert len(plan.subtitles) > 1
    for sub in plan.subtitles:
        assert len(sub.lines) <= 2


# --- overlays ---------------------------------------------------------------


def test_overlay_from_screen_number_only(tmp_path):
    plan = _build(tmp_path, screen_numbers={"1A": "1953"}, overlay_decisions=[_overlay_decision(shot="1A")])
    assert len(plan.overlays) == 1
    assert plan.overlays[0].type == "giant_number"
    assert plan.overlays[0].text == "1953"


def test_overlay_from_screen_label_only(tmp_path):
    stage2_input = _stage2_input([_planned_shot(shot="1A", screen_label="DESTRUCTION")])
    stage3_input = _stage3_input([_stage3_shot(shot="1A")])
    clip_manifest = _clip_manifest([_manifest_entry(shot="1A")])
    plan = build_edit_plan(
        "p", stage2_input, stage3_input, clip_manifest, _voice_input(),
        [_sync(shot="1A")], [_overlay_decision(shot="1A")], [], tmp_path,
    )
    assert len(plan.overlays) == 1
    assert plan.overlays[0].type == "label"
    assert plan.overlays[0].text == "DESTRUCTION"


def test_overlay_from_both_screen_number_and_label(tmp_path):
    stage2_input = _stage2_input([_planned_shot(shot="1A", screen_number="1953", screen_label="DESTRUCTION")])
    stage3_input = _stage3_input([_stage3_shot(shot="1A")])
    clip_manifest = _clip_manifest([_manifest_entry(shot="1A")])
    plan = build_edit_plan(
        "p", stage2_input, stage3_input, clip_manifest, _voice_input(),
        [_sync(shot="1A")], [_overlay_decision(shot="1A")], [], tmp_path,
    )
    assert len(plan.overlays) == 2
    assert {o.type for o in plan.overlays} == {"giant_number", "label"}


def test_no_overlay_when_neither_screen_field_present(tmp_path):
    plan = _build(tmp_path, screen_numbers={}, overlay_decisions=[])
    assert plan.overlays == []


# --- SFX mapping -------------------------------------------------------------


def test_discarded_sfx_serializes_file_as_empty_string(tmp_path):
    plan = _build(
        tmp_path, manifest_kwargs={"available": True},
        sfx_decisions=[_sfx_decision(shot="1A", action="discarded", file=None)],
    )
    assert plan.audio_layers.sfx_source.clips[0].file == ""
    assert plan.audio_layers.sfx_source.clips[0].action == "discarded"


def test_adopted_sfx_uses_real_file_path(tmp_path):
    (tmp_path / "sfx_1a.wav").write_bytes(b"fake wav")
    plan = _build(
        tmp_path, manifest_kwargs={"available": True},
        sfx_decisions=[_sfx_decision(shot="1A", action="adopted", file="sfx_1a.wav")],
    )
    assert plan.audio_layers.sfx_source.clips[0].file == "sfx_1a.wav"


def test_sfx_source_mode_stage3_optional_when_clips_present(tmp_path):
    plan = _build(
        tmp_path, manifest_kwargs={"available": True},
        sfx_decisions=[_sfx_decision(shot="1A", action="discarded")],
    )
    assert plan.audio_layers.sfx_source.mode == "stage3_optional"


def test_sfx_source_mode_none_when_no_candidates(tmp_path):
    plan = _build(tmp_path, manifest_kwargs={"available": False}, sfx_decisions=[])
    assert plan.audio_layers.sfx_source.mode == "none"
    assert plan.audio_layers.sfx_source.clips == []


# --- BGM ----------------------------------------------------------------------


def test_bgm_always_none(tmp_path):
    plan = _build(tmp_path)
    assert plan.audio_layers.bgm.mode == "none"
    assert plan.audio_layers.bgm.file is None


# --- write_edit_plan() ---------------------------------------------------------


def test_write_edit_plan_creates_file_with_exact_expected_keys(tmp_path):
    plan = _build(tmp_path)
    write_edit_plan(tmp_path, plan)

    data = json.loads((tmp_path / "edit_plan.json").read_text(encoding="utf-8"))
    assert set(data.keys()) == {
        "schema_version", "project", "format", "status", "timing_grade_default",
        "voice", "segments", "overlays", "subtitles", "audio_layers", "warnings",
    }
    assert set(data["audio_layers"].keys()) == {"voice", "sfx_source", "bgm"}
    assert set(data["audio_layers"]["sfx_source"].keys()) == {"mode", "clips"}
    assert set(data["audio_layers"]["bgm"].keys()) == {"mode", "file", "reference_db"}


def test_write_edit_plan_rejects_an_existing_file_without_modifying_it(tmp_path):
    original = tmp_path / "edit_plan.json"
    original.write_text('{"already": "here"}', encoding="utf-8")
    before = original.read_bytes()
    plan = _build(tmp_path)

    with pytest.raises(FileExistsError):
        write_edit_plan(tmp_path, plan)

    assert original.read_bytes() == before


def test_write_edit_plan_round_trips_korean_content_as_utf8(tmp_path):
    stage2_input = _stage2_input([_planned_shot(shot="1A", screen_number="1953")])
    stage3_input = _stage3_input([_stage3_shot(shot="1A", voice_text="1953년, 서울은 대부분 잔해였다.")])
    clip_manifest = _clip_manifest([_manifest_entry(shot="1A")])
    plan = build_edit_plan(
        "p", stage2_input, stage3_input, clip_manifest, _voice_input(),
        [_sync(shot="1A")], [_overlay_decision(shot="1A")], [], tmp_path,
    )
    write_edit_plan(tmp_path, plan)

    raw = (tmp_path / "edit_plan.json").read_bytes()
    data = json.loads(raw.decode("utf-8"))
    assert data["segments"][0]["voice_text"] == "1953년, 서울은 대부분 잔해였다."


def test_write_edit_plan_never_touches_clip_or_voice_files(tmp_path, make_synthetic_video):
    (tmp_path / "clips").mkdir()
    clip = make_synthetic_video(tmp_path / "clips" / "shot_1a_flow.mp4", duration_ms=6000)
    before = clip.read_bytes()
    plan = _build(tmp_path)
    write_edit_plan(tmp_path, plan)
    assert clip.read_bytes() == before


# --- measure_voice_duration() --------------------------------------------------


def test_measure_voice_duration_real_ffprobe(tmp_path, make_synthetic_video):
    audio = make_synthetic_video(tmp_path / "voice.mp4", duration_ms=3000, with_audio=True)
    duration_ms = measure_voice_duration(audio)
    assert 2900 <= duration_ms <= 3100


# --- CE-3 boundary integration ---------------------------------------------


def test_edit_plan_passes_ce3_run_preflight(tmp_path, make_synthetic_video):
    from content_engine.video_planning.clip_qc import write_clip_manifest
    from content_engine.video_studio.adapter import run_preflight

    project_dir = tmp_path
    (project_dir / "clips").mkdir()
    make_synthetic_video(project_dir / "clips" / "shot_1a_flow.mp4", duration_ms=6000, with_audio=True)
    make_synthetic_video(project_dir / "voice.mp4", duration_ms=4800, with_audio=True)

    stage2_input = _stage2_input([_planned_shot(shot="1A", screen_number="1953")])
    stage3_input = _stage3_input([_stage3_shot(shot="1A")])
    clip_manifest = _clip_manifest([_manifest_entry(shot="1A", available=True)])
    write_clip_manifest(project_dir, clip_manifest)

    voice_input = _voice_input(grade="EXACT", audio_file="voice.mp4", total_duration_ms=None)
    plan = build_edit_plan(
        "test-project", stage2_input, stage3_input, clip_manifest, voice_input,
        [_sync(shot="1A", start_ms=0, end_ms=4800, clip_in_ms=0, clip_out_ms=4800)],
        [_overlay_decision(shot="1A")],
        [_sfx_decision(shot="1A", action="discarded")],
        project_dir,
    )
    write_edit_plan(project_dir, plan)

    result = run_preflight(project_dir)

    assert result.ok is True, (result.category, result.stdout, result.stderr)
    assert result.payload["effective_status"] in ("READY", "REVIEW_REQUIRED", "NOT_READY")

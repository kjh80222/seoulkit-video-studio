from content_engine.video_planning.models import (
    BeatRow,
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
    MeasuredClipFacts,
    OptionalSourceAudio,
    PlannedShot,
    ShotQcDecision,
    ShotRow,
    Stage1BeatShotTable,
    Stage2InputPackage,
    Stage2ShotOutput,
    Stage3InputPackage,
    Stage3PlannedShot,
)


def test_planned_shot_holds_exactly_the_expected_fields():
    shot = PlannedShot(
        beat=1,
        shot="1A",
        shot_type="wide",
        visual_purpose="Establish scale of destruction",
        screen_number="1953",
        screen_label=None,
        on_screen_text="1953",
        voice_text="In 1953, Seoul was mostly rubble.",
    )

    for forbidden in (
        "story_function", "continuity", "camera_behavior", "duration",
        "flow_target_duration_ms", "expected_filename", "style_anchor",
    ):
        assert not hasattr(shot, forbidden)

    assert shot.shot == "1A"


def test_stage2_input_package_holds_exactly_schema_version_topic_shots():
    package = Stage2InputPackage(schema_version="1.0", topic="test topic", shots=[])

    for forbidden in ("stage2_master_style_block", "stage2_negative_block", "style_anchor_note"):
        assert not hasattr(package, forbidden)

    assert package.schema_version == "1.0"
    assert package.topic == "test topic"
    assert package.shots == []


def test_stage2_shot_output_holds_exactly_the_expected_fields():
    output = Stage2ShotOutput(
        shot="1A", beat=1, approved_keyframe_path="keyframes/shot_1a_keyframe.png",
        story_function="...", continuity="...",
    )

    assert output.shot == "1A"
    assert output.approved_keyframe_path == "keyframes/shot_1a_keyframe.png"


def test_stage3_planned_shot_holds_exactly_the_expected_fields():
    shot = Stage3PlannedShot(
        beat=1, shot="1A", visual_purpose="...", voice_text="...",
        approved_keyframe_path="keyframes/shot_1a_keyframe.png",
        story_function="...", continuity="...",
        expected_clip_filename="clips/shot_1a_flow.mp4",
    )

    for forbidden in ("camera_behavior", "motion_prompt", "duration", "flow_target_duration_ms", "style_anchor_path"):
        assert not hasattr(shot, forbidden)

    assert shot.expected_clip_filename == "clips/shot_1a_flow.mp4"


def test_stage3_input_package_holds_exactly_schema_version_topic_shots():
    package = Stage3InputPackage(schema_version="1.0", topic="test topic", shots=[])

    assert not hasattr(package, "style_anchor_path")
    assert package.schema_version == "1.0"
    assert package.topic == "test topic"
    assert package.shots == []


def test_measured_clip_facts_holds_exactly_the_expected_fields():
    facts = MeasuredClipFacts(shot="1A", source_duration_ms=6000, has_audio_stream=True)

    for forbidden in ("usable_start_ms", "usable_end_ms", "camera_behavior", "qc_passed"):
        assert not hasattr(facts, forbidden)

    assert facts.shot == "1A"
    assert facts.source_duration_ms == 6000
    assert facts.has_audio_stream is True


def test_shot_qc_decision_holds_exactly_the_expected_fields():
    decision = ShotQcDecision(
        shot="1A", usable_start_ms=0, usable_end_ms=5800, key_event_end_ms=4200,
        settle_start_ms=4800, camera_behavior="movement", qc_passed=True,
    )

    for forbidden in ("source_duration_ms", "has_audio_stream"):
        assert not hasattr(decision, forbidden)

    assert decision.camera_behavior == "movement"
    assert decision.qc_passed is True
    assert decision.key_event_end_ms == 4200


def test_shot_qc_decision_allows_null_key_event_end_and_settle_start():
    decision = ShotQcDecision(
        shot="1A", usable_start_ms=0, usable_end_ms=5800, key_event_end_ms=None,
        settle_start_ms=None, camera_behavior="locked-off", qc_passed=True,
    )

    assert decision.key_event_end_ms is None
    assert decision.settle_start_ms is None


def test_optional_source_audio_holds_exactly_available_and_file():
    audio = OptionalSourceAudio(available=True, file=None)

    assert audio.available is True
    assert audio.file is None


def test_clip_manifest_entry_holds_exactly_the_expected_fields():
    entry = ClipManifestEntry(
        shot="1A", file="clips/shot_1a_flow.mp4", camera_behavior="movement",
        source_duration_ms=6000, usable_start_ms=0, usable_end_ms=5800,
        key_event_end_ms=4200, settle_start_ms=4800,
        optional_source_audio=OptionalSourceAudio(available=False, file=None),
    )

    assert entry.file == "clips/shot_1a_flow.mp4"
    assert entry.optional_source_audio.available is False


def test_clip_manifest_holds_exactly_sfx_contract_version_and_clips():
    manifest = ClipManifest(sfx_contract_version=1, clips=[])

    assert manifest.sfx_contract_version == 1
    assert manifest.clips == []


def test_human_voice_timing_input_holds_exactly_the_expected_fields():
    v = HumanVoiceTimingInput(timing_grade="EXACT", audio_file="voice.wav", total_duration_ms=None)

    assert v.timing_grade == "EXACT"
    assert v.audio_file == "voice.wav"
    assert v.total_duration_ms is None
    assert not hasattr(v, "aligned")


def test_human_semantic_sync_segment_holds_exactly_the_expected_fields():
    s = HumanSemanticSyncSegment(shot="1A", start_ms=0, end_ms=4800, clip_in_ms=0, clip_out_ms=4800)

    assert s.clip_in_ms == 0
    assert s.clip_out_ms == 4800


def test_human_overlay_decision_holds_exactly_the_expected_fields():
    d = HumanOverlayDecision(shot="1A", position_preset="top-right", start_ms=0, end_ms=2400)

    assert d.position_preset == "top-right"
    assert not hasattr(d, "text")


def test_human_sfx_decision_holds_exactly_the_expected_fields():
    d = HumanSfxDecision(shot="1A", action="adopted", file="clips/sfx/1a.wav")

    assert d.action == "adopted"
    assert d.file == "clips/sfx/1a.wav"


def test_edit_plan_voice_holds_exactly_the_expected_fields():
    v = EditPlanVoice(audio_file="voice.wav", aligned=True, total_duration_ms=4800)

    assert v.aligned is True
    assert not hasattr(v, "timing_grade")


def test_edit_plan_segment_holds_exactly_the_expected_fields():
    s = EditPlanSegment(
        beat=1, shot="1A", start_ms=0, end_ms=4800, timing_grade="EXACT",
        source_clip="clips/shot_1a_flow.mp4", source_duration_ms=6000,
        usable_start_ms=0, usable_end_ms=5800, key_event_end_ms=4200, settle_start_ms=4800,
        clip_in_ms=0, clip_out_ms=4800, camera_behavior="movement",
        hold_strategy="none", hold_ms=0, trim_reason=None,
        voice_text="voice text", status="OK", status_note=None,
    )

    assert s.hold_strategy == "none"
    assert s.status == "OK"


def test_edit_plan_overlay_and_subtitle_hold_exactly_the_expected_fields():
    overlay = EditPlanOverlay(
        beat=1, text="1953", type="giant_number", start_ms=0, end_ms=2400,
        position_preset="top-right", position_override=None, source="stage1_beat_table",
    )
    subtitle = EditPlanSubtitle(
        beat=1, start_ms=0, end_ms=4800, lines=["voice text"],
        position_preset="bottom-center", timing_grade="EXACT",
    )

    assert overlay.source == "stage1_beat_table"
    assert subtitle.lines == ["voice text"]


def test_edit_plan_audio_layers_tree_holds_exactly_the_expected_fields():
    sfx_clip = EditPlanSfxClip(shot="1A", file="", action="discarded")
    sfx_source = EditPlanSfxSource(mode="stage3_optional", clips=[sfx_clip])
    bgm = EditPlanBgm()
    audio_layers = EditPlanAudioLayers(voice="authoritative", sfx_source=sfx_source, bgm=bgm)

    assert bgm.mode == "none"
    assert bgm.file is None
    assert audio_layers.sfx_source.clips[0].file == ""


def test_edit_plan_warning_holds_exactly_the_expected_fields():
    w = EditPlanWarning(code="SUBTITLE_TEXT_OVERFLOW", message="msg", severity="warning")

    assert w.segment_ref is None


def test_edit_plan_holds_exactly_the_expected_fields():
    voice = EditPlanVoice(audio_file=None, aligned=False, total_duration_ms=1000)
    audio_layers = EditPlanAudioLayers(
        voice="authoritative", sfx_source=EditPlanSfxSource(mode="none", clips=[]), bgm=EditPlanBgm(),
    )
    plan = EditPlan(
        project="test", status="REVIEW_REQUIRED", timing_grade_default="ESTIMATED",
        voice=voice, segments=[], overlays=[], subtitles=[], audio_layers=audio_layers,
    )

    assert plan.warnings == []
    assert plan.status == "REVIEW_REQUIRED"


def test_beat_row_holds_exactly_the_expected_fields():
    row = BeatRow(beat=1, narration="In 1953, Seoul was mostly rubble.",
                   visual_purpose="Establish scale of destruction", screen_number="1953", screen_label=None)

    assert row.beat == 1
    assert row.narration == "In 1953, Seoul was mostly rubble."
    assert not hasattr(row, "shot")


def test_shot_row_holds_exactly_the_expected_fields():
    row = ShotRow(shot="1A", beat=1, shot_type="wide", on_screen_text="1953")

    assert row.shot == "1A"
    assert not hasattr(row, "narration")
    assert not hasattr(row, "visual_purpose")


def test_stage1_beat_shot_table_holds_exactly_beats_and_shots():
    table = Stage1BeatShotTable(beats=[], shots=[])

    assert table.beats == []
    assert table.shots == []

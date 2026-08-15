from content_engine.video_planning.models import (
    ClipManifest,
    ClipManifestEntry,
    MeasuredClipFacts,
    OptionalSourceAudio,
    PlannedShot,
    ShotQcDecision,
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

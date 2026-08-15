from content_engine.video_planning.models import PlannedShot, Stage2InputPackage


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

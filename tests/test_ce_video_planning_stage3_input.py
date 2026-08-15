import json

import pytest

from content_engine.video_planning.models import PlannedShot, Stage2InputPackage, Stage2ShotOutput
from content_engine.video_planning.stage3_input import (
    MissingKeyframeError,
    ShotIdentityMismatchError,
    build_stage3_input,
    expected_clip_filename,
    write_stage3_input,
)


def _planned_shot(beat=1, shot="1A", visual_purpose="purpose", voice_text="voice text") -> PlannedShot:
    return PlannedShot(
        beat=beat, shot=shot, shot_type="wide", visual_purpose=visual_purpose,
        screen_number=None, screen_label=None, on_screen_text=None, voice_text=voice_text,
    )


def _stage2_output(shot="1A", beat=1, path="keyframes/shot_1a_keyframe.png",
                    story_function="story", continuity="continuity") -> Stage2ShotOutput:
    return Stage2ShotOutput(
        shot=shot, beat=beat, approved_keyframe_path=path,
        story_function=story_function, continuity=continuity,
    )


def _stage2_input(shots: list[PlannedShot]) -> Stage2InputPackage:
    return Stage2InputPackage(schema_version="1.0", topic="test topic", shots=shots)


def test_expected_clip_filename_basic():
    assert expected_clip_filename("1A") == "clips/shot_1a_flow.mp4"


def test_build_stage3_input_merges_matching_shots_correctly_and_preserves_order():
    stage2_input = _stage2_input([
        _planned_shot(shot="1A", visual_purpose="purpose A", voice_text="voice A"),
        _planned_shot(shot="1B", visual_purpose="purpose B", voice_text="voice B"),
    ])
    outputs = [
        _stage2_output(shot="1B", path="keyframes/shot_1b_keyframe.png", story_function="func B", continuity="cont B"),
        _stage2_output(shot="1A", path="keyframes/shot_1a_keyframe.png", story_function="func A", continuity="cont A"),
    ]

    package = build_stage3_input(stage2_input, outputs)

    assert [s.shot for s in package.shots] == ["1A", "1B"]
    first = package.shots[0]
    assert first.visual_purpose == "purpose A"
    assert first.voice_text == "voice A"
    assert first.approved_keyframe_path == "keyframes/shot_1a_keyframe.png"
    assert first.story_function == "func A"
    assert first.continuity == "cont A"
    assert first.expected_clip_filename == "clips/shot_1a_flow.mp4"


def test_build_stage3_input_raises_on_missing_shot():
    stage2_input = _stage2_input([_planned_shot(shot="1A"), _planned_shot(shot="1B")])
    outputs = [_stage2_output(shot="1A")]

    with pytest.raises(ShotIdentityMismatchError) as exc_info:
        build_stage3_input(stage2_input, outputs)

    assert exc_info.value.missing == ["1B"]
    assert exc_info.value.extra == []


def test_build_stage3_input_raises_on_extra_shot():
    stage2_input = _stage2_input([_planned_shot(shot="1A")])
    outputs = [_stage2_output(shot="1A"), _stage2_output(shot="1B")]

    with pytest.raises(ShotIdentityMismatchError) as exc_info:
        build_stage3_input(stage2_input, outputs)

    assert exc_info.value.extra == ["1B"]
    assert exc_info.value.missing == []


def test_build_stage3_input_raises_on_duplicate_shot():
    stage2_input = _stage2_input([_planned_shot(shot="1A")])
    outputs = [_stage2_output(shot="1A"), _stage2_output(shot="1A")]

    with pytest.raises(ShotIdentityMismatchError) as exc_info:
        build_stage3_input(stage2_input, outputs)

    assert exc_info.value.duplicate == ["1A"]


def test_build_stage3_input_raises_on_beat_mismatch():
    stage2_input = _stage2_input([_planned_shot(shot="1A", beat=1)])
    outputs = [_stage2_output(shot="1A", beat=2)]

    with pytest.raises(ShotIdentityMismatchError) as exc_info:
        build_stage3_input(stage2_input, outputs)

    assert exc_info.value.beat_mismatches == ["1A"]


def test_write_stage3_input_creates_file_with_exact_expected_keys(tmp_path):
    (tmp_path / "keyframes").mkdir()
    (tmp_path / "keyframes" / "shot_1a_keyframe.png").write_bytes(b"fake png")
    stage2_input = _stage2_input([_planned_shot()])
    package = build_stage3_input(stage2_input, [_stage2_output()])

    write_stage3_input(tmp_path, package)

    data = json.loads((tmp_path / "stage3_input.json").read_text(encoding="utf-8"))
    assert set(data.keys()) == {"schema_version", "topic", "shots"}
    assert set(data["shots"][0].keys()) == {
        "beat", "shot", "visual_purpose", "voice_text", "approved_keyframe_path",
        "story_function", "continuity", "expected_clip_filename",
    }
    forbidden = {"camera_behavior", "motion_prompt", "duration", "flow_target_duration_ms", "style_anchor_path"}
    assert not (forbidden & data.keys())
    assert not (forbidden & data["shots"][0].keys())


def test_write_stage3_input_rejects_absolute_keyframe_path(tmp_path):
    stage2_input = _stage2_input([_planned_shot()])
    package = build_stage3_input(stage2_input, [_stage2_output(path="/etc/passwd")])

    with pytest.raises(ValueError):
        write_stage3_input(tmp_path, package)


def test_write_stage3_input_rejects_path_traversal(tmp_path):
    stage2_input = _stage2_input([_planned_shot()])
    package = build_stage3_input(stage2_input, [_stage2_output(path="../outside.png")])

    with pytest.raises(ValueError):
        write_stage3_input(tmp_path, package)


def test_write_stage3_input_reports_every_missing_keyframe_together(tmp_path):
    stage2_input = _stage2_input([_planned_shot(shot="1A"), _planned_shot(shot="1B")])
    outputs = [
        _stage2_output(shot="1A", path="keyframes/shot_1a_keyframe.png"),
        _stage2_output(shot="1B", path="keyframes/shot_1b_keyframe.png"),
    ]
    package = build_stage3_input(stage2_input, outputs)
    # neither keyframe file actually created

    with pytest.raises(MissingKeyframeError) as exc_info:
        write_stage3_input(tmp_path, package)

    assert set(exc_info.value.missing_paths) == {
        "keyframes/shot_1a_keyframe.png", "keyframes/shot_1b_keyframe.png",
    }


def test_write_stage3_input_succeeds_when_keyframes_exist(tmp_path):
    (tmp_path / "keyframes").mkdir()
    (tmp_path / "keyframes" / "shot_1a_keyframe.png").write_bytes(b"fake png")
    stage2_input = _stage2_input([_planned_shot()])
    package = build_stage3_input(stage2_input, [_stage2_output()])

    write_stage3_input(tmp_path, package)

    assert (tmp_path / "stage3_input.json").is_file()


def test_write_stage3_input_rejects_an_existing_file_without_modifying_it(tmp_path):
    (tmp_path / "keyframes").mkdir()
    (tmp_path / "keyframes" / "shot_1a_keyframe.png").write_bytes(b"fake png")
    original = tmp_path / "stage3_input.json"
    original.write_text('{"already": "here"}', encoding="utf-8")
    before = original.read_bytes()
    stage2_input = _stage2_input([_planned_shot()])
    package = build_stage3_input(stage2_input, [_stage2_output()])

    with pytest.raises(FileExistsError):
        write_stage3_input(tmp_path, package)

    assert original.read_bytes() == before


def test_write_stage3_input_round_trips_korean_content_as_utf8(tmp_path):
    (tmp_path / "keyframes").mkdir()
    (tmp_path / "keyframes" / "shot_1a_keyframe.png").write_bytes(b"fake png")
    stage2_input = _stage2_input([_planned_shot(visual_purpose="파괴 규모를 보여줌", voice_text="1953년, 서울은 대부분 잔해였다.")])
    outputs = [_stage2_output(story_function="이야기 기능 설명", continuity="연속성 설명")]
    package = build_stage3_input(stage2_input, outputs)

    write_stage3_input(tmp_path, package)

    raw = (tmp_path / "stage3_input.json").read_bytes()
    data = json.loads(raw.decode("utf-8"))
    assert data["shots"][0]["voice_text"] == "1953년, 서울은 대부분 잔해였다."
    assert data["shots"][0]["story_function"] == "이야기 기능 설명"


def test_write_stage3_input_never_touches_the_keyframe_file_itself(tmp_path):
    (tmp_path / "keyframes").mkdir()
    keyframe = tmp_path / "keyframes" / "shot_1a_keyframe.png"
    keyframe.write_bytes(b"original keyframe bytes")
    stage2_input = _stage2_input([_planned_shot()])
    package = build_stage3_input(stage2_input, [_stage2_output()])

    write_stage3_input(tmp_path, package)

    assert keyframe.read_bytes() == b"original keyframe bytes"

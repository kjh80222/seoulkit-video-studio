import json

import pytest

from content_engine.video_planning.models import PlannedShot
from content_engine.video_planning.stage2_input import build_stage2_input, write_stage2_input


def _shot(beat=1, shot="1A", shot_type="wide", visual_purpose="purpose", screen_number="1953",
          screen_label=None, on_screen_text="1953", voice_text="voice text") -> PlannedShot:
    return PlannedShot(
        beat=beat, shot=shot, shot_type=shot_type, visual_purpose=visual_purpose,
        screen_number=screen_number, screen_label=screen_label,
        on_screen_text=on_screen_text, voice_text=voice_text,
    )


def test_build_stage2_input_carries_topic_and_shots_through(tmp_path):
    shots = [_shot()]

    package = build_stage2_input("test topic", shots)

    assert package.schema_version == "1.0"
    assert package.topic == "test topic"
    assert package.shots == shots


def test_write_stage2_input_creates_file_with_exact_expected_keys(tmp_path):
    package = build_stage2_input("test topic", [_shot()])

    write_stage2_input(tmp_path, package)

    data = json.loads((tmp_path / "stage2_input.json").read_text(encoding="utf-8"))
    assert set(data.keys()) == {"schema_version", "topic", "shots"}
    assert set(data["shots"][0].keys()) == {
        "beat", "shot", "shot_type", "visual_purpose", "screen_number",
        "screen_label", "on_screen_text", "voice_text",
    }
    forbidden = {
        "story_function", "continuity", "camera_behavior", "duration",
        "flow_target_duration_ms", "expected_filename", "style_anchor",
        "stage2_master_style_block", "stage2_negative_block",
    }
    assert not (forbidden & data.keys())
    assert not (forbidden & data["shots"][0].keys())


def test_write_stage2_input_serializes_optional_none_fields_as_null(tmp_path):
    package = build_stage2_input("test topic", [_shot(screen_number=None, screen_label=None, on_screen_text=None)])

    write_stage2_input(tmp_path, package)

    data = json.loads((tmp_path / "stage2_input.json").read_text(encoding="utf-8"))
    shot = data["shots"][0]
    assert shot["screen_number"] is None
    assert shot["screen_label"] is None
    assert shot["on_screen_text"] is None


def test_write_stage2_input_preserves_multiple_shots_in_order(tmp_path):
    shots = [
        _shot(beat=1, shot="1A", voice_text="one"),
        _shot(beat=1, shot="1B", voice_text="two"),
    ]
    package = build_stage2_input("test topic", shots)

    write_stage2_input(tmp_path, package)

    data = json.loads((tmp_path / "stage2_input.json").read_text(encoding="utf-8"))
    assert [s["shot"] for s in data["shots"]] == ["1A", "1B"]
    assert [s["voice_text"] for s in data["shots"]] == ["one", "two"]


def test_write_stage2_input_round_trips_korean_content_as_utf8(tmp_path):
    topic = "서울이 여러 번 파괴되고도 살아남은 역사"
    shots = [_shot(visual_purpose="파괴 규모를 보여줌", voice_text="1953년, 서울은 대부분 잔해였다.")]
    package = build_stage2_input(topic, shots)

    write_stage2_input(tmp_path, package)

    raw = (tmp_path / "stage2_input.json").read_bytes()
    data = json.loads(raw.decode("utf-8"))
    assert data["topic"] == topic
    assert data["shots"][0]["voice_text"] == "1953년, 서울은 대부분 잔해였다."


def test_write_stage2_input_rejects_an_existing_file_without_modifying_it(tmp_path):
    original = tmp_path / "stage2_input.json"
    original.write_text('{"already": "here"}', encoding="utf-8")
    before = original.read_bytes()

    with pytest.raises(FileExistsError):
        write_stage2_input(tmp_path, build_stage2_input("new topic", [_shot()]))

    assert original.read_bytes() == before


def test_write_stage2_input_touches_nothing_else_in_project_dir(tmp_path):
    (tmp_path / "clips").mkdir()

    write_stage2_input(tmp_path, build_stage2_input("test topic", [_shot()]))

    assert sorted(p.name for p in tmp_path.iterdir()) == ["clips", "stage2_input.json"]
    assert list((tmp_path / "clips").iterdir()) == []

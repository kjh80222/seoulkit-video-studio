import json

import pytest

from content_engine.video_planning.models import BeatRow, ShotRow, Stage1BeatShotTable
from content_engine.video_planning.stage1_input import (
    DuplicateOverlayInBeatError,
    DuplicateStage1IdentityError,
    InvalidShotTypeError,
    MalformedStage1InputError,
    MissingStage1InputError,
    OnScreenTextMismatchError,
    OrphanShotError,
    ShotBeatIdMismatchError,
    build_planned_shots,
    read_stage1_beat_shot_table,
)


def _beat_row(beat=1, narration="In 1953, Seoul was mostly rubble.", visual_purpose="purpose",
              screen_number=None, screen_label=None) -> BeatRow:
    return BeatRow(beat=beat, narration=narration, visual_purpose=visual_purpose,
                    screen_number=screen_number, screen_label=screen_label)


def _shot_row(shot="1A", beat=1, shot_type="wide", on_screen_text=None) -> ShotRow:
    return ShotRow(shot=shot, beat=beat, shot_type=shot_type, on_screen_text=on_screen_text)


def _table(beats, shots) -> Stage1BeatShotTable:
    return Stage1BeatShotTable(beats=beats, shots=shots)


def _write(project_dir, data) -> None:
    (project_dir / "script").mkdir(parents=True, exist_ok=True)
    (project_dir / "script" / "stage1_beat_shot_table.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8",
    )


# --- read_stage1_beat_shot_table() ---------------------------------------------


def test_read_raises_on_missing_file(tmp_path):
    with pytest.raises(MissingStage1InputError):
        read_stage1_beat_shot_table(tmp_path)


def test_read_raises_on_invalid_json(tmp_path):
    (tmp_path / "script").mkdir()
    (tmp_path / "script" / "stage1_beat_shot_table.json").write_text("{ not valid json", encoding="utf-8")

    with pytest.raises(MalformedStage1InputError):
        read_stage1_beat_shot_table(tmp_path)


def test_read_raises_when_beats_not_list(tmp_path):
    _write(tmp_path, {"beats": "nope", "shots": []})

    with pytest.raises(MalformedStage1InputError) as exc_info:
        read_stage1_beat_shot_table(tmp_path)

    assert any("beats" in issue for issue in exc_info.value.issues)


def test_read_raises_when_shots_not_list(tmp_path):
    _write(tmp_path, {"beats": [], "shots": "nope"})

    with pytest.raises(MalformedStage1InputError) as exc_info:
        read_stage1_beat_shot_table(tmp_path)

    assert any("shots" in issue for issue in exc_info.value.issues)


def test_read_raises_when_row_not_object(tmp_path):
    _write(tmp_path, {"beats": ["not an object"], "shots": []})

    with pytest.raises(MalformedStage1InputError) as exc_info:
        read_stage1_beat_shot_table(tmp_path)

    assert any("beats[0]" in issue for issue in exc_info.value.issues)


def test_read_raises_on_missing_required_field(tmp_path):
    _write(tmp_path, {"beats": [{"beat": 1, "visual_purpose": "p", "screen_number": None, "screen_label": None}],
                       "shots": []})

    with pytest.raises(MalformedStage1InputError) as exc_info:
        read_stage1_beat_shot_table(tmp_path)

    assert any("narration" in issue for issue in exc_info.value.issues)


def test_read_raises_on_unexpected_field(tmp_path):
    _write(tmp_path, {
        "beats": [{"beat": 1, "narration": "n", "visual_purpose": "p", "screen_number": None,
                   "screen_label": None, "extra_field": "oops"}],
        "shots": [],
    })

    with pytest.raises(MalformedStage1InputError) as exc_info:
        read_stage1_beat_shot_table(tmp_path)

    assert any("extra_field" in issue for issue in exc_info.value.issues)


def test_read_collects_multiple_malformed_rows_together(tmp_path):
    _write(tmp_path, {
        "beats": [
            {"beat": 1, "visual_purpose": "p", "screen_number": None, "screen_label": None},  # missing narration
            "not an object",
        ],
        "shots": [],
    })

    with pytest.raises(MalformedStage1InputError) as exc_info:
        read_stage1_beat_shot_table(tmp_path)

    assert len(exc_info.value.issues) == 2


def test_read_parses_real_example_shape_successfully(tmp_path):
    _write(tmp_path, {
        "note": "EXAMPLE",
        "beats": [
            {"beat": 1, "narration": "In 1953, Seoul was mostly rubble.",
             "visual_purpose": "Establish scale of destruction", "screen_number": "1953", "screen_label": None},
        ],
        "shots": [
            {"shot": "1A", "beat": 1, "shot_type": "wide", "on_screen_text": "1953"},
            {"shot": "1B", "beat": 1, "shot_type": "detail", "on_screen_text": None},
        ],
    })

    table = read_stage1_beat_shot_table(tmp_path)

    assert len(table.beats) == 1
    assert len(table.shots) == 2
    assert table.shots[0].shot == "1A"


# --- build_planned_shots(): identity / format validation -----------------------


def test_build_raises_on_duplicate_beat_id():
    table = _table([_beat_row(beat=1), _beat_row(beat=1)], [_shot_row(shot="1A", beat=1)])

    with pytest.raises(DuplicateStage1IdentityError) as exc_info:
        build_planned_shots(table)

    assert exc_info.value.duplicate_beats == [1]


def test_build_raises_on_duplicate_shot_id():
    table = _table([_beat_row(beat=1)], [_shot_row(shot="1A", beat=1), _shot_row(shot="1A", beat=1)])

    with pytest.raises(DuplicateStage1IdentityError) as exc_info:
        build_planned_shots(table)

    assert exc_info.value.duplicate_shots == ["1A"]


def test_build_raises_on_duplicate_beat_and_shot_id_together():
    table = _table(
        [_beat_row(beat=1), _beat_row(beat=1)],
        [_shot_row(shot="1A", beat=1), _shot_row(shot="1A", beat=1)],
    )

    with pytest.raises(DuplicateStage1IdentityError) as exc_info:
        build_planned_shots(table)

    assert exc_info.value.duplicate_beats == [1]
    assert exc_info.value.duplicate_shots == ["1A"]


def test_build_raises_on_shot_beat_id_mismatch_single():
    table = _table([_beat_row(beat=1), _beat_row(beat=2)], [_shot_row(shot="1A", beat=2)])

    with pytest.raises(ShotBeatIdMismatchError) as exc_info:
        build_planned_shots(table)

    assert exc_info.value.violations == [("1A", 2)]


def test_build_raises_on_shot_beat_id_mismatch_multiple():
    table = _table(
        [_beat_row(beat=1), _beat_row(beat=2)],
        [_shot_row(shot="1A", beat=2), _shot_row(shot="A1", beat=1)],  # malformed format too
    )

    with pytest.raises(ShotBeatIdMismatchError) as exc_info:
        build_planned_shots(table)

    assert len(exc_info.value.violations) == 2


def test_build_raises_on_orphan_shot_single():
    table = _table([_beat_row(beat=1)], [_shot_row(shot="1A", beat=1), _shot_row(shot="2A", beat=2)])

    with pytest.raises(OrphanShotError) as exc_info:
        build_planned_shots(table)

    assert exc_info.value.shots == ["2A"]


def test_build_raises_on_orphan_shot_multiple():
    table = _table([], [_shot_row(shot="1A", beat=1), _shot_row(shot="2A", beat=2)])

    with pytest.raises(OrphanShotError) as exc_info:
        build_planned_shots(table)

    assert set(exc_info.value.shots) == {"1A", "2A"}


def test_build_raises_on_invalid_shot_type_single():
    table = _table([_beat_row(beat=1)], [_shot_row(shot="1A", beat=1, shot_type="cinematic")])

    with pytest.raises(InvalidShotTypeError) as exc_info:
        build_planned_shots(table)

    assert exc_info.value.violations == [("1A", "cinematic")]


def test_build_raises_on_invalid_shot_type_multiple():
    table = _table(
        [_beat_row(beat=1)],
        [_shot_row(shot="1A", beat=1, shot_type="cinematic"), _shot_row(shot="1B", beat=1, shot_type="dramatic")],
    )

    with pytest.raises(InvalidShotTypeError) as exc_info:
        build_planned_shots(table)

    assert len(exc_info.value.violations) == 2


def test_build_raises_on_on_screen_text_mismatch_single():
    table = _table(
        [_beat_row(beat=1, screen_number="1953", screen_label=None)],
        [_shot_row(shot="1A", beat=1, on_screen_text="1954")],
    )

    with pytest.raises(OnScreenTextMismatchError) as exc_info:
        build_planned_shots(table)

    assert exc_info.value.violations == [("1A", "1954", "1953", None)]


def test_build_raises_on_on_screen_text_mismatch_multiple():
    table = _table(
        [_beat_row(beat=1, screen_number="1953", screen_label=None), _beat_row(beat=2, screen_number="$116")],
        [_shot_row(shot="1A", beat=1, on_screen_text="1954"), _shot_row(shot="2A", beat=2, on_screen_text="$117")],
    )

    with pytest.raises(OnScreenTextMismatchError) as exc_info:
        build_planned_shots(table)

    assert len(exc_info.value.violations) == 2


def test_build_raises_on_duplicate_overlay_in_beat():
    table = _table(
        [_beat_row(beat=1, screen_number="1953", screen_label=None)],
        [_shot_row(shot="1A", beat=1, on_screen_text="1953"), _shot_row(shot="1B", beat=1, on_screen_text="1953")],
    )

    with pytest.raises(DuplicateOverlayInBeatError) as exc_info:
        build_planned_shots(table)

    assert exc_info.value.violations == [(1, ["1A", "1B"])]


# --- build_planned_shots(): normal behavior -------------------------------------


def test_build_preserves_shot_order():
    table = _table(
        [_beat_row(beat=1)],
        [_shot_row(shot="1B", beat=1), _shot_row(shot="1A", beat=1)],
    )

    shots = build_planned_shots(table)

    assert [s.shot for s in shots] == ["1B", "1A"]


def test_build_copies_visual_purpose_screen_number_screen_label_from_beat():
    table = _table(
        [_beat_row(beat=1, visual_purpose="Establish scale", screen_number="1953", screen_label=None)],
        [_shot_row(shot="1A", beat=1)],
    )

    shots = build_planned_shots(table)

    assert shots[0].visual_purpose == "Establish scale"
    assert shots[0].screen_number == "1953"
    assert shots[0].screen_label is None


def test_build_duplicates_voice_text_across_beat_shots():
    table = _table(
        [_beat_row(beat=1, narration="In 1953, Seoul was mostly rubble.")],
        [_shot_row(shot="1A", beat=1), _shot_row(shot="1B", beat=1)],
    )

    shots = build_planned_shots(table)

    assert shots[0].voice_text == "In 1953, Seoul was mostly rubble."
    assert shots[1].voice_text == "In 1953, Seoul was mostly rubble."


def test_build_allows_null_on_screen_text_without_mismatch_check():
    table = _table(
        [_beat_row(beat=1, screen_number="1953", screen_label=None)],
        [_shot_row(shot="1A", beat=1, on_screen_text=None)],
    )

    shots = build_planned_shots(table)

    assert shots[0].on_screen_text is None


# --- CE-4c integration -------------------------------------------------------


def test_build_planned_shots_integrates_with_ce4c_build_stage2_input(tmp_path):
    from content_engine.video_planning.stage2_input import build_stage2_input, write_stage2_input

    table = _table(
        [_beat_row(beat=1, narration="In 1953, Seoul was mostly rubble.",
                    visual_purpose="Establish scale of destruction", screen_number="1953", screen_label=None)],
        [_shot_row(shot="1A", beat=1, shot_type="wide", on_screen_text="1953"),
         _shot_row(shot="1B", beat=1, shot_type="detail", on_screen_text=None)],
    )

    shots = build_planned_shots(table)
    package = build_stage2_input("Why Did Seoul Have to Rebuild Almost Everything?", shots)
    write_stage2_input(tmp_path, package)

    data = json.loads((tmp_path / "stage2_input.json").read_text(encoding="utf-8"))
    assert len(data["shots"]) == 2
    assert data["shots"][0]["voice_text"] == "In 1953, Seoul was mostly rubble."


# --- Korean / UTF-8 -----------------------------------------------------------


def test_read_and_build_round_trips_korean_narration(tmp_path):
    _write(tmp_path, {
        "beats": [{"beat": 1, "narration": "1953년, 서울은 대부분 잔해였다.",
                    "visual_purpose": "파괴 규모를 보여줌", "screen_number": "1953", "screen_label": None}],
        "shots": [{"shot": "1A", "beat": 1, "shot_type": "wide", "on_screen_text": "1953"}],
    })

    table = read_stage1_beat_shot_table(tmp_path)
    shots = build_planned_shots(table)

    assert shots[0].voice_text == "1953년, 서울은 대부분 잔해였다."
    assert shots[0].visual_purpose == "파괴 규모를 보여줌"

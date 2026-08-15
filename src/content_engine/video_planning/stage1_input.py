"""Topic / Research / Stage 1 Producer - CE-4b scope only.

CE-4b is Human + LLM assisted, matching Stage 2/3's already-established
pattern (and now Stage 4's Semantic Sync/SFX decisions): the Stage 1
manual (`SEOULKIT_Stage1_MINI_Script_System_v2.0`) is a self-contained
copy-paste master prompt designed to run inside a human's own ChatGPT/
Claude chat ("Paste this alone into ChatGPT or Claude... STOP and wait").
Content Engine never calls an LLM API or a web-search service here - CE-4b
only reads the human's *approved* Stage 1 output, validates it, and
converts it into the `list[PlannedShot]` shape CE-4c already requires.
This mirrors CE-4c/CE-4d's own precedent (structure a human's confirmed
creative output; never generate it) rather than introducing this
project's first LLM-provider dependency, first API credential, or first
non-deterministic external call.

## Input artifact

`project_dir/script/stage1_beat_shot_table.json` - a new workspace
convention (parallel to `clips/`/`keyframes/`), matching the real
`examples/sample-project/script/stage1_beat_shot_table.json` example's
shape exactly, plus one addition (`BeatRow.narration`, see below). A human
saves the confirmed Beat/Shot tables from their Stage 1 chat session here
before running CE-4b - the same "human places a file, Content Engine only
reads it" pattern CE-4d/CE-4e already use for `keyframes/`/`clips/`.

## Why `BeatRow.narration` exists

The Stage 1 manual's own machine-readable field mapping (sec. 17-1) marks
the Beat table's Narration column "reference only" and says
`edit_plan.json`'s `voice_text` should instead be "derived from the actual
Voice" - i.e. Stage 4's job, once real TTS exists. But CE-4f v1 was
deliberately built without TTS/forced-alignment (see `edit_plan.py`), and
CE-4c's already-frozen `PlannedShot.voice_text` is a required field with
no other source. `BeatRow.narration` is CE-4b's pragmatic v1 answer: the
approved Stage 1 narration text, copied as **provisional** `voice_text`
for every shot in that beat - not a final Voice-derived truth. When real
Voice/alignment automation exists, CE-4f (not CE-4b) is where final
timing against the actual waveform belongs; this module makes no claim
beyond "here is the best text available before Voice exists."

Narration is never split per shot here. The Stage 1 manual describes a
beat's ~10 seconds of narration becoming Shot A (wide) + Shot B (detail)
without specifying exactly where the words divide, and the Stage 4
manual's own Semantic Sync principle (ch. 06: "내레이션의 의미가 바뀌는
지점이 곧 Shot이 바뀌는 지점") explicitly assigns that word-level boundary
decision to CE-4f. Splitting it here would silently take over a judgment
CE-4f already owns.

## Validation - fixed category order, never aggregated across categories

Each category below is checked independently, in this order; a category
that finds violations collects every one of *that kind* and raises once,
never mixing violations from two different categories into one exception,
and never stopping at the first violation within a category:

1. File missing -> `MissingStage1InputError`.
2. JSON parse failure, non-object top level, `beats`/`shots` not lists,
   a row that isn't an object, missing/extra fields, or dataclass
   construction failure (a raw `TypeError`/`ValueError` never escapes
   this module) -> `MalformedStage1InputError`, every such issue across
   every row collected together.
3. Duplicate `beat`/`shot` identity - re-confirmed necessary because
   `{b.beat: b for b in table.beats}` would otherwise let a later
   duplicate silently overwrite an earlier one with no trace, exactly the
   kind of silent data corruption CE-4d/CE-4e's own identity checks exist
   to prevent -> `DuplicateStage1IdentityError`.
4. Shot ID format/consistency: a `shot` must match `{beat}{LETTER+}`
   (e.g. `"1A"` for `beat=1`) and its leading digits must equal its own
   `beat` field - the Stage 1 manual's own ID convention (sec. 13),
   verified rather than assumed -> `ShotBeatIdMismatchError`.
5. Orphan shots - a `shot.beat` with no matching row in `beats[]` at all
   -> `OrphanShotError`.
6. `shot_type` outside the Stage 1 manual's exact 7 values (sec. 13:
   `wide`/`detail`/`map`/`archive`/`diagram`/`transition`/`comparison`)
   -> `InvalidShotTypeError`.
7. `on_screen_text` (when not `None`) must equal its beat's
   `screen_number` or `screen_label` - the manual's own consistency rule
   (sec. 17-1: "must equal the Beat table's screen_number/screen_label...
   If they don't match, stop and ask") treated as a hard build failure,
   not a warning, since the confirmed Stage 1 structured input is what's
   wrong, not something a downstream phase should silently patch over
   -> `OnScreenTextMismatchError`.
8. At most one overlay-bearing shot (`on_screen_text is not None`) per
   beat - the manual's own reasoning (sec. 13-1: "Stage 4 plans a single
   overlay timing per beat, not per shot") -> `DuplicateOverlayInBeatError`.

Only after all eight pass does `build_planned_shots()` assemble the
`list[PlannedShot]`.

## CE-4b / CE-4c boundary

CE-4b's only output is `list[PlannedShot]`, held in memory. It never
writes `stage2_input.json` - that remains CE-4c's `write_stage2_input()`,
unmodified. The intended call sequence is:

    table = read_stage1_beat_shot_table(project_dir)
    shots = build_planned_shots(table)
    package = build_stage2_input(topic, shots)   # CE-4c, unchanged
    write_stage2_input(project_dir, package)      # CE-4c, unchanged
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from content_engine.video_planning.models import BeatRow, PlannedShot, ShotRow, Stage1BeatShotTable

_ALLOWED_SHOT_TYPES = {"wide", "detail", "map", "archive", "diagram", "transition", "comparison"}
_BEAT_ROW_FIELDS = {"beat", "narration", "visual_purpose", "screen_number", "screen_label"}
_SHOT_ROW_FIELDS = {"shot", "beat", "shot_type", "on_screen_text"}
_SHOT_ID_PATTERN = re.compile(r"^(\d+)([A-Za-z]+)$")


class MissingStage1InputError(Exception):
    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(f"{path} not found")


class MalformedStage1InputError(Exception):
    def __init__(self, path: Path, issues: list[str]) -> None:
        self.path = path
        self.issues = issues
        super().__init__(f"{path} is malformed: {issues!r}")


class DuplicateStage1IdentityError(Exception):
    def __init__(self, duplicate_beats: list[int], duplicate_shots: list[str]) -> None:
        self.duplicate_beats = duplicate_beats
        self.duplicate_shots = duplicate_shots
        super().__init__(f"duplicate identity: beats={duplicate_beats!r}, shots={duplicate_shots!r}")


class ShotBeatIdMismatchError(Exception):
    def __init__(self, violations: list[tuple[str, int]]) -> None:
        self.violations = violations
        super().__init__(f"shot ID does not match its beat field: {violations!r}")


class OrphanShotError(Exception):
    def __init__(self, shots: list[str]) -> None:
        self.shots = shots
        super().__init__(f"shots reference a beat not present in beats[]: {shots!r}")


class InvalidShotTypeError(Exception):
    def __init__(self, violations: list[tuple[str, str]]) -> None:
        self.violations = violations
        super().__init__(f"shot_type outside the allowed 7 values: {violations!r}")


class OnScreenTextMismatchError(Exception):
    def __init__(self, violations: list[tuple[str, str, str | None, str | None]]) -> None:
        self.violations = violations
        super().__init__(f"on_screen_text does not match its beat's screen_number/screen_label: {violations!r}")


class DuplicateOverlayInBeatError(Exception):
    def __init__(self, violations: list[tuple[int, list[str]]]) -> None:
        self.violations = violations
        super().__init__(f"more than one overlay-bearing shot within a beat: {violations!r}")


def _validate_row_shape(raw: Any, expected_fields: set[str], label: str) -> list[str]:
    if not isinstance(raw, dict):
        return [f"{label} is not an object"]
    missing = expected_fields - raw.keys()
    extra = raw.keys() - expected_fields
    issues = []
    if missing:
        issues.append(f"{label} missing fields: {sorted(missing)}")
    if extra:
        issues.append(f"{label} has unexpected fields: {sorted(extra)}")
    return issues


def read_stage1_beat_shot_table(project_dir: Path) -> Stage1BeatShotTable:
    path = project_dir / "script" / "stage1_beat_shot_table.json"
    if not path.is_file():
        raise MissingStage1InputError(path)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MalformedStage1InputError(path, [f"invalid JSON: {exc}"]) from exc

    if not isinstance(data, dict):
        raise MalformedStage1InputError(path, ["top-level JSON is not an object"])

    issues: list[str] = []
    raw_beats = data.get("beats")
    raw_shots = data.get("shots")
    if not isinstance(raw_beats, list):
        issues.append("'beats' is missing or not a list")
        raw_beats = []
    if not isinstance(raw_shots, list):
        issues.append("'shots' is missing or not a list")
        raw_shots = []

    beats: list[BeatRow] = []
    for i, raw in enumerate(raw_beats):
        row_issues = _validate_row_shape(raw, _BEAT_ROW_FIELDS, f"beats[{i}]")
        if row_issues:
            issues += row_issues
            continue
        try:
            beats.append(BeatRow(**raw))
        except (TypeError, ValueError) as exc:
            issues.append(f"beats[{i}]: {exc}")

    shots: list[ShotRow] = []
    for i, raw in enumerate(raw_shots):
        row_issues = _validate_row_shape(raw, _SHOT_ROW_FIELDS, f"shots[{i}]")
        if row_issues:
            issues += row_issues
            continue
        try:
            shots.append(ShotRow(**raw))
        except (TypeError, ValueError) as exc:
            issues.append(f"shots[{i}]: {exc}")

    if issues:
        raise MalformedStage1InputError(path, issues)

    return Stage1BeatShotTable(beats=beats, shots=shots)


def build_planned_shots(table: Stage1BeatShotTable) -> list[PlannedShot]:
    beat_counts = Counter(b.beat for b in table.beats)
    shot_counts = Counter(s.shot for s in table.shots)
    duplicate_beats = sorted(b for b, c in beat_counts.items() if c > 1)
    duplicate_shots = sorted(s for s, c in shot_counts.items() if c > 1)
    if duplicate_beats or duplicate_shots:
        raise DuplicateStage1IdentityError(duplicate_beats, duplicate_shots)

    id_violations = []
    for s in table.shots:
        match = _SHOT_ID_PATTERN.match(s.shot)
        if not match or int(match.group(1)) != s.beat:
            id_violations.append((s.shot, s.beat))
    if id_violations:
        raise ShotBeatIdMismatchError(id_violations)

    beats_by_id = {b.beat: b for b in table.beats}

    orphans = [s.shot for s in table.shots if s.beat not in beats_by_id]
    if orphans:
        raise OrphanShotError(orphans)

    invalid_types = [(s.shot, s.shot_type) for s in table.shots if s.shot_type not in _ALLOWED_SHOT_TYPES]
    if invalid_types:
        raise InvalidShotTypeError(invalid_types)

    mismatches = []
    for s in table.shots:
        if s.on_screen_text is None:
            continue
        beat = beats_by_id[s.beat]
        if s.on_screen_text not in (beat.screen_number, beat.screen_label):
            mismatches.append((s.shot, s.on_screen_text, beat.screen_number, beat.screen_label))
    if mismatches:
        raise OnScreenTextMismatchError(mismatches)

    overlay_shots_by_beat: dict[int, list[str]] = {}
    for s in table.shots:
        if s.on_screen_text is not None:
            overlay_shots_by_beat.setdefault(s.beat, []).append(s.shot)
    duplicate_overlays = [(beat, shots) for beat, shots in overlay_shots_by_beat.items() if len(shots) > 1]
    if duplicate_overlays:
        raise DuplicateOverlayInBeatError(duplicate_overlays)

    return [
        PlannedShot(
            beat=s.beat, shot=s.shot, shot_type=s.shot_type,
            visual_purpose=beats_by_id[s.beat].visual_purpose,
            screen_number=beats_by_id[s.beat].screen_number,
            screen_label=beats_by_id[s.beat].screen_label,
            on_screen_text=s.on_screen_text,
            voice_text=beats_by_id[s.beat].narration,
        )
        for s in table.shots
    ]

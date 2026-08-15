"""Stage 1 -> Stage 2 -> Stage 3 handoff data shapes - CE-4c/CE-4d scope.

Pure data shapes, exactly like CE-1's `Job` and CE-4a's `ContentPackage` -
no persistence methods, no derived fields.

`PlannedShot`/`Stage2InputPackage` (CE-4c): Stage 1 content only - nothing
Stage 2 itself decides (`story_function`, `continuity`, style anchor
selection) lives here, and nothing from later stages (`camera_behavior`,
motion prompts, clip duration, `expected_filename`) does either. See
`stage2_input.py`'s module docstring for why each of those was
deliberately excluded.

`Stage2ShotOutput` (CE-4d): the structured shape Stage 2's own output is
expected to arrive in - `approved_keyframe_path`/`story_function`/
`continuity` per shot, keyed by `shot` (matching the real
`references/stage2_shot_metadata.json` example's shape). Content Engine
does not generate any of these values; they are Stage 2's own creative
output, supplied as structured data rather than typed in ad hoc per call.

`Stage3PlannedShot`/`Stage3InputPackage` (CE-4d): the Stage 1 + Stage 2
data merged by shot identity, plus `expected_clip_filename` (computed
here, first consumed here). No `camera_behavior`, no motion/Flow prompt
content, no clip duration, and no top-level style-anchor field - Stage
3's own manual does not list a separate style-anchor input; the approved
keyframe already carries whatever style consistency Stage 2 established.
See `stage3_input.py`'s module docstring for the full reasoning.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PlannedShot:
    beat: int
    shot: str
    shot_type: str
    visual_purpose: str
    screen_number: str | None
    screen_label: str | None
    on_screen_text: str | None
    voice_text: str


@dataclass
class Stage2InputPackage:
    schema_version: str
    topic: str
    shots: list[PlannedShot]


@dataclass
class Stage2ShotOutput:
    shot: str
    beat: int
    approved_keyframe_path: str
    story_function: str
    continuity: str


@dataclass
class Stage3PlannedShot:
    beat: int
    shot: str
    visual_purpose: str
    voice_text: str
    approved_keyframe_path: str
    story_function: str
    continuity: str
    expected_clip_filename: str


@dataclass
class Stage3InputPackage:
    schema_version: str
    topic: str
    shots: list[Stage3PlannedShot]

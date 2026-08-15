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

`MeasuredClipFacts`/`ShotQcDecision`/`OptionalSourceAudio`/
`ClipManifestEntry`/`ClipManifest` (CE-4e): the Human-Assisted Stage 3 QC
shapes. `MeasuredClipFacts` is the only auto-measured data (ffprobe on the
real Flow clip); every other per-shot judgment
(`usable_start_ms`/`usable_end_ms`/`key_event_end_ms`/`settle_start_ms`/
`camera_behavior`/`qc_passed`) is a `ShotQcDecision` a human supplies after
watching the clip against the Stage 3 manual's G4 checklist - none of it
is inferred from pixels or from the Stage 3 manual's proportional timeline
structure. See `clip_qc.py`'s module docstring for the full reasoning,
including why `OptionalSourceAudio.file` is always `None` in this phase.

CE-4f's dataclasses split cleanly into two families, never conflated:
Human* (`HumanVoiceTimingInput`/`HumanSemanticSyncSegment`/
`HumanOverlayDecision`/`HumanSfxDecision`) are what a human supplies -
Voice/alignment grade, Semantic Sync timeline+clip selection, overlay
position/timing, and SFX adopt/discard decisions. EditPlan* (`EditPlanVoice`/
`EditPlanSegment`/`EditPlanOverlay`/`EditPlanSubtitle`/`EditPlanSfxClip`/
`EditPlanSfxSource`/`EditPlanBgm`/`EditPlanAudioLayers`/`EditPlanWarning`/
`EditPlan`) mirror the frozen `edit_plan.schema.json` shape field-for-field
- a human input dataclass is never serialized directly; `edit_plan.py`
converts Human* + `stage2_input.json`/`stage3_input.json`/`clip_manifest.json`
into EditPlan* before writing. See `edit_plan.py`'s module docstring for
the full reasoning, including why `EditPlanSfxClip.file` is `""` for a
discarded candidate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


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


@dataclass
class MeasuredClipFacts:
    shot: str
    source_duration_ms: int
    has_audio_stream: bool


@dataclass
class ShotQcDecision:
    shot: str
    usable_start_ms: int
    usable_end_ms: int
    key_event_end_ms: int | None
    settle_start_ms: int | None
    camera_behavior: Literal["movement", "locked-off"]
    qc_passed: bool


@dataclass
class OptionalSourceAudio:
    available: bool
    file: str | None


@dataclass
class ClipManifestEntry:
    shot: str
    file: str
    camera_behavior: str
    source_duration_ms: int
    usable_start_ms: int
    usable_end_ms: int
    key_event_end_ms: int | None
    settle_start_ms: int | None
    optional_source_audio: OptionalSourceAudio


@dataclass
class ClipManifest:
    sfx_contract_version: int
    clips: list[ClipManifestEntry]


@dataclass
class HumanVoiceTimingInput:
    timing_grade: Literal["EXACT", "AUDIO_BASED", "ESTIMATED"]
    audio_file: str | None
    total_duration_ms: int | None


@dataclass
class HumanSemanticSyncSegment:
    shot: str
    start_ms: int
    end_ms: int
    clip_in_ms: int
    clip_out_ms: int


@dataclass
class HumanOverlayDecision:
    shot: str
    position_preset: str
    start_ms: int
    end_ms: int


@dataclass
class HumanSfxDecision:
    shot: str
    action: Literal["adopted", "discarded"]
    file: str | None


@dataclass
class EditPlanVoice:
    audio_file: str | None
    aligned: bool
    total_duration_ms: int


@dataclass
class EditPlanSegment:
    beat: int
    shot: str
    start_ms: int
    end_ms: int
    timing_grade: str
    source_clip: str
    source_duration_ms: int
    usable_start_ms: int
    usable_end_ms: int
    key_event_end_ms: int | None
    settle_start_ms: int | None
    clip_in_ms: int
    clip_out_ms: int
    camera_behavior: str
    hold_strategy: str
    hold_ms: int
    trim_reason: str | None
    voice_text: str
    status: str
    status_note: str | None


@dataclass
class EditPlanOverlay:
    beat: int
    text: str
    type: str
    start_ms: int
    end_ms: int
    position_preset: str
    position_override: None
    source: str


@dataclass
class EditPlanSubtitle:
    beat: int
    start_ms: int
    end_ms: int
    lines: list[str]
    position_preset: str
    timing_grade: str


@dataclass
class EditPlanSfxClip:
    shot: str
    file: str
    action: Literal["adopted", "discarded"]


@dataclass
class EditPlanSfxSource:
    mode: Literal["stage3_optional", "none"]
    clips: list[EditPlanSfxClip]


@dataclass
class EditPlanBgm:
    mode: Literal["selected", "none"] = "none"
    file: str | None = None
    reference_db: float | None = None


@dataclass
class EditPlanAudioLayers:
    voice: str
    sfx_source: EditPlanSfxSource
    bgm: EditPlanBgm


@dataclass
class EditPlanWarning:
    code: str
    message: str
    severity: Literal["info", "warning", "blocking"]
    segment_ref: str | None = None


@dataclass
class EditPlan:
    project: str
    status: str
    timing_grade_default: str
    voice: EditPlanVoice
    segments: list[EditPlanSegment]
    overlays: list[EditPlanOverlay]
    subtitles: list[EditPlanSubtitle]
    audio_layers: EditPlanAudioLayers
    warnings: list[EditPlanWarning] = field(default_factory=list)

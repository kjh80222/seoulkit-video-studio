"""Stage 4 Voice / Alignment / Semantic Sync - CE-4f scope only.

CE-4f is the sole, first-ever producer of `edit_plan.json` (Stage 4/5
contract: "Stage 4 produces, Stage 5 only reads"). v1 does not call any
TTS engine or forced-alignment tool - the Stage 4 manual explicitly
refuses to hardcode a vendor, and running one would pull API auth, cost,
and retry handling into this phase before any of that is decided. Instead
CE-4f consumes a `HumanVoiceTimingInput` a human already prepared outside
this pipeline. The one thing CE-4f *does* measure automatically is a real
Voice audio file's duration via `ffprobe` (`measure_voice_duration()`) -
this is container inspection, not TTS/alignment, the same boundary CE-4e
already drew for `source_duration_ms`.

Semantic Sync (which shot's footage plays under which stretch of
narration, and which exact `clip_in_ms`/`clip_out_ms` of that footage is
used) is also human-authored, via `HumanSemanticSyncSegment`. CE-4f does
not recompute a clip's in/out point from `usable_start_ms` - doing so
would silently re-decide the same editorial judgment Semantic Sync
already made. CE-4f's own job on that data is narrower: verify the
selected clip range isn't longer than the timeline slot it's assigned to
(`selected_ms <= required_ms` - a plain arithmetic impossibility if
violated, not a judgment call), and compute whether a Hold is needed to
fill any shortfall.

## Timing grade -> `voice.total_duration_ms`

ESTIMATED means no final Voice audio exists at all (Stage 4 manual ch.04)
- `total_duration_ms` can only be a human estimate in that case.
AUDIO_BASED/EXACT both have a real `audio_file`, so `measure_voice_duration()`
ffprobes it directly rather than trusting a human-typed number - purely
mechanical, no different in kind from CE-4e's own `measure_clip()`.
`aligned` is never asked for separately; it is derived
(`timing_grade == "EXACT"`), since asking for it independently would let a
human declare an internal contradiction (e.g. `EXACT` with `aligned=False`)
with no code anywhere to catch it (`preflight/validator.py` never reads
`voice.aligned`, confirmed by full-repo grep).

## Clip fit / Hold (`compute_clip_fit()`)

`selected_ms = clip_out_ms - clip_in_ms`, `required_ms = end_ms - start_ms`,
both from the human's own `HumanSemanticSyncSegment`.

`selected_ms > required_ms` is not a "needs review" state - it means
`end_ms - start_ms != clip_out_ms - clip_in_ms + hold_ms` can never hold
for any `hold_ms >= 0`, i.e. the human input is already mathematically
inconsistent with Stage 5's own duration invariant. `build_edit_plan()`
collects every such shot and raises before computing anything else or
writing any file - this is not a REVIEW_REQUIRED-worthy plan, it is not a
plan at all.

`selected_ms == required_ms` needs no hold: `hold_strategy="none"`,
except a `locked-off` clip whose `clip_out_ms` reaches past
`key_event_end_ms` (using more of the stable region than the bare key
event needs) is labeled `"source_hold"` for that reason alone -
`render/hold.py`'s own docstring confirms `source_hold` and `none` are
handled identically at render time ("처리 없음" for both; the extra time
is already baked into `clip_in_ms`/`clip_out_ms` by the time `trim_clip()`
runs) - the label is purely descriptive, never load-bearing for
rendering. If `key_event_end_ms` is `None`, that classification can't be
made at all, so the segment is marked `REVIEW_REQUIRED` rather than
silently guessing `"none"`.

`selected_ms < required_ms` needs a settle-frame hold for the shortfall
(`shortage_ms = required_ms - selected_ms`) - but only if `settle_start_ms`
is known and `clip_in_ms`/`clip_out_ms` actually reaches into the
confirmed-stable region (`clip_out_ms >= settle_start_ms`); otherwise
there is no verified stable frame to freeze, and the segment is marked
`REVIEW_REQUIRED` rather than freezing an unconfirmed frame.
`max_settle_frame_hold_ms` reuses `preflight/validator.py`'s own
`DEFAULT_MAX_SETTLE_FRAME_HOLD_MS=1500` rather than inventing a new
threshold; exceeding it still produces a real (honest) `hold_ms`, marked
`REVIEW_REQUIRED` - `preflight`'s own `MAX_HOLD_MS_EXCEEDED` will catch it
again independently at render time, but CE-4f does not rely on that catch
to keep its own `status` field honest (Stage 4 manual: "문제가 있는데
READY로 표시하지 않는다").

`hold_strategy="artificial_video_stretch"` never appears anywhere in this
module - it is not in the schema's enum at all, so there is no branch
that could produce it.

## `optional_source_audio.available` / SFX candidate resolution

`HumanSfxDecision` is where a human finally resolves what CE-4e left
undecided: whether a Stage 3 audio candidate (`available=True` in
`clip_manifest.json`) is `adopted` or `discarded`, and - only if adopted -
where the real, usable SFX asset actually lives. `edit_plan.schema.json`'s
`sfx_source.clips[]` requires `file` to be a non-null string regardless of
`action` (re-confirmed: `"required": ["shot","file","action"]`, `"file":
{"type":"string"}` - no `"null"` in the type). `check_file_existence()`
(Video Studio) only checks `.file` when `action=="adopted"` - a
`discarded` entry's `.file` is never opened, decoded, or existence-checked
by anything downstream, and `check_sfx_contract_resolution()` never reads
`.file` at all (only `.get("available")`). Given that, CE-4f v1 writes
`file=""` for `discarded` entries: **this is a v1-only compatibility
representation for an explicitly discarded Stage 3 audio candidate,
required only because the frozen schema demands a string even when
`action="discarded"` - it does not represent an audio asset path, and is
never interpreted as one by any code that reads it.** `adopted` still
requires a real, existing, human-supplied project-relative file.

`sfx_source.mode` is `"stage3_optional"` when any `clips[]` entry exists,
`"none"` when no shot in the whole project ever had an available
candidate.

## BGM

`audio_layers.bgm = {"mode": "none"}`, unconditionally. The Stage 4
manual's own layer-authority table (ch.13) assigns BGM to "Stage
5(프로듀서)의 선택" - Stage 4 has no BGM decision to make at all.

## Overlays

Overlay *text* comes only from `stage2_input.json`'s `PlannedShot.screen_number`/
`screen_label` (CE-4c's preserved copy of Stage 1 data) - CE-4f reads
`stage2_input.json` read-only, for this purpose alone, never re-deriving
or re-typing Stage 1 content, and never writing to it. A shot with
`screen_number` gets a `"giant_number"` overlay; a shot with `screen_label`
gets a `"label"` overlay; a shot can produce both, one, or neither. Overlay
*position* and *exposure window* (`position_preset`/`start_ms`/`end_ms`)
are a separate human decision (`HumanOverlayDecision`) - the Stage 4
manual's own "PER-SHOT OVERRIDE only on conflict" rule has no concrete
conflict-detection algorithm specified, so CE-4f does not invent one; a
human supplies the final position/window directly. `position_override` is
always `None` and `source` is always the schema-fixed `"stage1_beat_table"`.

## Subtitles

`build_subtitle_lines()` is a **pure text-wrapping** operation (a display
format, not new wording) - greedy word-wrap at `voice_text`'s own word
boundaries, up to 2 lines of ~40 characters. It never invents text, only
breaks lines. If the wrapped result would need more than 2 lines, the
first 2 are still written (best-effort, schema-legal) and a
`SUBTITLE_TEXT_OVERFLOW` warning is recorded - text is never silently
truncated or summarized to "fix" the overflow. The manual's richer
clause-boundary-aware wrapping is not implemented in v1.

## `status` (project-level)

`status="READY"` **iff** `timing_grade_default=="EXACT"` **and** every
segment's `status=="OK"`. Nothing in `preflight/validator.py` or
`execution/` reads `timing_grade`/`voice.aligned` at all (confirmed by
grep) - `compute_effective_status()` only ever blends the plan's *own*
declared `status` with whatever Pre-flight issues it finds, so this
mapping is enforced nowhere except here. `status="NOT_READY"` is never
written into a file at all: a missing/mismatched required input (identity
mismatch across `stage3_input.json`/`clip_manifest.json`/human inputs) is
a hard build failure (an exception, no `edit_plan.json` written), matching
CE-4c/CE-4d/CE-4e's own precedent of never writing a file when required
input isn't trustworthy enough to build from.

## Write model

Create-only, matching CE-4c/CE-4d/CE-4e: `write_edit_plan()` raises
`FileExistsError` if `project_dir/edit_plan.json` already exists. No
overwrite, no backup, no versioning. The Stage 4 manual's own "discard and
rebuild from scratch when a better input arrives" principle is not
implemented as an automatic rewrite path in v1 - if a project needs a new
`edit_plan.json` from improved input, that is an out-of-band decision, not
something this module automates.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from content_engine.video_planning.models import (
    ClipManifest,
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
    Stage2InputPackage,
    Stage3InputPackage,
)

DEFAULT_MAX_SETTLE_FRAME_HOLD_MS = 1500


class VoiceInputInconsistentError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class MissingVoiceFileError(Exception):
    def __init__(self, audio_file: str) -> None:
        self.audio_file = audio_file
        super().__init__(f"voice audio_file not found: {audio_file!r}")


class EditPlanIdentityMismatchError(Exception):
    def __init__(
        self,
        missing_clip_manifest: list[str],
        extra_clip_manifest: list[str],
        missing_semantic_sync: list[str],
        extra_semantic_sync: list[str],
        duplicate_semantic_sync: list[str],
    ) -> None:
        self.missing_clip_manifest = missing_clip_manifest
        self.extra_clip_manifest = extra_clip_manifest
        self.missing_semantic_sync = missing_semantic_sync
        self.extra_semantic_sync = extra_semantic_sync
        self.duplicate_semantic_sync = duplicate_semantic_sync
        super().__init__(
            f"stage3_input/clip_manifest/semantic_sync shots do not match: "
            f"missing_clip_manifest={missing_clip_manifest!r}, extra_clip_manifest={extra_clip_manifest!r}, "
            f"missing_semantic_sync={missing_semantic_sync!r}, extra_semantic_sync={extra_semantic_sync!r}, "
            f"duplicate_semantic_sync={duplicate_semantic_sync!r}"
        )


class OverlayIdentityMismatchError(Exception):
    def __init__(self, missing: list[str], extra: list[str], duplicate: list[str]) -> None:
        self.missing = missing
        self.extra = extra
        self.duplicate = duplicate
        super().__init__(
            f"overlay decisions do not match shots requiring an overlay: "
            f"missing={missing!r}, extra={extra!r}, duplicate={duplicate!r}"
        )


class SfxIdentityMismatchError(Exception):
    def __init__(self, missing: list[str], extra: list[str], duplicate: list[str]) -> None:
        self.missing = missing
        self.extra = extra
        self.duplicate = duplicate
        super().__init__(
            f"SFX decisions do not match available candidates in clip_manifest.json: "
            f"missing={missing!r}, extra={extra!r}, duplicate={duplicate!r}"
        )


class InvalidSfxDecisionError(Exception):
    def __init__(self, shots: list[str]) -> None:
        self.shots = shots
        super().__init__(f"'adopted' SFX decisions missing a file path: {shots!r}")


class MissingSfxFileError(Exception):
    def __init__(self, missing_paths: list[str]) -> None:
        self.missing_paths = missing_paths
        super().__init__(f"adopted SFX asset files not found: {missing_paths!r}")


class InvalidSemanticSyncTimingError(Exception):
    def __init__(self, shots: list[str]) -> None:
        self.shots = shots
        super().__init__(f"start_ms >= end_ms for shots: {shots!r}")


class ClipSelectionExceedsRequiredDurationError(Exception):
    def __init__(self, violations: list[tuple[str, int, int]]) -> None:
        self.violations = violations
        super().__init__(
            "human clip selection is longer than its timeline slot (mathematically invalid, "
            f"not reviewable): {violations!r}"
        )


def measure_voice_duration(audio_file: Path) -> int:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(audio_file)],
        capture_output=True, text=True, check=True,
    )
    return round(float(proc.stdout.strip()) * 1000)


@dataclass
class ClipFitResult:
    hold_strategy: str
    hold_ms: int
    status: str
    status_note: str | None
    trim_reason: str | None


def compute_clip_fit(
    shot: str,
    required_ms: int,
    clip_in_ms: int,
    clip_out_ms: int,
    camera_behavior: str,
    key_event_end_ms: int | None,
    settle_start_ms: int | None,
    max_settle_frame_hold_ms: int = DEFAULT_MAX_SETTLE_FRAME_HOLD_MS,
) -> ClipFitResult:
    selected_ms = clip_out_ms - clip_in_ms

    if selected_ms == required_ms:
        if camera_behavior == "locked-off":
            if key_event_end_ms is None:
                return ClipFitResult(
                    "none", 0, "REVIEW_REQUIRED",
                    "key_event_end_ms not recorded - cannot classify source_hold vs none", None,
                )
            if clip_out_ms > key_event_end_ms:
                return ClipFitResult(
                    "source_hold", 0, "OK", None,
                    "source_hold: clip_out_ms extends beyond key_event_end_ms into the stable region",
                )
        return ClipFitResult("none", 0, "OK", None, None)

    shortage_ms = required_ms - selected_ms
    if settle_start_ms is None:
        return ClipFitResult(
            "none", 0, "REVIEW_REQUIRED",
            "settle_start_ms not recorded - cannot confirm a stable freeze point", None,
        )
    if clip_out_ms < settle_start_ms:
        return ClipFitResult(
            "none", 0, "REVIEW_REQUIRED",
            "clip_out_ms precedes the confirmed settle region", None,
        )

    trim_reason = f"settle_frame_hold: {shortage_ms}ms freeze applied beyond the selected clip range"
    if shortage_ms > max_settle_frame_hold_ms:
        return ClipFitResult(
            "settle_frame_hold", shortage_ms, "REVIEW_REQUIRED",
            f"required hold {shortage_ms}ms exceeds max {max_settle_frame_hold_ms}ms", trim_reason,
        )
    return ClipFitResult("settle_frame_hold", shortage_ms, "OK", None, trim_reason)


def build_subtitle_lines(voice_text: str, max_chars: int = 40, max_lines: int = 2) -> tuple[list[str], bool]:
    words = voice_text.split()
    all_lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                all_lines.append(current)
            current = word
    if current:
        all_lines.append(current)

    overflowed = len(all_lines) > max_lines
    return all_lines[:max_lines], overflowed


def build_edit_plan(
    project: str,
    stage2_input: Stage2InputPackage,
    stage3_input: Stage3InputPackage,
    clip_manifest: ClipManifest,
    voice_input: HumanVoiceTimingInput,
    semantic_sync: list[HumanSemanticSyncSegment],
    overlay_decisions: list[HumanOverlayDecision],
    sfx_decisions: list[HumanSfxDecision],
    project_dir: Path,
    max_settle_frame_hold_ms: int = DEFAULT_MAX_SETTLE_FRAME_HOLD_MS,
) -> EditPlan:
    if voice_input.timing_grade == "ESTIMATED":
        if voice_input.audio_file is not None:
            raise VoiceInputInconsistentError("timing_grade=ESTIMATED requires audio_file=None")
    else:
        if voice_input.audio_file is None:
            raise VoiceInputInconsistentError(f"timing_grade={voice_input.timing_grade} requires a real audio_file")

    if voice_input.audio_file is not None and not (project_dir / voice_input.audio_file).is_file():
        raise MissingVoiceFileError(voice_input.audio_file)

    stage3_shots = {s.shot: s for s in stage3_input.shots}
    manifest_shots = {c.shot: c for c in clip_manifest.clips}
    expected_shots = list(stage3_shots.keys())

    sync_by_shot: dict[str, HumanSemanticSyncSegment] = {}
    duplicate_sync: list[str] = []
    for seg in semantic_sync:
        if seg.shot in sync_by_shot:
            duplicate_sync.append(seg.shot)
        sync_by_shot[seg.shot] = seg

    missing_clip_manifest = [s for s in expected_shots if s not in manifest_shots]
    extra_clip_manifest = [s for s in manifest_shots if s not in stage3_shots]
    missing_semantic_sync = [s for s in expected_shots if s not in sync_by_shot]
    extra_semantic_sync = [s for s in sync_by_shot if s not in stage3_shots]

    if missing_clip_manifest or extra_clip_manifest or missing_semantic_sync or extra_semantic_sync or duplicate_sync:
        raise EditPlanIdentityMismatchError(
            missing_clip_manifest, extra_clip_manifest,
            missing_semantic_sync, extra_semantic_sync, sorted(set(duplicate_sync)),
        )

    stage2_shots = {s.shot: s for s in stage2_input.shots}
    overlay_required_shots = [
        s.shot for s in stage2_input.shots if s.screen_number is not None or s.screen_label is not None
    ]
    overlay_by_shot: dict[str, HumanOverlayDecision] = {}
    duplicate_overlay: list[str] = []
    for d in overlay_decisions:
        if d.shot in overlay_by_shot:
            duplicate_overlay.append(d.shot)
        overlay_by_shot[d.shot] = d

    missing_overlay = [s for s in overlay_required_shots if s not in overlay_by_shot]
    extra_overlay = [s for s in overlay_by_shot if s not in overlay_required_shots]
    if missing_overlay or extra_overlay or duplicate_overlay:
        raise OverlayIdentityMismatchError(missing_overlay, extra_overlay, sorted(set(duplicate_overlay)))

    sfx_required_shots = [c.shot for c in clip_manifest.clips if c.optional_source_audio.available]
    sfx_by_shot: dict[str, HumanSfxDecision] = {}
    duplicate_sfx: list[str] = []
    for d in sfx_decisions:
        if d.shot in sfx_by_shot:
            duplicate_sfx.append(d.shot)
        sfx_by_shot[d.shot] = d

    missing_sfx = [s for s in sfx_required_shots if s not in sfx_by_shot]
    extra_sfx = [s for s in sfx_by_shot if s not in sfx_required_shots]
    if missing_sfx or extra_sfx or duplicate_sfx:
        raise SfxIdentityMismatchError(missing_sfx, extra_sfx, sorted(set(duplicate_sfx)))

    invalid_adopted = [s for s, d in sfx_by_shot.items() if d.action == "adopted" and d.file is None]
    if invalid_adopted:
        raise InvalidSfxDecisionError(sorted(invalid_adopted))

    missing_sfx_files = [
        d.file for d in sfx_by_shot.values() if d.action == "adopted" and not (project_dir / d.file).is_file()
    ]
    if missing_sfx_files:
        raise MissingSfxFileError(missing_sfx_files)

    bad_timing = [seg.shot for seg in semantic_sync if seg.start_ms >= seg.end_ms]
    if bad_timing:
        raise InvalidSemanticSyncTimingError(sorted(bad_timing))

    overflow_violations = [
        (seg.shot, seg.clip_out_ms - seg.clip_in_ms, seg.end_ms - seg.start_ms)
        for seg in semantic_sync
        if (seg.clip_out_ms - seg.clip_in_ms) > (seg.end_ms - seg.start_ms)
    ]
    if overflow_violations:
        raise ClipSelectionExceedsRequiredDurationError(overflow_violations)

    voice_aligned = voice_input.timing_grade == "EXACT"
    if voice_input.timing_grade == "ESTIMATED":
        total_duration_ms = voice_input.total_duration_ms
    else:
        total_duration_ms = measure_voice_duration(project_dir / voice_input.audio_file)

    segments: list[EditPlanSegment] = []
    for shot in expected_shots:
        s3 = stage3_shots[shot]
        cm = manifest_shots[shot]
        sync = sync_by_shot[shot]
        required_ms = sync.end_ms - sync.start_ms
        fit = compute_clip_fit(
            shot, required_ms, sync.clip_in_ms, sync.clip_out_ms,
            cm.camera_behavior, cm.key_event_end_ms, cm.settle_start_ms, max_settle_frame_hold_ms,
        )
        segments.append(EditPlanSegment(
            beat=s3.beat, shot=shot, start_ms=sync.start_ms, end_ms=sync.end_ms,
            timing_grade=voice_input.timing_grade,
            source_clip=s3.expected_clip_filename,
            source_duration_ms=cm.source_duration_ms,
            usable_start_ms=cm.usable_start_ms, usable_end_ms=cm.usable_end_ms,
            key_event_end_ms=cm.key_event_end_ms, settle_start_ms=cm.settle_start_ms,
            clip_in_ms=sync.clip_in_ms, clip_out_ms=sync.clip_out_ms,
            camera_behavior=cm.camera_behavior,
            hold_strategy=fit.hold_strategy, hold_ms=fit.hold_ms, trim_reason=fit.trim_reason,
            voice_text=s3.voice_text, status=fit.status, status_note=fit.status_note,
        ))

    overlays: list[EditPlanOverlay] = []
    for shot in overlay_required_shots:
        planned = stage2_shots[shot]
        decision = overlay_by_shot[shot]
        if planned.screen_number is not None:
            overlays.append(EditPlanOverlay(
                beat=planned.beat, text=planned.screen_number, type="giant_number",
                start_ms=decision.start_ms, end_ms=decision.end_ms,
                position_preset=decision.position_preset, position_override=None,
                source="stage1_beat_table",
            ))
        if planned.screen_label is not None:
            overlays.append(EditPlanOverlay(
                beat=planned.beat, text=planned.screen_label, type="label",
                start_ms=decision.start_ms, end_ms=decision.end_ms,
                position_preset=decision.position_preset, position_override=None,
                source="stage1_beat_table",
            ))

    subtitles: list[EditPlanSubtitle] = []
    warnings: list[EditPlanWarning] = []
    for shot in expected_shots:
        s3 = stage3_shots[shot]
        sync = sync_by_shot[shot]
        lines, overflowed = build_subtitle_lines(s3.voice_text)
        if overflowed:
            warnings.append(EditPlanWarning(
                code="SUBTITLE_TEXT_OVERFLOW",
                message=f"voice_text for shot {shot!r} does not fit in 2 lines",
                severity="warning", segment_ref=f"shot={shot}",
            ))
        subtitles.append(EditPlanSubtitle(
            beat=s3.beat, start_ms=sync.start_ms, end_ms=sync.end_ms,
            lines=lines, position_preset="bottom-center", timing_grade=voice_input.timing_grade,
        ))

    sfx_clips: list[EditPlanSfxClip] = []
    for shot in expected_shots:
        if shot not in sfx_by_shot:
            continue
        d = sfx_by_shot[shot]
        if d.action == "adopted":
            sfx_clips.append(EditPlanSfxClip(shot=shot, file=d.file, action="adopted"))
        else:
            sfx_clips.append(EditPlanSfxClip(shot=shot, file="", action="discarded"))

    audio_layers = EditPlanAudioLayers(
        voice="authoritative",
        sfx_source=EditPlanSfxSource(
            mode="stage3_optional" if sfx_clips else "none",
            clips=sfx_clips,
        ),
        bgm=EditPlanBgm(mode="none"),
    )

    all_ok = all(seg.status == "OK" for seg in segments)
    status = "READY" if (voice_input.timing_grade == "EXACT" and all_ok) else "REVIEW_REQUIRED"

    return EditPlan(
        project=project,
        status=status,
        timing_grade_default=voice_input.timing_grade,
        voice=EditPlanVoice(
            audio_file=voice_input.audio_file, aligned=voice_aligned, total_duration_ms=total_duration_ms,
        ),
        segments=segments,
        overlays=overlays,
        subtitles=subtitles,
        audio_layers=audio_layers,
        warnings=warnings,
    )


def write_edit_plan(project_dir: Path, plan: EditPlan) -> None:
    path = project_dir / "edit_plan.json"
    if path.exists():
        raise FileExistsError(f"{path} already exists - CE-4f does not overwrite an existing plan")

    payload = {
        "schema_version": "1.0",
        "project": plan.project,
        "format": "MINI",
        "status": plan.status,
        "timing_grade_default": plan.timing_grade_default,
        "voice": {
            "audio_file": plan.voice.audio_file,
            "aligned": plan.voice.aligned,
            "total_duration_ms": plan.voice.total_duration_ms,
        },
        "segments": [
            {
                "beat": s.beat, "shot": s.shot, "start_ms": s.start_ms, "end_ms": s.end_ms,
                "timing_grade": s.timing_grade, "source_clip": s.source_clip,
                "source_duration_ms": s.source_duration_ms,
                "usable_start_ms": s.usable_start_ms, "usable_end_ms": s.usable_end_ms,
                "key_event_end_ms": s.key_event_end_ms, "settle_start_ms": s.settle_start_ms,
                "clip_in_ms": s.clip_in_ms, "clip_out_ms": s.clip_out_ms,
                "camera_behavior": s.camera_behavior,
                "hold_strategy": s.hold_strategy, "hold_ms": s.hold_ms, "trim_reason": s.trim_reason,
                "voice_text": s.voice_text, "status": s.status, "status_note": s.status_note,
            }
            for s in plan.segments
        ],
        "overlays": [
            {
                "beat": o.beat, "text": o.text, "type": o.type, "start_ms": o.start_ms, "end_ms": o.end_ms,
                "position_preset": o.position_preset, "position_override": o.position_override, "source": o.source,
            }
            for o in plan.overlays
        ],
        "subtitles": [
            {
                "beat": s.beat, "start_ms": s.start_ms, "end_ms": s.end_ms, "lines": s.lines,
                "position_preset": s.position_preset, "timing_grade": s.timing_grade,
            }
            for s in plan.subtitles
        ],
        "audio_layers": {
            "voice": plan.audio_layers.voice,
            "sfx_source": {
                "mode": plan.audio_layers.sfx_source.mode,
                "clips": [
                    {"shot": c.shot, "file": c.file, "action": c.action}
                    for c in plan.audio_layers.sfx_source.clips
                ],
            },
            "bgm": {
                "mode": plan.audio_layers.bgm.mode,
                "file": plan.audio_layers.bgm.file,
                "reference_db": plan.audio_layers.bgm.reference_db,
            },
        },
        "warnings": [
            {"code": w.code, "message": w.message, "severity": w.severity, "segment_ref": w.segment_ref}
            for w in plan.warnings
        ],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

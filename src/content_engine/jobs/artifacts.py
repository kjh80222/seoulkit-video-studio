"""Worker-side artifact (de)serialization - CE-2B scope only.

Two different gaps this module fills, neither by touching CE-4c/CE-4d/
CE-4e/CE-4f's own files:

1. **No reader ever existed for `stage2_input.json`/`stage3_input.json`/
   `clip_manifest.json`/`edit_plan.json`.** Every existing test in this
   project constructs `Stage2InputPackage`/`Stage3InputPackage`/
   `ClipManifest`/`EditPlan` directly in Python and keeps it in memory for
   the one call that needs it. A worker picking up a job on a later polling
   cycle (a different call, possibly a different process) cannot do that -
   it has only the project directory on disk. `read_stage2_input()`/
   `read_stage3_input()`/`read_clip_manifest()`/`read_edit_plan()` are each
   the exact structural inverse of that module's own `write_*()` function -
   same field set, same nesting, no new fields invented and no validation
   duplicated (a file that doesn't parse into the expected shape raises a
   plain `KeyError`/`TypeError` here; re-validating content the way
   CE-4b/CE-4d/CE-4e/CE-4f already do is their job, not this module's).

2. **`Stage2ShotOutput`/`ShotQcDecision`/`HumanVoiceTimingInput`/
   `HumanSemanticSyncSegment`/`HumanOverlayDecision`/`HumanSfxDecision` have
   never had a file format at all** - every existing test builds them
   in-process. CE-2B does not invent a new on-disk convention for these
   (that would be a bigger, riskier change than adding one DB column, and
   an unfounded new schema this project has no manual basis for) - instead
   `jobs.payload` (a JSON column, see `db/migrations.py`) carries them,
   supplied by whoever creates the job. The `*_from_payload()` functions
   below are the only place that JSON is turned back into these dataclasses;
   the `*_to_payload()` functions are the inverse, for whatever assembles a
   `create_job()` call.

Neither direction re-derives, defaults, or guesses a single human judgment
value (`usable_start_ms`, `camera_behavior`, `clip_in_ms`, `qc_passed`, SFX
`action`, ...) - every one of them must already be present in the JSON a
human (or whatever tool a human used) supplied. A payload missing a
required key raises a plain `KeyError`/`TypeError` from the dataclass
constructor - this module does not build a second validation layer on top
of CE-4b's precedent (`_validate_row_shape()`) for six new shapes; the
caller (`jobs/dispatch.py`) catches any such exception the same way it
catches every other job-body exception and records the job `FAILED`.
"""

from __future__ import annotations

import json
from pathlib import Path

from content_engine.video_planning.models import (
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
    OptionalSourceAudio,
    PlannedShot,
    ShotQcDecision,
    Stage2InputPackage,
    Stage2ShotOutput,
    Stage3InputPackage,
    Stage3PlannedShot,
)

# --- stage2_input.json / stage3_input.json / clip_manifest.json / edit_plan.json readers ---


def read_stage2_input(project_dir: Path) -> Stage2InputPackage:
    data = json.loads((project_dir / "stage2_input.json").read_text(encoding="utf-8"))
    return Stage2InputPackage(
        schema_version=data["schema_version"],
        topic=data["topic"],
        shots=[PlannedShot(**s) for s in data["shots"]],
    )


def read_stage3_input(project_dir: Path) -> Stage3InputPackage:
    data = json.loads((project_dir / "stage3_input.json").read_text(encoding="utf-8"))
    return Stage3InputPackage(
        schema_version=data["schema_version"],
        topic=data["topic"],
        shots=[Stage3PlannedShot(**s) for s in data["shots"]],
    )


def read_clip_manifest(project_dir: Path) -> ClipManifest:
    data = json.loads((project_dir / "clips" / "clip_manifest.json").read_text(encoding="utf-8"))
    return ClipManifest(
        sfx_contract_version=data["sfx_contract_version"],
        clips=[
            ClipManifestEntry(
                shot=c["shot"], file=c["file"], camera_behavior=c["camera_behavior"],
                source_duration_ms=c["source_duration_ms"],
                usable_start_ms=c["usable_start_ms"], usable_end_ms=c["usable_end_ms"],
                key_event_end_ms=c["key_event_end_ms"], settle_start_ms=c["settle_start_ms"],
                optional_source_audio=OptionalSourceAudio(**c["optional_source_audio"]),
            )
            for c in data["clips"]
        ],
    )


def read_edit_plan(project_dir: Path) -> EditPlan:
    data = json.loads((project_dir / "edit_plan.json").read_text(encoding="utf-8"))
    return EditPlan(
        project=data["project"],
        status=data["status"],
        timing_grade_default=data["timing_grade_default"],
        voice=EditPlanVoice(**data["voice"]),
        segments=[EditPlanSegment(**s) for s in data["segments"]],
        overlays=[EditPlanOverlay(**o) for o in data["overlays"]],
        subtitles=[EditPlanSubtitle(**s) for s in data["subtitles"]],
        audio_layers=EditPlanAudioLayers(
            voice=data["audio_layers"]["voice"],
            sfx_source=EditPlanSfxSource(
                mode=data["audio_layers"]["sfx_source"]["mode"],
                clips=[EditPlanSfxClip(**c) for c in data["audio_layers"]["sfx_source"]["clips"]],
            ),
            bgm=EditPlanBgm(**data["audio_layers"]["bgm"]),
        ),
        warnings=[EditPlanWarning(**w) for w in data["warnings"]],
    )


# --- jobs.payload (de)serializers - the only file format these ever get ---


def stage2_outputs_to_payload(outputs: list[Stage2ShotOutput]) -> dict:
    return {
        "stage2_outputs": [
            {
                "shot": o.shot, "beat": o.beat, "approved_keyframe_path": o.approved_keyframe_path,
                "story_function": o.story_function, "continuity": o.continuity,
            }
            for o in outputs
        ]
    }


def stage2_outputs_from_payload(payload: dict) -> list[Stage2ShotOutput]:
    return [Stage2ShotOutput(**o) for o in payload["stage2_outputs"]]


def qc_decisions_to_payload(decisions: list[ShotQcDecision]) -> dict:
    return {
        "decisions": [
            {
                "shot": d.shot, "usable_start_ms": d.usable_start_ms, "usable_end_ms": d.usable_end_ms,
                "key_event_end_ms": d.key_event_end_ms, "settle_start_ms": d.settle_start_ms,
                "camera_behavior": d.camera_behavior, "qc_passed": d.qc_passed,
            }
            for d in decisions
        ]
    }


def qc_decisions_from_payload(payload: dict) -> list[ShotQcDecision]:
    return [ShotQcDecision(**d) for d in payload["decisions"]]


def edit_plan_human_input_to_payload(
    voice_input: HumanVoiceTimingInput,
    semantic_sync: list[HumanSemanticSyncSegment],
    overlay_decisions: list[HumanOverlayDecision],
    sfx_decisions: list[HumanSfxDecision],
) -> dict:
    return {
        "voice_input": {
            "timing_grade": voice_input.timing_grade, "audio_file": voice_input.audio_file,
            "total_duration_ms": voice_input.total_duration_ms,
        },
        "semantic_sync": [
            {"shot": s.shot, "start_ms": s.start_ms, "end_ms": s.end_ms,
             "clip_in_ms": s.clip_in_ms, "clip_out_ms": s.clip_out_ms}
            for s in semantic_sync
        ],
        "overlay_decisions": [
            {"shot": o.shot, "position_preset": o.position_preset, "start_ms": o.start_ms, "end_ms": o.end_ms}
            for o in overlay_decisions
        ],
        "sfx_decisions": [
            {"shot": s.shot, "action": s.action, "file": s.file}
            for s in sfx_decisions
        ],
    }


def edit_plan_human_input_from_payload(
    payload: dict,
) -> tuple[HumanVoiceTimingInput, list[HumanSemanticSyncSegment], list[HumanOverlayDecision], list[HumanSfxDecision]]:
    voice_input = HumanVoiceTimingInput(**payload["voice_input"])
    semantic_sync = [HumanSemanticSyncSegment(**s) for s in payload["semantic_sync"]]
    overlay_decisions = [HumanOverlayDecision(**o) for o in payload["overlay_decisions"]]
    sfx_decisions = [HumanSfxDecision(**s) for s in payload["sfx_decisions"]]
    return voice_input, semantic_sync, overlay_decisions, sfx_decisions

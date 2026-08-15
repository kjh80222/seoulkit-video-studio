"""Stage 3 QC Assist - CE-4e scope only.

CE-4e is Human-Assisted QC, not automatic video QC. The Stage 3 manual's
own G4 clip-quality checklist (ch. 13) is entirely a human, watch-the-clip
checklist ("주요 피사체의 형태.비례.재질이 클립 전체에서 변하지 않았는가",
"최종 프레임이 지시된 Settle 위치에 도달하는가", ...) - no item in it is a
measurement a tool can perform, and no OpenCV/CV/ML dependency is added
here to attempt one. This module's only automated measurement is real
`ffprobe` container inspection of the actual generated Flow clip:
`source_duration_ms` (container duration) and `has_audio_stream` (does the
container carry an audio stream at all). Both are re-implemented here as
independent `subprocess` calls to the `ffprobe` binary, the same way
`render/report.py::_probe_duration_ms()` does it in Video Studio - never by
importing `seoulkit_studio` internals (the CE-3 boundary principle).

Everything else a `clip_manifest.json` entry needs -
`usable_start_ms`/`usable_end_ms`/`key_event_end_ms`/`settle_start_ms`/
`camera_behavior`/`qc_passed` - is supplied by a human as a `ShotQcDecision`
after watching the real clip. This module never infers any of them from
pixel content, and deliberately does NOT use the Stage 3 manual's
proportional timeline structure (ch. 05: 0-7%/7-17%/17-27%/27-43%/43-100%)
to auto-suggest a starting value for `key_event_end_ms`/`settle_start_ms`:
that structure describes what the *generation prompt asked for*, not what
the *generated clip actually contains* - showing it as a suggested default
risks a human anchoring their QC judgment to it instead of the real clip.
`usable_start_ms` is not defaulted to `0` for the same reason, even though
that is the overwhelmingly common case.

`camera_behavior` is a human QC input, not a measurement, for a different
reason: its value is decided during Stage 3's own prompt-authoring chat
("Camera [ONE movement, or locked-off]", Stage 3 manual ch. 08/16), but
that decision is never captured in any structured file this pipeline
produces - `stage3_input.json` (CE-4d) is Stage 3's *input*, not its
output. Detecting camera movement from raw pixels would need real computer
vision, out of scope here. So a human re-confirms it directly against the
G4 checklist's own camera item while doing QC, the smallest fix for a
missing structured carrier - matching how CE-4d already treated
`story_function`/`continuity` as data supplied by a human, not
data this pipeline derives.

## Timing invariant (CE-4e's own new contract, not a Video Studio one)

`schema/edit_plan.schema.json` only requires each of `usable_start_ms`/
`usable_end_ms`/`key_event_end_ms`/`settle_start_ms` to individually be a
non-negative integer or `null` - it has no `allOf` relating them to each
other or to `source_duration_ms`, and `clip_manifest.json` itself has no
schema file at all. `preflight/validator.py::check_time_consistency()`
only checks `clip_in_ms`/`clip_out_ms` against the usable range, never the
range's own internal consistency. So a human-entered
`usable_end_ms > source_duration_ms` (or `usable_start_ms >=
usable_end_ms`, or a `key_event_end_ms` outside the usable range) is
caught by NO existing Video Studio code today - not schema, not
preflight, not `execution/clip_manifest.py`. Because CE-4e is the first
writer of these facts from a real measured clip, it is CE-4e's own new
responsibility (numeric integrity, not creative judgment) to reject an
internally-inconsistent set of human-entered values before they ever reach
disk. `check_timing_invariants()` derives the five rules below from field
semantics (a usable range cannot exceed the clip's own real duration) and
the Stage 3 manual's ch. 05 Timeline Structure ordering (Hold ends before
Settle begins) - they are not a rediscovery of an existing enforced rule:

1. `usable_end_ms <= source_duration_ms`
2. `usable_start_ms < usable_end_ms`
3. `usable_start_ms <= key_event_end_ms <= usable_end_ms` (when not `None`)
4. `usable_start_ms <= settle_start_ms <= usable_end_ms` (when not `None`)
5. `key_event_end_ms <= settle_start_ms` (when both are not `None`)

## `optional_source_audio.file`

`sfx_contract_version` is declared as `1` on every manifest this module
writes, so `check_sfx_contract_resolution()` (Video Studio,
`execution/clip_manifest.py`) requires every clip entry to carry
`optional_source_audio`. Re-reading that checker directly confirms it only
ever reads `.get("available")` - `.file` is never read by any code in this
repository, and `render/audio_mix.py::mix_audio()` consumes an entirely
different field (`edit_plan.json`'s own, separately-written
`audio_layers.sfx_source.clips[].file`, Stage 4/CE-4f's resolved decision,
not this one). Because `optional_source_audio.file` has no
consumer-defined contract anywhere yet, CE-4e v1 does not invent an audio
asset path for it - it is always written as `None`, whether or not
`available` is `True`. This is a `None`-for-now placeholder pending
CE-4f's design, matching how CE-4c deferred `expected_filename` to CE-4d
rather than guessing its shape early - not a permanent meaning for this
field. `available` itself is real: an `ffprobe` check for the presence of
an audio stream in the container.

## Write model

No incremental/per-shot manifest update. All shots are QC'd, collected as
`ShotQcDecision`s, and only if every one has `qc_passed=True` is
`clip_manifest.json` built and written, once. `write_clip_manifest()`
itself performs no source-clip lookups or measurements - existence of the
real Flow clips is confirmed earlier, by `measure_clips()`, so that a
missing clip is discovered before a human spends time entering QC values
for it, not after. `write_clip_manifest()`'s only guard is: if
`clips/clip_manifest.json` already exists, raise `FileExistsError` (same
principle as CE-4c/CE-4d) - no overwrite, no backup, no versioning.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from content_engine.video_planning.models import (
    ClipManifest,
    ClipManifestEntry,
    MeasuredClipFacts,
    OptionalSourceAudio,
    ShotQcDecision,
    Stage3InputPackage,
)

_SFX_CONTRACT_VERSION = 1


class MissingClipFileError(Exception):
    def __init__(self, missing_paths: list[str]) -> None:
        self.missing_paths = missing_paths
        super().__init__(f"expected Flow clip files not found: {missing_paths!r}")


class ClipQcIdentityMismatchError(Exception):
    def __init__(self, missing: list[str], extra: list[str], duplicate: list[str]) -> None:
        self.missing = missing
        self.extra = extra
        self.duplicate = duplicate
        super().__init__(
            f"QC decisions do not match stage3_input shots: "
            f"missing={missing!r}, extra={extra!r}, duplicate={duplicate!r}"
        )


class TimingInvariantError(Exception):
    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        super().__init__(f"QC decisions violate timing invariants: {violations!r}")


class QcNotPassedError(Exception):
    def __init__(self, failed_shots: list[str]) -> None:
        self.failed_shots = failed_shots
        super().__init__(f"clip_manifest.json not written - QC not passed for shots: {failed_shots!r}")


def measure_clip(shot: str, clip_path: Path) -> MeasuredClipFacts:
    duration_proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(clip_path)],
        capture_output=True, text=True, check=True,
    )
    source_duration_ms = round(float(duration_proc.stdout.strip()) * 1000)

    audio_proc = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a",
            "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(clip_path),
        ],
        capture_output=True, text=True, check=True,
    )
    has_audio_stream = bool(audio_proc.stdout.strip())

    return MeasuredClipFacts(shot=shot, source_duration_ms=source_duration_ms, has_audio_stream=has_audio_stream)


def measure_clips(project_dir: Path, stage3_input: Stage3InputPackage) -> list[MeasuredClipFacts]:
    missing = [
        s.expected_clip_filename for s in stage3_input.shots
        if not (project_dir / s.expected_clip_filename).is_file()
    ]
    if missing:
        raise MissingClipFileError(missing)

    return [measure_clip(s.shot, project_dir / s.expected_clip_filename) for s in stage3_input.shots]


def check_timing_invariants(shot: str, decision: ShotQcDecision, source_duration_ms: int) -> list[str]:
    violations: list[str] = []

    if decision.usable_end_ms > source_duration_ms:
        violations.append(
            f"shot={shot}: usable_end_ms({decision.usable_end_ms}) > "
            f"source_duration_ms({source_duration_ms})"
        )
    if decision.usable_start_ms >= decision.usable_end_ms:
        violations.append(
            f"shot={shot}: usable_start_ms({decision.usable_start_ms}) >= "
            f"usable_end_ms({decision.usable_end_ms})"
        )
    if decision.key_event_end_ms is not None and not (
        decision.usable_start_ms <= decision.key_event_end_ms <= decision.usable_end_ms
    ):
        violations.append(
            f"shot={shot}: key_event_end_ms({decision.key_event_end_ms}) outside "
            f"usable range [{decision.usable_start_ms}, {decision.usable_end_ms}]"
        )
    if decision.settle_start_ms is not None and not (
        decision.usable_start_ms <= decision.settle_start_ms <= decision.usable_end_ms
    ):
        violations.append(
            f"shot={shot}: settle_start_ms({decision.settle_start_ms}) outside "
            f"usable range [{decision.usable_start_ms}, {decision.usable_end_ms}]"
        )
    if (
        decision.key_event_end_ms is not None
        and decision.settle_start_ms is not None
        and decision.key_event_end_ms > decision.settle_start_ms
    ):
        violations.append(
            f"shot={shot}: key_event_end_ms({decision.key_event_end_ms}) > "
            f"settle_start_ms({decision.settle_start_ms})"
        )

    return violations


def build_clip_manifest(
    stage3_input: Stage3InputPackage,
    measured: list[MeasuredClipFacts],
    decisions: list[ShotQcDecision],
) -> ClipManifest:
    measured_by_shot = {m.shot: m for m in measured}

    decision_by_shot: dict[str, ShotQcDecision] = {}
    duplicate: list[str] = []
    for d in decisions:
        if d.shot in decision_by_shot:
            duplicate.append(d.shot)
        decision_by_shot[d.shot] = d

    expected_shots = [s.shot for s in stage3_input.shots]
    missing = [shot for shot in expected_shots if shot not in decision_by_shot]
    extra = [shot for shot in decision_by_shot if shot not in measured_by_shot]

    if missing or extra or duplicate:
        raise ClipQcIdentityMismatchError(missing, extra, sorted(set(duplicate)))

    violations: list[str] = []
    for shot in expected_shots:
        violations += check_timing_invariants(shot, decision_by_shot[shot], measured_by_shot[shot].source_duration_ms)
    if violations:
        raise TimingInvariantError(violations)

    failed_shots = [shot for shot in expected_shots if not decision_by_shot[shot].qc_passed]
    if failed_shots:
        raise QcNotPassedError(failed_shots)

    clips = [
        ClipManifestEntry(
            shot=shot,
            file=next(s.expected_clip_filename for s in stage3_input.shots if s.shot == shot),
            camera_behavior=decision_by_shot[shot].camera_behavior,
            source_duration_ms=measured_by_shot[shot].source_duration_ms,
            usable_start_ms=decision_by_shot[shot].usable_start_ms,
            usable_end_ms=decision_by_shot[shot].usable_end_ms,
            key_event_end_ms=decision_by_shot[shot].key_event_end_ms,
            settle_start_ms=decision_by_shot[shot].settle_start_ms,
            optional_source_audio=OptionalSourceAudio(available=measured_by_shot[shot].has_audio_stream, file=None),
        )
        for shot in expected_shots
    ]

    return ClipManifest(sfx_contract_version=_SFX_CONTRACT_VERSION, clips=clips)


def write_clip_manifest(project_dir: Path, manifest: ClipManifest) -> None:
    path = project_dir / "clips" / "clip_manifest.json"
    if path.exists():
        raise FileExistsError(f"{path} already exists - CE-4e does not overwrite an existing manifest")

    payload = {
        "sfx_contract_version": manifest.sfx_contract_version,
        "clips": [
            {
                "shot": c.shot,
                "file": c.file,
                "camera_behavior": c.camera_behavior,
                "source_duration_ms": c.source_duration_ms,
                "usable_start_ms": c.usable_start_ms,
                "usable_end_ms": c.usable_end_ms,
                "key_event_end_ms": c.key_event_end_ms,
                "settle_start_ms": c.settle_start_ms,
                "optional_source_audio": {
                    "available": c.optional_source_audio.available,
                    "file": c.optional_source_audio.file,
                },
            }
            for c in manifest.clips
        ],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

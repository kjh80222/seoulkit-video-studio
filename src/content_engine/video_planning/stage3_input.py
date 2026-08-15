"""Stage 2 -> Stage 3 handoff package - CE-4d scope only.

`build_stage3_input()` merges CE-4c's `Stage2InputPackage` (Stage 1
content) with Stage 2's own structured output (`list[Stage2ShotOutput]`)
by `shot` identity - it does not zip the two lists positionally, since a
silent index-order mismatch would attach one shot's Story Function to a
different shot's keyframe without any error. Every one of four identity
problems is collected and reported together in a single
`ShotIdentityMismatchError`, not raised on the first one found: a shot
`stage2_input` declares but Stage 2's output never produced (`missing`),
a shot Stage 2's output has that `stage2_input` never declared (`extra`),
a `shot` id appearing more than once in either list (`duplicate`), and a
`shot` id present in both whose `beat` disagrees between the two
(`beat_mismatches`) - the CE-4c-side Stage 1 table remains authoritative
for `beat` when everything checks out. The merged result preserves
`stage2_input.shots`'s own order.

`expected_clip_filename()` is first computed and consumed here - Stage 2
never needed it, but Stage 3's human operator does, to know where to save
the Google Flow clip once generated. Same deterministic rule established
in CE-4c's design review: `shot="1A"` -> `clips/shot_1a_flow.mp4`.

Nothing here decides `camera_behavior`, motion prompt content, or clip
duration - those remain Stage 3's own creative decisions per its manual,
made from the approved keyframe and Story Function this module merely
relays. No top-level style-anchor field exists either: Stage 3's manual
lists exactly three required inputs (Beat+Shot table, approved keyframe
image, Story Function + Continuity) and does not separately reference a
style anchor - whichever shot is the anchor is already reflected in that
shot's own approved keyframe image by the time Stage 2 hands it off, so a
second top-level pointer to the same thing would be redundant, drift-prone
data an earlier draft mistakenly included.

`approved_keyframe_path` is a project-relative reference only.
`write_stage3_input()` confirms each referenced file actually exists on
disk (a check Video Studio itself has no concept of, so this is not the
kind of validation-duplication CE-5 deliberately avoided for `preflight`)
but never opens, decodes, copies, or moves the image itself - the
`keyframes/` directory is a human-populated convention (parallel to
`clips/`), and this module treats whatever a human already placed there
exactly like CE-4a/CE-4c treat any file a human supplies: read-only.

Validation and write order in `write_stage3_input()`, deliberately fixed
so a failed later check never partially invalidates the meaning of the
overwrite guard: (1) reject if `stage3_input.json` already exists, (2)
reject any `approved_keyframe_path` that is absolute or escapes
`project_dir` via `..`, (3) collect every missing keyframe file and raise
once with the complete list, (4) only then serialize and write.
"""

from __future__ import annotations

import json
from pathlib import Path

from content_engine.video_planning.models import (
    PlannedShot,
    Stage2InputPackage,
    Stage2ShotOutput,
    Stage3InputPackage,
    Stage3PlannedShot,
)


class ShotIdentityMismatchError(Exception):
    def __init__(
        self, missing: list[str], extra: list[str], duplicate: list[str], beat_mismatches: list[str]
    ) -> None:
        self.missing = missing
        self.extra = extra
        self.duplicate = duplicate
        self.beat_mismatches = beat_mismatches
        super().__init__(
            f"stage2 outputs do not match stage2_input shots: "
            f"missing={missing!r}, extra={extra!r}, duplicate={duplicate!r}, beat_mismatches={beat_mismatches!r}"
        )


class MissingKeyframeError(Exception):
    def __init__(self, missing_paths: list[str]) -> None:
        self.missing_paths = missing_paths
        super().__init__(f"approved keyframe files not found: {missing_paths!r}")


def expected_clip_filename(shot: str) -> str:
    return f"clips/shot_{shot.lower()}_flow.mp4"


def build_stage3_input(
    stage2_input: Stage2InputPackage, stage2_outputs: list[Stage2ShotOutput]
) -> Stage3InputPackage:
    input_by_shot: dict[str, PlannedShot] = {}
    duplicate: list[str] = []
    for s in stage2_input.shots:
        if s.shot in input_by_shot:
            duplicate.append(s.shot)
        input_by_shot[s.shot] = s

    output_by_shot: dict[str, Stage2ShotOutput] = {}
    for o in stage2_outputs:
        if o.shot in output_by_shot:
            duplicate.append(o.shot)
        output_by_shot[o.shot] = o

    missing = [shot for shot in input_by_shot if shot not in output_by_shot]
    extra = [shot for shot in output_by_shot if shot not in input_by_shot]
    beat_mismatches = [
        shot
        for shot in input_by_shot
        if shot in output_by_shot and input_by_shot[shot].beat != output_by_shot[shot].beat
    ]

    if missing or extra or duplicate or beat_mismatches:
        raise ShotIdentityMismatchError(missing, extra, sorted(set(duplicate)), beat_mismatches)

    shots = [
        Stage3PlannedShot(
            beat=input_by_shot[shot].beat,
            shot=shot,
            visual_purpose=input_by_shot[shot].visual_purpose,
            voice_text=input_by_shot[shot].voice_text,
            approved_keyframe_path=output_by_shot[shot].approved_keyframe_path,
            story_function=output_by_shot[shot].story_function,
            continuity=output_by_shot[shot].continuity,
            expected_clip_filename=expected_clip_filename(shot),
        )
        for shot in input_by_shot
    ]

    return Stage3InputPackage(schema_version="1.0", topic=stage2_input.topic, shots=shots)


def write_stage3_input(project_dir: Path, package: Stage3InputPackage) -> None:
    path = project_dir / "stage3_input.json"
    if path.exists():
        raise FileExistsError(f"{path} already exists - CE-4d does not overwrite an existing plan")

    for s in package.shots:
        kp = Path(s.approved_keyframe_path)
        if kp.is_absolute() or ".." in kp.parts:
            raise ValueError(
                f"approved_keyframe_path must be a project-relative path: {s.approved_keyframe_path!r}"
            )

    missing = [
        s.approved_keyframe_path for s in package.shots
        if not (project_dir / s.approved_keyframe_path).is_file()
    ]
    if missing:
        raise MissingKeyframeError(missing)

    payload = {
        "schema_version": package.schema_version,
        "topic": package.topic,
        "shots": [
            {
                "beat": s.beat,
                "shot": s.shot,
                "visual_purpose": s.visual_purpose,
                "voice_text": s.voice_text,
                "approved_keyframe_path": s.approved_keyframe_path,
                "story_function": s.story_function,
                "continuity": s.continuity,
                "expected_clip_filename": s.expected_clip_filename,
            }
            for s in package.shots
        ],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

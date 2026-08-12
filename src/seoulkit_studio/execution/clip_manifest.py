"""clip_manifest.json cross-validation (Stage 5 spec, ch. 24).

"Stage 3 writes -> Stage 4 consumes -> Stage 5 reads-and-verifies (never
recalculates, never writes)." Stage 5 never estimates or recomputes a
usable range - it only checks that the usable-range fields Stage 4 copied
into edit_plan.json still agree with what Stage 3 actually observed and
recorded in clip_manifest.json.

Scope: exactly the four fields Stage 4 is supposed to have copied from
clip_manifest.json into each segment - usable_start_ms, usable_end_ms,
key_event_end_ms, settle_start_ms. Whether clip_in_ms/clip_out_ms fall
inside that range is already Phase 1's job (against edit_plan.json's own
copy of the range); this module checks whether that copy itself is still
truthful, which Phase 1 has no way to know on its own.

Read-only, like everything else in this pipeline: `clip_manifest.json` is
never opened for writing, matching edit_plan.json's own immutability
guarantee (ch. 06).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from seoulkit_studio.preflight import PreflightIssue

_COMPARED_FIELDS = ("usable_start_ms", "usable_end_ms", "key_event_end_ms", "settle_start_ms")


def check_clip_manifest_consistency(data: dict[str, Any], clip_manifest_path: Path) -> list[PreflightIssue]:
    if not clip_manifest_path.is_file():
        return [
            PreflightIssue(
                "CLIP_MANIFEST_MISSING",
                "warning",
                f"{clip_manifest_path} not found - Stage 3 may not have run yet",
            )
        ]

    try:
        manifest = json.loads(clip_manifest_path.read_text())
    except json.JSONDecodeError as exc:
        return [PreflightIssue("CLIP_MANIFEST_UNREADABLE", "blocking", f"{clip_manifest_path}: {exc}")]

    clips = manifest.get("clips", [])

    # Detect duplicate shot entries BEFORE building the lookup table below.
    # A dict comprehension over clips[] would let the last entry for a
    # given shot silently win, with no trace that an earlier - possibly
    # authoritative - entry ever existed. Counting occurrences up front
    # makes the ambiguity explicit instead of hiding it inside dict
    # construction.
    shot_counts: dict[str, int] = {}
    for clip in clips:
        if "shot" in clip:
            shot_counts[clip["shot"]] = shot_counts.get(clip["shot"], 0) + 1
    duplicate_shots = {shot for shot, count in shot_counts.items() if count > 1}

    issues: list[PreflightIssue] = [
        PreflightIssue(
            "CLIP_MANIFEST_DUPLICATE_SHOT",
            "blocking",
            f"shot {shot!r} appears {shot_counts[shot]} times in {clip_manifest_path} "
            "- which entry is authoritative is ambiguous",
        )
        for shot in sorted(duplicate_shots)
    ]

    manifest_by_shot: dict[str, dict[str, Any]] = {clip["shot"]: clip for clip in clips if "shot" in clip}

    for segment in data.get("segments", []):
        shot = segment.get("shot")
        ref = f"beat={segment.get('beat')} shot={shot}"

        if shot in duplicate_shots:
            # Already reported above. Comparing against whichever entry
            # happened to win the dict build would only add a misleading
            # match/mismatch verdict on top of an already-ambiguous manifest.
            continue

        manifest_entry = manifest_by_shot.get(shot)
        if manifest_entry is None:
            issues.append(
                PreflightIssue(
                    "CLIP_MANIFEST_SHOT_MISSING",
                    "blocking",
                    f"shot {shot!r} not recorded in {clip_manifest_path}",
                    ref,
                )
            )
            continue

        for field in _COMPARED_FIELDS:
            plan_value = segment.get(field)
            manifest_value = manifest_entry.get(field)
            if plan_value != manifest_value:
                issues.append(
                    PreflightIssue(
                        "CLIP_MANIFEST_MISMATCH",
                        "blocking",
                        f"{field}: edit_plan.json has {plan_value!r}, clip_manifest.json has {manifest_value!r}",
                        ref,
                    )
                )

    return issues

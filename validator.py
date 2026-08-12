"""Pre-flight validation (Stage 5 spec, ch. 04).

Layering (spec ch. 04-6, ch. 05): Pre-flight only produces an issue list and
a PASS/FAIL verdict based purely on whether any *blocking* issue exists.
It does not decide READY/REVIEW_REQUIRED/NOT_READY on its own - "Pre-flight
는 상태를 직접 결정하지 않는다, 이슈 목록만 생성한다." Turning that issue
list into an effective_status (ch. 04-5) is exposed here as a separate,
independent function (`severity_to_execution` / `worst_severity`) that
`run_preflight` does not call - combining it with the plan's own declared
`status` into a final PLAN STATUS vs. STUDIO EXECUTION RESULT judgment is
Phase 2's job, not this module's.

Sub-checks, run in order - later ones only run if 04-1 passes, since a
structurally invalid plan can't be trusted enough to read its own paths
and timings from (same reasoning as Phase 0's schema-before-invariant
ordering):

- 04-1 structural: JSON Schema (Phase 0) + schema_version support.
- 04-2 file existence: voice audio, each segment's source clip, adopted
  SFX clips, selected BGM file. (overlays[]/subtitles[] have no file-path
  fields in the schema - text + timing only - so there is nothing to check
  there.)
- 04-3 time consistency: clip_in_ms < clip_out_ms, clip bounds inside the
  recorded usable range (absence of usable range data is a warning, not a
  blocker - clip_manifest.json may not exist yet, per ch. 07), no segment
  overlap, total segment length vs. voice duration, Duration Invariant
  (Phase 0, reused).
- 04-4 audio layer resolution: already fully enforced by the Phase 0
  schema (sfx action enum excludes "candidate"; bgm allOf requires `file`
  when mode="selected") - see the comment on `check_time_consistency`
  below for why this isn't reimplemented here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from seoulkit_studio.schema import (
    DEFAULT_DURATION_TOLERANCE_MS,
    validate_duration_invariant,
    validate_schema,
)

Severity = Literal["blocking", "warning", "info"]

# Verbatim from the Stage 5 spec, ch. 18 `preflight.severity_mapping` config.
# Fixed, no exceptions - Severity itself only has these three values, so
# there is no code path for a fourth case to slip in.
SEVERITY_MAPPING: dict[Severity, dict[str, Any]] = {
    "blocking": {"effective_status": "NOT_READY", "preview": False, "final": False},
    "warning": {"effective_status": "REVIEW_REQUIRED", "preview": True, "final": False},
    "info": {"effective_status": None, "preview": True, "final": True},
}

_SEVERITY_RANK: dict[Severity, int] = {"info": 0, "warning": 1, "blocking": 2}


@dataclass
class PreflightIssue:
    code: str
    severity: Severity
    detail: str
    segment_ref: str | None = None


@dataclass
class ExecutionPermission:
    effective_status: str | None
    preview_allowed: bool
    final_allowed: bool


@dataclass
class PreflightResult:
    """ch. 04-6 format. `preflight_result` reflects blocking-issue presence
    only - PASS does not mean "no issues at all," only "nothing that blocks
    the pipeline from proceeding" (ch. 05)."""

    preflight_result: Literal["PASS", "FAIL"]
    issues: list[PreflightIssue] = field(default_factory=list)


def worst_severity(issues: list[PreflightIssue]) -> Severity | None:
    """The 04-5 mapping's input: the single worst severity across all issues."""
    if not issues:
        return None
    return max((issue.severity for issue in issues), key=lambda s: _SEVERITY_RANK[s])


def severity_to_execution(severity: Severity | None) -> ExecutionPermission:
    """ch. 04-5 / ch. 18 mapping. Independent of `run_preflight` - combining
    this with the plan's declared `status` to get a final judgment is
    Phase 2's PLAN STATUS vs. STUDIO EXECUTION RESULT logic, not this
    module's."""
    if severity is None:
        return ExecutionPermission(effective_status=None, preview_allowed=True, final_allowed=True)
    mapping = SEVERITY_MAPPING[severity]
    return ExecutionPermission(
        effective_status=mapping["effective_status"],
        preview_allowed=mapping["preview"],
        final_allowed=mapping["final"],
    )


def check_structure(data: dict[str, Any]) -> list[PreflightIssue]:
    # 04-1 also calls for a schema_version support check, but the Phase 0
    # schema already pins schema_version to `"const": "1.0"` - any other
    # value is already a SCHEMA_VIOLATION here. A separate check would be
    # unreachable dead code today; it only becomes meaningful once the
    # schema itself accepts more than one version (e.g. `"enum": [...]`),
    # which is out of scope for this phase.
    return [PreflightIssue(issue.code, "blocking", issue.message) for issue in validate_schema(data)]


def _missing_file_issue(
    project_dir: Path, rel_path: str | None, code: str, segment_ref: str | None = None
) -> PreflightIssue | None:
    if rel_path is None:
        return None
    if not (project_dir / rel_path).is_file():
        return PreflightIssue(code, "blocking", f"{rel_path} not found", segment_ref)
    return None


def check_file_existence(data: dict[str, Any], project_dir: Path) -> list[PreflightIssue]:
    issues: list[PreflightIssue] = []

    voice_issue = _missing_file_issue(project_dir, data.get("voice", {}).get("audio_file"), "MISSING_VOICE_ASSET")
    if voice_issue:
        issues.append(voice_issue)

    for segment in data.get("segments", []):
        ref = f"beat={segment.get('beat')} shot={segment.get('shot')}"
        clip_issue = _missing_file_issue(project_dir, segment.get("source_clip"), "MISSING_CLIP", ref)
        if clip_issue:
            issues.append(clip_issue)

    audio_layers = data.get("audio_layers", {})

    bgm = audio_layers.get("bgm", {})
    if bgm.get("mode") == "selected":
        bgm_issue = _missing_file_issue(project_dir, bgm.get("file"), "MISSING_BGM_FILE")
        if bgm_issue:
            issues.append(bgm_issue)

    for clip in audio_layers.get("sfx_source", {}).get("clips", []):
        if clip.get("action") == "adopted":
            sfx_issue = _missing_file_issue(project_dir, clip.get("file"), "MISSING_SFX_FILE", clip.get("shot"))
            if sfx_issue:
                issues.append(sfx_issue)

    return issues


def check_time_consistency(
    data: dict[str, Any], tolerance_ms: int = DEFAULT_DURATION_TOLERANCE_MS
) -> list[PreflightIssue]:
    # 04-4 audio layer resolution is not reimplemented here: the Phase 0
    # schema already rejects an unresolved SFX action ("candidate" is not
    # in the adopted/discarded enum) and a selected BGM without a file
    # (allOf requires it) as SCHEMA_VIOLATION, i.e. 04-1 already catches
    # both cases described in ch. 04-4.
    issues: list[PreflightIssue] = []
    segments = data.get("segments", [])

    for segment in segments:
        ref = f"beat={segment.get('beat')} shot={segment.get('shot')}"
        clip_in_ms, clip_out_ms = segment.get("clip_in_ms"), segment.get("clip_out_ms")
        if clip_in_ms is not None and clip_out_ms is not None and clip_in_ms >= clip_out_ms:
            issues.append(
                PreflightIssue(
                    "INVALID_CLIP_RANGE", "blocking", f"clip_in_ms({clip_in_ms}) >= clip_out_ms({clip_out_ms})", ref
                )
            )

        usable_start_ms, usable_end_ms = segment.get("usable_start_ms"), segment.get("usable_end_ms")
        if usable_start_ms is None or usable_end_ms is None:
            issues.append(
                PreflightIssue("USABLE_RANGE_MISSING", "warning", "usable_start_ms/usable_end_ms not recorded", ref)
            )
        else:
            if clip_in_ms is not None and clip_in_ms < usable_start_ms:
                issues.append(
                    PreflightIssue(
                        "OUT_OF_USABLE_RANGE",
                        "blocking",
                        f"clip_in_ms({clip_in_ms}) < usable_start_ms({usable_start_ms})",
                        ref,
                    )
                )
            if clip_out_ms is not None and clip_out_ms > usable_end_ms:
                issues.append(
                    PreflightIssue(
                        "OUT_OF_USABLE_RANGE",
                        "blocking",
                        f"clip_out_ms({clip_out_ms}) > usable_end_ms({usable_end_ms})",
                        ref,
                    )
                )

    by_start = sorted(segments, key=lambda s: s.get("start_ms", 0))
    for earlier, later in zip(by_start, by_start[1:]):
        if earlier.get("end_ms", 0) > later.get("start_ms", 0):
            issues.append(
                PreflightIssue(
                    "SEGMENT_OVERLAP",
                    "blocking",
                    f"beat={earlier.get('beat')} shot={earlier.get('shot')} (end_ms={earlier.get('end_ms')}) "
                    f"overlaps beat={later.get('beat')} shot={later.get('shot')} (start_ms={later.get('start_ms')})",
                )
            )

    total_segment_ms = sum(segment.get("end_ms", 0) - segment.get("start_ms", 0) for segment in segments)
    voice_total_ms = data.get("voice", {}).get("total_duration_ms")
    if voice_total_ms is not None and abs(total_segment_ms - voice_total_ms) > tolerance_ms:
        issues.append(
            PreflightIssue(
                "TOTAL_DURATION_MISMATCH",
                "warning",
                f"sum of segment durations ({total_segment_ms}ms) != voice.total_duration_ms ({voice_total_ms}ms)",
            )
        )

    for schema_issue in validate_duration_invariant(data, tolerance_ms=tolerance_ms):
        issues.append(PreflightIssue(schema_issue.code, "blocking", schema_issue.message, schema_issue.segment_ref))

    return issues


def run_preflight(
    data: dict[str, Any], project_dir: Path, duration_tolerance_ms: int = DEFAULT_DURATION_TOLERANCE_MS
) -> PreflightResult:
    structure_issues = check_structure(data)
    if structure_issues:
        return PreflightResult(preflight_result="FAIL", issues=structure_issues)

    issues = check_file_existence(data, project_dir) + check_time_consistency(data, tolerance_ms=duration_tolerance_ms)
    has_blocking = any(issue.severity == "blocking" for issue in issues)
    return PreflightResult(preflight_result="FAIL" if has_blocking else "PASS", issues=issues)

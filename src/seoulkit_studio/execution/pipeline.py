"""Glues Plan Loader (ch. 06) + Pre-flight (ch. 04, Phase 1) + clip_manifest.json
cross-validation (ch. 24, Phase 2.5) + execution result / effective_status
(ch. 05, Phase 2) into one read-only pass over an edit_plan.json file.

This is the seam the immutability guarantee is tested against: everything
downstream of `load_plan_file` operates on in-memory data, and nothing in
this module (or anything it calls) ever opens `path` or `clip_manifest_path`
for writing.

Out of scope for Phase 2/2.5: the full Render Report format (`plan_status_as_declared`
+ `effective_status` + `studio_execution_result` together, ch. 17) is Phase 10's
job, not this module's - this only computes the values, it doesn't format a report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from seoulkit_studio.execution.clip_manifest import check_clip_manifest_consistency, check_sfx_contract_resolution
from seoulkit_studio.execution.plan_loader import LoadError, load_plan_file
from seoulkit_studio.execution.result import (
    PlanStatus,
    StudioExecutionResult,
    compute_effective_status,
    compute_execution_result,
)
from seoulkit_studio.preflight import PreflightIssue, PreflightResult, run_preflight
from seoulkit_studio.schema import DEFAULT_DURATION_TOLERANCE_MS


@dataclass
class EvaluationResult:
    load_error: LoadError | None
    preflight_result: PreflightResult | None
    clip_manifest_issues: list[PreflightIssue] = field(default_factory=list)
    studio_execution_result: StudioExecutionResult = "PASS"
    effective_status: PlanStatus = "READY"


def evaluate_plan(
    path: Path,
    project_dir: Path,
    clip_manifest_path: Path | None = None,
    duration_tolerance_ms: int = DEFAULT_DURATION_TOLERANCE_MS,
) -> EvaluationResult:
    load_result = load_plan_file(path)

    if not load_result.ok:
        execution_result = compute_execution_result(load_result.error, [])
        effective_status = compute_effective_status(execution_result, None, [])
        return EvaluationResult(load_result.error, None, [], execution_result, effective_status)

    preflight_result = run_preflight(load_result.data, project_dir, duration_tolerance_ms=duration_tolerance_ms)

    # A structurally invalid plan (04-1 SCHEMA_VIOLATION) can't be trusted
    # enough to read `segments[].shot` from either - same reasoning Phase 1
    # already applies to its own 04-2~04-4 checks.
    structure_failed = any(issue.code == "SCHEMA_VIOLATION" for issue in preflight_result.issues)
    if structure_failed or clip_manifest_path is None:
        clip_manifest_issues: list[PreflightIssue] = []
    else:
        clip_manifest_issues = check_clip_manifest_consistency(
            load_result.data, clip_manifest_path
        ) + check_sfx_contract_resolution(load_result.data, clip_manifest_path)

    all_issues = preflight_result.issues + clip_manifest_issues
    execution_result = compute_execution_result(None, all_issues)
    plan_status_as_declared = load_result.data.get("status") if isinstance(load_result.data, dict) else None
    effective_status = compute_effective_status(execution_result, plan_status_as_declared, all_issues)

    return EvaluationResult(None, preflight_result, clip_manifest_issues, execution_result, effective_status)

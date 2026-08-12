"""Glues Plan Loader (ch. 06) + Pre-flight (ch. 04, Phase 1) + execution
result / effective_status (ch. 05, this phase) into one read-only pass over
an edit_plan.json file.

This is the seam the immutability guarantee is tested against: everything
downstream of `load_plan_file` operates on an in-memory dict, and nothing
in this module (or anything it calls) ever opens `path` for writing.

Out of scope for Phase 2: the full Render Report format (`plan_status_as_declared`
+ `effective_status` + `studio_execution_result` together, ch. 17) is Phase 10's
job, not this module's - this only computes the values, it doesn't format a report.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from seoulkit_studio.execution.plan_loader import LoadError, load_plan_file
from seoulkit_studio.execution.result import (
    PlanStatus,
    StudioExecutionResult,
    compute_effective_status,
    compute_execution_result,
)
from seoulkit_studio.preflight import PreflightResult, run_preflight
from seoulkit_studio.schema import DEFAULT_DURATION_TOLERANCE_MS


@dataclass
class EvaluationResult:
    load_error: LoadError | None
    preflight_result: PreflightResult | None
    studio_execution_result: StudioExecutionResult
    effective_status: PlanStatus


def evaluate_plan(
    path: Path, project_dir: Path, duration_tolerance_ms: int = DEFAULT_DURATION_TOLERANCE_MS
) -> EvaluationResult:
    load_result = load_plan_file(path)

    if not load_result.ok:
        execution_result = compute_execution_result(load_result.error, None)
        effective_status = compute_effective_status(execution_result, None, [])
        return EvaluationResult(load_result.error, None, execution_result, effective_status)

    preflight_result = run_preflight(load_result.data, project_dir, duration_tolerance_ms=duration_tolerance_ms)
    execution_result = compute_execution_result(None, preflight_result)
    plan_status_as_declared = load_result.data.get("status") if isinstance(load_result.data, dict) else None
    effective_status = compute_effective_status(execution_result, plan_status_as_declared, preflight_result.issues)

    return EvaluationResult(None, preflight_result, execution_result, effective_status)

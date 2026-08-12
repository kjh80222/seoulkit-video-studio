"""STUDIO EXECUTION RESULT / effective_status computation (Stage 5 spec, ch. 05).

Two concepts kept strictly separate, per spec:

- PLAN STATUS: the `status` field Stage 4 already wrote into edit_plan.json.
  Stage 5 only ever reads it - never recomputes it, never writes it back.
- STUDIO EXECUTION RESULT: PASS | BLOCKED | ERROR, Stage 5's own verdict on
  its attempt to process the plan. This value is never written into
  edit_plan.json either.

`effective_status` blends the two, but only in memory, only for Stage 5's
own render-permission decision (ch. 05 실행 권한 매트릭스) - it is not a
plan-file field and this module never writes anything to disk.

ERROR vs. BLOCKED boundary: the spec's own ERROR example is "JSON parse
failure, schema validation failure." Phase 1's `run_preflight` already
draws exactly this line - a SCHEMA_VIOLATION issue (04-1, `check_structure`)
means the plan couldn't be trusted enough to even attempt 04-2~04-4, and
returns early. Reusing that boundary: a LoadError (file missing/unreadable,
JSON parse failure) or a SCHEMA_VIOLATION issue -> ERROR. Any other
blocking issue (04-2~04-4: missing files, bad timing, Duration Invariant)
-> BLOCKED.
"""

from __future__ import annotations

from typing import Literal

from seoulkit_studio.execution.plan_loader import LoadError
from seoulkit_studio.preflight import PreflightIssue, PreflightResult

StudioExecutionResult = Literal["PASS", "BLOCKED", "ERROR"]
PlanStatus = Literal["READY", "REVIEW_REQUIRED", "NOT_READY"]

_STATUS_RANK: dict[PlanStatus, int] = {"READY": 0, "REVIEW_REQUIRED": 1, "NOT_READY": 2}


def compute_execution_result(
    load_error: LoadError | None, preflight_result: PreflightResult | None
) -> StudioExecutionResult:
    if load_error is not None:
        return "ERROR"

    assert preflight_result is not None, "a plan that loaded successfully must have been preflighted"

    if any(issue.code == "SCHEMA_VIOLATION" for issue in preflight_result.issues):
        return "ERROR"
    if preflight_result.preflight_result == "FAIL":
        return "BLOCKED"
    return "PASS"


def _worse_status(a: PlanStatus, b: PlanStatus) -> PlanStatus:
    return a if _STATUS_RANK[a] >= _STATUS_RANK[b] else b


def compute_effective_status(
    execution_result: StudioExecutionResult,
    plan_status_as_declared: PlanStatus | None,
    preflight_issues: list[PreflightIssue],
) -> PlanStatus:
    if execution_result in ("ERROR", "BLOCKED"):
        return "NOT_READY"

    # PASS: MAX(plan.status, warning들로부터 도출된 최소 상태) - ch. 05. Even
    # with a clean Pre-flight, Stage 4 may have already declared a worse
    # status for reasons Stage 5 can't see (e.g. ESTIMATED timing grade).
    warning_derived: PlanStatus = "REVIEW_REQUIRED" if any(
        issue.severity == "warning" for issue in preflight_issues
    ) else "READY"

    declared: PlanStatus = plan_status_as_declared if plan_status_as_declared in _STATUS_RANK else "READY"
    return _worse_status(declared, warning_derived)

"""Derived project status / next-action - CE-11 scope only.

`derive_status()` never stores a workflow state anywhere - no new DB
column, no new `JobState`. It is a pure read: `ContentPackage.project_dir`
+ the presence of the five artifact files each CE-4b-4f job type is
already known to write (`stage2_input.json`/`stage3_input.json`/
`clips/clip_manifest.json`/`edit_plan.json`, plus the human-supplied
`script/stage1_beat_shot_table.json`) + the most recent job row per
job_type + CE-5's own `check_asset_readiness()` for the two Video-Studio-
gated job types. This is the exact derivation CE-4a's own docstring
already anticipated ("a whole-package progress view... gets computed by
joining `jobs` on `content_package_id` at read time").

The check order below is fixed and non-aggregated (the same discipline
CE-4b's validation already established): the first unmet step wins, and
nothing past it is evaluated - a project stuck on Stage 1 is never also
reported as "waiting on Semantic Sync".

`list_content_packages()` is nothing more than `derive_status()` applied
to every row in `content_packages`, newest first - no separate listing
logic, so `list` and `status` can never disagree about what stage a
project is in.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from content_engine.cli.project import resolve_project
from content_engine.content_packages.readiness import check_asset_readiness

_GATED_JOB_TYPES = frozenset({"preview_render", "final_render"})


@dataclass(frozen=True)
class ProjectStatus:
    content_package_id: str
    topic: str
    project_dir: str
    created_at: str
    status: str
    detail: str
    next_action: str


def _status(project, status: str, detail: str, next_action: str) -> ProjectStatus:
    return ProjectStatus(
        content_package_id=project.content_package_id, topic=project.topic,
        project_dir=project.project_dir, created_at=project.created_at,
        status=status, detail=detail, next_action=next_action,
    )


def _latest_job(conn: sqlite3.Connection, content_package_id: str, job_type: str) -> dict | None:
    row = conn.execute(
        "SELECT state, error_message FROM jobs WHERE content_package_id = ? AND job_type = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (content_package_id, job_type),
    ).fetchone()
    if row is None:
        return None
    return {"state": row[0], "error_message": row[1]}


def _build_step_status(
    conn, project, job_type: str, *, waiting_status: str, waiting_detail: str, waiting_action: str,
) -> ProjectStatus:
    job = _latest_job(conn, project.content_package_id, job_type)
    if job is None:
        return _status(project, waiting_status, waiting_detail, waiting_action)
    if job["state"] == "failed":
        return _status(
            project, f"{job_type.upper()}_FAILED", job["error_message"] or "job failed",
            "Fix the issue, then resubmit: content-engine submit "
            f"{project.content_package_id} {job_type} ...",
        )
    if job["state"] in ("pending", "processing"):
        return _status(
            project, f"{job_type.upper()}_RUNNING", "Job is queued or running.",
            "content-engine worker run --once",
        )
    # state == "complete" but the artifact is still missing - should not
    # happen given write_*()'s own create-only guarantee; reported rather
    # than silently guessing.
    return _status(project, "INCONSISTENT_STATE", f"{job_type} job is complete but its artifact is missing.", "")


def _render_step_status(
    conn, project, project_dir: Path, job_type: str, *,
    none_status: str, none_detail: str, none_action: str, failed_status: str, running_status: str,
    blocked_status: str,
) -> ProjectStatus | None:
    job = _latest_job(conn, project.content_package_id, job_type)
    if job is None:
        return _status(project, none_status, none_detail, none_action)
    if job["state"] == "failed":
        return _status(project, failed_status, job["error_message"] or "job failed", "Review the error, then resubmit.")
    if job["state"] == "pending":
        readiness = check_asset_readiness(project_dir)
        if not readiness.ready:
            missing = ", ".join(readiness.missing) if readiness.missing else "a required asset"
            return _status(project, blocked_status, f"Waiting on: {missing}", "Place the missing asset(s), then check status again.")
        return _status(project, running_status, "Job is queued.", "content-engine worker run --once")
    if job["state"] == "processing":
        return _status(project, running_status, "Job is running.", "content-engine worker run --once")
    return None  # complete - caller proceeds to the next step


def derive_status(conn: sqlite3.Connection, content_package_id: str) -> ProjectStatus:
    project = resolve_project(conn, content_package_id)
    project_dir = Path(project.project_dir)

    if not (project_dir / "script" / "stage1_beat_shot_table.json").is_file():
        return _status(
            project, "AWAITING_STAGE1_INPUT", "Stage 1 input file not found.",
            f"Place script/stage1_beat_shot_table.json under {project.project_dir}, then run: "
            f"content-engine submit {content_package_id} stage2_input_build",
        )

    if not (project_dir / "stage2_input.json").is_file():
        return _build_step_status(
            conn, project, "stage2_input_build",
            waiting_status="AWAITING_STAGE2_INPUT_BUILD",
            waiting_detail="Stage 1 file is present but stage2_input.json has not been built yet.",
            waiting_action=f"content-engine submit {content_package_id} stage2_input_build",
        )

    if not (project_dir / "stage3_input.json").is_file():
        return _build_step_status(
            conn, project, "stage3_input_build",
            waiting_status="AWAITING_STAGE3_INPUT_BUILD",
            waiting_detail="Waiting on Stage 2 keyframes/output and a stage3_input_build payload.",
            waiting_action=f"Place Stage 2 keyframes under keyframes/, then: "
                            f"content-engine submit {content_package_id} stage3_input_build payload.json",
        )

    if not (project_dir / "clips" / "clip_manifest.json").is_file():
        return _build_step_status(
            conn, project, "clip_manifest_build",
            waiting_status="AWAITING_CLIP_MANIFEST_BUILD",
            waiting_detail="Waiting on Google Flow clips and a clip_manifest_build payload.",
            waiting_action=f"Place Flow clips under clips/, then: "
                            f"content-engine submit {content_package_id} clip_manifest_build payload.json",
        )

    if not (project_dir / "edit_plan.json").is_file():
        return _build_step_status(
            conn, project, "edit_plan_build",
            waiting_status="AWAITING_EDIT_PLAN_BUILD",
            waiting_detail="Waiting on Voice/Semantic Sync/Overlay/SFX input and an edit_plan_build payload.",
            waiting_action=f"content-engine submit {content_package_id} edit_plan_build payload.json",
        )

    preview_status = _render_step_status(
        conn, project, project_dir, "preview_render",
        none_status="READY_FOR_PREVIEW", none_detail="edit_plan.json is ready. No preview has been requested yet.",
        none_action=f"content-engine preview {content_package_id}",
        failed_status="PREVIEW_FAILED", running_status="PREVIEW_RUNNING",
        blocked_status="AWAITING_FLOW_CLIPS_FOR_PREVIEW",
    )
    if preview_status is not None:
        return preview_status

    final_status = _render_step_status(
        conn, project, project_dir, "final_render",
        none_status="READY_FOR_FINAL_APPROVAL", none_detail="Preview completed. Review it, then approve final.",
        none_action=f"content-engine final {content_package_id}",
        failed_status="FINAL_FAILED", running_status="FINAL_RUNNING",
        blocked_status="AWAITING_FLOW_CLIPS_FOR_FINAL",
    )
    if final_status is not None:
        return final_status

    return _status(project, "DONE", "Final render completed.", "No further action needed.")


def list_content_packages(conn: sqlite3.Connection) -> list[ProjectStatus]:
    rows = conn.execute("SELECT id FROM content_packages ORDER BY created_at DESC").fetchall()
    return [derive_status(conn, row[0]) for row in rows]

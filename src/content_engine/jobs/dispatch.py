"""Job dispatch - CE-2B scope only.

`dispatch()` is a single `job_type -> handler` lookup, no class hierarchy,
matching CE-1/CE-2A's own "no JobRepository/DAO" precedent. Each handler
does exactly three things, in order: (1) load whatever this job type needs
from disk/`job.payload` via `jobs/artifacts.py`, (2) call the *existing*
CE-4b/CE-4c/CE-4d/CE-4e/CE-4f `build_*()`/`write_*()` function or CE-6's
`render_project()` - never a re-implementation of any of their logic, and
(3) translate the outcome into `complete_job()`/`fail_job()` (CE-2A,
unmodified).

`stage2_input_build` folds together what would otherwise be two separate
CE-phase-numbered steps (CE-4b's parse+validate, CE-4c's build+write): CE-4b
never writes a file of its own (`list[PlannedShot]` only ever existed in
memory, confirmed by its own module docstring), so there is no on-disk
boundary between the two a job system could observe or trigger against -
they are necessarily one job body.

Every handler wraps its body in a bare `except Exception` that calls
`fail_job(error_message=str(exc))` - this is deliberately not a curated
list of "expected" exceptions. A `MalformedStage1InputError`, a
`ClipSelectionExceedsRequiredDurationError`, a `KeyError` from a
malformed `payload`, all land the same way: the job is `FAILED` and the
original exception's own message is preserved verbatim in
`error_message`, for a human to read and act on (Architecture Freeze
Review decision #4 - no automatic retry, no artifact-overwrite recovery
attempt, a human decides what to do next).

`preview_render`/`final_render` never call `check_asset_readiness()`
themselves - that already happened once, before this job was even claimed
(`jobs/claim.py`), so a call here would be the redundant second evaluation
CE-6 itself was designed to avoid. If Video Studio's own gate rejects the
render anyway (CE-5's readiness check is narrower than the full preflight
- see `content_packages/readiness.py`'s own docstring - so this can still
happen for reasons CE-5 does not check, e.g. a missing BGM/SFX/voice
asset), that is recorded as `FAILED` with the full `issues` list in
`error_message`, on the same "a human must act, not an automatic retry"
principle as any other failure here - not because it is a code bug.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from content_engine.jobs.artifacts import (
    edit_plan_human_input_from_payload,
    qc_decisions_from_payload,
    read_clip_manifest,
    read_stage2_input,
    read_stage3_input,
    stage2_outputs_from_payload,
)
from content_engine.jobs.models import Job
from content_engine.jobs.transitions import complete_job, fail_job
from content_engine.video_planning.clip_qc import build_clip_manifest, measure_clips, write_clip_manifest
from content_engine.video_planning.edit_plan import build_edit_plan, write_edit_plan
from content_engine.video_planning.stage1_input import build_planned_shots, read_stage1_beat_shot_table
from content_engine.video_planning.stage2_input import build_stage2_input, write_stage2_input
from content_engine.video_planning.stage3_input import build_stage3_input, write_stage3_input
from content_engine.video_studio.pipeline import render_project


def _content_package_for(conn: sqlite3.Connection, job: Job) -> tuple[Path, str]:
    row = conn.execute(
        "SELECT project_dir, topic FROM content_packages WHERE id = ?", (job.content_package_id,)
    ).fetchone()
    return Path(row[0]), row[1]


def _payload_dict(job: Job) -> dict:
    return json.loads(job.payload) if job.payload else {}


def _handle_stage2_input_build(conn: sqlite3.Connection, job: Job) -> None:
    project_dir, topic = _content_package_for(conn, job)
    try:
        table = read_stage1_beat_shot_table(project_dir)
        shots = build_planned_shots(table)
        package = build_stage2_input(topic, shots)
        write_stage2_input(project_dir, package)
    except Exception as exc:
        fail_job(conn, job.id, error_message=str(exc))
        return
    complete_job(conn, job.id)


def _handle_stage3_input_build(conn: sqlite3.Connection, job: Job) -> None:
    project_dir, _ = _content_package_for(conn, job)
    try:
        stage2_input = read_stage2_input(project_dir)
        stage2_outputs = stage2_outputs_from_payload(_payload_dict(job))
        package = build_stage3_input(stage2_input, stage2_outputs)
        write_stage3_input(project_dir, package)
    except Exception as exc:
        fail_job(conn, job.id, error_message=str(exc))
        return
    complete_job(conn, job.id)


def _handle_clip_manifest_build(conn: sqlite3.Connection, job: Job) -> None:
    project_dir, _ = _content_package_for(conn, job)
    try:
        stage3_input = read_stage3_input(project_dir)
        measured = measure_clips(project_dir, stage3_input)
        decisions = qc_decisions_from_payload(_payload_dict(job))
        manifest = build_clip_manifest(stage3_input, measured, decisions)
        write_clip_manifest(project_dir, manifest)
    except Exception as exc:
        fail_job(conn, job.id, error_message=str(exc))
        return
    complete_job(conn, job.id)


def _handle_edit_plan_build(conn: sqlite3.Connection, job: Job) -> None:
    project_dir, topic = _content_package_for(conn, job)
    try:
        stage2_input = read_stage2_input(project_dir)
        stage3_input = read_stage3_input(project_dir)
        clip_manifest = read_clip_manifest(project_dir)
        voice_input, semantic_sync, overlay_decisions, sfx_decisions = edit_plan_human_input_from_payload(
            _payload_dict(job)
        )
        plan = build_edit_plan(
            topic, stage2_input, stage3_input, clip_manifest,
            voice_input, semantic_sync, overlay_decisions, sfx_decisions, project_dir,
        )
        write_edit_plan(project_dir, plan)
    except Exception as exc:
        fail_job(conn, job.id, error_message=str(exc))
        return
    complete_job(conn, job.id)


def _render_failure_message(result) -> str:
    issues = result.payload.get("issues") if result.payload else None
    return f"category={result.category.value}, exit_code={result.exit_code}, issues={issues}, stderr={result.stderr!r}"


def _finish_render_job(conn: sqlite3.Connection, job: Job, result) -> None:
    if result.category is None:
        complete_job(conn, job.id)
        return
    fail_job(conn, job.id, error_message=_render_failure_message(result))


def _handle_preview_render(conn: sqlite3.Connection, job: Job) -> None:
    project_dir, _ = _content_package_for(conn, job)
    result = render_project(project_dir, "preview")
    _finish_render_job(conn, job, result)


def _handle_final_render(conn: sqlite3.Connection, job: Job) -> None:
    project_dir, _ = _content_package_for(conn, job)
    result = render_project(project_dir, "final")
    _finish_render_job(conn, job, result)


_HANDLERS = {
    "stage2_input_build": _handle_stage2_input_build,
    "stage3_input_build": _handle_stage3_input_build,
    "clip_manifest_build": _handle_clip_manifest_build,
    "edit_plan_build": _handle_edit_plan_build,
    "preview_render": _handle_preview_render,
    "final_render": _handle_final_render,
}


def dispatch(conn: sqlite3.Connection, job: Job) -> None:
    _HANDLERS[job.job_type](conn, job)

"""Pending job atomic claim - CE-2B scope only.

`claim_next_runnable_job()` never invents a new atomicity mechanism - the
actual `PENDING -> PROCESSING` transition is CE-2A's own `start_job()`,
called exactly as any other caller would call it. The only thing this
module adds on top is candidate *selection*: scan `PENDING` rows oldest
first, and for the two job types that carry a Video Studio precondition
(`preview_render`/`final_render`), skip a row whose Google Flow clips
aren't all present yet rather than claiming it - CE-2A itself has no
"skip and try the next one" concept, so that loop lives here.

A skipped gated row is left exactly as it was (`PENDING`, never touched) -
this is CE-2B's Architecture Freeze Review decision #2 (a human gate is
represented by a job simply not progressing, not by a new `JobState`) and
decision #7 (CE-5 readiness filters `preview_render`/`final_render`
*before* they run, rather than letting Video Studio's own gate reject them
and recording that as a failure) applied together. The readiness check
itself is CE-5's existing `check_asset_readiness()`, called read-only,
never re-implemented.

If `check_asset_readiness()` itself raises (Video Studio/CE-3 failed to
run at all, not "clip missing") - CE-5's own documented behavior for that
case - this function does not swallow it into "not ready" either; it
propagates, exactly as CE-5 designed it to. That is an infrastructure
failure this module has no more insight into than CE-5 already provides.

A second `try: start_job() except InvalidJobTransitionError` covers the
case where a different caller (a second worker process, in a future
multi-worker world - not assumed to exist in this v1) claimed the same row
between the `SELECT` and this attempt; the loop simply moves on to the
next candidate rather than treating that as an error.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from content_engine.content_packages.readiness import check_asset_readiness
from content_engine.jobs.models import Job, JobState
from content_engine.jobs.transitions import InvalidJobTransitionError, start_job

_GATED_JOB_TYPES = frozenset({"preview_render", "final_render"})


def _project_dir_for(conn: sqlite3.Connection, job_id: str) -> Path:
    row = conn.execute(
        """
        SELECT content_packages.project_dir
        FROM jobs JOIN content_packages ON jobs.content_package_id = content_packages.id
        WHERE jobs.id = ?
        """,
        (job_id,),
    ).fetchone()
    return Path(row[0])


def _readiness_gate_passes(conn: sqlite3.Connection, job_id: str) -> bool:
    project_dir = _project_dir_for(conn, job_id)
    return check_asset_readiness(project_dir).ready


def _load_job(conn: sqlite3.Connection, job_id: str) -> Job:
    row = conn.execute(
        """
        SELECT id, job_type, state, stage, error_message, created_at, updated_at,
               started_at, completed_at, content_package_id, payload
        FROM jobs WHERE id = ?
        """,
        (job_id,),
    ).fetchone()
    return Job(
        id=row[0], job_type=row[1], state=JobState(row[2]), stage=row[3], error_message=row[4],
        created_at=row[5], updated_at=row[6], started_at=row[7], completed_at=row[8],
        content_package_id=row[9], payload=row[10],
    )


def claim_next_runnable_job(conn: sqlite3.Connection) -> Job | None:
    candidates = conn.execute(
        "SELECT id, job_type FROM jobs WHERE state = ? ORDER BY created_at ASC",
        (JobState.PENDING.value,),
    ).fetchall()

    for job_id, job_type in candidates:
        if job_type in _GATED_JOB_TYPES and not _readiness_gate_passes(conn, job_id):
            continue
        try:
            start_job(conn, job_id)
        except InvalidJobTransitionError:
            continue
        return _load_job(conn, job_id)

    return None

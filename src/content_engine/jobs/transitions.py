"""Job state transitions - CE-2A scope only.

Seven functions, one per state a job can be explicitly driven into by a
caller that already knows the job's id (`start_job`/`pause_job`/
`resume_job`/`stop_job`/`cancel_job`/`complete_job`/`fail_job`). There is
no "claim the next pending job" function here and no worker loop that
calls these on its own - that's CE-2B, not yet built. This module only
answers "is this specific job allowed to move to this specific state
right now, and if so, make it so atomically" - it has no opinion on when
or why a caller decides to ask.

Atomicity: every transition is a single conditional
`UPDATE jobs SET ... WHERE id = ? AND state IN (...)` - never a
SELECT-then-UPDATE pair, which would leave a TOCTOU race between reading
the current state and acting on it. If the UPDATE affects zero rows, a
second, read-only `SELECT state FROM jobs WHERE id = ?` runs *only then*,
purely to classify the failure: no row at all -> `JobNotFoundError`;
a row that exists but wasn't in an allowed source state ->
`InvalidJobTransitionError`. The 7x7 legal-transition table below is the
same one from the CE-2A design review, unchanged.

`started_at` is set exactly once, by `start_job()` (the PENDING ->
PROCESSING transition) - `resume_job()` (PAUSED -> PROCESSING) never
touches it, so it always reflects when work first began, not when it was
last resumed. `completed_at` is set by the four transitions that reach a
terminal state (`stop_job`/`cancel_job`/`complete_job`/`fail_job`) and by
no others. `updated_at` is set on every successful transition, with no
exception.

Deliberately not here (see `docs/content-engine-phase-plan.md`'s CE-2A/
CE-2B split): no worker, no queue, no "next pending job" selection, no
`retry_count`, no crash recovery for a process that dies mid-`PROCESSING`
- none of that has anything to run yet, since nothing outside a test
calls these functions today.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from content_engine.jobs.models import JobState


class JobNotFoundError(Exception):
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(f"no job with id {job_id!r}")


class InvalidJobTransitionError(Exception):
    def __init__(self, job_id: str, current_state: JobState, attempted_state: JobState) -> None:
        self.job_id = job_id
        self.current_state = current_state
        self.attempted_state = attempted_state
        super().__init__(
            f"job {job_id!r} cannot move from {current_state.value!r} to {attempted_state.value!r}"
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _transition(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    from_states: tuple[JobState, ...],
    to_state: JobState,
    extra_columns: dict[str, str | None] | None = None,
) -> None:
    columns = {"state": to_state.value, "updated_at": _now(), **(extra_columns or {})}
    set_clause = ", ".join(f"{name} = ?" for name in columns)
    from_placeholders = ", ".join("?" for _ in from_states)

    cursor = conn.execute(
        f"UPDATE jobs SET {set_clause} WHERE id = ? AND state IN ({from_placeholders})",
        (*columns.values(), job_id, *[s.value for s in from_states]),
    )

    if cursor.rowcount == 0:
        row = conn.execute("SELECT state FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFoundError(job_id)
        raise InvalidJobTransitionError(job_id, JobState(row[0]), to_state)

    conn.commit()


def start_job(conn: sqlite3.Connection, job_id: str) -> None:
    _transition(
        conn, job_id,
        from_states=(JobState.PENDING,), to_state=JobState.PROCESSING,
        extra_columns={"started_at": _now()},
    )


def pause_job(conn: sqlite3.Connection, job_id: str) -> None:
    _transition(conn, job_id, from_states=(JobState.PROCESSING,), to_state=JobState.PAUSED)


def resume_job(conn: sqlite3.Connection, job_id: str) -> None:
    _transition(conn, job_id, from_states=(JobState.PAUSED,), to_state=JobState.PROCESSING)


def stop_job(conn: sqlite3.Connection, job_id: str) -> None:
    _transition(
        conn, job_id,
        from_states=(JobState.PROCESSING, JobState.PAUSED), to_state=JobState.STOPPED,
        extra_columns={"completed_at": _now()},
    )


def cancel_job(conn: sqlite3.Connection, job_id: str) -> None:
    _transition(
        conn, job_id,
        from_states=(JobState.PENDING, JobState.PROCESSING, JobState.PAUSED), to_state=JobState.CANCELLED,
        extra_columns={"completed_at": _now()},
    )


def complete_job(conn: sqlite3.Connection, job_id: str) -> None:
    _transition(
        conn, job_id,
        from_states=(JobState.PROCESSING,), to_state=JobState.COMPLETE,
        extra_columns={"completed_at": _now()},
    )


def fail_job(conn: sqlite3.Connection, job_id: str, *, error_message: str, stage: str | None = None) -> None:
    _transition(
        conn, job_id,
        from_states=(JobState.PROCESSING,), to_state=JobState.FAILED,
        extra_columns={"completed_at": _now(), "error_message": error_message, "stage": stage},
    )

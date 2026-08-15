"""Worker polling loop - CE-2B scope only.

`run_worker()` is deliberately small: claim one runnable job
(`jobs/claim.py`), dispatch it (`jobs/dispatch.py`), repeat; if nothing is
claimable, wait `poll_interval_seconds` and try again. Job bodies are all
short synchronous calls (a file read/write, one `ffprobe` invocation, one
`seoulkit-studio` subprocess call - confirmed by reading every CE-4b-4f/CE-6
function this dispatches to), so there is no mid-job cancellation
mechanism: `stop_event` is only checked between jobs, never during one.

This module assumes a single worker process, matching the phase table's own
"Single worker / queue execution" description - `claim_next_runnable_job()`'s
atomicity (reusing CE-2A's `start_job()`) would already make a second
concurrent worker safe, but nothing here spawns, coordinates, or even
detects one; that is out of v1's scope on the same YAGNI grounds CE-1
already used for `retry_count`/`worker_id`/`heartbeat_at`.

`_recover_stale_processing_jobs()` is the only crash-recovery mechanism -
no new `heartbeat_at`/`worker_id` column, matching the CE-2B Architecture
Freeze Review's decision not to add retry-adjacent fields without a
concrete need. It uses the existing `updated_at` column: any row still
`PROCESSING` after `stale_after_seconds` with no update is assumed to
belong to a worker process that died mid-job, and is moved to `FAILED` via
CE-2A's own `fail_job()` (not a raw UPDATE) - a human then decides whether
to create a fresh job to retry the work, per Architecture Freeze Review
decision #4 (no automatic artifact-overwrite recovery).
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from content_engine.jobs.claim import claim_next_runnable_job
from content_engine.jobs.dispatch import dispatch
from content_engine.jobs.models import JobState
from content_engine.jobs.transitions import fail_job

_DEFAULT_STALE_AFTER_SECONDS = 3600


def _recover_stale_processing_jobs(conn: sqlite3.Connection, stale_after_seconds: float) -> None:
    threshold = (datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)).isoformat()
    stale_ids = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM jobs WHERE state = ? AND updated_at < ?",
            (JobState.PROCESSING.value, threshold),
        ).fetchall()
    ]
    for job_id in stale_ids:
        fail_job(conn, job_id, error_message="worker crash suspected (stale PROCESSING row)")


def run_worker(
    conn: sqlite3.Connection,
    poll_interval_seconds: float = 5.0,
    stop_event: threading.Event | None = None,
    stale_after_seconds: float = _DEFAULT_STALE_AFTER_SECONDS,
) -> None:
    stop_event = stop_event or threading.Event()
    _recover_stale_processing_jobs(conn, stale_after_seconds)

    while not stop_event.is_set():
        job = claim_next_runnable_job(conn)
        if job is None:
            stop_event.wait(poll_interval_seconds)
            continue
        dispatch(conn, job)

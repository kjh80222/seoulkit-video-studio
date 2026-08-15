"""Job creation - CE-2B scope only.

`create_job()` is the sole entry point that puts a new row into `jobs` in
`PENDING` state - CE-2A's `transitions.py` never creates rows, only moves
an already-existing `job_id` between states (see that module's own
docstring), and nothing before CE-2B ever called `INSERT INTO jobs` outside
a test.

`job_type` is restricted to the six CE-2B job types (see
`docs/content-engine-phase-plan.md`'s CE-2B phase section for the full
per-type callable/input/output table). `content_package_create` is
deliberately not one of them: at the moment that action runs, no
`ContentPackage` (and therefore no `content_package_id`) exists yet to
attach a job row to, and CE-4a's own `create_content_package()` was never
designed around a Job wrapper - it stays a direct synchronous call.

`payload` carries whatever a human-authored value a job's body needs that
has no on-disk file format of its own (`Stage2ShotOutput`, `ShotQcDecision`,
`HumanVoiceTimingInput`, `HumanSemanticSyncSegment`, `HumanOverlayDecision`,
`HumanSfxDecision` - see `jobs/artifacts.py`'s docstring for the full
reasoning). `create_job()` itself does not interpret `payload`'s contents
at all - it only serializes whatever `dict` the caller supplies to JSON and
stores it verbatim; validating that the right shape of human decision was
supplied is `jobs/dispatch.py`'s job, at the point it actually needs to
deserialize and use it. This keeps job creation itself agnostic to which
of the six job types is being created beyond the bare `job_type` string
check - exactly the kind of decision CE-2B does not make on a human's
behalf.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from content_engine.jobs.models import Job, JobState

JOB_TYPES = (
    "stage2_input_build",
    "stage3_input_build",
    "clip_manifest_build",
    "edit_plan_build",
    "preview_render",
    "final_render",
)


class UnknownJobTypeError(Exception):
    def __init__(self, job_type: str) -> None:
        self.job_type = job_type
        super().__init__(f"unknown job_type {job_type!r}, expected one of {JOB_TYPES!r}")


class ContentPackageNotFoundError(Exception):
    def __init__(self, content_package_id: str) -> None:
        self.content_package_id = content_package_id
        super().__init__(f"no content_package with id {content_package_id!r}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(
    conn: sqlite3.Connection, content_package_id: str, job_type: str, payload: dict | None = None
) -> Job:
    if job_type not in JOB_TYPES:
        raise UnknownJobTypeError(job_type)

    if conn.execute(
        "SELECT 1 FROM content_packages WHERE id = ?", (content_package_id,)
    ).fetchone() is None:
        raise ContentPackageNotFoundError(content_package_id)

    job_id = str(uuid.uuid4())
    now = _now()
    payload_json = json.dumps(payload) if payload is not None else None

    conn.execute(
        """
        INSERT INTO jobs (id, job_type, state, content_package_id, payload, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (job_id, job_type, JobState.PENDING.value, content_package_id, payload_json, now, now),
    )
    conn.commit()

    return Job(
        id=job_id, job_type=job_type, state=JobState.PENDING, created_at=now, updated_at=now,
        content_package_id=content_package_id, payload=payload_json,
    )

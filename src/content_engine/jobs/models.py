"""`Job`/`JobState` data model - CE-1 scope, plus CE-2B's two extra fields.

Pure data shape, nothing else: no persistence methods (no `to_row()`/
`from_row()`), no queue/worker logic, no retry handling. `JobState` covers
only the generic execution states a job manager needs (CE-2) - it
deliberately does not yet include `AWAITING_HUMAN_ASSET` (that's a CE-5
concept; CE-5 shipped without ever needing it - see
`content_packages/readiness.py` - and CE-2B does not add it either, see
`docs/content-engine-phase-plan.md`'s CE-2B Architecture Freeze Review).

`content_package_id`/`payload` are CE-2B additions (see
`db/migrations.py`), added here only as two more optional fields on the
same plain dataclass - no new persistence method, no change to how any
existing field round-trips.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class JobState(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    PAUSED = "paused"
    STOPPED = "stopped"
    CANCELLED = "cancelled"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class Job:
    id: str
    job_type: str
    state: JobState
    created_at: str
    updated_at: str
    stage: str | None = None
    error_message: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    content_package_id: str | None = None
    payload: str | None = None

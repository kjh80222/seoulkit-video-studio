"""`Job`/`JobState` data model - CE-1 scope only.

Pure data shape, nothing else: no persistence methods (no `to_row()`/
`from_row()`), no queue/worker logic, no retry handling. `JobState` covers
only the generic execution states a job manager needs (CE-2) - it
deliberately does not yet include `AWAITING_HUMAN_ASSET` (that's a CE-5
concept, added to this enum when CE-5 actually needs it; a SQLite TEXT
column requires no migration for a new enum value, only a new state
handled by future CE phases' code).
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

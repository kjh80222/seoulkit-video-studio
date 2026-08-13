from content_engine.db.connection import get_connection
from content_engine.jobs.models import Job, JobState


def test_job_state_has_exactly_seven_values():
    assert {s.value for s in JobState} == {
        "pending", "processing", "paused", "stopped",
        "cancelled", "complete", "failed",
    }
    assert len(JobState) == 7


def test_job_state_does_not_yet_include_awaiting_human_asset():
    # CE-5 concept, deliberately not part of CE-1's generic execution states
    # (see the module docstring and the Architecture Freeze Review).
    assert not any(s.value == "awaiting_human_asset" for s in JobState)


def test_job_dataclass_holds_exactly_the_expected_fields():
    job = Job(
        id="job-1",
        job_type="render_pipeline",
        state=JobState.PENDING,
        created_at="2026-08-13T00:00:00+00:00",
        updated_at="2026-08-13T00:00:00+00:00",
    )

    assert job.stage is None
    assert job.error_message is None
    assert job.started_at is None
    assert job.completed_at is None


def test_job_round_trips_through_raw_sql_insert_and_select(tmp_path):
    # No JobRepository/DAO in CE-1 (Architecture Freeze Review) - this test
    # does its own raw SQL, exactly as any CE-1 caller would have to.
    conn = get_connection(tmp_path / "content_engine.db")

    original = Job(
        id="job-42",
        job_type="render_pipeline",
        state=JobState.PROCESSING,
        created_at="2026-08-13T10:00:00+00:00",
        updated_at="2026-08-13T10:05:00+00:00",
        stage="rendering",
        error_message=None,
        started_at="2026-08-13T10:01:00+00:00",
        completed_at=None,
    )

    conn.execute(
        """
        INSERT INTO jobs
            (id, job_type, state, stage, error_message, created_at, updated_at, started_at, completed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            original.id, original.job_type, original.state.value, original.stage, original.error_message,
            original.created_at, original.updated_at, original.started_at, original.completed_at,
        ),
    )
    conn.commit()

    row = conn.execute(
        "SELECT id, job_type, state, stage, error_message, created_at, updated_at, started_at, completed_at "
        "FROM jobs WHERE id = ?",
        (original.id,),
    ).fetchone()

    restored = Job(
        id=row[0], job_type=row[1], state=JobState(row[2]), stage=row[3], error_message=row[4],
        created_at=row[5], updated_at=row[6], started_at=row[7], completed_at=row[8],
    )

    assert restored == original


def test_job_round_trip_preserves_null_stage_and_error_message(tmp_path):
    conn = get_connection(tmp_path / "content_engine.db")

    original = Job(
        id="job-99",
        job_type="render_pipeline",
        state=JobState.COMPLETE,
        created_at="2026-08-13T11:00:00+00:00",
        updated_at="2026-08-13T11:10:00+00:00",
        completed_at="2026-08-13T11:10:00+00:00",
    )

    conn.execute(
        """
        INSERT INTO jobs
            (id, job_type, state, stage, error_message, created_at, updated_at, started_at, completed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            original.id, original.job_type, original.state.value, original.stage, original.error_message,
            original.created_at, original.updated_at, original.started_at, original.completed_at,
        ),
    )
    conn.commit()

    row = conn.execute(
        "SELECT stage, error_message, started_at FROM jobs WHERE id = ?", (original.id,)
    ).fetchone()

    assert row == (None, None, None)

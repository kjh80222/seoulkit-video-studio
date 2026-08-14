import sqlite3

import pytest

from content_engine.db.connection import get_connection
from content_engine.jobs.models import JobState
from content_engine.jobs.transitions import (
    InvalidJobTransitionError,
    JobNotFoundError,
    cancel_job,
    complete_job,
    fail_job,
    pause_job,
    resume_job,
    start_job,
    stop_job,
)

ALL_STATES = list(JobState)

# (function, legal source states, target state it produces) - the same
# mapping the CE-2A design review approved as the 7x7 matrix, expressed
# per function rather than as an abstract grid, since each function owns
# its own guard.
_FUNCTIONS = {
    "start_job": (start_job, frozenset({JobState.PENDING}), JobState.PROCESSING),
    "pause_job": (pause_job, frozenset({JobState.PROCESSING}), JobState.PAUSED),
    "resume_job": (resume_job, frozenset({JobState.PAUSED}), JobState.PROCESSING),
    "stop_job": (stop_job, frozenset({JobState.PROCESSING, JobState.PAUSED}), JobState.STOPPED),
    "cancel_job": (
        cancel_job, frozenset({JobState.PENDING, JobState.PROCESSING, JobState.PAUSED}), JobState.CANCELLED,
    ),
    "complete_job": (complete_job, frozenset({JobState.PROCESSING}), JobState.COMPLETE),
    "fail_job": (lambda conn, job_id: fail_job(conn, job_id, error_message="boom"),
                 frozenset({JobState.PROCESSING}), JobState.FAILED),
}


def _insert_job(
    conn: sqlite3.Connection, job_id: str, state: JobState, *,
    created_at: str = "2020-01-01T00:00:00+00:00", updated_at: str = "2020-01-01T00:00:00+00:00",
    started_at: str | None = None, completed_at: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO jobs (id, job_type, state, created_at, updated_at, started_at, completed_at) "
        "VALUES (?, 'test_job', ?, ?, ?, ?, ?)",
        (job_id, state.value, created_at, updated_at, started_at, completed_at),
    )
    conn.commit()


def _row(conn: sqlite3.Connection, job_id: str) -> tuple:
    return conn.execute(
        "SELECT id, job_type, state, stage, error_message, created_at, updated_at, started_at, completed_at "
        "FROM jobs WHERE id = ?", (job_id,),
    ).fetchone()


# --- full 7x7 transition matrix (49 parametrized cases) ---------------------

_MATRIX_CASES = [
    (name, func, source, to_state, source in legal_from)
    for name, (func, legal_from, to_state) in _FUNCTIONS.items()
    for source in ALL_STATES
]


@pytest.mark.parametrize(
    "name,func,source,to_state,is_legal", _MATRIX_CASES,
    ids=[f"{name}:{source.value}->{'ok' if legal else 'reject'}" for name, _, source, _, legal in _MATRIX_CASES],
)
def test_transition_matrix_matches_the_approved_table(tmp_path, name, func, source, to_state, is_legal):
    conn = get_connection(tmp_path / "content_engine.db")
    _insert_job(conn, "job-1", source)

    if is_legal:
        func(conn, "job-1")
        assert _row(conn, "job-1")[2] == to_state.value
    else:
        with pytest.raises(InvalidJobTransitionError) as exc_info:
            func(conn, "job-1")
        assert exc_info.value.job_id == "job-1"
        assert exc_info.value.current_state == source
        assert exc_info.value.attempted_state == to_state
        assert _row(conn, "job-1")[2] == source.value  # unchanged


# --- JobNotFoundError vs InvalidJobTransitionError ---------------------------

@pytest.mark.parametrize("name,func_entry", list(_FUNCTIONS.items()), ids=list(_FUNCTIONS.keys()))
def test_missing_job_raises_job_not_found_error_not_invalid_transition(tmp_path, name, func_entry):
    func, _, _ = func_entry
    conn = get_connection(tmp_path / "content_engine.db")

    with pytest.raises(JobNotFoundError) as exc_info:
        func(conn, "does-not-exist")
    assert exc_info.value.job_id == "does-not-exist"


# --- started_at: set once, preserved across pause/resume ---------------------

def test_started_at_is_set_only_by_start_job_and_survives_pause_resume(tmp_path):
    conn = get_connection(tmp_path / "content_engine.db")
    _insert_job(conn, "job-1", JobState.PENDING)
    assert _row(conn, "job-1")[7] is None  # started_at

    start_job(conn, "job-1")
    started_at_after_start = _row(conn, "job-1")[7]
    assert started_at_after_start is not None

    pause_job(conn, "job-1")
    resume_job(conn, "job-1")

    assert _row(conn, "job-1")[7] == started_at_after_start


# --- completed_at: set only on terminal transitions ---------------------------

@pytest.mark.parametrize("name,func_entry", [
    (n, e) for n, e in _FUNCTIONS.items() if n in ("stop_job", "cancel_job", "complete_job", "fail_job")
], ids=["stop_job", "cancel_job", "complete_job", "fail_job"])
def test_completed_at_is_set_by_each_terminal_transition(tmp_path, name, func_entry):
    func, _, _ = func_entry
    conn = get_connection(tmp_path / "content_engine.db")
    _insert_job(conn, "job-1", JobState.PROCESSING)
    assert _row(conn, "job-1")[8] is None  # completed_at

    func(conn, "job-1")

    assert _row(conn, "job-1")[8] is not None


def test_completed_at_stays_none_through_pause_and_resume(tmp_path):
    conn = get_connection(tmp_path / "content_engine.db")
    _insert_job(conn, "job-1", JobState.PROCESSING)

    pause_job(conn, "job-1")
    assert _row(conn, "job-1")[8] is None

    resume_job(conn, "job-1")
    assert _row(conn, "job-1")[8] is None


# --- updated_at: fixed-past fixture, no sleep, no clock abstraction ----------

def test_updated_at_advances_past_a_fixed_old_fixture_value(tmp_path):
    conn = get_connection(tmp_path / "content_engine.db")
    old_updated_at = "2020-01-01T00:00:00+00:00"
    _insert_job(conn, "job-1", JobState.PENDING, updated_at=old_updated_at)

    start_job(conn, "job-1")

    new_updated_at = _row(conn, "job-1")[6]
    assert new_updated_at != old_updated_at
    from datetime import datetime
    assert datetime.fromisoformat(new_updated_at) > datetime.fromisoformat(old_updated_at)


# --- fail_job: stage/error_message -------------------------------------------

def test_fail_job_records_stage_and_error_message(tmp_path):
    conn = get_connection(tmp_path / "content_engine.db")
    _insert_job(conn, "job-1", JobState.PROCESSING)

    fail_job(conn, "job-1", error_message="ffmpeg exited with non-zero status", stage="rendering")

    row = _row(conn, "job-1")
    assert row[3] == "rendering"  # stage
    assert row[4] == "ffmpeg exited with non-zero status"  # error_message


def test_fail_job_requires_error_message(tmp_path):
    conn = get_connection(tmp_path / "content_engine.db")
    _insert_job(conn, "job-1", JobState.PROCESSING)

    with pytest.raises(TypeError):
        fail_job(conn, "job-1")  # type: ignore[call-arg]


# --- atomicity: a rejected transition leaves every column untouched ---------

def test_rejected_transition_leaves_the_entire_row_byte_identical(tmp_path):
    conn = get_connection(tmp_path / "content_engine.db")
    _insert_job(
        conn, "job-1", JobState.COMPLETE,
        created_at="2020-01-01T00:00:00+00:00", updated_at="2020-01-01T00:00:00+00:00",
        started_at="2020-01-01T00:00:01+00:00", completed_at="2020-01-01T00:00:02+00:00",
    )
    before = _row(conn, "job-1")

    with pytest.raises(InvalidJobTransitionError):
        pause_job(conn, "job-1")  # COMPLETE is terminal

    assert _row(conn, "job-1") == before

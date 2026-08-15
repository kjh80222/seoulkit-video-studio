import json

import pytest

from content_engine.content_packages.create import create_content_package
from content_engine.db.connection import get_connection
from content_engine.jobs.creation import (
    JOB_TYPES,
    ContentPackageNotFoundError,
    UnknownJobTypeError,
    create_job,
)
from content_engine.jobs.models import JobState


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_ENGINE_PROJECTS_ROOT", str(tmp_path / "projects"))
    return get_connection(tmp_path / "content_engine.db")


@pytest.fixture
def package(conn):
    return create_content_package(conn, "test topic")


def _row(conn, job_id):
    return conn.execute(
        "SELECT id, job_type, state, content_package_id, payload, created_at, updated_at "
        "FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()


@pytest.mark.parametrize("job_type", JOB_TYPES)
def test_create_job_inserts_a_pending_row_for_each_valid_job_type(conn, package, job_type):
    job = create_job(conn, package.id, job_type)

    row = _row(conn, job.id)
    assert row == (job.id, job_type, "pending", package.id, None, job.created_at, job.updated_at)


def test_create_job_rejects_unknown_job_type(conn, package):
    with pytest.raises(UnknownJobTypeError):
        create_job(conn, package.id, "not_a_real_job_type")


def test_create_job_rejects_unknown_content_package_id(conn):
    with pytest.raises(ContentPackageNotFoundError):
        create_job(conn, "no-such-package", "stage2_input_build")


def test_create_job_without_payload_stores_null(conn, package):
    job = create_job(conn, package.id, "stage2_input_build")

    assert job.payload is None
    assert _row(conn, job.id)[4] is None


def test_create_job_serializes_payload_to_json(conn, package):
    payload = {"decisions": [{"shot": "1A", "qc_passed": True}]}

    job = create_job(conn, package.id, "clip_manifest_build", payload=payload)

    assert json.loads(job.payload) == payload
    assert json.loads(_row(conn, job.id)[4]) == payload


def test_create_job_ids_are_unique(conn, package):
    first = create_job(conn, package.id, "stage2_input_build")
    second = create_job(conn, package.id, "stage2_input_build")

    assert first.id != second.id


def test_create_job_returned_job_state_is_pending(conn, package):
    job = create_job(conn, package.id, "stage2_input_build")

    assert job.state == JobState.PENDING

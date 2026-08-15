import json

import pytest

from content_engine.cli.main import main


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_ENGINE_DB_PATH", str(tmp_path / "content_engine.db"))
    monkeypatch.setenv("CONTENT_ENGINE_PROJECTS_ROOT", str(tmp_path / "projects"))
    return tmp_path


def _create_project(capsys) -> str:
    main(["create", "topic", "--json"])
    return json.loads(capsys.readouterr().out)["content_package_id"]


def test_preview_creates_a_pending_job(capsys, _env):
    project = _create_project(capsys)

    exit_code = main(["preview", project, "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["job_type"] == "preview_render"
    assert payload["state"] == "pending"


def test_preview_rejects_unknown_project(capsys, _env):
    exit_code = main(["preview", "no-such-project"])

    assert exit_code == 3


def test_preview_never_calls_render_project_directly(capsys, _env, monkeypatch):
    project = _create_project(capsys)

    import content_engine.cli.main as cli_main

    def _boom(*args, **kwargs):
        raise AssertionError("cmd_preview must never call render_project() directly")

    # cmd_preview does not import render_project at all - this asserts that
    # even if it did, calling `preview` alone (without running the worker)
    # produces no render attempt: the job stays PENDING.
    monkeypatch.setattr("content_engine.video_studio.pipeline.render_project", _boom)

    main(["preview", project])

    from content_engine.db.connection import get_connection
    from content_engine.config import resolve_db_path
    conn = get_connection(resolve_db_path())
    row = conn.execute("SELECT state FROM jobs WHERE content_package_id = ?", (project,)).fetchone()
    assert row == ("pending",)

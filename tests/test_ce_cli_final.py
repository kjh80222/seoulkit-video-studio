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


def test_final_creates_a_pending_job(capsys, _env):
    project = _create_project(capsys)

    exit_code = main(["final", project, "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["job_type"] == "final_render"
    assert payload["state"] == "pending"


def test_final_rejects_unknown_project(capsys, _env):
    exit_code = main(["final", "no-such-project"])

    assert exit_code == 3


def test_final_does_not_require_a_prior_preview_job(capsys, _env):
    # CE-6's own design never enforces "preview before final" - CE-11 does
    # not invent that enforcement either (status only advises it).
    project = _create_project(capsys)

    exit_code = main(["final", project])

    assert exit_code == 0


def test_final_never_calls_render_project_directly(capsys, _env, monkeypatch):
    project = _create_project(capsys)

    def _boom(*args, **kwargs):
        raise AssertionError("cmd_final must never call render_project() directly")

    monkeypatch.setattr("content_engine.video_studio.pipeline.render_project", _boom)

    main(["final", project])

    from content_engine.db.connection import get_connection
    from content_engine.config import resolve_db_path
    conn = get_connection(resolve_db_path())
    row = conn.execute("SELECT state FROM jobs WHERE content_package_id = ?", (project,)).fetchone()
    assert row == ("pending",)

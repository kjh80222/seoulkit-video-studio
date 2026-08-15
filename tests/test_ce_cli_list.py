import json

import pytest

from content_engine.cli.main import main


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_ENGINE_DB_PATH", str(tmp_path / "content_engine.db"))
    monkeypatch.setenv("CONTENT_ENGINE_PROJECTS_ROOT", str(tmp_path / "projects"))
    return tmp_path


def test_list_with_no_projects(capsys, _env):
    exit_code = main(["list"])

    assert exit_code == 0
    assert "No projects found." in capsys.readouterr().out


def test_list_shows_a_single_project(capsys, _env):
    main(["create", "solo topic"])
    capsys.readouterr()

    exit_code = main(["list"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "solo topic" in out
    assert "AWAITING_STAGE1_INPUT" in out


def test_list_sorts_newest_first(capsys, _env):
    main(["create", "first"])
    capsys.readouterr()
    main(["create", "second"])
    capsys.readouterr()

    main(["list"])
    out = capsys.readouterr().out

    assert out.index("second") < out.index("first")


def test_list_json_includes_project_dir(capsys, _env):
    main(["create", "topic"])
    capsys.readouterr()

    main(["list", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert len(payload["projects"]) == 1
    assert "project_dir" in payload["projects"][0]


def test_list_text_output_excludes_project_dir(capsys, _env):
    main(["create", "topic"])
    capsys.readouterr()

    main(["list"])
    out = capsys.readouterr().out

    assert str(_env / "projects") not in out

import json

import pytest

from content_engine.cli.main import main
from content_engine.db.connection import get_connection


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_ENGINE_DB_PATH", str(tmp_path / "content_engine.db"))
    monkeypatch.setenv("CONTENT_ENGINE_PROJECTS_ROOT", str(tmp_path / "projects"))
    return tmp_path


def test_create_success_prints_id_and_project_dir(capsys, _env):
    exit_code = main(["create", "test topic"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "topic: test topic" in out
    assert "project_dir" in out


def test_create_success_inserts_row(_env):
    main(["create", "test topic"])

    conn = get_connection(_env / "content_engine.db")
    row = conn.execute("SELECT topic FROM content_packages").fetchone()
    assert row == ("test topic",)


def test_create_rejects_empty_topic(capsys, _env):
    exit_code = main(["create", ""])

    assert exit_code == 3
    assert "Error" in capsys.readouterr().out


def test_create_rejects_whitespace_only_topic(capsys, _env):
    exit_code = main(["create", "   "])

    assert exit_code == 3


def test_create_json_output_is_valid_json(capsys, _env):
    exit_code = main(["create", "test topic", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["topic"] == "test topic"
    assert "content_package_id" in payload
    assert "project_dir" in payload

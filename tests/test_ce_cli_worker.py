import json
import threading

import pytest

from content_engine.cli.main import main
from content_engine.jobs.creation import create_job


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_ENGINE_DB_PATH", str(tmp_path / "content_engine.db"))
    monkeypatch.setenv("CONTENT_ENGINE_PROJECTS_ROOT", str(tmp_path / "projects"))
    return tmp_path


def _create_project(capsys) -> str:
    main(["create", "topic", "--json"])
    return json.loads(capsys.readouterr().out)["content_package_id"]


def test_worker_run_once_with_nothing_pending(capsys, _env):
    exit_code = main(["worker", "run", "--once"])

    assert exit_code == 0
    assert "Processed 0 job(s)" in capsys.readouterr().out


def test_worker_run_once_completes_a_job(capsys, _env, tmp_path):
    project = _create_project(capsys)
    (tmp_path / "projects" / project / "script").mkdir(parents=True, exist_ok=True)
    data = {"beats": [{"beat": 1, "narration": "voice", "visual_purpose": "p",
                        "screen_number": None, "screen_label": None}],
            "shots": [{"shot": "1A", "beat": 1, "shot_type": "wide", "on_screen_text": None}]}
    (tmp_path / "projects" / project / "script" / "stage1_beat_shot_table.json").write_text(json.dumps(data))
    main(["submit", project, "stage2_input_build"])
    capsys.readouterr()

    exit_code = main(["worker", "run", "--once"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "1 completed" in out


def test_worker_run_once_reports_failed_job(capsys, _env):
    project = _create_project(capsys)
    main(["submit", project, "stage2_input_build"])  # no stage1 file -> will fail
    capsys.readouterr()

    exit_code = main(["worker", "run", "--once"])

    assert exit_code == 2
    assert "1 failed" in capsys.readouterr().out


def test_worker_run_once_reports_blocked_job(capsys, _env):
    project = _create_project(capsys)
    main(["preview", project])  # edit_plan.json does not exist -> readiness check fails, job stays PENDING
    capsys.readouterr()

    exit_code = main(["worker", "run", "--once"])

    assert exit_code == 1
    assert "blocked on human input" in capsys.readouterr().out


def test_worker_run_persistent_mode_calls_run_worker_unmodified(capsys, _env, monkeypatch):
    import content_engine.cli.main as cli_main

    calls = {}

    def _fake_run_worker(conn, poll_interval_seconds=5.0, stop_event=None, **kwargs):
        calls["poll_interval_seconds"] = poll_interval_seconds
        calls["stop_event"] = stop_event
        stop_event.set()  # simulate immediate SIGINT so the test does not hang

    monkeypatch.setattr(cli_main, "run_worker", _fake_run_worker)

    exit_code = main(["worker", "run", "--poll-interval", "2.5"])

    assert exit_code == 0
    assert calls["poll_interval_seconds"] == 2.5
    assert isinstance(calls["stop_event"], threading.Event)

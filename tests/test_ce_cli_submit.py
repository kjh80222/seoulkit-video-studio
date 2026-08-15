import json

import pytest

from content_engine.cli.main import main
from content_engine.db.connection import get_connection


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_ENGINE_DB_PATH", str(tmp_path / "content_engine.db"))
    monkeypatch.setenv("CONTENT_ENGINE_PROJECTS_ROOT", str(tmp_path / "projects"))
    return tmp_path


def _create_project(capsys) -> str:
    main(["create", "topic", "--json"])
    return json.loads(capsys.readouterr().out)["content_package_id"]


def test_submit_stage2_input_build_no_payload_succeeds(capsys, _env):
    project = _create_project(capsys)

    exit_code = main(["submit", project, "stage2_input_build"])

    assert exit_code == 0
    assert "Created job" in capsys.readouterr().out


def test_submit_stage2_input_build_rejects_payload(capsys, _env, tmp_path):
    project = _create_project(capsys)
    payload_path = tmp_path / "payload.json"
    payload_path.write_text("{}")

    exit_code = main(["submit", project, "stage2_input_build", str(payload_path)])

    assert exit_code == 3


def test_submit_stage3_input_build_requires_payload(capsys, _env):
    project = _create_project(capsys)

    exit_code = main(["submit", project, "stage3_input_build"])

    assert exit_code == 3
    assert "requires a payload" in capsys.readouterr().out


def test_submit_stage3_input_build_with_valid_payload(capsys, _env, tmp_path):
    project = _create_project(capsys)
    payload = {"stage2_outputs": [
        {"shot": "1A", "beat": 1, "approved_keyframe_path": "keyframes/a.png",
         "story_function": "f", "continuity": "c"}
    ]}
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(payload))

    exit_code = main(["submit", project, "stage3_input_build", str(payload_path)])

    assert exit_code == 0


def test_submit_clip_manifest_build_with_valid_payload(capsys, _env, tmp_path):
    project = _create_project(capsys)
    payload = {"decisions": [
        {"shot": "1A", "usable_start_ms": 0, "usable_end_ms": 2000, "key_event_end_ms": 1000,
         "settle_start_ms": 1500, "camera_behavior": "locked-off", "qc_passed": True}
    ]}
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(payload))

    exit_code = main(["submit", project, "clip_manifest_build", str(payload_path)])

    assert exit_code == 0


def test_submit_edit_plan_build_with_valid_payload(capsys, _env, tmp_path):
    project = _create_project(capsys)
    payload = {
        "voice_input": {"timing_grade": "ESTIMATED", "audio_file": None, "total_duration_ms": 2000},
        "semantic_sync": [], "overlay_decisions": [], "sfx_decisions": [],
    }
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(payload))

    exit_code = main(["submit", project, "edit_plan_build", str(payload_path)])

    assert exit_code == 0


def test_submit_rejects_preview_render_job_type(capsys, _env):
    project = _create_project(capsys)

    exit_code = main(["submit", project, "preview_render"])

    assert exit_code != 0


def test_submit_rejects_unknown_project(capsys, _env):
    exit_code = main(["submit", "no-such-project", "stage2_input_build"])

    assert exit_code == 3


def test_submit_rejects_malformed_json_file(capsys, _env, tmp_path):
    project = _create_project(capsys)
    payload_path = tmp_path / "payload.json"
    payload_path.write_text("{ not valid json")

    exit_code = main(["submit", project, "stage3_input_build", str(payload_path)])

    assert exit_code == 3
    assert "could not read/parse" in capsys.readouterr().out


def test_submit_rejects_wrong_shape_payload(capsys, _env, tmp_path):
    project = _create_project(capsys)
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps({"not_the_right_key": []}))

    exit_code = main(["submit", project, "stage3_input_build", str(payload_path)])

    assert exit_code == 3
    assert "does not match expected shape" in capsys.readouterr().out


def test_submit_missing_payload_file_path(capsys, _env, tmp_path):
    project = _create_project(capsys)

    exit_code = main(["submit", project, "clip_manifest_build", str(tmp_path / "nope.json")])

    assert exit_code == 3

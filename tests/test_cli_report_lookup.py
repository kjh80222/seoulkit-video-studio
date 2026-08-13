import json
from pathlib import Path

from seoulkit_studio.cli.report_lookup import discover_reports, find_report, latest_report, log_path_for_version


def _write_report(path: Path, *, render_version: str, render_type: str = "preview") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"render_version": render_version, "render_type": render_type}))


def test_discover_reports_finds_files_across_all_three_directories(tmp_path):
    _write_report(tmp_path / "preview" / "preview_v001.render_report.json", render_version="v001")
    _write_report(tmp_path / "output" / "final_v002.render_report.json", render_version="v002", render_type="final")
    _write_report(tmp_path / "logs" / "render_v003.render_report.json", render_version="v003", render_type="final")

    found = discover_reports(tmp_path)

    assert {r.version for r in found} == {1, 2, 3}


def test_discover_reports_ignores_unrelated_files(tmp_path):
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "final_v001.mp4").write_bytes(b"")
    (tmp_path / "output" / "README.md").write_text("not a report")

    assert discover_reports(tmp_path) == []


def test_discover_reports_on_a_project_with_no_renders_yet(tmp_path):
    assert discover_reports(tmp_path) == []


def test_latest_report_picks_the_max_version_regardless_of_which_directory_holds_it(tmp_path):
    # Deliberately put the max version in logs/ (a failed/gated attempt),
    # not in preview/ or output/, so a bug that only checks the
    # success-shaped directories is actually caught here.
    _write_report(tmp_path / "preview" / "preview_v001.render_report.json", render_version="v001")
    _write_report(tmp_path / "output" / "final_v002.render_report.json", render_version="v002", render_type="final")
    _write_report(tmp_path / "logs" / "render_v005.render_report.json", render_version="v005", render_type="final")

    latest = latest_report(tmp_path)

    assert latest is not None
    assert latest.version == 5
    assert latest.report_path == tmp_path / "logs" / "render_v005.render_report.json"


def test_latest_report_is_none_for_an_empty_project(tmp_path):
    assert latest_report(tmp_path) is None


def test_find_report_locates_a_specific_version_wherever_it_actually_lives(tmp_path):
    _write_report(tmp_path / "preview" / "preview_v001.render_report.json", render_version="v001")
    _write_report(tmp_path / "output" / "final_v002.render_report.json", render_version="v002", render_type="final")

    found = find_report(tmp_path, 2)

    assert found is not None
    assert found.report_path == tmp_path / "output" / "final_v002.render_report.json"


def test_find_report_returns_none_for_a_version_that_was_never_written(tmp_path):
    _write_report(tmp_path / "preview" / "preview_v001.render_report.json", render_version="v001")

    assert find_report(tmp_path, 99) is None


def test_log_path_for_version_returns_the_path_when_the_log_file_exists(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "render_v001.log").write_text("ffmpeg ...\n")

    assert log_path_for_version(tmp_path, 1) == log_dir / "render_v001.log"


def test_log_path_for_version_returns_none_when_the_log_file_is_missing(tmp_path):
    assert log_path_for_version(tmp_path, 1) is None

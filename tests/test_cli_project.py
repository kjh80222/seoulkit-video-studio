from pathlib import Path

from seoulkit_studio.cli.project import resolve_project_paths


def test_default_paths_are_the_conventional_project_layout(tmp_path):
    paths = resolve_project_paths(tmp_path)

    assert paths.project_dir == tmp_path
    assert paths.edit_plan_path == tmp_path / "edit_plan.json"
    assert paths.clip_manifest_path == tmp_path / "clips" / "clip_manifest.json"


def test_default_clip_manifest_path_is_returned_even_when_the_file_does_not_exist(tmp_path):
    # No clips/ directory at all - resolve_project_paths() must not check
    # existence, only build the conventional Path.
    paths = resolve_project_paths(tmp_path)

    assert paths.clip_manifest_path == tmp_path / "clips" / "clip_manifest.json"
    assert not paths.clip_manifest_path.exists()


def test_clip_manifest_override_replaces_the_default(tmp_path):
    override = tmp_path / "elsewhere" / "manifest.json"

    paths = resolve_project_paths(tmp_path, clip_manifest_override=override)

    assert paths.clip_manifest_path == override
    assert paths.edit_plan_path == tmp_path / "edit_plan.json"

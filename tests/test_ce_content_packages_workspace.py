import pytest

from content_engine.content_packages.workspace import assemble_workspace


def test_assemble_workspace_creates_project_dir_and_clips(tmp_path):
    project_dir = tmp_path / "pkg-1"

    assemble_workspace(project_dir)

    assert project_dir.is_dir()
    assert (project_dir / "clips").is_dir()


def test_assemble_workspace_called_twice_is_a_no_op(tmp_path):
    project_dir = tmp_path / "pkg-1"

    assemble_workspace(project_dir)
    assemble_workspace(project_dir)  # must not raise

    assert project_dir.is_dir()
    assert (project_dir / "clips").is_dir()


def test_assemble_workspace_raises_when_project_dir_is_a_file(tmp_path):
    project_dir = tmp_path / "pkg-1"
    project_dir.write_text("not a directory")

    with pytest.raises(OSError):
        assemble_workspace(project_dir)


def test_assemble_workspace_raises_when_clips_is_a_file(tmp_path):
    project_dir = tmp_path / "pkg-1"
    project_dir.mkdir()
    (project_dir / "clips").write_text("not a directory")

    with pytest.raises(OSError):
        assemble_workspace(project_dir)


def test_assemble_workspace_never_touches_existing_files_inside_clips(tmp_path):
    project_dir = tmp_path / "pkg-1"
    assemble_workspace(project_dir)
    sentinel = project_dir / "clips" / "shot_1a_flow.mp4"
    sentinel.write_bytes(b"a human already dropped this Flow clip here")

    assemble_workspace(project_dir)  # re-run, e.g. as part of a retry

    assert sentinel.read_bytes() == b"a human already dropped this Flow clip here"

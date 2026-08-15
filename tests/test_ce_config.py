from content_engine.config import resolve_projects_root


def test_resolve_projects_root_default_is_absolute(monkeypatch):
    monkeypatch.delenv("CONTENT_ENGINE_PROJECTS_ROOT", raising=False)

    root = resolve_projects_root()

    assert root.is_absolute()
    assert root.name == "projects"
    assert "seoulkit-content-engine" in root.parts


def test_resolve_projects_root_absolute_override_is_returned_resolved(monkeypatch, tmp_path):
    override = tmp_path / "explicit-root"
    monkeypatch.setenv("CONTENT_ENGINE_PROJECTS_ROOT", str(override))

    root = resolve_projects_root()

    assert root.is_absolute()
    assert root == override.resolve()


def test_resolve_projects_root_relative_override_is_resolved_to_absolute(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CONTENT_ENGINE_PROJECTS_ROOT", "relative-projects")

    root = resolve_projects_root()

    assert root.is_absolute()
    assert root == (tmp_path / "relative-projects").resolve()


def test_resolve_projects_root_tilde_override_is_expanded_and_absolute(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CONTENT_ENGINE_PROJECTS_ROOT", "~/tilde-projects")

    root = resolve_projects_root()

    assert root.is_absolute()
    assert root == (tmp_path / "tilde-projects").resolve()
    assert "~" not in str(root)

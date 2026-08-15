"""Content Engine configuration - CE-1/CE-4a scope.

Where Content Engine's own runtime data lives: the SQLite database file
(CE-1) and, as of CE-4a, the root directory under which each
`ContentPackage`'s Video Studio workspace is created. No credentials, no
general settings system - those are explicitly out of scope (see the
Architecture Freeze Review: credential storage is deferred to CE-9a, when
it's actually needed).

`resolve_projects_root()` always returns an absolute, `~`-expanded path -
`CONTENT_ENGINE_PROJECTS_ROOT` may be set to a relative path, but
`content_packages.project_dir` is a stored DB value that must stay valid
regardless of the current working directory at read time, so the relative
form is resolved once, here, rather than carried as-is.
"""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "seoulkit-content-engine" / "content_engine.db"
_ENV_VAR = "CONTENT_ENGINE_DB_PATH"

_DEFAULT_PROJECTS_ROOT = Path.home() / ".local" / "share" / "seoulkit-content-engine" / "projects"
_PROJECTS_ROOT_ENV_VAR = "CONTENT_ENGINE_PROJECTS_ROOT"


def resolve_db_path() -> Path:
    override = os.environ.get(_ENV_VAR)
    return Path(override) if override else _DEFAULT_DB_PATH


def resolve_projects_root() -> Path:
    override = os.environ.get(_PROJECTS_ROOT_ENV_VAR)
    root = Path(override).expanduser() if override else _DEFAULT_PROJECTS_ROOT
    return root.resolve()

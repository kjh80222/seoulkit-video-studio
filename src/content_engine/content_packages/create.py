"""`ContentPackage` creation - CE-4a scope only.

`create_content_package()` is the single entry point that guarantees the
right order: DB row inserted and committed *before* the workspace is
assembled on disk. This ordering is deliberate, not incidental - a
`content_packages` row is the thing a caller can look up again later, so
if it exists first, a filesystem failure afterward still leaves a
recoverable state (the row's own `project_dir` is all `assemble_workspace()`
needs to finish the job on a retry, since it's idempotent). The reverse
order has no such recovery path: a directory created before the DB write
commits would be an orphan nothing can find again if the write then fails.

When workspace assembly does fail after the row is already committed, the
raw `OSError` from `assemble_workspace()` is deliberately not left to
propagate as-is - it's wrapped in `WorkspaceAssemblyError`, which carries
the already-persisted `content_package_id` and `project_dir` back to the
caller. This wrapping does not roll back or delete the DB row - the row
staying put *is* the recovery mechanism. A caller that catches this
exception has everything needed to retry: call
`assemble_workspace(Path(exc.project_dir))` again.

No `JobRepository`-style abstraction here, matching CE-1: this is one
function, not a class, and there is no corresponding `get_content_package()`
lookup - CE-4a's own tests read rows back with the same raw SQL any other
CE-1-era caller would use.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from content_engine.config import resolve_projects_root
from content_engine.content_packages.models import ContentPackage
from content_engine.content_packages.workspace import assemble_workspace


class WorkspaceAssemblyError(Exception):
    def __init__(self, content_package_id: str, project_dir: str, cause: OSError) -> None:
        self.content_package_id = content_package_id
        self.project_dir = project_dir
        self.cause = cause
        super().__init__(
            f"workspace assembly failed for content_package {content_package_id!r} at {project_dir!r}: {cause}"
        )


def create_content_package(conn: sqlite3.Connection, topic: str) -> ContentPackage:
    topic = topic.strip()
    if not topic:
        raise ValueError("topic must not be empty")

    package_id = str(uuid.uuid4())
    project_dir = resolve_projects_root() / package_id
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        "INSERT INTO content_packages (id, topic, project_dir, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (package_id, topic, str(project_dir), now, now),
    )
    conn.commit()

    try:
        assemble_workspace(project_dir)
    except OSError as exc:
        raise WorkspaceAssemblyError(package_id, str(project_dir), exc) from exc

    return ContentPackage(id=package_id, topic=topic, project_dir=str(project_dir), created_at=now, updated_at=now)

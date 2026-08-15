"""Project (`ContentPackage`) resolution by CLI-facing identifier - CE-11 scope only.

`content_package_id` is the sole CLI project identifier (CE-11 Architecture
Freeze Review): it is already `ContentPackage.id`'s own primary key, and
`project_dir` is derived from it 1:1 (`resolve_projects_root() /
content_package_id`, confirmed in `content_packages/create.py`) - no new
identifier is invented here, and `topic` is explicitly not unique
(confirmed by `test_create_content_package_same_topic_twice_yields_two_distinct_packages`)
so it is never used to look a project up.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


class ProjectNotFoundError(Exception):
    def __init__(self, content_package_id: str) -> None:
        self.content_package_id = content_package_id
        super().__init__(f"no project with id {content_package_id!r}")


@dataclass(frozen=True)
class ResolvedProject:
    content_package_id: str
    topic: str
    project_dir: str
    created_at: str


def resolve_project(conn: sqlite3.Connection, content_package_id: str) -> ResolvedProject:
    row = conn.execute(
        "SELECT id, topic, project_dir, created_at FROM content_packages WHERE id = ?",
        (content_package_id,),
    ).fetchone()
    if row is None:
        raise ProjectNotFoundError(content_package_id)
    return ResolvedProject(content_package_id=row[0], topic=row[1], project_dir=row[2], created_at=row[3])

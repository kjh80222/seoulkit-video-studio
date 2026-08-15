"""`ContentPackage` data model - CE-4a scope only.

Pure data shape, exactly like `jobs/models.py::Job` - no persistence
methods, no `status` field. `jobs.state` remains the sole source of truth
for execution state (CE-1 Architecture Freeze); `ContentPackage` carries
no derived/cached progress field of its own, since nothing consumes one
yet. If a whole-package progress view is ever needed, it gets computed by
joining `jobs` on `content_package_id` at read time, not stored here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ContentPackage:
    id: str
    topic: str
    project_dir: str
    created_at: str
    updated_at: str

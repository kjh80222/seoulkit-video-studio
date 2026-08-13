"""Project-folder path resolution (Phase 11).

Pure `Path` arithmetic, nothing else - no existence checking, no file
reads, no judgment. `clip_manifest_path` is always the conventional
default (`<project>/clips/clip_manifest.json`) unless `--clip-manifest`
overrides it, and it is always returned as a concrete `Path`, never
`None`: `execution/pipeline.py::evaluate_plan()` and both
`render_*_and_report()` functions already treat a missing
`clip_manifest.json` as a `CLIP_MANIFEST_MISSING` warning (see
`execution/clip_manifest.py::check_clip_manifest_consistency()`), not an
error, so the CLI does not need - and must not add - its own existence
check or fallback-to-`None` logic here. The `clip_manifest_path=None`
call signature on those functions remains a direct-API-call convenience
for callers other than this CLI; nothing here relies on it or changes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class ProjectPaths:
    project_dir: Path
    edit_plan_path: Path
    clip_manifest_path: Path


def resolve_project_paths(project_dir: Path, clip_manifest_override: Path | None = None) -> ProjectPaths:
    return ProjectPaths(
        project_dir=project_dir,
        edit_plan_path=project_dir / "edit_plan.json",
        clip_manifest_path=clip_manifest_override
        if clip_manifest_override is not None
        else project_dir / "clips" / "clip_manifest.json",
    )

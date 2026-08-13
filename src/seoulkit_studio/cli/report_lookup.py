"""Read-only discovery of already-written Render Reports (Phase 11 `status`/`report`).

`status` and `report` must never call `evaluate_plan()` or any
`render_*`/`render_*_and_report()` function - they only read files that a
previous `preview`/`render` invocation already wrote via
`render/report.py::_run_and_report()`. That constraint rules out the
obvious-looking shortcut of reusing `render/report.py::next_render_version()`
to find "the latest version": that function answers a different question
("what's the next free slot to write"), and knowing a version number is
not enough to know where its report lives. `_run_and_report()` puts a
report in exactly one of `preview/`, `output/`, or `logs/` depending on
`render_type` (preview vs. final) *and* outcome (success vs.
gate-rejected/failed) - there is no formula for which, only the fact of
which file actually got written. So this module glob-scans all three
directories for `*.render_report.json` files and reads each file's own
already-recorded `render_version` field as ground truth, rather than
re-deriving a version number from a filename pattern or re-running any
version-assignment logic.

Version numbers are never reused (ch. 23, confirmed by
`render/report.py`'s own docstring and tests), so at most one report file
is expected per version across the three directories - `find_report()`
returns the first match it sees.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_REPORT_DIRS = ("preview", "output", "logs")


@dataclass
class DiscoveredReport:
    version: int
    report_path: Path
    data: dict


def discover_reports(project_dir: Path) -> list[DiscoveredReport]:
    found: list[DiscoveredReport] = []
    for subdir in _REPORT_DIRS:
        d = project_dir / subdir
        if not d.is_dir():
            continue
        for entry in sorted(d.glob("*.render_report.json")):
            data = json.loads(entry.read_text())
            version_str = data.get("render_version", "")
            if not version_str.startswith("v"):
                continue
            try:
                version = int(version_str[1:])
            except ValueError:
                continue
            found.append(DiscoveredReport(version=version, report_path=entry, data=data))
    return found


def latest_report(project_dir: Path) -> DiscoveredReport | None:
    reports = discover_reports(project_dir)
    if not reports:
        return None
    return max(reports, key=lambda r: r.version)


def find_report(project_dir: Path, version: int) -> DiscoveredReport | None:
    for r in discover_reports(project_dir):
        if r.version == version:
            return r
    return None


def log_path_for_version(project_dir: Path, version: int) -> Path | None:
    """`logs/render_v{NNN}.log` is always written by `_run_and_report()`
    regardless of outcome (`render/report.py`: "always written, success or
    failure"), so its path is a deterministic function of the version
    number alone, unlike the report path above - this is formatting a
    known, stable naming convention, not guessing a location. Still
    returns `None` rather than a dangling path if the file isn't actually
    there, so callers never report a log that doesn't exist.
    """
    path = project_dir / "logs" / f"render_v{version:03d}.log"
    return path if path.is_file() else None

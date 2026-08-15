"""Video Studio workspace assembly - CE-4a scope only.

`assemble_workspace()` knows only "given this path, make sure the minimum
directory skeleton exists" - it never touches the database, never
generates an id, and never knows about `ContentPackage`. It creates
exactly two directory nodes: `project_dir` itself (Video Studio's own
`cli/main.py` requires `project_dir.is_dir()` before running any command)
and `project_dir/clips` (the one place a human drops Google Flow output,
confirmed by reading the real example project - Video Studio itself never
creates this directory, only checks individual files under it).

Deliberately not created here: `edit_plan.json` (a Stage 1-4 artifact -
this repository has no code that computes one), `clips/clip_manifest.json`
(owned by Stage 3 QC per the real file's own header comment, read-only
for Stage 4/5), `clips/audio/*`, `preview/`, `output/`, `logs/`
(Video Studio creates these itself at render time via its own
`mkdir(parents=True, exist_ok=True)` calls in `render/report.py`), and
`seoulkit.project.json` (never read by any Video Studio code - confirmed
by grep, not part of the real contract).

Idempotent by construction: `mkdir(..., exist_ok=True)` silently no-ops
if the directory already exists, and never inspects or touches whatever
files a human has already placed inside `clips/` - there is no listing,
no clearing, no overwrite anywhere in this function. If either path is
occupied by something that isn't a directory, the standard library's own
`FileExistsError`/`NotADirectoryError` propagates unwrapped - that's a
corrupted-state signal a caller needs to see raw, not a normal outcome
worth a custom exception.
"""

from __future__ import annotations

from pathlib import Path


def assemble_workspace(project_dir: Path) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "clips").mkdir(exist_ok=True)

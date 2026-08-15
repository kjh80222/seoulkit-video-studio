"""Video Studio invocation wiring - CE-6 scope only.

CE-6 is the thinnest possible connector between Content Engine and CE-3's
already-complete adapter: `preflight_project()`/`render_project()` each do
exactly one thing - pick which of `run_preflight()`/`run_preview()`/
`run_render()` to call, and return whatever `AdapterResult` comes back,
unmodified.

Everything a naive "wiring" phase might be tempted to add here already
exists elsewhere and is deliberately not duplicated:

- `run_preview()`/`run_render()` already run `evaluate_plan()` internally
  and refuse via `RenderResult.gate_error` when the plan isn't ready
  (`render/report.py`, frozen) - so `render_project()` never calls
  `run_preflight()` first. Doing so would be a second, redundant
  evaluation of the same plan, not an extra safety net.
- CE-5's `check_asset_readiness()` is a narrower, human-facing "are the
  Flow clips here yet" signal, not a render precondition - CE-6 never
  calls it. A caller who wants that signal calls CE-5 itself.
- CE-3's `AdapterResult` already carries a complete failure taxonomy
  (`ok`/`category`/`exit_code`/`payload`/`stdout`/`stderr`). CE-6 defines
  no dataclass of its own, reformats nothing, and never converts
  `USAGE_ERROR`/`ADAPTER_INVOCATION_ERROR` into an exception - the caller
  gets the exact same `AdapterResult` `run_preflight()`/`run_preview()`/
  `run_render()` would have returned directly.
- Choosing `"preview"` vs `"final"` is never inferred from `effective_status`
  or anything else - the caller states it explicitly. A policy like
  "REVIEW_REQUIRED means auto-fall-back-to-preview" belongs to whatever
  calls CE-6 (a human today, CE-2B's worker later), not to this module.

The only thing CE-6 validates itself is `mode` - an invalid value raises
`ValueError` before any subprocess is started, since that's a caller
mistake in this module's own argument, not a Video Studio outcome.

CE-6 has no knowledge of the database, `JobState`, queues, or retries -
matching CE-3/CE-4a-4f's own precedent of staying a pure function library.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from content_engine.video_studio.adapter import AdapterResult, run_preflight, run_preview, run_render

_VALID_MODES = ("preview", "final")


def preflight_project(project_dir: Path) -> AdapterResult:
    return run_preflight(project_dir)


def render_project(project_dir: Path, mode: Literal["preview", "final"]) -> AdapterResult:
    if mode not in _VALID_MODES:
        raise ValueError(f"mode must be one of {_VALID_MODES!r}, got {mode!r}")
    if mode == "preview":
        return run_preview(project_dir)
    return run_render(project_dir)

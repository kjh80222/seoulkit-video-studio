"""Google Flow human clip readiness - CE-5 scope only.

CE-5 answers exactly one question: *can we determine the required Google
Flow clips, and are they all present?* Nothing more. It is not a
"Final Render asset readiness" layer - `Voice`/`BGM`/`SFX` assets are
each some other Stage's/future orchestration's responsibility (CE-5 does
not adopt ownership of them just because `run_preflight()` happens to
report on them too), and `clips/clip_manifest.json` remains Stage 3 QC's
unresolved responsibility (see `docs/content-engine-phase-plan.md`'s
CE-5 Architecture Freeze Review).

`check_asset_readiness()` is a thin re-interpretation of CE-3's
`run_preflight()` - it never touches the filesystem itself, never
re-validates anything Video Studio already validates, and never adds a
second, competing severity judgment on top of Video Studio's own. Two
outcomes are treated as "not ready", both because they both boil down to
"we cannot get the Flow clip a human still needs to supply":

- `payload["load_error"]` is set: `edit_plan.json` itself is missing or
  invalid, so there is no source of truth for which Flow clips are even
  required.
- A `MISSING_CLIP` issue is present: `edit_plan.json` is valid and names
  a `source_clip` that isn't on disk yet - the exact human-in-the-loop
  gap this phase exists to surface.

Every other preflight issue code (`MISSING_VOICE_ASSET`,
`MISSING_BGM_FILE`, `MISSING_SFX_FILE`, `CLIP_MANIFEST_MISSING`, and any
plan/QC-consistency code such as `CLIP_MANIFEST_MISMATCH`) is
deliberately excluded from `ready` - Video Studio's own preflight output
still reports them untouched, CE-5 simply has no opinion on them.

`run_preflight()` returning `ok=False` (a `USAGE_ERROR` or
`ADAPTER_INVOCATION_ERROR`) is not a "human hasn't supplied a clip yet"
outcome - it's CE-3/Video Studio itself failing to run, an infrastructure
problem this function does not swallow into `ready=False`. It re-raises
as a plain `RuntimeError` carrying `category`/`exit_code`/`stderr` for
diagnosis, matching CE-2A's precedent of using plain stdlib exceptions
rather than inventing a new exception class for a case this narrow.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from content_engine.video_studio.adapter import run_preflight

_ASSET_ISSUE_CODES = {
    "MISSING_CLIP",
}


@dataclass(frozen=True)
class AssetReadinessResult:
    ready: bool
    missing: list[str]


def check_asset_readiness(project_dir: Path) -> AssetReadinessResult:
    result = run_preflight(project_dir)

    if not result.ok:
        raise RuntimeError(
            f"preflight could not run: category={result.category}, "
            f"exit_code={result.exit_code}, stderr={result.stderr!r}"
        )

    payload = result.payload
    if payload.get("load_error"):
        return AssetReadinessResult(ready=False, missing=[f"edit_plan.json: {payload['load_error']}"])

    missing = [issue["detail"] for issue in payload["issues"] if issue["code"] in _ASSET_ISSUE_CODES]
    return AssetReadinessResult(ready=not missing, missing=missing)

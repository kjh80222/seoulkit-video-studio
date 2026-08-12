# SEOULKIT MINI Video Studio

The deterministic execution layer (Stage 5) for the SEOULKIT MINI pipeline:
topic → script (Stage 1) → keyframe image prompts (Stage 2) → motion prompts
(Stage 3) → Voice + `edit_plan.json` (Stage 4) → **rendered MP4 (Stage 5,
this repo)**.

Stage 5 makes no creative judgments. It reads `edit_plan.json` as the
Source of Truth and renders it deterministically via FFmpeg — the same
input always produces the same output.

## Status

Phase 0 only: `edit_plan.json` JSON Schema validation + Duration Invariant
validation (`src/seoulkit_studio/schema/`). See `docs/phase-plan.md` for the
full Phase 0-11 sequence this repo will follow.

## Layout

- `src/seoulkit_studio/schema/` — `edit_plan.json` JSON Schema + Duration
  Invariant validator (Phase 0).
- `tests/` — pytest suite with valid/invalid/invariant-violation fixtures.
- `examples/sample-project/` — a filled-in example of the per-video project
  folder structure Stage 5 expects, with a schema-valid `edit_plan.json`.

## Development

```
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

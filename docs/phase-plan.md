# Phase plan

Development sequence from the SEOULKIT Video Studio Technical Specification
v1.0, ch. 21. Each phase's "done" criterion is the spec's own.

| Phase | Scope | Done when | Status |
|---|---|---|---|
| 0 | `edit_plan.json` schema validator + Duration Invariant validator | Valid/invalid samples + invariant-violation samples all pass their expected result | ✅ Done |
| 1 | Pre-flight validator (file existence, time consistency, severity mapping) | issue → severity → effective_status conversion matches ch. 04-5 exactly | ✅ Done |
| 2 | PLAN STATUS / STUDIO EXECUTION RESULT separation | Original `edit_plan.json` file is provably unmodified even when it's malformed | Not started |
| 2.5 | `clip_manifest.json` cross-validation (spec ch. 24: "Stage 3 writes → Stage 4 consumes → Stage 5 reads-and-verifies", "READ FOR VALIDATION ≠ RECALCULATE") | `edit_plan.json` usable-range fields that disagree with Stage 3's recorded values in `clip_manifest.json` are flagged as a blocking issue; neither file is ever modified by this check | Not started |
| 3 | Single-clip trim + FFmpeg execution | One clip trims to the exact expected boundaries | Not started |
| 4 | Concat | Multiple segments concatenate correctly | Not started |
| 5 | Hold handling (`source_hold` = no filter / `settle_frame_hold` = `tpad`) | Explicit test that `hold_ms` is never double-applied | Not started |
| 6 | ASS subtitle generation + burn-in | Subtitle position/style actually burns into the render | Not started |
| 7 | Overlay rendering | Preset coordinates verified | Not started |
| 8 | Audio mix (adopted/selected only, ducking, loudness) | Unresolved SFX/BGM forces the mix into REVIEW_REQUIRED | Not started |
| 9 | Preview/Final split, hard gate | Final is refused while REVIEW_REQUIRED | Not started |
| 10 | Render report (records both plan_status and effective_status) | Report field completeness | Not started |
| 11 | CLI integration | End-to-end test | Not started |

Phase 0 and Phase 1 are implemented (`src/seoulkit_studio/schema/`,
`src/seoulkit_studio/preflight/`; 22/22 tests passing as of this update).
Phase 2 onward are not started and should not be assumed to work - do not
reimplement Phase 0/1 in a new session; extend from here.

## Known gaps

- Config loading is not implemented. `duration_tolerance_ms` (spec ch. 18)
  is a hardcoded Python default (`DEFAULT_DURATION_TOLERANCE_MS = 50` in
  `src/seoulkit_studio/schema/validator.py`), overridable per call but not
  read from any file. Loading the full ch. 18 config block (render,
  subtitle, overlay, audio, preflight, naming settings) as YAML is not
  assigned to any phase yet and should be picked up before it's needed
  (Phase 1's severity mapping already references this config block by
  hardcoded value, and Phase 8's ducking/loudness defaults will need it too).

- `clip_manifest.json` cross-validation is not implemented - **now formally
  scheduled as Phase 2.5** (see table above), between PLAN STATUS/STUDIO
  EXECUTION RESULT separation and clip trim. Phase 1's
  `check_time_consistency` only checks that `edit_plan.json` is internally
  consistent (clip bounds inside its own recorded usable range); it never
  opens `clip_manifest.json` to compare against Stage 3's actual observed
  values, so a Stage 4 mistake that writes a self-consistent but wrong
  usable range currently passes Pre-flight undetected. This must land
  before Phase 3 starts trusting `clip_in_ms`/`clip_out_ms` for real
  FFmpeg calls.

- The `info` severity tier (ch. 18 `severity_mapping`) is fully wired
  (`SEVERITY_MAPPING`, `severity_to_execution`, tests) but no Pre-flight
  check in Phase 1 currently emits an `info`-level issue - the spec's own
  ch. 04-6 example only shows `blocking`/`warning` issue codes. Not a bug,
  just worth knowing if a review goes looking for an `info` example and
  doesn't find one in `tests/test_preflight.py`'s issue-generating tests
  (only in the direct `severity_to_execution("info")` mapping test).

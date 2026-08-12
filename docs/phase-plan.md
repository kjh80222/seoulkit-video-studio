# Phase plan

Development sequence from the SEOULKIT Video Studio Technical Specification
v1.0, ch. 21. Each phase's "done" criterion is the spec's own.

| Phase | Scope | Done when |
|---|---|---|
| 0 | `edit_plan.json` schema validator + Duration Invariant validator | Valid/invalid samples + invariant-violation samples all pass their expected result |
| 1 | Pre-flight validator (file existence, time consistency, severity mapping) | issue → severity → effective_status conversion matches ch. 04-5 exactly |
| 2 | PLAN STATUS / STUDIO EXECUTION RESULT separation | Original `edit_plan.json` file is provably unmodified even when it's malformed |
| 3 | Single-clip trim + FFmpeg execution | One clip trims to the exact expected boundaries |
| 4 | Concat | Multiple segments concatenate correctly |
| 5 | Hold handling (`source_hold` = no filter / `settle_frame_hold` = `tpad`) | Explicit test that `hold_ms` is never double-applied |
| 6 | ASS subtitle generation + burn-in | Subtitle position/style actually burns into the render |
| 7 | Overlay rendering | Preset coordinates verified |
| 8 | Audio mix (adopted/selected only, ducking, loudness) | Unresolved SFX/BGM forces the mix into REVIEW_REQUIRED |
| 9 | Preview/Final split, hard gate | Final is refused while REVIEW_REQUIRED |
| 10 | Render report (records both plan_status and effective_status) | Report field completeness |
| 11 | CLI integration | End-to-end test |

Only Phase 0 is implemented so far. Later phases are not started and should
not be assumed to work.

## Known gaps

- Config loading is not implemented. `duration_tolerance_ms` (spec ch. 18)
  is a hardcoded Python default (`DEFAULT_DURATION_TOLERANCE_MS = 50` in
  `src/seoulkit_studio/schema/validator.py`), overridable per call but not
  read from any file. Loading the full ch. 18 config block (render,
  subtitle, overlay, audio, preflight, naming settings) as YAML is not
  assigned to any phase yet and should be picked up before it's needed
  (Phase 1's severity mapping is the first thing that references it).

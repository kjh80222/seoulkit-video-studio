# Phase plan

Development sequence from the SEOULKIT Video Studio Technical Specification
v1.0, ch. 21. Each phase's "done" criterion is the spec's own.

| Phase | Scope | Done when | Status |
|---|---|---|---|
| 0 | `edit_plan.json` schema validator + Duration Invariant validator | Valid/invalid samples + invariant-violation samples all pass their expected result | ✅ Done |
| 1 | Pre-flight validator (file existence, time consistency, severity mapping) | issue → severity → effective_status conversion matches ch. 04-5 exactly | ✅ Done |
| 2 | PLAN STATUS / STUDIO EXECUTION RESULT separation | Original `edit_plan.json` file is provably unmodified even when it's malformed | ✅ Done |
| 2.5 | `clip_manifest.json` cross-validation (spec ch. 24: "Stage 3 writes → Stage 4 consumes → Stage 5 reads-and-verifies", "READ FOR VALIDATION ≠ RECALCULATE") | `edit_plan.json` usable-range fields that disagree with Stage 3's recorded values in `clip_manifest.json` are flagged as a blocking issue; neither file is ever modified by this check | ✅ Done |
| 3 | Single-clip trim + FFmpeg execution | One clip trims to the exact expected boundaries | Not started |
| 4 | Concat | Multiple segments concatenate correctly | Not started |
| 5 | Hold handling (`source_hold` = no filter / `settle_frame_hold` = `tpad`) | Explicit test that `hold_ms` is never double-applied | Not started |
| 6 | ASS subtitle generation + burn-in | Subtitle position/style actually burns into the render | Not started |
| 7 | Overlay rendering | Preset coordinates verified | Not started |
| 8 | Audio mix (adopted/selected only, ducking, loudness) | Unresolved SFX/BGM forces the mix into REVIEW_REQUIRED | Not started |
| 9 | Preview/Final split, hard gate | Final is refused while REVIEW_REQUIRED | Not started |
| 10 | Render report (records both plan_status and effective_status) | Report field completeness | Not started |
| 11 | CLI integration | End-to-end test | Not started |

Phase 0 through Phase 2.5 are implemented (`src/seoulkit_studio/schema/`,
`src/seoulkit_studio/preflight/`, `src/seoulkit_studio/execution/`
including `execution/clip_manifest.py`; 62/62 tests passing as of this
update). Phase 3 onward are not started and should not be assumed to work -
do not reimplement Phase 0/1/2/2.5 in a new session; extend from here.

## Known gaps

- Config loading is not implemented. `duration_tolerance_ms` (spec ch. 18)
  is a hardcoded Python default (`DEFAULT_DURATION_TOLERANCE_MS = 50` in
  `src/seoulkit_studio/schema/validator.py`), overridable per call but not
  read from any file. Loading the full ch. 18 config block (render,
  subtitle, overlay, audio, preflight, naming settings) as YAML is not
  assigned to any phase yet and should be picked up before it's needed
  (Phase 1's severity mapping already references this config block by
  hardcoded value, and Phase 8's ducking/loudness defaults will need it too).

- ~~`clip_manifest.json` cross-validation~~ **Resolved in Phase 2.5.**
  `src/seoulkit_studio/execution/clip_manifest.py` now compares
  `usable_start_ms`/`usable_end_ms`/`key_event_end_ms`/`settle_start_ms`
  between `edit_plan.json` and `clip_manifest.json` per shot. The stale-flag
  risk noted here previously was resolved by changing
  `compute_execution_result()` to take a raw `list[PreflightIssue]` instead
  of trusting a pre-computed `PreflightResult.preflight_result` flag
  (option (b) from the design discussion) - PASS/FAIL is now always derived
  fresh from whatever issues are passed in, so a second independent issue
  source (clip_manifest cross-validation) can be merged in without any
  stale-state risk. `tests/test_execution.py::test_clip_manifest_mismatch_downgrades_a_phase1_pass_to_blocked`
  is the regression test proving this: a plan that Phase 1 alone would call
  PASS correctly comes back BLOCKED once a clip_manifest mismatch is added.

- ~~`check_clip_manifest_consistency()` silently let a duplicate `shot` in
  `clip_manifest.json` resolve via last-wins~~ **Fixed, same session as
  Phase 2.5.** The risk wasn't the duplicate itself - it was that the
  wrong (or the right, by chance) entry would be picked with zero trace,
  meaning a genuinely mismatched `edit_plan.json` could pass Pre-flight
  undetected purely because a later manifest entry happened to agree with
  it. Occurrence counting now runs as its own pass over `clips[]` *before*
  the lookup dict is built, so a duplicate is reported
  (`CLIP_MANIFEST_DUPLICATE_SHOT`, blocking) regardless of which entry a
  naive dict build would have picked -
  `tests/test_clip_manifest.py::test_duplicate_shot_in_manifest_is_blocking`
  specifically constructs the "duplicate happens to match" case and
  confirms it's still reported. Once a shot is flagged, no further
  `CLIP_MANIFEST_MISMATCH`/`SHOT_MISSING` check runs for it, to avoid
  layering a second, possibly-misleading verdict on an already-ambiguous
  entry.

- `compute_effective_status()` (Phase 2) returns only the `PlanStatus`
  string (`READY`/`REVIEW_REQUIRED`/`NOT_READY`) - there is no function that
  takes that *final*, blended status and returns Preview/Final `bool`s the
  way Phase 1's `severity_to_execution()` does for a raw severity.
  `tests/test_execution.py` now proves the two mappings agree row-for-row
  (`NOT_READY` ↔ `blocking`, `REVIEW_REQUIRED` ↔ `warning`, `READY` ↔
  `info`/none - the last pair via `None` vs. `"READY"`, same meaning,
  different representation), so a future edit to either one that breaks
  the correspondence will fail a test instead of silently drifting. Still
  true, though: no code converts a `compute_effective_status()` result
  into an actual Preview/Final gate today. Not urgent for Phase 2.5, but
  Phase 9 (Preview/Final split, hard gate) will need exactly this - add a
  small `effective_status -> ExecutionPermission` mapping there, or reuse
  `severity_to_execution` if by then it turns out to be the same function
  in disguise.

- The `info` severity tier (ch. 18 `severity_mapping`) is fully wired
  (`SEVERITY_MAPPING`, `severity_to_execution`, tests) but no Pre-flight
  check in Phase 1 currently emits an `info`-level issue - the spec's own
  ch. 04-6 example only shows `blocking`/`warning` issue codes. Not a bug,
  just worth knowing if a review goes looking for an `info` example and
  doesn't find one in `tests/test_preflight.py`'s issue-generating tests
  (only in the direct `severity_to_execution("info")` mapping test).

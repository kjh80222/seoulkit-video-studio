# Phase plan

Development sequence from the SEOULKIT Video Studio Technical Specification
v1.0, ch. 21. Each phase's "done" criterion is the spec's own.

| Phase | Scope | Done when | Status |
|---|---|---|---|
| 0 | `edit_plan.json` schema validator + Duration Invariant validator | Valid/invalid samples + invariant-violation samples all pass their expected result | ✅ Done |
| 1 | Pre-flight validator (file existence, time consistency, severity mapping) | issue → severity → effective_status conversion matches ch. 04-5 exactly | ✅ Done |
| 2 | PLAN STATUS / STUDIO EXECUTION RESULT separation | Original `edit_plan.json` file is provably unmodified even when it's malformed | ✅ Done |
| 2.5 | `clip_manifest.json` cross-validation (spec ch. 24: "Stage 3 writes → Stage 4 consumes → Stage 5 reads-and-verifies", "READ FOR VALIDATION ≠ RECALCULATE") | `edit_plan.json` usable-range fields that disagree with Stage 3's recorded values in `clip_manifest.json` are flagged as a blocking issue; neither file is ever modified by this check | ✅ Done |
| 3 | Single-clip trim + FFmpeg execution | One clip trims to the exact expected boundaries | ✅ Done |
| 4 | Concat | Multiple segments concatenate correctly | ✅ Done |
| 5 | Hold handling (`source_hold` = no filter / `settle_frame_hold` = `tpad`) | Explicit test that `hold_ms` is never double-applied | ✅ Done |
| 6 | ASS subtitle generation + burn-in | Subtitle position/style actually burns into the render | Not started |
| 7 | Overlay rendering | Preset coordinates verified | Not started |
| 8 | Audio mix (adopted/selected only, ducking, loudness) | Unresolved SFX/BGM forces the mix into REVIEW_REQUIRED | Not started |
| 9 | Preview/Final split, hard gate | Final is refused while REVIEW_REQUIRED | Not started |
| 10 | Render report (records both plan_status and effective_status) | Report field completeness | Not started |
| 11 | CLI integration | End-to-end test | Not started |

Phase 0 through Phase 5 are implemented (`src/seoulkit_studio/schema/`,
`src/seoulkit_studio/preflight/`, `src/seoulkit_studio/execution/`
including `execution/clip_manifest.py`, `src/seoulkit_studio/render/`
including `render/time_format.py`, `render/trim.py`, `render/concat.py`,
and `render/hold.py`; 152/152 tests passing as of this update, with 14 of
those requiring a system `ffmpeg`/`ffprobe` install and auto-skipping
when absent). Phase 6 onward are not started and should not be assumed to
work - do not reimplement Phase 0/1/2/2.5/3/4/5 in a new session; extend
from here.

Phase 3 introduces the project's first external system dependency:
`ffmpeg`/`ffprobe` must be installed to run the full test suite (not just
to use the library). `tests/test_trim.py`'s real-execution tests are
guarded with `@pytest.mark.skipif` and skip cleanly if missing; no test
fixture video is committed to the repo - `tests/conftest.py`'s
`make_synthetic_video` fixture generates one on demand via FFmpeg's
`lavfi` synthetic source (`testsrc`/`sine`), so nothing binary needs to
go through this session's GitHub-web-UI text-paste upload workflow.

## Known gaps

- `render/hold.py::hold_clip()` cannot prevent being *called twice* on the
  same clip - it is a stateless function with no memory of prior calls, so
  "`hold_ms` is never double-applied" is not something this module can
  enforce on its own. What it does do: reject `hold_ms < 1` immediately
  with a `ValueError` rather than silently no-opping, so a caller mistake
  that would invoke `hold_clip()` for a `none`/`source_hold` segment
  (schema-guaranteed `hold_ms == 0`) fails loudly instead of quietly
  producing a wrong render. Real double-application prevention - deciding
  *whether* to call `hold_clip()` at all for a given segment - is the
  responsibility of the same not-yet-built assembly layer already named in
  the `concat_clips()` ordering-contract gap below (likely Phase 9-11).
  `tests/test_hold.py::test_calling_hold_clip_twice_visibly_doubles_the_extension`
  doesn't prove prevention; it proves *detection* - two real, unmocked
  `hold_clip()` calls chained together measurably produce `2000 + 500 + 500`
  instead of `2000 + 500`, giving that future assembly layer a concrete
  real-measurement pattern to build its own regression test against, the
  same way Phase 4's ordering test did for `concat_clips()`.

- `preflight/validator.py` did not check the ch. 18
  `hold.settle_frame_hold.max_ms` cap (default 1500ms, per-segment) until
  Phase 5 planning surfaced this - a false assumption ("Phase 1 already
  enforces this") was made out loud, checked against the actual code
  (`grep hold preflight/validator.py` turned up nothing), and found to be
  wrong. Fixed same session, before Phase 5 itself was implemented, same
  reasoning as the Phase 2.5 duplicate-shot fix: an unenforced `hold_ms`
  was a low-stakes gap while nothing consumed it, but Phase 5 was about to
  start generating real frames from that exact unchecked value, raising an
  unbounded `hold_ms` from "a validation gap" to "a render that silently
  takes as long / as much disk as whatever number happens to be in
  `edit_plan.json`." `check_time_consistency()` now takes a
  `max_settle_frame_hold_ms` parameter (default
  `DEFAULT_MAX_SETTLE_FRAME_HOLD_MS = 1500`) and emits
  `MAX_HOLD_MS_EXCEEDED` (blocking) for any `settle_frame_hold` segment
  over it. `tests/test_preflight.py`'s
  `test_hold_ms_exceeding_default_max_is_blocking`,
  `test_hold_ms_within_default_max_is_allowed`, and
  `test_max_hold_ms_is_configurable_per_call` cover it.

- `render/concat.py::concat_clips()` does not sort `clip_paths` - it joins
  them in exactly the order given. Determining beat/shot ascending order
  (ch. 10) requires reading `edit_plan.json`, which `concat_clips()`
  deliberately has no knowledge of (same layering as `trim_clip()` never
  recomputing `clip_in_ms`). This means **the assembly layer that turns
  `edit_plan.json`'s `segments[]` into an ordered `list[Path]` of
  Phase-3-trimmed files does not exist yet**, and until it does, nothing
  actually enforces the ordering contract end-to-end - only
  `concat_clips()`'s own unit test
  (`tests/test_concat.py::test_concat_preserves_the_given_order_not_sorted`)
  proves the function itself honors it. Whichever phase first builds that
  assembly step (likely Phase 9-11, when a real multi-segment pipeline run
  gets wired up) must pass an already-sorted list, and should be reviewed
  specifically for that.

  Worth remembering when that assembly layer gets built: this same test
  demonstrated that a `sorted(clip_paths)` reorder is a *silent* bug -
  FFmpeg raised no error and produced a fully valid, fully wrong-order
  video. That's different from two other bugs deliberately injected during
  Phase 3/4 development (writing over the source clip's own path, and
  mapping the concat filter's unconnected output), both of which FFmpeg
  itself refused outright ("cannot edit existing files in-place",
  "unconnected output"). Ordering mistakes get no such safety net from
  FFmpeg - only a real-content test (here, distinguishable colored clips
  sampled by actual pixel value, not just duration) catches them. Phase 5
  (`tpad`, actually manipulating frames) should assume the same: FFmpeg
  will not save it from a logic error, only a test that inspects real
  output content will.

- Phase 3's `render/trim.py::trim_clip()` returns a `TrimResult` carrying
  the exact `command` (list[str]) and `stderr`/`stdout` it produced, but
  nothing writes that to `logs/render_v{NNN}.log` yet (ch. 09: "모든
  FFmpeg 호출은 로그에 원본 커맨드를 기록한다"). This was a deliberate
  scope boundary, not an oversight - Phase 3 only trims one clip and
  reports what it did; assembling the shared Preview/Final render
  sequence log across every FFmpeg call in the pipeline needs the fuller
  orchestration that later phases (Concat, Audio Mix, Render Report) will
  add. Whichever phase first wires up a real multi-step render sequence
  should be the one that starts writing `TrimResult.command`/`.stderr`
  into an actual log file.

- `trim_clip()` always passes `-an` to FFmpeg, discarding whatever audio
  the source clip itself carries (if any). This isn't a gap so much as a
  design decision worth recording so a future phase doesn't "fix" it by
  accident: ch. 01's architecture diagram draws "Clip Engine
  (trim/hold/concat)" and "Audio Mixer (adopted/selected assets only)" as
  separate boxes that only meet at pipeline step 6, and ch. 12 names
  exactly three audio sources for the final mix - Voice (always
  authoritative), adopted SFX, selected BGM - with no path for a source
  clip's own embedded audio to reach the output. `tests/test_trim.py::test_trim_drops_audio_even_when_source_has_audio`
  generates a synthetic clip that *does* carry audio and asserts the
  trimmed output has none, specifically so a later regression (e.g.
  someone removing `-an` while wiring up Concat) fails loudly instead of
  quietly leaking an unused audio stream through the pipeline.

  This decision was made without prior plan approval (unlike every other
  Phase 3 choice), so it was revisited and confirmed after the fact:
  `clips/*.mp4` is Stage 3's authoritative, read-only-for-Stage-5 output
  (ch. 02) - if a future phase ever did need the source clip's original
  audio, it's still sitting untouched on disk, because `-an` only ever
  affects the *trimmed output* `trim_clip()` writes, never the source it
  reads from. `tests/test_trim.py::test_trim_never_modifies_the_source_clip`
  proves this the same way Phase 2/2.5 proved their own immutability
  guarantees - SHA-256 hash of the source file, before vs. after. Trying
  to fake a failure here (swapping the command's final argument from
  `output_path` to `source_path`, simulating an input/output mixup) didn't
  even get as far as a hash mismatch: FFmpeg itself refused with "cannot
  edit existing files in-place" and the trim failed outright, which the
  test still caught via `result.ok`. So there are two independent guards
  against this bug class today, not one.

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

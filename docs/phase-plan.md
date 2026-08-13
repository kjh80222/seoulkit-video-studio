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
| 6 | ASS subtitle generation + burn-in | Subtitle position/style actually burns into the render | ✅ Done (automated tests + one manual visual QC pass, see Known gaps) |
| 7 | Overlay rendering | Preset coordinates verified | ✅ Done (automated tests + one manual visual QC pass, see Known gaps) |
| 8 | Audio mix (adopted/selected only, ducking, loudness) | Unresolved SFX/BGM forces the mix into REVIEW_REQUIRED | ✅ Done (automated tests + real dB/LUFS measurement, see Known gaps) |
| 9 | Preview/Final split, hard gate | Final is refused while REVIEW_REQUIRED | ✅ Done |
| 10 | Render report (records both plan_status and effective_status) | Report field completeness | ✅ Done |
| 11 | CLI integration | End-to-end test | Not started |

Phase 0 through Phase 10 are implemented (`src/seoulkit_studio/schema/`,
`src/seoulkit_studio/preflight/`, `src/seoulkit_studio/execution/`
including `execution/clip_manifest.py`, `src/seoulkit_studio/render/`
including `render/time_format.py`, `render/trim.py`, `render/concat.py`,
`render/hold.py`, `render/subtitle.py`, `render/fonts.py`,
`render/overlay.py`, `render/audio_mix.py`, `render/encode.py`,
`render/pipeline.py`, and `render/report.py`; 325/325 tests passing as of
this update, with the ffmpeg/ffprobe-requiring subset auto-skipping when
those tools are absent). Phase 11 onward is not started and should not be
assumed to work - do not reimplement Phase 0/1/2/2.5/3/4/5/6/7/8/9/10 in a
new session; extend from here.

**Phase 11 is explicitly not started, on the user's direct instruction.**
Before it begins, a separate Phase 9 hotfix is planned (see Known gaps:
`mux_and_encode()` can leave a partial file at the real output path on a
mid-encode failure) - that hotfix, plus a full regression run, comes
between Phase 10 and Phase 11, not folded into either.

Phase 10 (`render/report.py`) implements the Stage 5 spec ch. 17 Render
Report and owns version-numbered output path assignment
(`preview_v{NNN}.mp4`/`final_v{NNN}.{mp4,ass,srt}`), both deferred since
Phase 9. Two decisions worth recording because they came from a second
review pass, not the first plan:
- **Report location is decided by `RenderResult.ok`, not
  `studio_execution_result`.** A Final attempt can have
  `studio_execution_result="PASS"` (nothing blocking) while
  `effective_status="REVIEW_REQUIRED"` still correctly refuses it (ch. 05)
  - using `studio_execution_result` to decide where the report goes would
  have tried to write a successful-looking report (and QC the
  never-created output file) for an attempt that produced nothing.
  Confirmed with a red/green demo: switching the location check to
  `studio_execution_result == "PASS"` made a gate-rejected attempt crash
  with a real `ffprobe` `CalledProcessError` trying to measure a file that
  was never rendered.
- **`duration_invariant_post_render` measures the real rendered file, not
  the input JSON a second time.** `schema.validate_duration_invariant()`
  only checks `edit_plan.json`'s own arithmetic - re-running it after
  render would prove nothing about what FFmpeg actually produced. Instead
  this QC check sums `schema.segment_expected_duration_ms()` (a helper
  extracted from that same validator, pure refactor, no behavior change)
  across all segments and compares the sum to the real muxed output's
  `ffprobe`-measured duration. This is aggregate-level, not
  per-segment-attributable, and the report's `detail` field says so
  explicitly - see Known gaps for why a true per-segment version isn't
  implemented yet.

`render_v{NNN}.log` (Known gap since Phase 3: "nothing writes FFmpeg
commands to a log file") is also closed here - one line per FFmpeg command
actually executed, written for every attempt regardless of outcome.

Between Phase 9 and Phase 10, `execution/clip_manifest.py` gained a
second check, `check_sfx_contract_resolution()` - not a numbered phase of
its own, since it doesn't add a pipeline stage, it closes a Known gap
left open since Phase 8 (see Known gaps for the full writeup: Stage 5 can
now detect a silently-dropped SFX candidate via an opt-in
`clip_manifest.json` field, instead of relying entirely on Stage 4 having
remembered to write a `warnings[]` entry).

Phase 9 is this project's first module that turns an `edit_plan.json`
into an actual end-to-end `.mp4` - `render/pipeline.py`'s
`render_preview()`/`render_final()` wire every prior phase's primitive
(trim, hold, concat, overlay, subtitle burn-in, audio mix, and the new
`render/encode.py::mux_and_encode()` for the final mux+resolution/CRF
encode) into the ch. 09 order (trim → hold, per segment → concat →
overlay → subtitle → audio mix → mux+encode), with the ch. 05 execution
matrix enforced as a hard gate: `render_preview()` refuses only
`NOT_READY`, `render_final()` refuses anything but `READY`, and the gate
check is the literal first thing either function does - before any temp
directory, file write, or FFmpeg invocation - by calling
`execution/pipeline.py::evaluate_plan()` and never recomputing
`effective_status` itself. Verified with a real end-to-end render from a
single hand-built REVIEW_REQUIRED `edit_plan.json` (2 segments, one
`giant_number` overlay, English + Korean subtitles, no SFX/BGM): Preview
succeeded (720x1280, "PREVIEW" watermark + overlay + both subtitles all
visible in captured frames, no tofu boxes), Final was refused
(`gate_error="review_required"`, no `final.mp4`/`.ass`/`.srt` written at
all) against the exact same project. Two red/green demos beyond the
normal per-module TDD cycle specifically targeted this phase's two new
risks: (1) disabling the entire gate check to confirm a real
`final.mp4` gets produced from a REVIEW_REQUIRED plan when the gate is
bypassed - proving the hard gate is load-bearing, not decorative; (2)
skipping the hold step to confirm `hold_clip()` is actually invoked
per-segment before concat when wired into the real orchestrator (final
duration stayed at 4000ms instead of the expected 5000ms with the bug
injected), not just correct in `hold.py`'s own isolated tests.

Phase 8's "done" criterion ("unresolved SFX/BGM forces REVIEW_REQUIRED")
turned out to already be answered by the schema's own `warnings[]` field
(see Known gaps) rather than needing new SFX/BGM-specific logic -
`preflight/validator.py::check_declared_warnings()` folds any declared
`warnings[]` entry into the existing issue list generically, the same way
`MAX_HOLD_MS_EXCEEDED` was added in Phase 5. Phase 8 also introduced this
project's first *audio* real-content verification, parallel to how Phase
6/7 required a human to look at an actual rendered frame rather than trust
automated tests alone: automated tests measure real dB levels (FFmpeg's
own `volumedetect`/`ebur128` filters - never mocked, never inferred from
duration or file size), and a sample render's numbers were reviewed
directly (output duration, final integrated LUFS, SFX presence, BGM
ducking direction) rather than taken on faith from the code.

Phase 3 introduces the project's first external system dependency:
`ffmpeg`/`ffprobe` must be installed to run the full test suite (not just
to use the library). `tests/test_trim.py`'s real-execution tests are
guarded with `@pytest.mark.skipif` and skip cleanly if missing; no test
fixture video is committed to the repo - `tests/conftest.py`'s
`make_synthetic_video` fixture generates one on demand via FFmpeg's
`lavfi` synthetic source (`testsrc`/`sine`), so nothing binary needs to
go through this session's GitHub-web-UI text-paste upload workflow.

## Known gaps

- **`render/pipeline.py::_render()`'s final `mux_and_encode()` call writes
  directly to the caller-supplied real output path, not a temp path -
  found during Phase 10 planning, not fixed there.** Every stage before
  the last one writes into `_render()`'s own `tempfile.TemporaryDirectory()`;
  the very last FFmpeg call (mux + resolution/CRF encode) is the one
  exception, since its `output_path` argument is whatever
  `render/report.py` (or any caller) decided the real final destination
  is. If that specific FFmpeg invocation fails partway through, FFmpeg can
  leave a partial or corrupt file sitting at that real path - directly
  contradicting ch. 17/23's "부분 산출물은 output/ 에 두지 않는다"
  principle. Deliberately **not fixed in Phase 10**: doing so from inside
  the new Render Report module would quietly patch a Phase 9 defect
  without it ever being visible as one, and Phase 9 is supposed to be
  frozen, already-approved code. `render/report.py` correctly writes
  `output_file: null` in this case regardless of whether a stray file
  exists on disk - the report doesn't claim success it didn't have. A
  dedicated Phase 9 hotfix (have `mux_and_encode()` target a temp path and
  only move it into place on success) is planned before Phase 11 starts,
  followed by a full regression run.

- **A true per-segment `duration_invariant_post_render` QC check is not
  implemented - only an aggregate-level one.** `render/pipeline.py::_render()`
  discards every intermediate trim/hold output inside its own
  `TemporaryDirectory()` before returning; `render/report.py` calls
  `render_preview()`/`render_final()` from the outside, so by the time
  either returns there is nothing left to `ffprobe` per segment - only the
  final muxed file survives. The QC check implemented in Phase 10 sums
  `schema.segment_expected_duration_ms()` across all segments and compares
  that total to the real output's measured duration - genuinely
  `ffprobe`-based, not a second call to `validate_duration_invariant()`
  against the input JSON, but it can't attribute a mismatch to one
  specific segment (two segments' errors could cancel out and still read
  PASS). A true per-segment version would need Phase 9's own
  `_run_clip_stage()` to measure each trim/hold output with `ffprobe`
  before its temp directory is cleaned up and expose that measurement on
  `RenderResult` - a real behavior change to already-approved Phase 9
  code, proposed but not implemented here.

- **Preview watermark's exact implementation (text/position/opacity/size)
  is this phase's own choice, not spec.** ch. 18 gives only a boolean
  (`render.preview.watermark: true`) with no detail at all.
  `render/encode.py::mux_and_encode()`'s defaults - text `"PREVIEW"`,
  centered, white at alpha 0.4, font size `h*0.05` - were picked and
  approved during Phase 9 planning as reasonable placeholders. Revisit if
  a real requirement (exact wording, corner placement, opacity spec,
  etc.) ever surfaces.

- ~~Version-numbered output filenames (`final_v{NNN}.mp4`,
  `preview_v{NNN}.mp4`, and the same for `.ass`/`.srt`) are out of scope
  for Phase 9~~ **Resolved in Phase 10.** `render/report.py::next_render_version()`
  scans `preview/`, `output/`, `logs/` for existing `_v{NNN}.` filenames and
  returns max+1 - no separate counter state file, so a failed attempt
  (report-only, in `logs/`) still consumes a number and it's never reused
  (ch. 23). `render_preview_and_report()`/`render_final_and_report()` own
  building the actual paths and calling Phase 9's `render_preview()`/
  `render_final()` with them.

- ~~`RenderResult.stage_results` collects every stage's `command`/
  `stdout`/`stderr`/`error`, but nothing writes it to
  `logs/render_v{NNN}.log` yet~~ **Resolved in Phase 10.**
  `render/report.py`'s `_run_and_report()` writes `logs/render_v{NNN}.log`
  on every attempt (success or failure) - one line per FFmpeg command
  actually executed, sourced from the same `stage_results[].command` list
  that feeds the JSON report's `ffmpeg_commands[]`.

- ~~`preflight/validator.py::check_declared_warnings()` relies on a
  convention, not an enforced contract`~~ **Resolved for SFX, still open
  for BGM/Voice.** `execution/clip_manifest.py::check_sfx_contract_resolution()`
  (added after Phase 9) closes the specific silently-dropped-candidate
  case this gap was about, by giving Stage 5 an independent record of
  which shots actually had an SFX candidate - `clip_manifest.json`'s
  `optional_source_audio` field, gated behind an opt-in top-level
  `sfx_contract_version` key so pre-existing manifests aren't judged
  against a contract their writer never adopted. Two new issue codes:
  `SFX_UNRESOLVED_CANDIDATE` (warning, matches Stage 5 spec ch. 04-6's own
  example) when an `available: true` candidate never shows up in
  `sfx_source.clips[]`, and `SFX_CONTRACT_FIELD_MISSING` (blocking, same
  severity as `CLIP_MANIFEST_SHOT_MISSING` - both mean "this shot cannot
  be verified at all") when `sfx_contract_version` is declared but a
  specific clip entry omits `optional_source_audio` anyway - a broken
  promise, not a soft "still deciding" signal. Proven with two red/green
  demos: disabling the legacy-manifest skip broke an *unrelated* existing
  Phase 2.5 test (`test_evaluate_plan_never_modifies_edit_plan_or_clip_manifest`,
  PASS -> BLOCKED) - concrete proof that judging old manifests against
  the new contract would have broken pre-existing projects, not just a
  theoretical risk; disabling the unresolved-candidate detection let a
  plan with a real dropped SFX candidate report `effective_status="READY"`
  instead of `REVIEW_REQUIRED` - the exact failure mode this gap
  described, reproduced and then closed.

  **What's still open:** this only covers SFX. `warnings[]` remains the
  *only* mechanism for BGM/Voice not-yet-ready states (ch. 18's `bgm.mode`
  has the identical "no pending value, `$comment` says must be resolved
  by READY" shape as SFX's `action` enum, but `clip_manifest.json` carries
  no BGM equivalent of `optional_source_audio` - there's nothing Stage 3
  could record ahead of time that would let Stage 5 independently verify
  "a BGM decision was supposed to happen here"). And even for SFX, this
  is opt-in by construction: if Stage 3's writer never adds
  `sfx_contract_version` to a project's `clip_manifest.json`, that
  project's SFX candidates are exactly as unverifiable as they were
  before this change - the contract is meaningless.

- **`audio_layers.bgm.reference_db` is read from `edit_plan.json` but
  never consumed by `mix_audio()`.** ch. 14 only describes two normalize
  steps (Voice pre-mix, final output) and never mentions normalizing BGM
  to any per-file reference level before mixing - since the final step
  normalizes the *entire* mix to `target_lufs` regardless of what level
  BGM started at, a BGM-specific starting level doesn't change the final
  result under this design. Treated as Stage 3/4 metadata this module
  doesn't need, not an oversight.

- **BGM ducking depth: the compressor itself hits the spec target closely;
  measuring that from a finished mix does not.** ch. 14 states a "-12dB
  depth" as if it were a fixed attenuation, but `sidechaincompress` is a
  real compressor (attenuation depends on how far the sidechain signal is
  above `threshold`, scaled by `ratio`), so `_DUCK_THRESHOLD_LINEAR`/
  `_DUCK_RATIO` were tuned empirically rather than derived exactly.
  Measured directly at `sidechaincompress`'s own output (before `amix`/
  the final `loudnorm`), against a synthetic loudnorm'd voice tone: a
  **11.8dB** reduction - within 0.2dB of the -12dB target. But measuring
  the *same* effect from a finished mix (voice+SFX+BGM combined, isolating
  BGM's frequency band after the fact with a lowpass filter, since there
  are no separate stems) reads much lower - as low as **5-6.7dB** in the
  sample render reviewed for this phase - because a lowpass filter has
  rolloff, not a brick-wall cutoff, and some voice energy leaks through
  and dilutes the measurement once voice is loud relative to BGM. This is
  a limitation of after-the-fact frequency-domain measurement on a mixed
  file, not evidence the duck itself is weaker than tuned - `tests/
  test_audio_mix.py::test_bgm_is_measurably_ducked_while_voice_is_present`
  only asserts the (much smaller) directionally-correct effect it can
  actually observe this way, and says so in its own comment. If a real
  SEOULKIT MINI render is ever reviewed and BGM sounds too loud under
  dialogue, this is the first place to check - but by ear, not by
  re-running this lowpass measurement technique, which is known to
  understate the true depth.

- **A selected BGM file shorter than the mix's total duration is not
  looped.** It plays once via `atrim`/`apad` and leaves silence for the
  remainder. Looping would need explicit handling (e.g. detecting the
  source is shorter and using `-stream_loop` or a concat-based loop)
  this phase doesn't implement.

- Loudness normalize uses FFmpeg's single-pass `loudnorm` (measure-and-
  normalize in one invocation) both for Voice's pre-mix normalize and the
  final mix-wide normalize, not the more accurate two-pass mode (which
  needs a separate analysis pass, parses its JSON stats output, then
  re-invokes with the measured values). Single-pass is less precise -
  the sample render reviewed for this phase landed at -15.6 LUFS against
  a -14 LUFS target (1.6dB off) - but keeps this phase's FFmpeg invocation
  count in line with every other `render/*` module here, all of which are
  single-invocation. Worth revisiting with two-pass if real renders show
  single-pass isn't hitting the target closely enough in practice.

- ch. 14 doesn't state a target LUFS for Voice's own pre-mix normalize
  pass (only for the final output). `mix_audio()` assumes the same
  `target_lufs` for both, since no other value is given anywhere in the
  spec text.

- The Phase 6 manual visual QC pass only exercised English text on
  `bottom-center`, leaving Korean subtitle rendering and `top-center`
  unverified by a real render - **resolved in Phase 7**, together with
  the font-bundling work that also fixed overlay text. Both gaps closed
  by the same fix (bundled Noto Sans KR font files, no fontconfig
  dependency) and the same sample render: Korean glyphs (overlay +
  subtitle, same frame) render correctly with no tofu boxes, and a
  `top-center` subtitle coexisting with a `top-center` overlay in the
  same frame does not collide or break either layer.

- **Root cause of the tofu-box risk, and why fonts are bundled instead of
  resolved via fontconfig.** Phase 7 development found that both
  `subtitle.py` and (the pre-Phase-7 draft of) `overlay.py` depended on
  system fontconfig to find "Noto Sans KR," and that dependency was never
  actually safe: `fc-match` **never fails**, even for a font name that
  plainly doesn't exist - it always silently returns *some* fallback,
  exit code 0. Fix: `render/fonts.py` bundles the actual "Noto Sans KR"
  Regular/Bold `.ttf` files (Google's official release, SIL OFL 1.1,
  extracted from the upstream variable font) as repo assets and resolves
  them directly - `overlay.py`'s `resolve_font_file()` and `subtitle.py`'s
  `burn_subtitles()` (via `fontsdir=`) no longer consult fontconfig at
  all, for either layer. `FontSubstitutionIssue` now means "the bundled
  font asset itself is missing/corrupt/lacks Hangul coverage" - a real
  packaging defect with no reasonable fallback.

- `drawtext`'s `font` option does not accept fontconfig pattern syntax
  (e.g. `"Noto Sans KR:style=Bold"`) at all - confirmed empirically during
  Phase 7 development, fails with `"Error applying option 'style' to
  filter 'drawtext': Option not found"` (the colon is parsed as an option
  separator by `drawtext`'s own parser, same underlying issue class as
  `subtitle.py`'s `ass_path` colon guard, just a different filter).
  `fontfile=<path>` is the only thing that works.

- **Severity: real and OS-dependent, not a rare edge case.**
  `render/subtitle.py::burn_subtitles()` rejects a colon in `ass_path` with
  a `ValueError` before ever invoking FFmpeg (FFmpeg's own filtergraph
  parser treats `:` as an option separator inside a `-vf` value). On
  Windows, an absolute path is `C:\...` by construction, so this isn't an
  occasional edge case there - it's the default shape of every absolute
  path. `burn_subtitles()` today will refuse to run at all against a
  Windows-style absolute `ass_path`. The same guard was added for
  `render.fonts.FONT_DIR` in Phase 7, for the identical reason. Proper
  filtergraph escaping (or writing the `.ass` file somewhere guaranteed
  colon-free and passing a relative/short path instead) is unimplemented
  and should be picked up before this pipeline is ever expected to run on
  Windows.

- `render/subtitle.py::generate_ass()` sets `PlayResX`/`PlayResY` to
  whatever resolution the caller supplies (typically the real input
  video's, via `probe_video_resolution()`), not a hardcoded canonical
  resolution. ch. 18's `margin_v: 120`/`100` pixel values were almost
  certainly chosen with some specific target resolution in mind, but
  nothing in the pipeline today guarantees a clip is at that resolution
  by the time subtitles are burned into it. The same reasoning applies to
  `overlay.py`'s `x_pct`/`y_pct` percentages, which is why they're
  percentages of the *actual* probed resolution rather than a hardcoded
  canonical size too.

- `generate_ass()`'s style colors (white text, black outline/shadow) are
  this module's own default, not a ch. 18 spec value - the config block
  gives font/size/outline-width/shadow-width but no color fields.
  `overlay.py` reuses the same white/black scheme for the same reason.

- Clarified, not fixed (nothing was wrong): when `render/hold.py::hold_clip()`'s
  `hold_ms < 1` guard was temporarily removed to red/green-test it,
  `hold_ms=-100` still raised `ValueError` while `hold_ms=0` did not. The
  reflex explanation - "the Phase 0 schema's `hold_ms: {minimum: 0}`
  constraint already caught the negative case" - is wrong: `hold_clip()` is
  a standalone primitive that never checks whether its caller went through
  schema validation, so schema validation gives it exactly zero
  protection. What actually caught it was `ms_to_seconds_str()`'s own
  `ms < 0` guard (written for an unrelated reason), which `hold_clip()`
  happens to call downstream - an incidental side effect, not a designed
  second line of defense, and it doesn't cover `hold_ms=0` at all.

- ~~`render/hold.py::hold_clip()` cannot prevent being *called twice* on
  the same clip... Real double-application prevention... is the
  responsibility of the same not-yet-built assembly layer~~ **Resolved in
  Phase 9.** `render/pipeline.py::_run_clip_stage()` calls `hold_clip()`
  at most once per segment, and only when
  `segment["hold_strategy"] == "settle_frame_hold"` - proven by a
  red/green demo that skipped the call entirely and observed the final
  render's duration stay 1000ms short of expected.
  `tests/test_hold.py::test_calling_hold_clip_twice_visibly_doubles_the_extension`
  still documents that `hold_clip()` itself has no self-protection; the
  guarantee now lives one layer up, in the orchestrator that decides
  whether to call it at all.

- `preflight/validator.py` did not check the ch. 18
  `hold.settle_frame_hold.max_ms` cap (default 1500ms, per-segment) until
  Phase 5 planning surfaced this - a false assumption ("Phase 1 already
  enforces this") was made out loud, checked against the actual code, and
  found to be wrong. Fixed same session, before Phase 5 itself was
  implemented. `check_time_consistency()` now takes a
  `max_settle_frame_hold_ms` parameter (default
  `DEFAULT_MAX_SETTLE_FRAME_HOLD_MS = 1500`) and emits
  `MAX_HOLD_MS_EXCEEDED` (blocking) for any `settle_frame_hold` segment
  over it.

- ~~`render/concat.py::concat_clips()` does not sort `clip_paths`... the
  assembly layer that turns `edit_plan.json`'s `segments[]` into an
  ordered `list[Path]`... does not exist yet~~ **Resolved in Phase 9.**
  `render/pipeline.py::_run_clip_stage()` is that assembly layer - it
  iterates `data["segments"]` in the exact order they appear in
  `edit_plan.json` (still no sorting; ch. 10 ordering is the plan
  author's responsibility, not this layer's) and passes the resulting
  `list[Path]` straight to `concat_clips()`. `_render()` also now
  enforces the full ch. 09 call order (trim → hold → concat → overlay →
  subtitle → audio mix → mux+encode) as a fixed sequence of Python
  statements, not a promise.

  Worth remembering how this was found to matter: a `sorted(clip_paths)`
  reorder is a *silent* bug - FFmpeg raised no error and produced a fully
  valid, fully wrong-order video. That's different from bugs FFmpeg
  itself refuses outright ("cannot edit existing files in-place",
  "unconnected output"). Ordering mistakes get no such safety net from
  FFmpeg - only a real-content test catches them (`tests/
  test_pipeline.py::test_concat_order_is_preserved_through_the_whole_pipeline`
  is that test for this phase).

- ~~Phase 3's `render/trim.py::trim_clip()` returns a `TrimResult` carrying
  the exact `command`... but nothing writes that to `logs/render_v{NNN}.log`
  yet~~ **Resolved in Phase 10**, same fix as the identical gap recorded
  above - `render/report.py` writes `logs/render_v{NNN}.log` from the
  combined `stage_results[].command` list every `trim.py`/`overlay.py`/
  `subtitle.py`/`audio_mix.py`/`encode.py` primitive already produced.

- `trim_clip()` always passes `-an` to FFmpeg, discarding whatever audio
  the source clip itself carries (if any). This isn't a gap so much as a
  design decision worth recording: ch. 01's architecture diagram draws
  "Clip Engine (trim/hold/concat)" and "Audio Mixer (adopted/selected
  assets only)" as separate boxes that only meet at pipeline step 6, and
  ch. 12 names exactly three audio sources for the final mix - Voice,
  adopted SFX, selected BGM - with no path for a source clip's own
  embedded audio to reach the output. `overlay.py` and `subtitle.py` both
  pass `-an` too, for the identical reason - neither layer should be the
  one deciding what happens to audio, and now that Phase 8 exists, this
  decision is confirmed correct: `audio_mix.py` is where audio actually
  gets assembled, entirely independent of the video-only clips these
  earlier phases produce.

- Config loading is not implemented. `duration_tolerance_ms` (spec ch. 18)
  is a hardcoded Python default (`DEFAULT_DURATION_TOLERANCE_MS = 50` in
  `src/seoulkit_studio/schema/validator.py`), overridable per call but not
  read from any file. Loading the full ch. 18 config block (render,
  subtitle, overlay, audio, preflight, naming settings) as YAML is not
  assigned to any phase yet - Phase 8's `DEFAULT_TARGET_LUFS`/
  `DEFAULT_BGM_DUCKING_DB`/etc. are the same kind of hardcoded-but-
  overridable default as everything before them.

- ~~`clip_manifest.json` cross-validation~~ **Resolved in Phase 2.5.**
  `src/seoulkit_studio/execution/clip_manifest.py` now compares
  `usable_start_ms`/`usable_end_ms`/`key_event_end_ms`/`settle_start_ms`
  between `edit_plan.json` and `clip_manifest.json` per shot.

- ~~`check_clip_manifest_consistency()` silently let a duplicate `shot` in
  `clip_manifest.json` resolve via last-wins~~ **Fixed, same session as
  Phase 2.5.** Occurrence counting now runs as its own pass over
  `clips[]` *before* the lookup dict is built, so a duplicate is reported
  (`CLIP_MANIFEST_DUPLICATE_SHOT`, blocking) regardless of which entry a
  naive dict build would have picked.

- ~~`compute_effective_status()` (Phase 2) returns only the `PlanStatus`
  string... there is no function that takes that *final*, blended status
  and returns Preview/Final `bool`s~~ **Resolved in Phase 9** - not as a
  standalone reusable function, but inline as each entry point's own
  `gate_ok` predicate: `render_preview` uses `status != "NOT_READY"`,
  `render_final` uses `status == "READY"`. Turned out simple enough
  (two one-line lambdas passed into a shared `_render()` helper) that
  extracting a separate `effective_status -> ExecutionPermission` mapping
  function would have been an abstraction with only two call sites and no
  reuse - `severity_to_execution` was not reused here since it maps a
  single issue's severity, not a plan's already-blended final status.

- The `info` severity tier (ch. 18 `severity_mapping`) is fully wired
  (`SEVERITY_MAPPING`, `severity_to_execution`, tests) but no Pre-flight
  check currently emits an `info`-level issue - the spec's own ch. 04-6
  example only shows `blocking`/`warning` issue codes. Not a bug, just
  worth knowing if a review goes looking for an `info` example and
  doesn't find one.

## Process lessons

Not code gaps - mistakes in how a session verified its own state during
Phase 7 development. Recorded here, separately from Known gaps, because
they're about *how to work on this project*, not about the code itself:

- **A stale local clone was mistaken for the repo's real state.** An
  existing local checkout (from earlier work) was reused without
  re-fetching, and appeared to show only a Phase-0 commit - making it
  look like Phase 1-6 had never actually been pushed. The real repository
  (confirmed via a fresh `git clone`, and independently via the GitHub
  API querying a specific commit SHA directly) had Phase 0-6 fully
  committed the whole time.

- **`get_me`-style authentication success was mistaken for write
  authorization.** A GitHub MCP tool call that only proves "this session
  is authenticated as some identity" was treated as proof that this
  session could also *write* to the repo. It couldn't: the first actual
  write attempt (via both the GitHub API write tools and a raw `git
  push`) returned `403` for every write path tried.

  Both mistakes share the same shape: a positive but *indirect* signal
  was treated as equivalent to directly testing the thing actually in
  question. Going forward: a claim should be stated as **"확인됨"
  (confirmed)** only when it comes from an attempt that actually
  exercises the thing being claimed - a real fetch, a real write, a real
  render, a real test run, a real measurement - and as **"아마도"
  (probably)** when it's inferred from an indirect signal instead. This
  project already had a version of this discipline for rendering
  correctness (automated tests prove *some* content was drawn; only a
  human looking at a real frame, or a real dB/LUFS measurement, proves
  it's the *right* content) - the same distinction applies to session
  infrastructure state and to which layer of a pipeline actually produced
  a given number, not just to render output.

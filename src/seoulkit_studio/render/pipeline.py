"""Preview/Final render orchestration, hard gate (Stage 5 spec, ch. 05, 07-09).

This is the first module that turns an `edit_plan.json` into an actual
video end to end - everything before this phase (`trim.py`, `hold.py`,
`concat.py`, `overlay.py`, `subtitle.py`, `audio_mix.py`, `encode.py`) is
an independently-testable primitive with no idea what order it runs in or
whether it's even allowed to run at all. `render_preview()`/
`render_final()` are the ones that decide both.

Judgment vs. execution stays split exactly the way it has since Phase 2:
`execution/pipeline.py::evaluate_plan()` is the only thing that decides
`effective_status` (ch. 05) - this module calls it and acts on the result,
it never recomputes or second-guesses it. That's also why the gate check
is the literal first thing either entry point does, before any temp
directory, any file write, any FFmpeg invocation: ch. 05's own text is
explicit that no `--force`-style bypass should ever exist, and the
cheapest way to guarantee that is to make the blocked path physically
incapable of reaching FFmpeg, not just discard its output afterward.
`tests/test_pipeline.py`'s hard-gate tests prove this by wrapping
`subprocess.run` itself and asserting it is never called - not by
inspecting the result and hoping nothing slipped through.

ch. 05's execution matrix has exactly three `effective_status` values and
two gates:
- Preview: blocked only by NOT_READY (READY and REVIEW_REQUIRED both run).
- Final: blocked by anything except READY (a hard gate, ch. 08: "READY일
  때만 실행 가능").

ch. 09 pipeline order, exactly as documented - not the order Phases 3/4/5
happened to be *built* in (see `docs/phase-plan.md` Known gaps for that
distinction): trim each segment -> hold the ones that need it (per
segment, before concat - `hold_clip()` operates on a single clip file via
`tpad`, so it structurally cannot run on an already-concatenated video) ->
concat -> overlay -> subtitle burn-in -> audio mix (built as its own
audio-only file, ch. 12) -> mux the video and audio streams together and
encode at the target resolution/CRF (`encode.py`, new in this phase - ch.
09 lists "결합" and "최종 인코딩" as two steps, done here as one FFmpeg
call like every other primitive in this package).

Every intermediate file (each segment's trim/hold output, the concatenated
video, the overlaid video, the subtitled-but-unmuxed video, the audio mix)
lives in a per-call `tempfile.TemporaryDirectory()` and is discarded when
the call returns. Only what the caller explicitly asked for -
`output_path`, and for `render_final()` also `ass_output_path`/
`srt_output_path` (ch. 08: burn-in MP4 + separately-exported `.ass`/`.srt`)
- survives. Assigning real version-numbered filenames (`final_v{NNN}.mp4`)
is out of scope here; the caller already owns those exact paths by the
time it calls this module - that's Render Report (Phase 10) or CLI (Phase
11) territory, not this one's.

Preview watermark: ch. 18 only gives a boolean (`render.preview.
watermark: true`), no actual text/position/opacity/size - `encode.py`'s
defaults are this phase's own choice, not spec, and are recorded as such
in `docs/phase-plan.md` Known gaps.

`RenderResult.studio_execution_result`/`effective_status`/
`plan_status_as_declared`/`preflight_issues` (added for Phase 10) are a
pure additive exposure of the `evaluate_plan()` call already made above -
`_render()` still calls it exactly once per invocation. Phase 10's Render
Report (ch. 17) needs these values on every attempt, including a gated
one, so they're populated at every `RenderResult(...)` return point in
this function, not just the successful path.

Publish hotfix (post-Phase 10): every output file - the muxed video, and
for Final also `.ass`/`.srt` - now stays inside `_render()`'s own
`TemporaryDirectory()` until the very end, and is only committed to its
real destination after everything, including the copy itself, has
succeeded. This closes a real defect found while planning Phase 10:
`mux_and_encode()` used to write straight to the caller's real
`output_path`, and `.ass`/`.srt` used to be written to their real
destinations right after the overlay stage - long before the pipeline as
a whole was known to succeed. Both were empirically reproduced before this
fix: a corrupted voice file made a real `render_final()` call fail at the
audio-mix stage while still leaving valid `.ass`/`.srt` content at the
real `output/` paths; a deliberately-killed real FFmpeg encode left a
genuine partial `.mp4` at its real destination.

Publish is a best-effort two-phase commit, not a true filesystem
transaction. `os.replace()` is atomic for one file on one filesystem, but
three independent files (mp4/ass/srt) can't be committed as a single unit
with only POSIX rename semantics:

  Phase 1 (`_stage_copies`): copy every staged file to a uniquely-named
  (`uuid4`-suffixed, to avoid colliding with a stale leftover from a
  crashed prior attempt) temp file beside its real destination. Nothing
  real is touched yet. If any copy fails, every temp file already created
  is removed and nothing is committed.

  Phase 2 (`_commit_all`): only entered once every copy in phase 1
  succeeded. For each file in order: back up the existing destination (if
  any) via `os.replace()`, then `os.replace()` the temp file into place.
  If a later file's commit fails, every already-committed file in this
  attempt is rolled back in reverse order. Rollback is also just
  `os.replace()` calls and is expected to succeed in practice, but it is
  not guaranteed to - if a rollback's own `os.replace()` fails too, that
  is reported as `publish_rollback_failed`, distinct from an ordinary
  `publish_commit_failed`, and never silently absorbed. The output
  directory may be left inconsistent in that (rare, OS/filesystem-level)
  case, and the report says so rather than claiming a clean recovery it
  didn't actually achieve.

Publish failure is a real execution failure, not a gate rejection - it is
recorded as a `PublishResult` (same `command`/`stdout`/`stderr`/`error`/
`ok` shape as every other `StageResult`) appended to `stage_results`, so
Phase 10's `render/report.py::_errors_from_stage_results()` already picks
it up into the Render Report's `errors[]` with zero changes needed in
that module - `report.py` only gained two small cosmetic dict entries
(`_STAGE_NAMES`/`_ERROR_MESSAGES`) for a friendlier `stage`/`message`,
nothing contract-shaped.

`RenderResult.report_path`/`log_path` (added for Phase 11 CLI): additive,
default-`None` fields this module itself never sets - `render_preview()`/
`render_final()` don't know the version-numbered `preview/`/`output/`/
`logs/` paths a caller will use, only `render/report.py::_run_and_report()`
does. That function fills these in on the already-built `RenderResult`
right before returning it, so the CLI can print exactly where a report/log
landed without recomputing `_preview_paths()`/`_final_paths()`/
`_log_paths()` itself. Deliberately not added to `RenderReport`: that
dataclass is persisted verbatim as ch. 17's Render Report JSON
(`json.dumps(asdict(report), ...)` in `report.py`), and this phase must not
change that already-shipped schema.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Union

from seoulkit_studio.execution import PlanStatus, StudioExecutionResult, evaluate_plan, load_plan_file
from seoulkit_studio.preflight import PreflightIssue
from seoulkit_studio.render.audio_mix import AudioMixResult, mix_audio
from seoulkit_studio.render.concat import ConcatResult, concat_clips
from seoulkit_studio.render.encode import EncodeResult, mux_and_encode
from seoulkit_studio.render.hold import HoldResult, hold_clip
from seoulkit_studio.render.overlay import OverlayResult, burn_overlays
from seoulkit_studio.render.subtitle import (
    BurnInResult,
    burn_subtitles,
    generate_ass,
    generate_srt,
    probe_video_resolution,
)
from seoulkit_studio.render.trim import TrimResult, trim_clip

# ch. 07 Preview config.
PREVIEW_RESOLUTION = "720x1280"
PREVIEW_CRF = 28
PREVIEW_WATERMARK_TEXT = "PREVIEW"

# ch. 08 Final config.
FINAL_RESOLUTION = "1080x1920"
FINAL_CRF = 18

PublishErrorKind = Literal["publish_copy_failed", "publish_commit_failed", "publish_rollback_failed"]


@dataclass
class PublishResult:
    output_path: Path | None
    command: list[str] = field(default_factory=list)
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: PublishErrorKind | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


StageResult = Union[
    TrimResult, HoldResult, ConcatResult, OverlayResult, BurnInResult, AudioMixResult, EncodeResult, PublishResult
]
GateError = Literal["not_ready", "review_required"]


@dataclass
class RenderResult:
    ok: bool
    output_path: Path | None
    ass_path: Path | None = None
    srt_path: Path | None = None
    stage_results: list[StageResult] = field(default_factory=list)
    gate_error: GateError | None = None
    studio_execution_result: StudioExecutionResult | None = None
    effective_status: PlanStatus | None = None
    plan_status_as_declared: str | None = None
    preflight_issues: list[PreflightIssue] = field(default_factory=list)
    report_path: Path | None = None
    log_path: Path | None = None

    @property
    def gated(self) -> bool:
        return self.gate_error is not None


def _run_clip_stage(
    project_dir: Path, segments: list[dict], work_dir: Path
) -> tuple[list[Path] | None, list[StageResult]]:
    stage_results: list[StageResult] = []
    clip_paths: list[Path] = []

    for i, segment in enumerate(segments):
        trimmed_path = work_dir / f"trim_{i}.mp4"
        trim_result = trim_clip(
            project_dir / segment["source_clip"], trimmed_path, segment["clip_in_ms"], segment["clip_out_ms"]
        )
        stage_results.append(trim_result)
        if not trim_result.ok:
            return None, stage_results

        if segment["hold_strategy"] == "settle_frame_hold":
            held_path = work_dir / f"hold_{i}.mp4"
            hold_result = hold_clip(trimmed_path, held_path, segment["hold_ms"])
            stage_results.append(hold_result)
            if not hold_result.ok:
                return None, stage_results
            clip_paths.append(held_path)
        else:
            clip_paths.append(trimmed_path)

    return clip_paths, stage_results


@dataclass
class _Publishable:
    staged_path: Path
    dest_path: Path
    tmp_path: Path | None = None
    backup_path: Path | None = None
    had_existing_dest: bool = False


def _unique_sibling(dest_path: Path, tag: str) -> Path:
    return dest_path.with_name(f".{dest_path.name}.{uuid.uuid4().hex}.{tag}")


def _stage_copies(publishables: list[_Publishable]) -> str | None:
    """Phase 1: copy each staged file to a unique temp path beside its real
    destination. Returns None on full success, or an error message if any
    copy failed - in which case every temp file already created is removed
    and no real destination has been touched at all."""
    for p in publishables:
        p.tmp_path = _unique_sibling(p.dest_path, "tmp")
        try:
            shutil.copy2(p.staged_path, p.tmp_path)
        except OSError as exc:
            for done in publishables:
                if done.tmp_path is not None and done.tmp_path.exists():
                    done.tmp_path.unlink(missing_ok=True)
            return f"failed to stage {p.dest_path.name}: {exc}"
    return None


def _rollback(committed: list[_Publishable]) -> str | None:
    errors = []
    for p in reversed(committed):
        try:
            if p.had_existing_dest and p.backup_path is not None:
                os.replace(p.backup_path, p.dest_path)
            else:
                p.dest_path.unlink(missing_ok=True)
        except OSError as exc:
            errors.append(f"{p.dest_path.name}: {exc}")
    return "; ".join(errors) if errors else None


def _cleanup(publishables: list[_Publishable]) -> None:
    for p in publishables:
        if p.tmp_path is not None and p.tmp_path.exists():
            p.tmp_path.unlink(missing_ok=True)
        if p.backup_path is not None and p.backup_path.exists():
            p.backup_path.unlink(missing_ok=True)


def _commit_all(publishables: list[_Publishable]) -> tuple[PublishErrorKind | None, str]:
    """Phase 2: back up + replace each destination in turn, only ever
    called once every phase-1 copy has already succeeded. On failure,
    rolls back everything already committed in this attempt, in reverse
    order, then cleans up temp/backup files either way. Returns
    (None, "") on full success. A rollback failure is reported as
    publish_rollback_failed and never silently absorbed - the output
    directory may be left inconsistent in that case, and that is what
    gets reported, not a false "recovered cleanly."

    A failure can land in the middle of a single publishable's own two-step
    commit (dest backed up, but tmp -> dest not yet done) - that file's
    original is safely sitting in `backup_path` but the file was never
    appended to `committed` (only a fully-completed commit is). Restoring
    it therefore needs `failed_on` added to the restore set whenever its
    backup already succeeded (`had_existing_dest`); when the backup step
    itself is what failed, `had_existing_dest` is still False and the
    original destination was never touched, so nothing needs restoring
    there."""
    committed: list[_Publishable] = []
    try:
        for p in publishables:
            if p.dest_path.exists():
                p.backup_path = _unique_sibling(p.dest_path, "bak")
                os.replace(p.dest_path, p.backup_path)
                p.had_existing_dest = True
            os.replace(p.tmp_path, p.dest_path)
            committed.append(p)
    except OSError as exc:
        failed_on = publishables[len(committed)]
        to_restore = committed if not failed_on.had_existing_dest else committed + [failed_on]
        rollback_error = _rollback(to_restore)
        _cleanup(publishables)
        if rollback_error:
            return "publish_rollback_failed", (
                f"commit failed on {failed_on.dest_path.name}: {exc}; rollback of "
                f"{len(committed)} already-committed file(s) also failed: {rollback_error} "
                "- output directory may be inconsistent, manual check needed"
            )
        return "publish_commit_failed", (
            f"commit failed on {failed_on.dest_path.name}: {exc}; rolled back "
            f"{len(committed)} already-committed file(s) successfully"
        )

    _cleanup(publishables)
    return None, ""


def _render(
    edit_plan_path: Path,
    project_dir: Path,
    clip_manifest_path: Path | None,
    *,
    gate_ok,
    resolution: str,
    crf: int,
    watermark_text: str | None,
    output_path: Path,
    want_ass_srt: bool,
    ass_output_path: Path | None = None,
    srt_output_path: Path | None = None,
) -> RenderResult:
    evaluation = evaluate_plan(edit_plan_path, project_dir, clip_manifest_path)
    preflight_issues = (
        list(evaluation.preflight_result.issues) if evaluation.preflight_result else []
    ) + evaluation.clip_manifest_issues

    def _evaluation_fields() -> dict:
        return {
            "studio_execution_result": evaluation.studio_execution_result,
            "effective_status": evaluation.effective_status,
            "plan_status_as_declared": evaluation.plan_status_as_declared,
            "preflight_issues": preflight_issues,
        }

    if not gate_ok(evaluation.effective_status):
        gate_error: GateError = "not_ready" if evaluation.effective_status == "NOT_READY" else "review_required"
        return RenderResult(ok=False, output_path=None, gate_error=gate_error, **_evaluation_fields())

    # evaluate_plan() already proved this file loads and passes schema
    # validation (a load/schema failure forces effective_status to
    # NOT_READY, which the gate check above would have already refused) -
    # loading it again here is a second, independent read (Plan Loader is
    # read-only either way), not a second decision.
    data = load_plan_file(edit_plan_path).data

    stage_results: list[StageResult] = []

    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)

        clip_paths, clip_stage_results = _run_clip_stage(project_dir, data["segments"], work_dir)
        stage_results += clip_stage_results
        if clip_paths is None:
            return RenderResult(ok=False, output_path=None, stage_results=stage_results, **_evaluation_fields())

        concat_result = concat_clips(clip_paths, work_dir / "concat.mp4")
        stage_results.append(concat_result)
        if not concat_result.ok:
            return RenderResult(ok=False, output_path=None, stage_results=stage_results, **_evaluation_fields())

        overlay_result = burn_overlays(concat_result.output_path, data.get("overlays", []), work_dir / "overlay.mp4")
        stage_results.append(overlay_result)
        if not overlay_result.ok:
            return RenderResult(ok=False, output_path=None, stage_results=stage_results, **_evaluation_fields())

        width, height = probe_video_resolution(overlay_result.output_path)
        # Always staged in work_dir, even for Final - never the real
        # ass_output_path directly. See module docstring: this used to
        # write straight to the real destination here, before the
        # pipeline's success was known.
        ass_path = work_dir / "subs.ass"
        ass_path.write_text(generate_ass(data.get("subtitles", []), width, height))

        subtitle_result = burn_subtitles(overlay_result.output_path, ass_path, work_dir / "subtitled.mp4")
        stage_results.append(subtitle_result)
        if not subtitle_result.ok:
            return RenderResult(
                ok=False,
                output_path=None,
                ass_path=None,
                stage_results=stage_results,
                **_evaluation_fields(),
            )

        srt_path: Path | None = None
        if want_ass_srt:
            srt_path = work_dir / "subs.srt"  # staged, not srt_output_path directly - see above
            srt_path.write_text(generate_srt(data.get("subtitles", [])))

        audio_result = mix_audio(project_dir, data["voice"], data["segments"], data["audio_layers"], work_dir / "audio.wav")
        stage_results.append(audio_result)
        if not audio_result.ok:
            return RenderResult(
                ok=False,
                output_path=None,
                ass_path=None,
                srt_path=None,
                stage_results=stage_results,
                **_evaluation_fields(),
            )

        encoded_staging_path = work_dir / "encoded.mp4"
        encode_result = mux_and_encode(
            subtitle_result.output_path,
            audio_result.output_path,
            encoded_staging_path,
            resolution=resolution,
            crf=crf,
            watermark_text=watermark_text,
        )
        stage_results.append(encode_result)
        if not encode_result.ok:
            return RenderResult(
                ok=False,
                output_path=None,
                ass_path=None,
                srt_path=None,
                stage_results=stage_results,
                **_evaluation_fields(),
            )

        # --- publish: everything succeeded so far, only now touch the real destinations ---
        publishables = [_Publishable(staged_path=encoded_staging_path, dest_path=output_path)]
        if want_ass_srt:
            publishables.append(_Publishable(staged_path=ass_path, dest_path=ass_output_path))
            publishables.append(_Publishable(staged_path=srt_path, dest_path=srt_output_path))

        copy_error = _stage_copies(publishables)
        if copy_error is not None:
            stage_results.append(PublishResult(output_path=None, error="publish_copy_failed", stderr=copy_error))
            return RenderResult(
                ok=False,
                output_path=None,
                ass_path=None,
                srt_path=None,
                stage_results=stage_results,
                **_evaluation_fields(),
            )

        commit_error_kind, commit_error_message = _commit_all(publishables)
        if commit_error_kind is not None:
            stage_results.append(
                PublishResult(output_path=None, error=commit_error_kind, stderr=commit_error_message)
            )
            return RenderResult(
                ok=False,
                output_path=None,
                ass_path=None,
                srt_path=None,
                stage_results=stage_results,
                **_evaluation_fields(),
            )

        stage_results.append(PublishResult(output_path=output_path))

    return RenderResult(
        ok=True,
        output_path=output_path,
        ass_path=ass_output_path if want_ass_srt else None,
        srt_path=srt_output_path if want_ass_srt else None,
        stage_results=stage_results,
        **_evaluation_fields(),
    )


def render_preview(
    edit_plan_path: Path,
    project_dir: Path,
    output_path: Path,
    clip_manifest_path: Path | None = None,
) -> RenderResult:
    return _render(
        edit_plan_path,
        project_dir,
        clip_manifest_path,
        gate_ok=lambda status: status != "NOT_READY",
        resolution=PREVIEW_RESOLUTION,
        crf=PREVIEW_CRF,
        watermark_text=PREVIEW_WATERMARK_TEXT,
        output_path=output_path,
        want_ass_srt=False,
    )


def render_final(
    edit_plan_path: Path,
    project_dir: Path,
    output_path: Path,
    ass_output_path: Path,
    srt_output_path: Path,
    clip_manifest_path: Path | None = None,
) -> RenderResult:
    return _render(
        edit_plan_path,
        project_dir,
        clip_manifest_path,
        gate_ok=lambda status: status == "READY",
        resolution=FINAL_RESOLUTION,
        crf=FINAL_CRF,
        watermark_text=None,
        output_path=output_path,
        want_ass_srt=True,
        ass_output_path=ass_output_path,
        srt_output_path=srt_output_path,
    )

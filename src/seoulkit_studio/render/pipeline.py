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
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Union

from seoulkit_studio.execution import evaluate_plan, load_plan_file
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

StageResult = Union[TrimResult, HoldResult, ConcatResult, OverlayResult, BurnInResult, AudioMixResult, EncodeResult]
GateError = Literal["not_ready", "review_required"]


@dataclass
class RenderResult:
    ok: bool
    output_path: Path | None
    ass_path: Path | None = None
    srt_path: Path | None = None
    stage_results: list[StageResult] = field(default_factory=list)
    gate_error: GateError | None = None

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
    if not gate_ok(evaluation.effective_status):
        gate_error: GateError = "not_ready" if evaluation.effective_status == "NOT_READY" else "review_required"
        return RenderResult(ok=False, output_path=None, gate_error=gate_error)

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
            return RenderResult(ok=False, output_path=None, stage_results=stage_results)

        concat_result = concat_clips(clip_paths, work_dir / "concat.mp4")
        stage_results.append(concat_result)
        if not concat_result.ok:
            return RenderResult(ok=False, output_path=None, stage_results=stage_results)

        overlay_result = burn_overlays(concat_result.output_path, data.get("overlays", []), work_dir / "overlay.mp4")
        stage_results.append(overlay_result)
        if not overlay_result.ok:
            return RenderResult(ok=False, output_path=None, stage_results=stage_results)

        width, height = probe_video_resolution(overlay_result.output_path)
        ass_path = ass_output_path if want_ass_srt else work_dir / "subs.ass"
        ass_path.write_text(generate_ass(data.get("subtitles", []), width, height))

        subtitle_result = burn_subtitles(overlay_result.output_path, ass_path, work_dir / "subtitled.mp4")
        stage_results.append(subtitle_result)
        if not subtitle_result.ok:
            return RenderResult(
                ok=False, output_path=None, ass_path=ass_path if want_ass_srt else None, stage_results=stage_results
            )

        srt_path: Path | None = None
        if want_ass_srt:
            srt_path = srt_output_path
            srt_path.write_text(generate_srt(data.get("subtitles", [])))

        audio_result = mix_audio(project_dir, data["voice"], data["segments"], data["audio_layers"], work_dir / "audio.wav")
        stage_results.append(audio_result)
        if not audio_result.ok:
            return RenderResult(
                ok=False,
                output_path=None,
                ass_path=ass_path if want_ass_srt else None,
                srt_path=srt_path,
                stage_results=stage_results,
            )

        encode_result = mux_and_encode(
            subtitle_result.output_path,
            audio_result.output_path,
            output_path,
            resolution=resolution,
            crf=crf,
            watermark_text=watermark_text,
        )
        stage_results.append(encode_result)
        if not encode_result.ok:
            return RenderResult(
                ok=False,
                output_path=None,
                ass_path=ass_path if want_ass_srt else None,
                srt_path=srt_path,
                stage_results=stage_results,
            )

    return RenderResult(
        ok=True,
        output_path=output_path,
        ass_path=ass_path if want_ass_srt else None,
        srt_path=srt_path,
        stage_results=stage_results,
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

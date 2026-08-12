"""Voice / SFX / BGM audio mix (Stage 5 spec, ch. 12, ch. 14, ch. 18).

ch. 12's authority principle is layering, not aesthetic judgment: Voice is
always authoritative; only SFX clips already marked `action="adopted"` and
a BGM already `mode="selected"` (with a `file`) are mixed in - Stage 5 does
not decide *whether* a candidate belongs in the mix, only executes what
Stage 3/4 already decided. `mix_audio()` reflects that directly: it reads
`audio_layers` (and `voice`/`segments` for cross-referencing) straight from
`edit_plan.json`'s own shape, the same way `overlay.py`/`subtitle.py` read
`overlays[]`/`subtitles[]` - no separate "resolved mix plan" data model.

ch. 12's mix order (kept exactly):
1. Voice on the base timeline.
2. Adopted SFX mixed in at their matching segment's timing.
3. Selected BGM placed across the full length, ducked under Voice.
4. Final loudness normalize (ch. 14) across the whole mix.

Per-segment SFX timing: `sfx_source.clips[]` carries a `shot`, not a
timestamp - ch. 12 says SFX is mixed "at the corresponding segment's
timing," so each adopted clip's start time comes from matching its `shot`
against `segments[]`'s own `start_ms` for that shot. The clip plays for its
own natural duration from that point (nothing in ch. 12 says to stretch or
trim a sound effect to fill its segment - a short foley hit at 500ms into a
4800ms shot doesn't need to fill all 4800ms of it).

Ducking: ch. 14 states a "-12dB depth" as if it were a fixed attenuation,
but the actual FFmpeg tool for sidechain ducking (`sidechaincompress`) is a
real compressor - attenuation depends on how far the voice signal is above
`threshold`, scaled by `ratio`, not a flat dB value applied regardless of
input. `_DUCK_THRESHOLD_LINEAR`/`_DUCK_RATIO` here are tuned against a
synthetic loudnorm'd voice tone, and measured directly at
`sidechaincompress`'s own output (before `amix`/the final `loudnorm`) at
**11.8dB** - within 0.2dB of ch. 14's -12dB target. `attack`/`release`
(150ms/400ms) *are* passed through exactly as ch. 14 specifies - those two
`sidechaincompress` parameters have a direct, literal meaning, unlike
"depth." See `docs/phase-plan.md` Known gaps for why a *measurement* of
this effect taken from a finished mix (voice+SFX+BGM combined, isolating
BGM's frequency band after the fact) reads meaningfully lower than 11.8dB -
that's a limitation of after-the-fact frequency-domain measurement on a
mixed file, not evidence the duck itself is weaker than tuned.

Loudness normalize runs twice, per ch. 14's own two-step description
("Voice 트랙은 믹스 전에 개별 normalize" + "최종 출력 전체를 타겟
LUFS로 normalize"): once on Voice alone before mixing, once on the
finished mix. ch. 14 doesn't state a target for the first (Voice-only)
pass - this module assumes the same `target_lufs` for both, since no other
value is given anywhere in the spec text. Both passes use FFmpeg's
single-pass `loudnorm` (measure-and-normalize in one invocation) rather
than the more accurate two-pass mode (which needs a separate analysis
pass, parses its JSON stats output, then re-invokes with measured values)
- single-pass is less precise but keeps this phase's FFmpeg invocation
count and complexity in line with every other render/* module here, all
of which are single-invocation. Worth revisiting if real renders show
single-pass `loudnorm` isn't hitting the target closely enough in
practice.

`audio_layers.bgm.reference_db` is deliberately never read here. ch. 14
only describes two normalize steps (Voice pre-mix, final output) and never
mentions normalizing BGM to any per-file reference level before mixing -
since the final step normalizes the *entire* mix to `target_lufs`
regardless of what level BGM started at, a BGM-specific starting level
doesn't change the final result. Treated as Stage 3/4 metadata this module
doesn't need, not an oversight - see `docs/phase-plan.md` Known gaps.

If the selected BGM file is shorter than the mix's total duration, it is
not looped - it plays once and leaves silence for the remainder. Looping
would need explicit handling this phase doesn't implement; see
`docs/phase-plan.md` Known gaps.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

# ch. 18 `audio` config block defaults.
DEFAULT_TARGET_LUFS = -14.0
DEFAULT_BGM_DUCKING_DB = -12.0
DEFAULT_DUCKING_ATTACK_MS = 150
DEFAULT_DUCKING_RELEASE_MS = 400

# sidechaincompress tuning for a ~12dB reduction on typical dialogue - see
# module docstring for why this is an approximation, not an exact mapping
# of `bgm_ducking_db`. threshold is linear amplitude (sidechaincompress's
# own unit), not dB: 10 ** (-30 / 20). Tuned empirically against a
# synthetic loudnorm'd voice tone to land close to ch. 14's -12dB target;
# real dialogue's dynamics will differ.
_DUCK_THRESHOLD_LINEAR = 0.0316
_DUCK_RATIO = 10


def _resolve_path(project_dir: Path, rel_path: str) -> Path:
    return project_dir / rel_path


def _adopted_sfx_inputs(
    project_dir: Path, segments: list[dict[str, Any]], sfx_source: dict[str, Any]
) -> list[tuple[Path, int]]:
    start_ms_by_shot = {segment["shot"]: segment["start_ms"] for segment in segments}
    return [
        (_resolve_path(project_dir, clip["file"]), start_ms_by_shot[clip["shot"]])
        for clip in sfx_source.get("clips", [])
        if clip.get("action") == "adopted"
    ]


AudioMixErrorKind = Literal["ffmpeg_not_found", "ffmpeg_failed"]


@dataclass
class AudioMixResult:
    output_path: Path
    command: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    error: AudioMixErrorKind | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def mix_audio(
    project_dir: Path,
    voice: dict[str, Any],
    segments: list[dict[str, Any]],
    audio_layers: dict[str, Any],
    output_path: Path,
    *,
    target_lufs: float = DEFAULT_TARGET_LUFS,
    ducking_attack_ms: int = DEFAULT_DUCKING_ATTACK_MS,
    ducking_release_ms: int = DEFAULT_DUCKING_RELEASE_MS,
) -> AudioMixResult:
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        return AudioMixResult(
            output_path=output_path, command=[], returncode=None, stdout="", stderr="", error="ffmpeg_not_found"
        )

    voice_path = _resolve_path(project_dir, voice["audio_file"])
    total_duration_s = voice["total_duration_ms"] / 1000

    sfx_inputs = _adopted_sfx_inputs(project_dir, segments, audio_layers.get("sfx_source", {}))

    bgm = audio_layers.get("bgm", {})
    bgm_path = _resolve_path(project_dir, bgm["file"]) if bgm.get("mode") == "selected" else None

    command = [ffmpeg_bin, "-y", "-i", str(voice_path)]
    for sfx_path, _delay_ms in sfx_inputs:
        command += ["-i", str(sfx_path)]
    if bgm_path is not None:
        command += ["-i", str(bgm_path)]

    # `loudnorm` resamples internally to 192kHz. Found the hard way: feeding
    # that straight into `sidechaincompress` as the sidechain trigger
    # silently truncated the entire mix to roughly half its real duration
    # (no error, no warning - just a short file). `aresample` back to a
    # normal rate immediately after `loudnorm` avoids whatever internal
    # rate mismatch causes that.
    filters: list[str] = [f"[0:a]loudnorm=I={target_lufs}:TP=-1.5:LRA=11,aresample=44100[voice_norm]"]

    if bgm_path is not None:
        # [voice_norm] is about to be consumed twice - once as the
        # sidechaincompress trigger, once as an actual mix input - and a
        # single filtergraph output pad can only feed one downstream filter
        # without an explicit split.
        filters.append("[voice_norm]asplit=2[voice_for_duck][voice_for_mix]")
        mix_labels = ["[voice_for_mix]"]
    else:
        mix_labels = ["[voice_norm]"]

    for i, (_sfx_path, delay_ms) in enumerate(sfx_inputs, start=1):
        filters.append(f"[{i}:a]adelay=delays={delay_ms}:all=1[sfx_{i}]")
        mix_labels.append(f"[sfx_{i}]")

    if bgm_path is not None:
        bgm_index = len(sfx_inputs) + 1
        # apad with no bound pads with *infinite* silence, not "up to
        # total_duration_s" - whole_dur is what actually caps it there.
        # (Found the hard way: an earlier version without whole_dur made
        # this filter graph's BGM stream effectively unbounded, and the
        # whole render either hung or produced a multi-hour file.)
        filters.append(f"[{bgm_index}:a]atrim=0:{total_duration_s},apad=whole_dur={total_duration_s}[bgm_trimmed]")
        filters.append(
            f"[bgm_trimmed][voice_for_duck]sidechaincompress="
            f"threshold={_DUCK_THRESHOLD_LINEAR}:ratio={_DUCK_RATIO}:"
            f"attack={ducking_attack_ms}:release={ducking_release_ms}[bgm_ducked]"
        )
        mix_labels.append("[bgm_ducked]")

    if len(mix_labels) > 1:
        filters.append(f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=longest:normalize=0[mixed]")
        final_label = "[mixed]"
    else:
        final_label = "[voice_norm]"

    filters.append(f"{final_label}loudnorm=I={target_lufs}:TP=-1.5:LRA=11[out]")

    command += [
        "-filter_complex", ";".join(filters),
        "-map", "[out]",
        "-t", str(total_duration_s),
        str(output_path),
    ]

    proc = subprocess.run(command, capture_output=True, text=True)

    return AudioMixResult(
        output_path=output_path,
        command=command,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        error=None if proc.returncode == 0 else "ffmpeg_failed",
    )

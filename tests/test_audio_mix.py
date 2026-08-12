import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from seoulkit_studio.render.audio_mix import mix_audio

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed on this system",
)


def _tone(path: Path, duration_ms: int, frequency: int, volume_db: float) -> None:
    duration_s = f"{duration_ms / 1000:.3f}"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={duration_s}",
            "-af", f"volume={volume_db}dB", "-ar", "44100", "-ac", "2", str(path),
        ],
        capture_output=True, text=True, check=True,
    )


def _silence(path: Path, duration_ms: int) -> None:
    duration_s = f"{duration_ms / 1000:.3f}"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={duration_s}", str(path)],
        capture_output=True, text=True, check=True,
    )


def _concat(parts: list[Path], out: Path) -> None:
    inputs = []
    for p in parts:
        inputs += ["-i", str(p)]
    labels = "".join(f"[{i}:a]" for i in range(len(parts)))
    subprocess.run(
        ["ffmpeg", "-y", *inputs, "-filter_complex", f"{labels}concat=n={len(parts)}:v=0:a=1[out]", "-map", "[out]", str(out)],
        capture_output=True, text=True, check=True,
    )


@pytest.fixture
def voice_project(tmp_path):
    """A project_dir with a 6s voice track: 2s tone (1000Hz, loud), 2s real
    digital silence, 2s tone again - simulates a speech pause, which is
    exactly what sidechain ducking needs to release during. Also lays down
    an SFX clip and a BGM bed at well-separated frequencies (1000Hz vs
    80Hz) so a lowpass filter can isolate BGM from voice/SFX in the mixed
    output later."""
    project_dir = tmp_path / "project"
    audio_dir = project_dir / "clips" / "audio"
    (audio_dir / "sfx").mkdir(parents=True)
    (audio_dir / "bgm").mkdir(parents=True)

    parts_dir = tmp_path / "parts"
    parts_dir.mkdir()
    part1, part2, part3 = parts_dir / "p1.wav", parts_dir / "p2.wav", parts_dir / "p3.wav"
    _tone(part1, 2000, frequency=1000, volume_db=-15)
    _silence(part2, 2000)
    _tone(part3, 2000, frequency=1000, volume_db=-15)
    _concat([part1, part2, part3], audio_dir / "voice_final.wav")

    # lavfi's raw sine source is itself well below 0dBFS (empirically ~-24dB
    # via volumedetect, not full scale) - blip needs a real boost, not just
    # a mild attenuation, to be clearly audible once mixed against a voice
    # track that's already been loudnorm'd up to the target LUFS.
    _tone(audio_dir / "sfx" / "blip.wav", 500, frequency=4000, volume_db=15)
    _tone(audio_dir / "bgm" / "bgm01.wav", 8000, frequency=80, volume_db=-10)

    return project_dir


def voice_dict() -> dict:
    return {"audio_file": "clips/audio/voice_final.wav", "aligned": True, "total_duration_ms": 6000}


def segments_list() -> list[dict]:
    return [
        {"shot": "1A", "start_ms": 0, "end_ms": 3000},
        {"shot": "1B", "start_ms": 3000, "end_ms": 6000},
    ]


# --- ffmpeg_not_found: pure logic, no real execution needed -----------------


def test_ffmpeg_not_found_is_reported_without_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)

    result = mix_audio(
        tmp_path, {"audio_file": "v.wav", "total_duration_ms": 1000}, [], {"sfx_source": {}, "bgm": {"mode": "none"}},
        tmp_path / "out.wav",
    )

    assert not result.ok
    assert result.error == "ffmpeg_not_found"
    assert result.command == []


# --- real FFmpeg execution: no mocking --------------------------------------


@requires_ffmpeg
def test_voice_only_mix_has_the_correct_duration_and_lands_near_target_lufs(
    voice_project, probe_duration_ms, measure_integrated_lufs
):
    audio_layers = {"sfx_source": {"mode": "none", "clips": []}, "bgm": {"mode": "none", "file": None}}
    output = voice_project.parent / "out.wav"

    result = mix_audio(voice_project, voice_dict(), segments_list(), audio_layers, output)

    assert result.ok, result.stderr
    assert abs(probe_duration_ms(output) - 6000) <= 50
    assert abs(measure_integrated_lufs(output) - (-14.0)) <= 1.5


@requires_ffmpeg
def test_never_modifies_the_source_voice_file(voice_project):
    voice_path = voice_project / "clips" / "audio" / "voice_final.wav"
    hash_before = hashlib.sha256(voice_path.read_bytes()).hexdigest()
    audio_layers = {"sfx_source": {"mode": "none", "clips": []}, "bgm": {"mode": "none", "file": None}}

    result = mix_audio(voice_project, voice_dict(), segments_list(), audio_layers, voice_project.parent / "out.wav")

    assert result.ok, result.stderr
    assert hashlib.sha256(voice_path.read_bytes()).hexdigest() == hash_before


@requires_ffmpeg
def test_ffmpeg_failure_on_missing_voice_file_is_captured_not_raised(tmp_path):
    audio_layers = {"sfx_source": {"mode": "none", "clips": []}, "bgm": {"mode": "none", "file": None}}
    voice = {"audio_file": "does_not_exist.wav", "total_duration_ms": 1000}

    result = mix_audio(tmp_path, voice, [], audio_layers, tmp_path / "out.wav")

    assert not result.ok
    assert result.error == "ffmpeg_failed"
    assert result.returncode != 0
    assert result.stderr != ""


@requires_ffmpeg
def test_adopted_sfx_is_audible_only_near_its_matching_segment_start(voice_project, measure_mean_volume_db):
    # blip.wav is 4000Hz, 500ms, attached to shot=1A (start_ms=0) - it
    # should be measurably present right at the start and gone well before
    # the segment ends.
    audio_layers = {
        "sfx_source": {"mode": "stage3_optional", "clips": [{"shot": "1A", "file": "clips/audio/sfx/blip.wav", "action": "adopted"}]},
        "bgm": {"mode": "none", "file": None},
    }
    output = voice_project.parent / "out.wav"

    result = mix_audio(voice_project, voice_dict(), segments_list(), audio_layers, output)
    assert result.ok, result.stderr

    during_blip = measure_mean_volume_db(output, start_ms=0, end_ms=400)
    after_blip = measure_mean_volume_db(output, start_ms=1000, end_ms=1900)  # still within the voice tone, blip long gone

    assert during_blip > after_blip + 5  # clearly louder while the blip is actually playing


@requires_ffmpeg
def test_discarded_sfx_is_never_mixed_in(voice_project, measure_mean_volume_db):
    # Same blip file, but action="discarded" - ch. 12: only "adopted" clips
    # are mixed. If this were mixed in by mistake, the 0-400ms window would
    # be measurably louder than it is with the blip correctly excluded.
    audio_layers_with = {
        "sfx_source": {"mode": "stage3_optional", "clips": [{"shot": "1A", "file": "clips/audio/sfx/blip.wav", "action": "adopted"}]},
        "bgm": {"mode": "none", "file": None},
    }
    audio_layers_without = {
        "sfx_source": {"mode": "stage3_optional", "clips": [{"shot": "1A", "file": "clips/audio/sfx/blip.wav", "action": "discarded"}]},
        "bgm": {"mode": "none", "file": None},
    }
    out_with = voice_project.parent / "with.wav"
    out_without = voice_project.parent / "without.wav"

    result_with = mix_audio(voice_project, voice_dict(), segments_list(), audio_layers_with, out_with)
    result_without = mix_audio(voice_project, voice_dict(), segments_list(), audio_layers_without, out_without)
    assert result_with.ok, result_with.stderr
    assert result_without.ok, result_without.stderr

    with_blip = measure_mean_volume_db(out_with, start_ms=0, end_ms=400)
    without_blip = measure_mean_volume_db(out_without, start_ms=0, end_ms=400)

    assert with_blip > without_blip + 1.5


@requires_ffmpeg
def test_bgm_mode_none_produces_no_low_frequency_bed(voice_project, measure_lowpass_mean_volume_db):
    audio_layers = {"sfx_source": {"mode": "none", "clips": []}, "bgm": {"mode": "none", "file": None}}
    output = voice_project.parent / "out.wav"

    result = mix_audio(voice_project, voice_dict(), segments_list(), audio_layers, output)
    assert result.ok, result.stderr

    # 2-4s is real digital silence in the voice track - with no BGM, that
    # window's low-frequency content should be at floor, not just quiet.
    low_freq_during_silence = measure_lowpass_mean_volume_db(output, start_ms=2000, end_ms=4000)
    assert low_freq_during_silence < -60.0


@requires_ffmpeg
def test_bgm_is_measurably_ducked_while_voice_is_present(voice_project, measure_lowpass_mean_volume_db):
    # ch. 14: BGM ducks under Voice. The actual duck (measured directly at
    # sidechaincompress's own output, before amix/final loudnorm) lands
    # within 0.2dB of ch. 14's -12dB target - see render/audio_mix.py's
    # module docstring. What this test can observe is much smaller
    # (~1.5-6dB depending on content): a 250Hz lowpass isolating bgm01.wav
    # (80Hz) from the voice tone (1000Hz) has rolloff, not a brick-wall
    # cutoff, so some voice energy leaks through and partially masks the
    # true depth in a *measurement* taken from the finished mix. The
    # assertion here is deliberately just "measurably quieter, in the
    # right direction" - not a proxy for the real ~12dB duck, which the
    # module docstring documents from a cleaner measurement point.
    # 2000-4000ms is the real silence gap in the voice track (ducking
    # should release there); 0-1500ms and 4500-6000ms are inside the voice
    # tone (ducking should be engaged, after its own 150ms attack/400ms
    # release settle).
    audio_layers = {
        "sfx_source": {"mode": "none", "clips": []},
        "bgm": {"mode": "selected", "file": "clips/audio/bgm/bgm01.wav", "reference_db": -18.0},
    }
    output = voice_project.parent / "out.wav"

    result = mix_audio(voice_project, voice_dict(), segments_list(), audio_layers, output)
    assert result.ok, result.stderr

    ducked_early = measure_lowpass_mean_volume_db(output, start_ms=0, end_ms=1500)
    released = measure_lowpass_mean_volume_db(output, start_ms=2000, end_ms=4000)
    ducked_late = measure_lowpass_mean_volume_db(output, start_ms=4500, end_ms=6000)

    assert released > ducked_early + 1.5
    assert released > ducked_late + 1.5

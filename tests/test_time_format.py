import pytest

from seoulkit_studio.render import ms_to_ffmpeg_timestamp


@pytest.mark.parametrize(
    ("ms", "expected"),
    [
        (0, "00:00:00.000"),
        (1, "00:00:00.001"),
        (999, "00:00:00.999"),
        (1_000, "00:00:01.000"),
        (59_999, "00:00:59.999"),
        (60_000, "00:01:00.000"),
        (3_599_999, "00:59:59.999"),
        (3_600_000, "01:00:00.000"),
        (12_345, "00:00:12.345"),
        (3_723_456, "01:02:03.456"),
    ],
)
def test_ms_to_ffmpeg_timestamp_boundaries(ms, expected):
    assert ms_to_ffmpeg_timestamp(ms) == expected


def test_negative_ms_is_rejected():
    with pytest.raises(ValueError):
        ms_to_ffmpeg_timestamp(-1)


@pytest.mark.parametrize("ms", list(range(0, 5000, 97)))
def test_conversion_never_drifts_from_integer_arithmetic(ms):
    # Reconstructing ms from the formatted string via pure integer parsing
    # (no float division anywhere) must reproduce the original value
    # exactly, for every millisecond in the range - not just the boundary
    # cases above. This is what "zero rounding error" actually means: not
    # "small enough," but exactly reversible.
    formatted = ms_to_ffmpeg_timestamp(ms)
    hh, mm, rest = formatted.split(":")
    ss, mmm = rest.split(".")
    reconstructed = int(hh) * 3_600_000 + int(mm) * 60_000 + int(ss) * 1_000 + int(mmm)
    assert reconstructed == ms

import pytest

from seoulkit_studio.render import ms_to_ass_timestamp, ms_to_ffmpeg_timestamp


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


@pytest.mark.parametrize(
    ("ms", "expected"),
    [
        (0, "0:00:00.00"),
        (10, "0:00:00.01"),
        (14, "0:00:00.01"),  # rounds down: 14ms is closer to 10ms than 20ms
        (15, "0:00:00.02"),  # tie: rounds up (+5 // 10)
        (16, "0:00:00.02"),
        (994, "0:00:00.99"),
        (5_000, "0:00:05.00"),
        (60_000, "0:01:00.00"),
        (3_599_994, "0:59:59.99"),
        (3_600_000, "1:00:00.00"),
    ],
)
def test_ms_to_ass_timestamp_boundaries(ms, expected):
    assert ms_to_ass_timestamp(ms) == expected


def test_ass_timestamp_rounds_to_nearest_centisecond_unlike_ffmpeg_format():
    # Unlike ms_to_ffmpeg_timestamp (zero rounding error - ASS simply can't
    # represent 999ms in a centisecond field), 999ms rounds UP to the next
    # full second here. This is the documented, deliberate difference
    # between the two formats, not a bug - pinned down explicitly so nobody
    # "fixes" it into matching ms_to_ffmpeg_timestamp's behavior later.
    assert ms_to_ffmpeg_timestamp(999) == "00:00:00.999"
    assert ms_to_ass_timestamp(999) == "0:00:01.00"


def test_negative_ms_is_rejected_by_ass_timestamp():
    with pytest.raises(ValueError):
        ms_to_ass_timestamp(-1)


@pytest.mark.parametrize("ms", list(range(0, 5000, 97)))
def test_ass_timestamp_rounding_error_never_exceeds_5ms(ms):
    formatted = ms_to_ass_timestamp(ms)
    h, rest = formatted.split(":", 1)
    mm, rest = rest.split(":", 1)
    ss, cc = rest.split(".")
    reconstructed_ms = (int(h) * 3600 + int(mm) * 60 + int(ss)) * 1_000 + int(cc) * 10
    assert abs(reconstructed_ms - ms) <= 5

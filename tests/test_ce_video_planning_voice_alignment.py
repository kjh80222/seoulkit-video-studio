"""Pure-function tests for voice_alignment.py.

None of these require faster-whisper or an audio file - normalize_tokens(),
align_tokens(), decide_timing_grade(), and map_reference_timestamps() are
plain text/data functions. transcribe_with_word_timestamps() and
align_narration_to_voice() (the only functions that touch the optional
faster-whisper dependency and real audio) are exercised separately against
the real project in the operational dry run, not here - a unit suite that
needs a downloaded ASR model and a real audio file would be slow, flaky,
and network-dependent, which is exactly what this file avoids.
"""

import pytest

from content_engine.video_planning.voice_alignment import (
    EXACT_MAX_WORD_ERROR_RATE,
    AlignmentStats,
    align_tokens,
    decide_timing_grade,
    map_reference_timestamps,
    normalize_tokens,
    transcribe_with_word_timestamps,
)


# --- normalize_tokens() --------------------------------------------------------


def test_normalize_tokens_lowercases_and_strips_punctuation():
    assert normalize_tokens("By 1953, Seoul lay in ruins.") == ["by", "1953", "seoul", "lay", "in", "ruins"]


def test_normalize_tokens_keeps_contraction_apostrophe():
    assert normalize_tokens("didn't") == ["didn't"]


def test_normalize_tokens_keeps_possessive_apostrophe():
    assert normalize_tokens("Seoul's skyline") == ["seoul's", "skyline"]


def test_normalize_tokens_normalizes_curly_quotes():
    assert normalize_tokens("didn’t") == ["didn't"]


def test_normalize_tokens_splits_on_em_dash_as_whitespace():
    assert normalize_tokens("recover — it") == ["recover", "it"]


def test_normalize_tokens_folds_spoken_year_three_word_form():
    assert normalize_tokens("nineteen fifty three") == ["1953"]


def test_normalize_tokens_folds_spoken_year_hyphenated_form():
    assert normalize_tokens("nineteen fifty-three") == ["1953"]


def test_normalize_tokens_folds_spoken_year_in_context():
    assert normalize_tokens("In nineteen fifty three, Seoul") == ["in", "1953", "seoul"]


def test_normalize_tokens_does_not_fold_ordinary_number_words():
    # "four times" is not a two-part year pattern (four < 10) - left as words.
    assert normalize_tokens("four times") == ["four", "times"]


def test_normalize_tokens_digit_year_passes_through_unchanged():
    assert normalize_tokens("in July 1953") == ["in", "july", "1953"]


def test_normalize_tokens_hyphenated_word_splits_into_parts():
    assert normalize_tokens("centuries-old character") == ["centuries", "old", "character"]


def test_normalize_tokens_empty_string():
    assert normalize_tokens("") == []


# --- align_tokens() -------------------------------------------------------------


def test_align_tokens_identical_sequences_all_matched():
    tokens = ["by", "1953", "seoul", "lay", "in", "ruins"]
    stats = align_tokens(tokens, list(tokens))
    assert stats.matched == len(tokens)
    assert stats.substituted == stats.missing == stats.extra == 0
    assert stats.word_error_rate == 0.0


def test_align_tokens_single_substitution():
    ref = ["by", "1953", "seoul", "lay", "in", "ruins"]
    hyp = ["by", "1953", "seoul", "lies", "in", "ruins"]
    stats = align_tokens(ref, hyp)
    assert stats.matched == 5
    assert stats.substituted == 1
    assert stats.missing == 0
    assert stats.extra == 0


def test_align_tokens_missing_word():
    ref = ["seoul", "had", "changed", "hands"]
    hyp = ["seoul", "changed", "hands"]
    stats = align_tokens(ref, hyp)
    assert stats.matched == 3
    assert stats.missing == 1
    assert stats.substituted == 0
    assert stats.extra == 0


def test_align_tokens_extra_word():
    ref = ["seoul", "changed", "hands"]
    hyp = ["seoul", "really", "changed", "hands"]
    stats = align_tokens(ref, hyp)
    assert stats.matched == 3
    assert stats.extra == 1
    assert stats.substituted == 0
    assert stats.missing == 0


def test_align_tokens_empty_hypothesis_all_missing():
    ref = ["a", "b", "c"]
    stats = align_tokens(ref, [])
    assert stats.matched == 0
    assert stats.missing == 3


def test_alignment_stats_word_error_rate_zero_ref_tokens():
    stats = AlignmentStats(ref_token_count=0, matched=0, substituted=0, missing=0, extra=0)
    assert stats.word_error_rate == 0.0


# --- decide_timing_grade() -------------------------------------------------------


def test_decide_timing_grade_perfect_match_is_exact():
    stats = AlignmentStats(ref_token_count=100, matched=100, substituted=0, missing=0, extra=0)
    assert decide_timing_grade(stats) == "EXACT"


def test_decide_timing_grade_at_threshold_boundary_is_exact():
    # 5 errors / 100 ref tokens = exactly the 5% threshold -> still EXACT ("<=").
    stats = AlignmentStats(ref_token_count=100, matched=95, substituted=5, missing=0, extra=0)
    assert stats.word_error_rate == EXACT_MAX_WORD_ERROR_RATE
    assert decide_timing_grade(stats) == "EXACT"


def test_decide_timing_grade_just_above_threshold_is_audio_based():
    stats = AlignmentStats(ref_token_count=100, matched=94, substituted=6, missing=0, extra=0)
    assert stats.word_error_rate > EXACT_MAX_WORD_ERROR_RATE
    assert decide_timing_grade(stats) == "AUDIO_BASED"


def test_decide_timing_grade_never_forces_exact_on_poor_match():
    stats = AlignmentStats(ref_token_count=10, matched=2, substituted=3, missing=3, extra=2)
    assert decide_timing_grade(stats) == "AUDIO_BASED"


# --- map_reference_timestamps() --------------------------------------------------


def test_map_reference_timestamps_matched_positions_only():
    ref = ["seoul", "had", "changed", "hands"]
    hyp = ["seoul", "changed", "hands"]
    hyp_ts = [(0.0, 0.5), (0.5, 1.0), (1.0, 1.5)]
    out = map_reference_timestamps(ref, hyp, hyp_ts)
    assert out[0] == (0, 500)     # "seoul"
    assert 1 not in out           # "had" - missing from hyp, no fabricated timestamp
    assert out[2] == (500, 1000)  # "changed"
    assert out[3] == (1000, 1500)  # "hands"


def test_map_reference_timestamps_raises_on_length_mismatch():
    with pytest.raises(ValueError):
        map_reference_timestamps(["a"], ["a", "b"], [(0.0, 1.0)])


# --- transcribe_with_word_timestamps() optional-dependency guard -----------------


def test_transcribe_raises_actionable_error_without_faster_whisper(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "faster_whisper":
            raise ImportError("no module named faster_whisper")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ImportError, match=r"seoulkit-studio\[alignment\]"):
        transcribe_with_word_timestamps("fake_path.wav")

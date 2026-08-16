"""Voice <-> narration forced alignment - EXACT-grade verification, CE-4f scope.

Stage 4 manual ch.04: EXACT means "timestamps aligned and verified against
the actual final Voice asset" via a waveform-based forced-alignment tool (or
equivalent) - never just "an ASR tool produced some word timestamps". This
module treats `faster-whisper` (an ASR tool, not a classical forced aligner)
as exactly that: a source of *candidate* word timestamps that must still be
verified against the approved narration text before anything is trusted as
EXACT. If the ASR transcript disagrees with the approved narration beyond a
fixed, testable threshold (EXACT_MAX_WORD_ERROR_RATE), this module refuses
to call it EXACT and the caller stays at AUDIO_BASED - never a silent
downgrade-then-promote.

Two separate responsibilities, two separate function groups:

1. Text alignment (normalize_tokens / align_tokens / decide_timing_grade /
   map_reference_timestamps) - pure functions, no I/O, fully unit-testable
   without an audio file or faster-whisper installed.
2. ASR transcription (transcribe_with_word_timestamps) - the only function
   here that touches disk/the optional faster-whisper dependency. Imports
   faster_whisper lazily inside the function body, not at module import
   time, so importing this module (or anything that imports
   content_engine.video_planning) never fails just because the optional
   `alignment` extra isn't installed - only actually calling this function
   does, with an actionable error message.

Number-word normalization is intentionally narrow: it recognizes the
two-part spoken-year convention ("nineteen fifty-three" -> "1953"), not
general cardinal numbers ("one hundred fifty-three" is NOT converted). This
project's narration only ever needs to compare spoken years against digit
years; claiming broader number-word support than that would be untested and
unverified.

No production wiring reads this module's output automatically.
`EXACT_MAX_WORD_ERROR_RATE` is a system POLICY value (the conventional
"near-perfect ASR" bar), not a physical constant, and
`AlignmentResult.timing_grade_candidate` is named "candidate" on purpose:
this module can recommend EXACT, but `edit_plan.py`'s
`HumanVoiceTimingInput.timing_grade` (the value that actually reaches
`build_edit_plan()`) is still supplied directly by whoever submits the
`edit_plan_build` payload - never auto-populated from here. As of this
module's introduction, `decide_timing_grade()`'s threshold has been
verified only against synthetic `AlignmentStats` in unit tests, not once
against a real recognized transcript (the sandboxed development
environment blocks the ASR model-weight download this needs) - so no
project has yet had a chance to actually reach EXACT through this path.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

EXACT_MAX_WORD_ERROR_RATE = 0.05
"""5% word error rate - the conventional "near-perfect" ASR-quality bar.
Below this, we cannot trust that a matched word's ASR timestamp actually
corresponds to what the Voice asset says at that instant, so the project
stays at AUDIO_BASED rather than claim a forced alignment that hasn't
actually been verified against the approved narration.
"""

_CURLY_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})

_ONES_TEENS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}


def _value_at(tokens: list[str], i: int) -> tuple[int, int] | None:
    """One "part" of a spoken year: a lone ones/teens word, or a
    tens[-ones] pair (e.g. "fifty", "fifty three", "fifty-three" already
    split into ["fifty", "three"] by tokenization). Returns (value,
    tokens_consumed) or None.
    """
    if i >= len(tokens):
        return None
    if tokens[i] in _ONES_TEENS:
        return _ONES_TEENS[tokens[i]], 1
    if tokens[i] in _TENS:
        base = _TENS[tokens[i]]
        if i + 1 < len(tokens) and tokens[i + 1] in _ONES_TEENS and _ONES_TEENS[tokens[i + 1]] < 10:
            return base + _ONES_TEENS[tokens[i + 1]], 2
        return base, 1
    return None


def _try_year_reading(tokens: list[str], start: int) -> tuple[str, int] | None:
    """Detect the two-part spoken-year convention: a first part read as a
    bare 17-20 value (centuries 1700-2099) followed immediately by a second
    0-99 part, e.g. ["nineteen", "fifty", "three"] -> "1953". Returns
    (year_string, tokens_consumed) or None if this isn't a year pattern.
    """
    first = _value_at(tokens, start)
    if first is None:
        return None
    first_value, first_len = first
    if not (10 <= first_value <= 20):
        return None
    second = _value_at(tokens, start + first_len)
    if second is None:
        return None
    second_value, second_len = second
    return str(first_value * 100 + second_value), first_len + second_len


def normalize_tokens(text: str) -> list[str]:
    """Lowercase, strip punctuation (keeping apostrophes for contractions/
    possessives), normalize curly quotes to straight, and fold spoken-year
    number-word runs into their digit form. Splits hyphenated number words
    ("fifty-three") into separate word tokens before year-pattern detection
    (hyphens elsewhere, e.g. "centuries-old", are treated the same way -
    this project's alignment only cares about word identity, not hyphen
    placement).
    """
    normalized = text.translate(_CURLY_QUOTES).lower()
    raw = re.findall(r"[a-z0-9']+", normalized.replace("-", " "))

    tokens: list[str] = []
    i = 0
    while i < len(raw):
        year = _try_year_reading(raw, i)
        if year is not None:
            tokens.append(year[0])
            i += year[1]
        else:
            tokens.append(raw[i])
            i += 1
    return tokens


@dataclass
class AlignmentStats:
    ref_token_count: int
    matched: int
    substituted: int
    missing: int
    extra: int

    @property
    def word_error_rate(self) -> float:
        if self.ref_token_count == 0:
            return 0.0
        return (self.substituted + self.missing + self.extra) / self.ref_token_count


def align_tokens(ref_tokens: list[str], hyp_tokens: list[str]) -> AlignmentStats:
    """Aligns hypothesis (ASR) tokens against reference (approved
    narration) tokens via difflib's opcode-based sequence alignment, and
    reports matched/substituted/missing(deleted from ref)/extra(inserted
    by ASR) counts - the same decomposition a WER calculation uses.
    """
    matcher = difflib.SequenceMatcher(None, ref_tokens, hyp_tokens, autojunk=False)
    matched = substituted = missing = extra = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        ref_len, hyp_len = i2 - i1, j2 - j1
        if tag == "equal":
            matched += ref_len
        elif tag == "replace":
            sub = min(ref_len, hyp_len)
            substituted += sub
            missing += max(ref_len - hyp_len, 0)
            extra += max(hyp_len - ref_len, 0)
        elif tag == "delete":
            missing += ref_len
        elif tag == "insert":
            extra += hyp_len
    return AlignmentStats(len(ref_tokens), matched, substituted, missing, extra)


def decide_timing_grade(stats: AlignmentStats) -> Literal["EXACT", "AUDIO_BASED"]:
    """EXACT iff the ASR transcript agrees with the approved narration
    closely enough (word_error_rate <= EXACT_MAX_WORD_ERROR_RATE) that its
    word timestamps can stand in for real forced alignment. This is the
    single, explicit, testable rule this module uses to decide EXACT vs
    AUDIO_BASED - never a manual/subjective judgment call.
    """
    return "EXACT" if stats.word_error_rate <= EXACT_MAX_WORD_ERROR_RATE else "AUDIO_BASED"


def map_reference_timestamps(
    ref_tokens: list[str], hyp_tokens: list[str], hyp_word_timestamps: list[tuple[float, float]],
) -> dict[int, tuple[int, int]]:
    """Maps each *matched* reference-token index to (start_ms, end_ms) taken
    from the corresponding ASR word's real timestamp, using the identical
    alignment align_tokens() computed. Unmatched reference tokens
    (substituted/missing) get no entry - callers must never fabricate a
    timestamp for a word the ASR didn't actually confirm.
    """
    if len(hyp_tokens) != len(hyp_word_timestamps):
        raise ValueError(
            f"hyp_tokens ({len(hyp_tokens)}) and hyp_word_timestamps ({len(hyp_word_timestamps)}) "
            "must be the same length - one timestamp per hypothesis token"
        )
    matcher = difflib.SequenceMatcher(None, ref_tokens, hyp_tokens, autojunk=False)
    out: dict[int, tuple[int, int]] = {}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            continue
        for k in range(i2 - i1):
            start_s, end_s = hyp_word_timestamps[j1 + k]
            out[i1 + k] = (round(start_s * 1000), round(end_s * 1000))
    return out


@dataclass
class AlignmentResult:
    """The result of one alignment run. `timing_grade_candidate` is
    deliberately not named `timing_grade`: it is this module's own
    assessment from a single ASR-vs-reference comparison, not an
    authoritative value. No code in edit_plan.py/jobs/dispatch.py/the CLI
    reads this field or auto-populates HumanVoiceTimingInput.timing_grade
    from it - a human reviews `stats` (and, ideally, spot-checks
    `ref_timestamps_ms`) and decides whether to actually put "EXACT" in a
    real edit_plan_build payload. This module can recommend EXACT; only a
    human submitting the payload can make it EXACT.
    """

    ref_tokens: list[str]
    hyp_tokens: list[str]
    stats: AlignmentStats
    timing_grade_candidate: Literal["EXACT", "AUDIO_BASED"]
    ref_timestamps_ms: dict[int, tuple[int, int]]


def transcribe_with_word_timestamps(
    audio_path: Path, model_size: str = "base.en",
) -> list[dict]:
    """Runs faster-whisper on audio_path and returns a flat, time-ordered
    list of {"word": str, "start": float, "end": float} (seconds).

    Requires the optional 'alignment' extra (`pip install
    'seoulkit-studio[alignment]'`) - raises ImportError with an actionable
    message if faster-whisper isn't installed. Imported lazily here, not at
    module scope, so this module can always be imported (and its pure
    alignment functions always tested) regardless of whether the optional
    dependency is present.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise ImportError(
            "faster-whisper is required for forced alignment but is not installed. "
            "Install with: pip install 'seoulkit-studio[alignment]'"
        ) from exc

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(audio_path), word_timestamps=True, language="en")

    words: list[dict] = []
    for segment in segments:
        for w in segment.words:
            words.append({"word": w.word, "start": w.start, "end": w.end})
    return words


def align_narration_to_voice(
    narration_text: str, audio_path: Path, model_size: str = "base.en",
) -> AlignmentResult:
    """End-to-end: transcribe audio_path, align its words against the
    approved narration_text, and compute a `timing_grade_candidate` per
    decide_timing_grade(). Requires the optional 'alignment' extra.

    This function performs all three steps EXACT grading needs (alignment
    execution, reference/ASR token-alignment verification, and timestamp
    mapping) - but its output is still only a candidate. Nothing in this
    codebase treats `AlignmentResult.timing_grade_candidate == "EXACT"` as
    self-certifying; see AlignmentResult's docstring.
    """
    ref_tokens = normalize_tokens(narration_text)
    hyp_words = transcribe_with_word_timestamps(audio_path, model_size)
    hyp_raw = [w["word"] for w in hyp_words]
    hyp_tokens = normalize_tokens(" ".join(hyp_raw))

    if len(hyp_tokens) != len(hyp_words):
        # normalize_tokens() can merge multiple raw ASR words into one
        # (spoken-year folding) or drop empty punctuation-only tokens -
        # re-tokenize word-by-word instead of joining, so hyp_tokens and
        # hyp_words stay 1:1 for map_reference_timestamps().
        hyp_tokens = []
        for w in hyp_raw:
            toks = normalize_tokens(w)
            hyp_tokens.append(toks[0] if toks else "")

    stats = align_tokens(ref_tokens, hyp_tokens)
    grade_candidate = decide_timing_grade(stats)
    timestamps = map_reference_timestamps(
        ref_tokens, hyp_tokens, [(w["start"], w["end"]) for w in hyp_words],
    )
    return AlignmentResult(
        ref_tokens=ref_tokens, hyp_tokens=hyp_tokens, stats=stats,
        timing_grade_candidate=grade_candidate, ref_timestamps_ms=timestamps,
    )

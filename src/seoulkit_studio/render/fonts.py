"""Bundled Noto Sans KR font resolution (Stage 5 spec, ch. 18).

Both `subtitle.py` (libass, via the `ass` filter's `fontsdir` option) and
`overlay.py` (`drawtext`'s `fontfile` option) need a real `.ttf` on disk for
"Noto Sans KR" - neither can be pointed at a font *name* and trusted to find
the right glyphs, because that would mean depending on whatever fontconfig
state happens to exist in a given environment. Phase 7 development found
that dependency to be a real, silent failure mode: `fc-match` never fails
even for a font that doesn't exist, and this project's dev sandbox doesn't
actually have "Noto Sans KR" installed at all - every `fc-match` resolution
silently substituted a fallback, and the fallback `drawtext` picked
(DejaVu Sans) has no Hangul glyphs, so Korean overlay text rendered as empty
tofu boxes with no error anywhere. Subtitles happened to look fine only by
coincidence - libass, unlike `drawtext`, does its own per-glyph fallback and
happened to find a CJK-capable font (WenQuanYi Zen Hei) on its own. Bundling
the actual font files and never asking fontconfig removes that dependency
entirely, for both call sites, not just the one that happened to fail first.

The two `.ttf` files (`assets/fonts/NotoSansKR-{Regular,Bold}.ttf`) are
committed as ordinary binary assets, the same way any other project would
check in a font - no encoding trick involved. That's worth spelling out
because an earlier version of this module *did* try to route the files
through a base64 text layer, specifically to work around this session's
API-based GitHub write path only accepting text content for a file. That
turned out to solve the wrong problem: the real limit isn't the transport,
it's that producing ~16MB of literal base64 text as tool-call output isn't
something an LLM turn can do at all, regardless of encoding. The two `.ttf`
files were uploaded directly through GitHub's own binary upload path
instead (a human, not this session, did that part) - once the bytes are
actually in the repo, this module just reads them like any other asset.

Both fonts are Google's "Noto Sans KR" release (SIL Open Font License 1.1,
see the bundled `OFL.txt` - the license explicitly permits bundling/
embedding with software; it only forbids selling the font by itself, which
doesn't apply here), extracted as static Regular/Bold instances from the
upstream variable font via `fonttools varLib.instancer --update-name-table`.

A missing or corrupt bundled `.ttf` is a packaging defect, not an
environment difference - the whole point of bundling was to remove "does
this environment happen to have the right font" as a variable.
`BundledFontError` fails loud for exactly that reason: there is no
reasonable fallback once fontconfig is out of the picture, so unlike the
old `fc-match`-based `FontSubstitutionIssue` (which recorded a substitution
and let rendering proceed with *some* font), there is nothing sensible to
substitute here.

The other thing that can go wrong even with a structurally valid font file
is a wrong *file* - someone commits the wrong one (e.g. a Latin-only "Noto
Sans" that isn't "Noto Sans KR" at all) and it would still pass a basic
sfnt-signature check while silently reproducing the exact tofu-box bug
bundling was meant to fix, just from a different cause.
`_assert_hangul_coverage()` is the defensive check for that: it opens the
font with `fontTools` and confirms its cmap actually contains a couple of
representative Hangul syllable codepoints before trusting the file. Since
the bundled files never change at runtime, this only needs to run once per
process - `_validated` is a simple in-process cache of paths that have
already passed, so repeated calls (once per overlay, for example) don't
re-parse the same 6MB file over and over.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"

_BUNDLED_FONT_FILES: dict[str, str] = {
    "regular": "NotoSansKR-Regular.ttf",
    "bold": "NotoSansKR-Bold.ttf",
}

# TrueType (`\x00\x01\x00\x00`), OpenType/CFF (`OTTO`), an older Mac TrueType
# tag (`true`), and a TrueType Collection (`ttcf`) - the sfnt signatures a
# genuine font file could plausibly start with.
_SFNT_MAGICS = (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf")

# U+AC00 '가' (the first Hangul syllable) and U+D55C '한' (as in "한글") -
# not exhaustive glyph coverage, just enough to catch "this isn't actually a
# Korean-capable font" rather than certify full-alphabet coverage.
_HANGUL_PROBE_CODEPOINTS = (0xAC00, 0xD55C)

_validated: set[Path] = set()


class BundledFontError(RuntimeError):
    """The bundled font asset (`.ttf`) is missing, not a valid font, or
    lacks Hangul glyph coverage."""


def _assert_hangul_coverage(ttf_path: Path) -> None:
    from fontTools.ttLib import TTFont  # local import: only needed here

    font = TTFont(str(ttf_path), lazy=True, fontNumber=0)
    cmap = font.getBestCmap() or {}
    missing = [cp for cp in _HANGUL_PROBE_CODEPOINTS if cp not in cmap]
    if missing:
        missing_hex = ", ".join(f"U+{cp:04X}" for cp in missing)
        raise BundledFontError(
            f"{ttf_path} is a structurally valid font, but its cmap is "
            f"missing Hangul codepoint(s) {missing_hex} - this is not a "
            f"Korean-capable 'Noto Sans KR' file"
        )


def resolve_bundled_font(weight: Literal["regular", "bold"]) -> Path:
    filename = _BUNDLED_FONT_FILES[weight]
    ttf_path = FONT_DIR / filename

    if not ttf_path.exists():
        raise BundledFontError(f"bundled font asset missing: {ttf_path} does not exist")

    if ttf_path not in _validated:
        if ttf_path.read_bytes()[:4] not in _SFNT_MAGICS:
            raise BundledFontError(
                f"{ttf_path} does not start with a recognized font signature - the bundled asset is corrupt"
            )
        _assert_hangul_coverage(ttf_path)
        _validated.add(ttf_path)

    return ttf_path

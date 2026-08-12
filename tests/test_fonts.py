from pathlib import Path

import pytest

from seoulkit_studio.render import fonts

_DEJAVU_SANS = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


def test_resolve_bundled_font_returns_a_real_font_for_both_weights():
    regular = fonts.resolve_bundled_font("regular")
    bold = fonts.resolve_bundled_font("bold")

    assert regular.exists() and regular.stat().st_size > 0
    assert bold.exists() and bold.stat().st_size > 0
    assert regular != bold
    # sfnt signature - proves these are real font files, not just some
    # bytes with the right filename.
    assert regular.read_bytes()[:4] in fonts._SFNT_MAGICS
    assert bold.read_bytes()[:4] in fonts._SFNT_MAGICS


def test_missing_bundled_font_raises_bundled_font_error(tmp_path, monkeypatch):
    monkeypatch.setattr(fonts, "FONT_DIR", tmp_path)  # empty directory

    with pytest.raises(fonts.BundledFontError, match="missing"):
        fonts.resolve_bundled_font("regular")


def test_a_file_with_the_wrong_signature_raises_bundled_font_error(tmp_path, monkeypatch):
    monkeypatch.setattr(fonts, "FONT_DIR", tmp_path)
    (tmp_path / "NotoSansKR-Regular.ttf").write_bytes(b"this is not a font file at all")

    with pytest.raises(fonts.BundledFontError, match="signature"):
        fonts.resolve_bundled_font("regular")


@pytest.mark.skipif(not _DEJAVU_SANS.exists(), reason="DejaVu Sans not installed on this system")
def test_a_real_font_without_hangul_coverage_is_rejected(tmp_path, monkeypatch):
    # Reproduces the exact bug class bundling was meant to fix, from a
    # different cause: a structurally valid, real font file that simply
    # isn't Korean-capable (e.g. the wrong file got committed by mistake)
    # must still be caught, not silently accepted because it "is a font."
    monkeypatch.setattr(fonts, "FONT_DIR", tmp_path)
    (tmp_path / "NotoSansKR-Regular.ttf").write_bytes(_DEJAVU_SANS.read_bytes())

    with pytest.raises(fonts.BundledFontError, match="Hangul"):
        fonts.resolve_bundled_font("regular")


def test_validation_is_cached_after_the_first_successful_call(tmp_path, monkeypatch):
    # The bundled files never change at runtime, so a real font is only
    # ever parsed/validated once per process - corrupting the file *after*
    # a successful first call must not be caught by a second call, since
    # that would mean redundantly re-parsing a 6MB file on every use. This
    # pins that deliberate performance tradeoff, not just "it works twice."
    real_regular_bytes = (fonts.FONT_DIR / "NotoSansKR-Regular.ttf").read_bytes()
    monkeypatch.setattr(fonts, "FONT_DIR", tmp_path)
    isolated_path = tmp_path / "NotoSansKR-Regular.ttf"
    isolated_path.write_bytes(real_regular_bytes)

    first = fonts.resolve_bundled_font("regular")
    assert first == isolated_path

    isolated_path.write_bytes(b"corrupted after the fact")
    second = fonts.resolve_bundled_font("regular")  # does not re-validate - cached
    assert second == isolated_path

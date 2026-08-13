from pathlib import Path

from seoulkit_studio.render.filter_escape import escape_filter_path


def test_ordinary_posix_path_is_unchanged():
    # No colon, no backslash - the identity case every existing Linux/macOS
    # call site in this project relies on. Explicit character-level
    # comparison, not just "no crash".
    assert escape_filter_path("/tmp/project/output/final.ass") == "/tmp/project/output/final.ass"


def test_ordinary_posix_path_as_pathlib_path_is_unchanged():
    assert escape_filter_path(Path("/tmp/project/output/final.ass")) == "/tmp/project/output/final.ass"


def test_colon_in_posix_path_gets_double_backslash_escaped():
    # A colon is a legal POSIX filename character, so this is directly
    # testable on Linux without any Windows-style path at all - and it's
    # the same character-level transform a Windows drive letter triggers.
    result = escape_filter_path("/tmp/fake:drive/subs.ass")
    # Exact character count, not a substring/regex check: "fake" followed
    # by exactly two literal backslash characters, then ":drive/subs.ass".
    assert result == "/tmp/fake" + "\\" * 2 + ":drive/subs.ass"
    assert result.count("\\") == 2


def test_synthetic_windows_path_is_escaped_to_the_exact_expected_runtime_string():
    # This is the case the whole module exists for. Pin the exact runtime
    # string so there is no ambiguity between "how many backslashes are in
    # the Python source literal" and "how many backslash characters
    # actually end up in the string handed to subprocess.run()".
    windows_path = r"C:\Users\foo\subs.ass"  # 3 backslashes as path separators in the source input

    result = escape_filter_path(windows_path)

    # Expected transform, spelled out character by character:
    #   1. backslash path separators -> forward slash: C:/Users/foo/subs.ass
    #   2. colon -> two literal backslashes + colon:    C\\:/Users/foo/subs.ass
    expected = "C" + "\\" * 2 + ":/Users/foo/subs.ass"
    assert result == expected
    assert result.count("\\") == 2  # not 1, not 3, not 4 - all confirmed insufficient/wrong against real FFmpeg
    assert "\\" not in result.replace("\\" * 2 + ":", "")  # no leftover path-separator backslashes anywhere else


def test_multiple_colons_each_get_escaped_independently():
    result = escape_filter_path("C:\\a:b.ass")
    assert result == "C" + "\\" * 2 + ":/a" + "\\" * 2 + ":b.ass"


def test_windows_path_with_no_colon_still_normalizes_backslashes():
    result = escape_filter_path(r"relative\sub\dir\subs.ass")
    assert result == "relative/sub/dir/subs.ass"
    assert "\\" not in result

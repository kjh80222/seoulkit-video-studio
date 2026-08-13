"""FFmpeg filtergraph path-value escaping.

Every FFmpeg filtergraph option value (`ass=`, `fontsdir=`, `fontfile=`) is
parsed by FFmpeg's own filter-description grammar, where `:` is the
option-key/value separator and `\\` is that grammar's escape character.
A Windows absolute path's drive-letter colon (`C:\\...`) is not an edge
case there - it is the shape of every absolute path on that OS - so a
path passed into one of these option values unescaped breaks on sight,
confusingly: FFmpeg reports it as a misparsed *option*, not as a bad path
(e.g. `Unable to parse option value "drive/subs.ass" as image size`,
reproduced empirically against a real `ass=`/`fontsdir=` filter with a
colon in a directory name).

The escaping rule below was determined empirically against a real FFmpeg
binary, not assumed from documentation: colon must become **two**
backslashes followed by a colon (`\\\\:`) to survive both FFmpeg's outer
filtergraph-description tokenizer and the individual filter's own
option-value parser when the value is delivered as a single argv element
(`subprocess.run([...])`, no shell involved - there is no shell-level
backslash consumption to account for separately here). Three backslashes
is tolerated but redundant; four fails again (misparsed, same as zero).
One backslash is also insufficient, but not in the same visible way for
every filter: for `ass=...:fontsdir=...`, one backslash still gets
misparsed and FFmpeg refuses to run. For `drawtext=fontfile=...`, one
backslash (and even zero - a completely unescaped colon) does *not*
misparse - FFmpeg exits 0 and silently substitutes a different font
instead, confirmed by rendering visibly non-bold text from a request for
the bold weight. Only two backslashes produced the correct bundled font
in that comparison. Both call sites were checked directly against real
output, not inferred from one filter's behavior - the same two-backslash
rule is simply what both need, for two different underlying reasons.

The `\\` -> `/` normalization step (Windows path separators to forward
slashes) is standard, widely-documented FFmpeg-on-Windows behavior, but
unlike the colon rule above it has **not** been verified against a real
Windows FFmpeg binary in this project (no Windows environment available)
- see `docs/phase-plan.md` Known gaps for what remains genuinely
unverified. It doubles as what makes the colon rule sufficient on its
own: once no literal backslash remains in the value, there is nothing
left for the escape character itself to collide with.

On an ordinary POSIX path (no colon, no backslash - true of every path
already in this project's test suite and every example project) this
function is the identity transform, so existing Linux/macOS behavior is
provably unchanged.
"""

from __future__ import annotations

from pathlib import Path


def escape_filter_path(path: Path | str) -> str:
    text = str(path).replace("\\", "/")
    return text.replace(":", "\\\\:")

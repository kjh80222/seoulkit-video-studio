"""Process exit codes for the `seoulkit-studio` CLI (Phase 11).

Five codes, one meaning each - deliberately not reused across commands so
a caller (a script, CI, another program) can branch on the number alone
without also parsing stdout:

    0  OK               - command completed and, where applicable, the plan
                           was allowed to run (`preflight`: READY;
                           `preview`: READY or REVIEW_REQUIRED, since ch. 05
                           allows Preview to run under both; `render`:
                           READY; `status`/`report`: the lookup itself
                           succeeded, independent of what it found).
    1  REVIEW_REQUIRED   - `preflight` evaluated the plan as REVIEW_REQUIRED,
                           or `render` (Final) was gate-refused for that
                           reason (`gate_error == "review_required"`, ch.
                           08: Final only runs on READY). Never used by
                           `preview`, which is allowed to run under
                           REVIEW_REQUIRED (`effective_status` is still
                           surfaced in its output either way).
    2  NOT_READY         - `preflight` evaluated the plan as NOT_READY, or
                           `preview`/`render` were gate-refused
                           (`gate_error == "not_ready"`, ch. 05/08 both
                           refuse NOT_READY).
    3  EXECUTION_FAILED  - the gate allowed the attempt to proceed but the
                           render/publish pipeline itself failed (an actual
                           FFmpeg/primitive/publish error, not a gate
                           rejection - `preflight` never returns this code,
                           since it never runs the pipeline).
    4  USAGE_ERROR       - bad arguments, a project directory that doesn't
                           exist, or any other unhandled error caught by
                           `cli/main.py`'s top-level exception handler
                           (e.g. `ffmpeg`/`ffprobe` missing on PATH).
"""

from __future__ import annotations

OK = 0
REVIEW_REQUIRED = 1
NOT_READY = 2
EXECUTION_FAILED = 3
USAGE_ERROR = 4

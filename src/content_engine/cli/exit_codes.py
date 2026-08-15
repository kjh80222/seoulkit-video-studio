"""Process exit codes for the `content-engine` CLI - CE-11 scope only.

Four codes, one meaning each, mirroring `seoulkit_studio.cli.exit_codes`'s
own "distinct code per meaning" convention rather than a generic 0/1
pass/fail:

    0  OK          - the command completed. For read-only commands
                      (`list`/`status`) this is unconditional - an empty
                      list or a project waiting on a human is a normal,
                      successfully-reported outcome, not a failure.
    1  BLOCKED      - a job the command touched (or, for `worker run`,
                      left behind after draining) is `PENDING` because a
                      human hasn't supplied something yet (CE-5 readiness
                      not met). Nothing is broken - there is simply
                      nothing more this command can do right now.
    2  JOB_FAILED   - a job the command touched ended in `FAILED` state.
                      The job's own `error_message` is printed verbatim -
                      this code never carries a second, CLI-invented
                      classification of *why* it failed.
    3  USAGE_ERROR  - bad arguments, an unknown project, a malformed or
                      wrong-shaped payload file, or any other mistake in
                      how the command was invoked, caught before any job
                      is created.
"""

from __future__ import annotations

OK = 0
BLOCKED = 1
JOB_FAILED = 2
USAGE_ERROR = 3

"""Video Studio Adapter - CE-3 scope only.

The only way Content Engine talks to Video Studio: a real OS subprocess
running the already-public `seoulkit-studio` CLI with `--json`, invoked as
`sys.executable -m seoulkit_studio.cli.main <command> <project_dir> --json`
rather than the installed console-script name, so it doesn't depend on
`PATH` containing the venv's `bin/` directory. Nothing in this module
imports `seoulkit_studio` internals, touches the Content Engine database,
knows about `job_id`/`JobState`, creates project directories, or
re-parses Render Report file contents - `output_path`/`report_path`/
`log_path` are passed through exactly as the CLI's own JSON payload
already exposes them, with no path rewriting of any kind (their
absolute-vs-relative form simply mirrors whatever form `project_dir` was
passed in as, since that's how the underlying pipeline builds them).

Command scope: exactly `preflight`, `preview`, `render`. `status`/`report`
are out of scope for CE-3. There is no timeout, retry, cancel, pause,
worker, or queue here - a caller gets one subprocess run and one result.

`preflight` is not a job - it's a read-only validation operation. A
`preflight` call that runs successfully and *discovers* REVIEW_REQUIRED or
NOT_READY is an adapter success (`ok=True`); the render was never
attempted, so there is nothing to have failed. `preview`/`render` are the
opposite: exit 1/2 there means the gate genuinely refused to run the
render, so that's an adapter/execution-shaped failure (`GATE_REJECTED`,
`ok=False`). This is why `_classify()` takes the command name - the same
numeric exit code means something different depending on which command
produced it.

Exit-code collision this module defends against (found empirically, not
assumed, during CE-3 investigation): an argparse-level usage error (e.g. a
missing required positional argument) exits with code 2 and leaves stdout
completely empty, which numerically collides with the app's own domain
NOT_READY=2. `_classify()` never trusts the exit code before it has first
confirmed stdout parses as a JSON object shaped like a real Video Studio
payload - a bare exit code is not enough to tell them apart, but a valid
payload always accompanies a real domain outcome and an argparse error
never has one.

There is deliberately no `SEOULKIT_STUDIO_NOT_FOUND` category: an
uninstalled `seoulkit_studio` was empirically confirmed (a bare venv
without the package, invoked exactly as above) to fail as a normal
subprocess exit (1) with empty stdout and a plain-text traceback on
stderr, not as a Python-level `FileNotFoundError` in the calling process
(`sys.executable` itself always exists). That shape is indistinguishable
from the argparse collision above, so both fall into the same
`ADAPTER_INVOCATION_ERROR` bucket rather than a speculative dedicated one.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class AdapterFailureCategory(str, Enum):
    GATE_REJECTED = "gate_rejected"
    EXECUTION_FAILED = "execution_failed"
    USAGE_ERROR = "usage_error"
    ADAPTER_INVOCATION_ERROR = "adapter_invocation_error"


@dataclass(frozen=True)
class AdapterResult:
    command: str
    ok: bool
    category: AdapterFailureCategory | None
    exit_code: int | None
    payload: dict | None
    stdout: str
    stderr: str


def _parse_payload(stdout: str) -> dict | None:
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _classify(command: str, exit_code: int, stdout: str) -> tuple[AdapterFailureCategory | None, dict | None]:
    payload = _parse_payload(stdout)

    if payload is not None and exit_code == 4 and "error" in payload and "exit_code" in payload:
        return AdapterFailureCategory.USAGE_ERROR, payload

    if payload is None or "effective_status" not in payload:
        return AdapterFailureCategory.ADAPTER_INVOCATION_ERROR, None

    if command == "preflight":
        if exit_code in (0, 1, 2):
            return None, payload
        return AdapterFailureCategory.ADAPTER_INVOCATION_ERROR, None

    # preview / render
    if exit_code == 0:
        return None, payload
    if exit_code in (1, 2):
        return AdapterFailureCategory.GATE_REJECTED, payload
    if exit_code == 3:
        return AdapterFailureCategory.EXECUTION_FAILED, payload
    return AdapterFailureCategory.ADAPTER_INVOCATION_ERROR, None


def _run_cli(command: str, project_dir: Path) -> AdapterResult:
    argv = [sys.executable, "-m", "seoulkit_studio.cli.main", command, str(project_dir), "--json"]

    try:
        completed = subprocess.run(argv, capture_output=True, text=True)
    except OSError as exc:
        return AdapterResult(
            command=command, ok=False, category=AdapterFailureCategory.ADAPTER_INVOCATION_ERROR,
            exit_code=None, payload=None, stdout="", stderr=str(exc),
        )

    category, payload = _classify(command, completed.returncode, completed.stdout)
    return AdapterResult(
        command=command, ok=category is None, category=category,
        exit_code=completed.returncode, payload=payload,
        stdout=completed.stdout, stderr=completed.stderr,
    )


def run_preflight(project_dir: Path) -> AdapterResult:
    return _run_cli("preflight", project_dir)


def run_preview(project_dir: Path) -> AdapterResult:
    return _run_cli("preview", project_dir)


def run_render(project_dir: Path) -> AdapterResult:
    return _run_cli("render", project_dir)

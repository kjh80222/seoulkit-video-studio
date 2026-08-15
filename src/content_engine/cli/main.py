"""`content-engine` command-line entry point - CE-11 scope only.

A thin operator adapter over already-completed CE-1-CE-6/CE-2B code, in
exactly the shape `seoulkit_studio.cli.main` already established (read
that module's own docstring): each `cmd_*` function resolves its project
(`cli/project.py`), calls one existing public Content Engine function, and
formats the result (`cli/output.py`) - none of them re-validate Stage
1-4 input, re-judge readiness, re-run a Video Studio gate, or duplicate
`jobs/dispatch.py`'s own COMPLETE/FAILED classification.

Production path for every job-creating command (`submit`/`preview`/
`final`) is exactly `create_job()` - never a direct call into
`video_studio.pipeline.render_project()` or any CE-4b-4f `build_*()`/
`write_*()` function. `worker run` is the only command that actually
executes job bodies: `--once` composes the same `claim_next_runnable_job()`/
`dispatch()` CE-2B already exposes into a bounded drain loop (not a new
queue/retry mechanism, just their existing return values observed from
outside); without `--once` it calls `run_worker()` completely unmodified,
wired to a `threading.Event` that `SIGINT` sets for a graceful stop -
`run_worker()`'s own `stop_event` parameter exists for exactly this.

`submit` accepts only the four build job types
(`stage2_input_build`/`stage3_input_build`/`clip_manifest_build`/
`edit_plan_build`) - `preview_render`/`final_render` have their own
dedicated `preview`/`final` commands so there is never more than one way
to create a given job type.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from pathlib import Path

from content_engine.cli import exit_codes, output
from content_engine.cli.project import ProjectNotFoundError, resolve_project
from content_engine.cli.status import derive_status, list_content_packages
from content_engine.config import resolve_db_path
from content_engine.content_packages.create import create_content_package
from content_engine.db.connection import get_connection
from content_engine.jobs.artifacts import (
    edit_plan_human_input_from_payload,
    qc_decisions_from_payload,
    stage2_outputs_from_payload,
)
from content_engine.jobs.claim import claim_next_runnable_job
from content_engine.jobs.creation import ContentPackageNotFoundError, UnknownJobTypeError, create_job
from content_engine.jobs.dispatch import dispatch
from content_engine.jobs.models import JobState
from content_engine.jobs.worker import run_worker

_SUBMITTABLE_JOB_TYPES = ("stage2_input_build", "stage3_input_build", "clip_manifest_build", "edit_plan_build")
_PAYLOAD_VALIDATORS = {
    "stage3_input_build": stage2_outputs_from_payload,
    "clip_manifest_build": qc_decisions_from_payload,
    "edit_plan_build": edit_plan_human_input_from_payload,
}


def _get_conn():
    return get_connection(resolve_db_path())


def cmd_create(args: argparse.Namespace) -> int:
    conn = _get_conn()
    try:
        package = create_content_package(conn, args.topic)
    except ValueError as exc:
        print(output.format_usage_error(str(exc), args.json))
        return exit_codes.USAGE_ERROR
    project = resolve_project(conn, package.id)
    print(output.format_create(project, args.json))
    return exit_codes.OK


def cmd_list(args: argparse.Namespace) -> int:
    conn = _get_conn()
    statuses = list_content_packages(conn)
    print(output.format_list(statuses, args.json))
    return exit_codes.OK


def cmd_status(args: argparse.Namespace) -> int:
    conn = _get_conn()
    try:
        status = derive_status(conn, args.project)
    except ProjectNotFoundError as exc:
        print(output.format_usage_error(str(exc), args.json))
        return exit_codes.USAGE_ERROR
    print(output.format_status(status, args.json))
    return exit_codes.OK


def cmd_submit(args: argparse.Namespace) -> int:
    conn = _get_conn()

    if args.job_type not in _SUBMITTABLE_JOB_TYPES:
        print(output.format_usage_error(
            f"job_type must be one of {_SUBMITTABLE_JOB_TYPES!r} (use 'preview'/'final' for render jobs)",
            args.json,
        ))
        return exit_codes.USAGE_ERROR

    payload = None
    if args.job_type == "stage2_input_build":
        if args.payload is not None:
            print(output.format_usage_error("stage2_input_build takes no payload file", args.json))
            return exit_codes.USAGE_ERROR
    else:
        if args.payload is None:
            print(output.format_usage_error(f"{args.job_type} requires a payload JSON file", args.json))
            return exit_codes.USAGE_ERROR
        try:
            raw = json.loads(Path(args.payload).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(output.format_usage_error(f"could not read/parse payload file: {exc}", args.json))
            return exit_codes.USAGE_ERROR
        try:
            _PAYLOAD_VALIDATORS[args.job_type](raw)
        except (KeyError, TypeError) as exc:
            print(output.format_usage_error(f"payload does not match expected shape: {exc}", args.json))
            return exit_codes.USAGE_ERROR
        payload = raw

    try:
        job = create_job(conn, args.project, args.job_type, payload=payload)
    except (ContentPackageNotFoundError, UnknownJobTypeError) as exc:
        print(output.format_usage_error(str(exc), args.json))
        return exit_codes.USAGE_ERROR

    print(output.format_submit(job, args.json))
    return exit_codes.OK


def _create_render_job(args: argparse.Namespace, job_type: str) -> int:
    conn = _get_conn()
    try:
        job = create_job(conn, args.project, job_type)
    except ContentPackageNotFoundError as exc:
        print(output.format_usage_error(str(exc), args.json))
        return exit_codes.USAGE_ERROR
    print(output.format_submit(job, args.json))
    return exit_codes.OK


def cmd_preview(args: argparse.Namespace) -> int:
    return _create_render_job(args, "preview_render")


def cmd_final(args: argparse.Namespace) -> int:
    return _create_render_job(args, "final_render")


def cmd_worker(args: argparse.Namespace) -> int:
    conn = _get_conn()

    if not args.once:
        stop_event = threading.Event()
        signal.signal(signal.SIGINT, lambda *_a: stop_event.set())
        run_worker(conn, poll_interval_seconds=args.poll_interval, stop_event=stop_event)
        return exit_codes.OK

    processed = completed = failed = 0
    while True:
        job = claim_next_runnable_job(conn)
        if job is None:
            break
        dispatch(conn, job)
        processed += 1
        state = conn.execute("SELECT state FROM jobs WHERE id = ?", (job.id,)).fetchone()[0]
        if state == JobState.COMPLETE.value:
            completed += 1
        elif state == JobState.FAILED.value:
            failed += 1

    blocked = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE state = ? AND job_type IN ('preview_render', 'final_render')",
        (JobState.PENDING.value,),
    ).fetchone()[0]

    print(output.format_worker_result(processed, completed, failed, blocked, args.json))
    if failed:
        return exit_codes.JOB_FAILED
    if blocked:
        return exit_codes.BLOCKED
    return exit_codes.OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="content-engine")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def _add_json(p: argparse.ArgumentParser) -> None:
        p.add_argument("--json", action="store_true", help="emit machine-readable JSON output")

    create_p = subparsers.add_parser("create", help="Create a new ContentPackage/project")
    create_p.add_argument("topic")
    _add_json(create_p)
    create_p.set_defaults(func=cmd_create)

    list_p = subparsers.add_parser("list", help="List existing projects, newest first")
    _add_json(list_p)
    list_p.set_defaults(func=cmd_list)

    status_p = subparsers.add_parser("status", help="Show a project's derived status and next action")
    status_p.add_argument("project")
    _add_json(status_p)
    status_p.set_defaults(func=cmd_status)

    submit_p = subparsers.add_parser("submit", help="Create a stage2/3/clip_manifest/edit_plan build job")
    submit_p.add_argument("project")
    submit_p.add_argument("job_type")  # validated in cmd_submit(), not via argparse choices=,
    # so an invalid value returns exit_codes.USAGE_ERROR(3) with a JSON-compatible message
    # instead of argparse's own exit(2) - keeping a single, documented exit code contract.
    submit_p.add_argument("payload", nargs="?", default=None, metavar="payload.json")
    _add_json(submit_p)
    submit_p.set_defaults(func=cmd_submit)

    preview_p = subparsers.add_parser("preview", help="Request a preview render")
    preview_p.add_argument("project")
    _add_json(preview_p)
    preview_p.set_defaults(func=cmd_preview)

    final_p = subparsers.add_parser("final", help="Approve and request a final render")
    final_p.add_argument("project")
    _add_json(final_p)
    final_p.set_defaults(func=cmd_final)

    worker_p = subparsers.add_parser("worker", help="Run the job worker")
    worker_subparsers = worker_p.add_subparsers(dest="worker_command", required=True)
    worker_run_p = worker_subparsers.add_parser("run", help="Claim and dispatch pending jobs")
    worker_run_p.add_argument("--once", action="store_true", help="drain currently pending jobs, then exit")
    worker_run_p.add_argument("--poll-interval", type=float, default=5.0, dest="poll_interval")
    _add_json(worker_run_p)
    worker_run_p.set_defaults(func=cmd_worker)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    json_output = args.json

    try:
        return args.func(args)
    except Exception as exc:  # top-level catch-all: unexpected DB/filesystem errors
        print(output.format_usage_error(f"unexpected error: {exc}", json_output))
        return exit_codes.USAGE_ERROR


if __name__ == "__main__":
    sys.exit(main())

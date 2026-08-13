"""`seoulkit-studio` command-line entry point (Phase 11).

A thin orchestration layer, nothing more: every `cmd_*` function below
does exactly three things - resolve paths (`cli/project.py`), call one
existing public API function, format the result (`cli/output.py`). No
`cmd_*` function recomputes `effective_status`, re-implements the ch. 05
gate, assigns a render version, or touches publish/rollback logic - all of
that already lives in `execution/pipeline.py::evaluate_plan()` and
`render/report.py::render_preview_and_report()`/`render_final_and_report()`,
which are called exactly once each per command.

Command surface: `preflight` (alias `validate`), `preview`, `render`
(alias `final`), `status`, `report`. Aliases are wired via `argparse`'s
own `aliases=` kwarg on the same subparser object, so both names share one
`set_defaults(func=...)` and there is exactly one implementation per
command, never two.

`status`/`report` are read-only lookups over files `preview`/`render`
already wrote (`cli/report_lookup.py`) - they never call `evaluate_plan()`
or any `render_*` function, per Phase 11's explicit approval constraint.

Exit codes: see `cli/exit_codes.py` for the full table. The one
CLI-level existence check this module makes - `project_dir.is_dir()` -
is deliberately about the *directory itself*, not about
`edit_plan.json`/`clip_manifest.json` inside it; those two are always
handed to the existing Stage 5 validation as concrete paths regardless of
whether they exist, exactly as `cli/project.py` documents, so a missing
`edit_plan.json` correctly surfaces as NOT_READY (exit 2) through the
normal `evaluate_plan()` path, not as a usage error here.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from seoulkit_studio.cli import output
from seoulkit_studio.cli import exit_codes
from seoulkit_studio.cli import report_lookup
from seoulkit_studio.cli.project import resolve_project_paths
from seoulkit_studio.execution import evaluate_plan
from seoulkit_studio.render.report import render_final_and_report, render_preview_and_report


class _UsageError(Exception):
    pass


def cmd_preflight(args: argparse.Namespace) -> int:
    paths = resolve_project_paths(args.project_dir, args.clip_manifest)
    result = evaluate_plan(paths.edit_plan_path, paths.project_dir, paths.clip_manifest_path)
    print(output.format_validate(result, args.json))

    if result.effective_status == "READY":
        return exit_codes.OK
    if result.effective_status == "REVIEW_REQUIRED":
        return exit_codes.REVIEW_REQUIRED
    return exit_codes.NOT_READY


def cmd_preview(args: argparse.Namespace) -> int:
    paths = resolve_project_paths(args.project_dir, args.clip_manifest)
    result, report = render_preview_and_report(paths.edit_plan_path, paths.project_dir, paths.clip_manifest_path)
    print(output.format_render(result, report, args.json))

    if result.gate_error == "not_ready":
        return exit_codes.NOT_READY
    if not result.ok:
        return exit_codes.EXECUTION_FAILED
    return exit_codes.OK


def cmd_render(args: argparse.Namespace) -> int:
    paths = resolve_project_paths(args.project_dir, args.clip_manifest)
    result, report = render_final_and_report(paths.edit_plan_path, paths.project_dir, paths.clip_manifest_path)
    print(output.format_render(result, report, args.json))

    if result.gate_error == "review_required":
        return exit_codes.REVIEW_REQUIRED
    if result.gate_error == "not_ready":
        return exit_codes.NOT_READY
    if not result.ok:
        return exit_codes.EXECUTION_FAILED
    return exit_codes.OK


def cmd_status(args: argparse.Namespace) -> int:
    latest = report_lookup.latest_report(args.project_dir)
    if latest is None:
        print(output.format_no_renders(args.project_dir, args.json))
        return exit_codes.OK

    log_path = report_lookup.log_path_for_version(args.project_dir, latest.version)
    print(output.format_status(latest, log_path, args.json))
    return exit_codes.OK


def cmd_report(args: argparse.Namespace) -> int:
    if args.version is not None:
        found = report_lookup.find_report(args.project_dir, args.version)
        if found is None:
            print(output.format_report_not_found(args.project_dir, args.version, args.json))
            return exit_codes.USAGE_ERROR
    else:
        found = report_lookup.latest_report(args.project_dir)
        if found is None:
            print(output.format_no_renders(args.project_dir, args.json))
            return exit_codes.OK

    print(output.format_report(found, args.json))
    return exit_codes.OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="seoulkit-studio")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def _add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("project_dir", type=Path)
        p.add_argument("--json", action="store_true")

    def _add_manifest_override(p: argparse.ArgumentParser) -> None:
        p.add_argument("--clip-manifest", type=Path, default=None, dest="clip_manifest")

    preflight_p = subparsers.add_parser(
        "preflight", aliases=["validate"], help="Evaluate an edit_plan.json without rendering"
    )
    _add_common(preflight_p)
    _add_manifest_override(preflight_p)
    preflight_p.set_defaults(func=cmd_preflight)

    preview_p = subparsers.add_parser("preview", help="Render a watermarked Preview")
    _add_common(preview_p)
    _add_manifest_override(preview_p)
    preview_p.set_defaults(func=cmd_preview)

    render_p = subparsers.add_parser("render", aliases=["final"], help="Render the Final output")
    _add_common(render_p)
    _add_manifest_override(render_p)
    render_p.set_defaults(func=cmd_render)

    status_p = subparsers.add_parser("status", help="Show the latest render's status (read-only)")
    _add_common(status_p)
    status_p.set_defaults(func=cmd_status)

    report_p = subparsers.add_parser("report", help="Show a previously-written Render Report (read-only)")
    _add_common(report_p)
    report_p.add_argument("--version", type=int, default=None)
    report_p.set_defaults(func=cmd_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    json_output = args.json

    try:
        if not args.project_dir.is_dir():
            raise _UsageError(f"project directory not found: {args.project_dir}")
        return args.func(args)
    except _UsageError as exc:
        print(output.format_usage_error(str(exc), json_output))
        return exit_codes.USAGE_ERROR
    except Exception as exc:  # top-level catch-all: ffmpeg/ffprobe missing, etc.
        print(output.format_usage_error(f"unexpected error: {exc}", json_output))
        return exit_codes.USAGE_ERROR


if __name__ == "__main__":
    sys.exit(main())

"""Human-readable + `--json` formatting for CLI command output (Phase 11).

Every function here only reformats values already computed by the public
API (`EvaluationResult`, `RenderResult`, `RenderReport`, or a
`report_lookup.DiscoveredReport` read straight off disk) - no new
judgment, no new path computation. When `json_output` is true the return
value is always a single `json.dumps(...)` object and nothing else, so a
caller can always do `json.loads(...)` on stdout regardless of which
command or outcome produced it - errors included (see `format_usage_error()`).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from seoulkit_studio.cli.report_lookup import DiscoveredReport
from seoulkit_studio.execution import EvaluationResult
from seoulkit_studio.preflight import PreflightIssue
from seoulkit_studio.render.pipeline import RenderResult
from seoulkit_studio.render.report import RenderReport


def _issue_dicts(issues: list[PreflightIssue]) -> list[dict]:
    return [asdict(issue) for issue in issues]


def _dump(payload: dict) -> str:
    return json.dumps(payload, indent=2, default=str)


def _issue_lines(issues: list[PreflightIssue]) -> list[str]:
    lines = []
    for issue in issues:
        ref = f" ({issue.segment_ref})" if issue.segment_ref else ""
        lines.append(f"  [{issue.severity}] {issue.code}{ref}: {issue.detail}")
    return lines


def format_validate(result: EvaluationResult, json_output: bool) -> str:
    issues = (list(result.preflight_result.issues) if result.preflight_result else []) + result.clip_manifest_issues

    payload = {
        "command": "preflight",
        "effective_status": result.effective_status,
        "plan_status_as_declared": result.plan_status_as_declared,
        "studio_execution_result": result.studio_execution_result,
        "load_error": asdict(result.load_error) if result.load_error else None,
        "issues": _issue_dicts(issues),
    }
    if json_output:
        return _dump(payload)

    lines = [
        f"Status: {payload['effective_status']}",
        f"Declared: {payload['plan_status_as_declared']}",
        f"Execution result: {payload['studio_execution_result']}",
    ]
    if payload["load_error"]:
        lines.append(f"Load error: {payload['load_error']['kind']}: {payload['load_error']['message']}")
    lines.append("Issues:" if issues else "Issues: none")
    lines += _issue_lines(issues)
    return "\n".join(lines)


def format_render(result: RenderResult, report: RenderReport, json_output: bool) -> str:
    payload = {
        "command": report.render_type,
        "ok": result.ok,
        "gate_error": result.gate_error,
        "effective_status": result.effective_status,
        "plan_status_as_declared": result.plan_status_as_declared,
        "studio_execution_result": result.studio_execution_result,
        "output_path": str(result.output_path) if result.output_path else None,
        "report_path": str(result.report_path) if result.report_path else None,
        "log_path": str(result.log_path) if result.log_path else None,
        "render_version": report.render_version,
        "issues": _issue_dicts(result.preflight_issues),
    }
    if json_output:
        return _dump(payload)

    lines = [
        f"OK: {payload['ok']}",
        f"Effective status: {payload['effective_status']}",
    ]
    if payload["gate_error"]:
        lines.append(f"Gate rejected: {payload['gate_error']}")
    if payload["output_path"]:
        lines.append(f"Output: {payload['output_path']}")
    if payload["report_path"]:
        lines.append(f"Report: {payload['report_path']}")
    if payload["log_path"]:
        lines.append(f"Log: {payload['log_path']}")
    if result.preflight_issues:
        lines.append("Issues:")
        lines += _issue_lines(result.preflight_issues)
    return "\n".join(lines)


def format_no_renders(project_dir: Path, json_output: bool) -> str:
    payload = {"project_dir": str(project_dir), "has_renders": False}
    if json_output:
        return _dump(payload)
    return f"No renders found yet for {project_dir}"


def format_status(discovered: DiscoveredReport, log_path: Path | None, json_output: bool) -> str:
    data = discovered.data
    payload = {
        "has_renders": True,
        "render_version": data.get("render_version"),
        "render_type": data.get("render_type"),
        "effective_status": data.get("effective_status"),
        "studio_execution_result": data.get("studio_execution_result"),
        "output_file": data.get("output_file"),
        "report_path": str(discovered.report_path),
        "log_path": str(log_path) if log_path else None,
    }
    if json_output:
        return _dump(payload)

    lines = [
        f"Latest render: {payload['render_version']} ({payload['render_type']})",
        f"Effective status: {payload['effective_status']}",
        f"Execution result: {payload['studio_execution_result']}",
        f"Output file: {payload['output_file'] or '(none)'}",
        f"Report: {payload['report_path']}",
    ]
    if payload["log_path"]:
        lines.append(f"Log: {payload['log_path']}")
    return "\n".join(lines)


def format_report_not_found(project_dir: Path, version: int, json_output: bool) -> str:
    payload = {"project_dir": str(project_dir), "version": f"v{version:03d}", "found": False}
    if json_output:
        return _dump(payload)
    return f"No render report found for v{version:03d} in {project_dir}"


def format_report(discovered: DiscoveredReport, json_output: bool) -> str:
    if json_output:
        return _dump(discovered.data)

    data = discovered.data
    lines = [
        f"Render: {data.get('render_version')} ({data.get('render_type')})",
        f"Effective status: {data.get('effective_status')}",
        f"Execution result: {data.get('studio_execution_result')}",
        f"Output file: {data.get('output_file') or '(none)'}",
        f"Started: {data.get('started_at')}",
        f"Completed: {data.get('completed_at')} ({data.get('duration_ms')}ms)",
    ]
    qc_results = data.get("qc_results") or []
    if qc_results:
        lines.append("QC:")
        for qc in qc_results:
            lines.append(f"  [{qc.get('result')}] {qc.get('check')}: {qc.get('detail')}")
    errors = data.get("errors") or []
    if errors:
        lines.append("Errors:")
        for err in errors:
            lines.append(f"  [{err.get('code')}] {err.get('stage')}: {err.get('message')}")
    return "\n".join(lines)


def format_usage_error(message: str, json_output: bool) -> str:
    payload = {"error": message, "exit_code": 4}
    if json_output:
        return _dump(payload)
    return f"Error: {message}"

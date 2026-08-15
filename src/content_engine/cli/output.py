"""Human-readable + `--json` formatting for the `content-engine` CLI - CE-11 scope only.

Mirrors `seoulkit_studio.cli.output`'s own convention: every function only
reformats values already computed elsewhere (a `ResolvedProject`, a
`ProjectStatus`, a `Job`) - no new judgment, no new path computation.
`--json` output is always a single `json.dumps(...)` object, so a caller
can always `json.loads()` stdout regardless of which command or outcome
produced it - errors included (`format_usage_error()`).
"""

from __future__ import annotations

import json

from content_engine.cli.project import ResolvedProject
from content_engine.cli.status import ProjectStatus
from content_engine.jobs.models import Job


def _dump(payload: dict) -> str:
    return json.dumps(payload, indent=2, default=str)


def format_create(project: ResolvedProject, json_output: bool) -> str:
    payload = {
        "content_package_id": project.content_package_id,
        "topic": project.topic,
        "project_dir": project.project_dir,
    }
    if json_output:
        return _dump(payload)
    return (
        f"Created project {project.content_package_id}\n"
        f"  topic: {project.topic}\n"
        f"  project_dir: {project.project_dir}"
    )


def _status_dict(status: ProjectStatus) -> dict:
    return {
        "content_package_id": status.content_package_id,
        "topic": status.topic,
        "project_dir": status.project_dir,
        "created_at": status.created_at,
        "status": status.status,
        "detail": status.detail,
        "next_action": status.next_action,
    }


def format_status(status: ProjectStatus, json_output: bool) -> str:
    if json_output:
        return _dump(_status_dict(status))
    return (
        f"Project: {status.content_package_id} ({status.topic})\n"
        f"Project dir: {status.project_dir}\n"
        f"Status: {status.status}\n"
        f"Detail: {status.detail}\n"
        f"Next action: {status.next_action}"
    )


def format_list(statuses: list[ProjectStatus], json_output: bool) -> str:
    if json_output:
        return _dump({"projects": [_status_dict(s) for s in statuses]})
    if not statuses:
        return "No projects found."
    return "\n".join(f"{s.content_package_id}  {s.created_at}  [{s.status}]  {s.topic}" for s in statuses)


def format_submit(job: Job, json_output: bool) -> str:
    payload = {"job_id": job.id, "job_type": job.job_type, "state": job.state.value}
    if json_output:
        return _dump(payload)
    return f"Created job {job.id} ({job.job_type}), state={job.state.value}"


def format_worker_result(processed: int, completed: int, failed: int, blocked: int, json_output: bool) -> str:
    payload = {"processed": processed, "completed": completed, "failed": failed, "blocked_pending": blocked}
    if json_output:
        return _dump(payload)
    return (
        f"Processed {processed} job(s): {completed} completed, {failed} failed, "
        f"{blocked} still pending (blocked on human input)."
    )


def format_usage_error(message: str, json_output: bool) -> str:
    payload = {"error": message}
    if json_output:
        return _dump(payload)
    return f"Error: {message}"

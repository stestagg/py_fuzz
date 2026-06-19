from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from common import utc_now

from pyfuzz.project import Project
from pyfuzz.summary import summarize_fuzzing

from .registry import Registry

if TYPE_CHECKING:
    from server import DashboardSocket

registry = Registry()
handler = registry.handler


def list_projects() -> list[str]:
    return sorted(Project.projects())


def load_project(name: str | None) -> Project | None:
    if not name:
        return None
    return Project.load(name)


def project_snapshot(project: Project) -> dict[str, Any]:
    config = asdict(project)
    config.pop("_name", None)
    return {
        "name": project.name,
        "repo": project.repo,
        "cloneRef": project.clone_ref,
        "fuzzTarget": project.fuzz_target,
        "config": config,
        "importantConfig": {
            "repo": project.repo,
            "cloneRef": project.clone_ref,
            "prId": project.pr_id,
            "branch": project.branch,
            "commit": project.commit,
            "asan": project.asan,
            "harness": project.harness,
            "vmMem": project.vm_mem,
            "ncpu": project.ncpu,
            "fuzzTimeoutMs": project.fuzz_timeout_ms,
            "fuzzMemLimit": project.fuzz_mem_limit,
        },
        "paths": {
            "root": str(project.path()),
            "config": str(project.config_path),
        },
    }


def summary_payload(project: Project) -> dict[str, Any]:
    try:
        values = summarize_fuzzing(project)
        return {
            "status": "ready",
            "updatedAt": utc_now(),
            "values": values,
            "error": None,
        }
    except Exception as exc:
        return {
            "status": "unavailable",
            "updatedAt": utc_now(),
            "values": {"project": project.name},
            "error": str(exc),
        }


def dashboard_payload(project: Project | None) -> dict[str, Any]:
    if project is None:
        return {
            "selectedProject": None,
            "summary": None,
        }
    return {
        "selectedProject": project_snapshot(project),
        "summary": summary_payload(project),
    }


@handler("projects:list", requires_project=False)
async def projects_list(socket: DashboardSocket, message: dict[str, Any]) -> Any:
    return {"projects": list_projects()}


@handler("project:get", requires_project=False)
async def project_get(socket: DashboardSocket, message: dict[str, Any]) -> Any:
    return {
        "projects": list_projects(),
        **dashboard_payload(socket.project),
    }


@handler("project:select", requires_project=False)
async def project_select(socket: DashboardSocket, message: dict[str, Any]) -> Any:
    project_name = str(message.get("projectName") or "")
    socket.project = load_project(project_name)
    return {
        "projects": list_projects(),
        **dashboard_payload(socket.project),
    }


@handler("summary:refresh")
async def summary_refresh(socket: DashboardSocket, message: dict[str, Any]) -> Any:
    # Lightweight, user-initiated refresh — not worth a tracked task / toast.
    summary = await asyncio.to_thread(summary_payload, socket.project)
    return {"summary": summary}

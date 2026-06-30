from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from json_repair import repair_json

from pyfuzz.project import Project
from pyfuzz.summary import summarize_fuzzing

from .tasks import utc_now


PROJECT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def list_projects() -> list[str]:
    return sorted(Project.projects())


def find_default_project(start: Path) -> str | None:
    current = start.resolve()
    while True:
        marker = current / ".pyfuzz_project"
        if marker.is_file():
            value = marker.read_text(encoding="utf-8").strip()
            return value or None
        if current.parent == current:
            return None
        current = current.parent


def validate_project_name(name: str) -> str:
    if not PROJECT_NAME_PATTERN.fullmatch(name):
        raise ValueError("Project names must start with a letter or number and contain only letters, numbers, '.', '_' or '-'")
    return name


def create_project(name: str) -> Project:
    validate_project_name(name)
    return Project.create(name)


def project_config(project: Project) -> dict[str, Any]:
    config = dataclasses.asdict(project)
    config.pop("_name", None)
    return config


def _project_defaults() -> dict[str, Any]:
    defaults: dict[str, Any] = {}
    for field in dataclasses.fields(Project):
        if field.name.startswith("_"):
            continue
        if field.default is not dataclasses.MISSING:
            defaults[field.name] = field.default
        elif field.default_factory is not dataclasses.MISSING:
            defaults[field.name] = field.default_factory()
    return defaults


def update_project_config(project: Project, raw: str) -> Project:
    repaired = repair_json(raw)
    if not repaired:
        raise ValueError("The configuration is not valid JSON and could not be repaired")
    try:
        data = json.loads(repaired)
    except json.JSONDecodeError as exc:
        raise ValueError("The configuration is not valid JSON and could not be repaired") from exc
    if not isinstance(data, dict):
        raise ValueError("Project configuration must be a JSON object")

    allowed = {field.name for field in dataclasses.fields(Project) if not field.name.startswith("_")}
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"Unknown project configuration field: {unknown[0]}")
    try:
        Project(**data)
    except TypeError as exc:
        raise ValueError(str(exc)) from exc

    defaults = _project_defaults()
    stripped = {key: value for key, value in data.items() if key not in defaults or value != defaults[key]}
    contents = json.dumps(stripped, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix="project-", suffix=".json", dir=project.config_path.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(contents, encoding="utf-8")
        os.replace(temporary, project.config_path)
    finally:
        temporary.unlink(missing_ok=True)
    return Project.load(project.name)


def project_snapshot(project: Project) -> dict[str, Any]:
    config = project_config(project)
    clone_kind, clone_value = project.clone_ref
    return {
        "name": project.name,
        "repo": project.repo,
        "cloneRef": f"{clone_kind}:{clone_value}",
        "fuzzTarget": project.fuzz_target,
        "config": config,
        "importantConfig": {
            "repo": project.repo,
            "cloneRef": f"{clone_kind}:{clone_value}",
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
        "paths": {"root": str(project.path()), "config": str(project.config_path)},
    }


def summary_payload(project: Project) -> dict[str, Any]:
    try:
        values = summarize_fuzzing(project)
        return {"status": "ready", "updatedAt": utc_now(), "values": values, "error": None}
    except Exception as exc:
        return {
            "status": "unavailable",
            "updatedAt": utc_now(),
            "values": {"project": project.name},
            "error": str(exc),
        }

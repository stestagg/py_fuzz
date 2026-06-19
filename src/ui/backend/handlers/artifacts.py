from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyfuzz.analysis import (
    Artifact,
    ArtifactType,
    analyze_core_artifact,
    get_artifact,
    list_artifacts,
    sync_artifacts,
)
from pyfuzz.lldb import analyze_core
from pyfuzz.llm import (
    DEFAULT_OPENAI_MODEL,
    create_openai_client,
    describe_artifact,
    validate_artifact_result_filename,
)
from pyfuzz.project import Project

from .registry import Registry

if TYPE_CHECKING:
    from server import DashboardSocket

registry = Registry()
handler = registry.handler

GROUP_FILE_READ_LIMIT = 4096
GROUP_LABEL_LIMIT = 100


@dataclass(frozen=True)
class ArtifactGroupSpec:
    raw: str
    kind: str
    argument: str | None = None


def validate_group_filename(filename: str, prefix: str) -> str:
    if not filename:
        raise ValueError(f"{prefix}: requires a filename")
    path = Path(filename)
    if path.is_absolute() or filename in (".", "..") or "/" in filename or "\\" in filename:
        raise ValueError(f"{prefix}: filename must be local to the artifact directory")
    return filename


def parse_artifact_group_spec(raw: str) -> ArtifactGroupSpec:
    spec = str(raw).strip()
    if not spec:
        raise ValueError("Grouping spec cannot be empty")
    if spec == "type":
        return ArtifactGroupSpec(raw=spec, kind="type")

    key, sep, rest = spec.partition(":")
    if not sep:
        raise ValueError(f"Unknown grouping spec {spec!r}")
    if key == "file":
        return ArtifactGroupSpec(raw=spec, kind="file", argument=validate_group_filename(rest, "file"))
    if key == "exists":
        return ArtifactGroupSpec(raw=spec, kind="exists", argument=validate_group_filename(rest, "exists"))
    if key == "meta":
        if not rest:
            raise ValueError("meta: requires a key")
        return ArtifactGroupSpec(raw=spec, kind="meta", argument=rest)
    raise ValueError(f"Unknown grouping spec {spec!r}")


def parse_artifact_group_specs(raw_specs: Any) -> list[ArtifactGroupSpec]:
    if raw_specs is None:
        return []
    if not isinstance(raw_specs, list):
        raise ValueError("groupSpecs must be a list")
    return [parse_artifact_group_spec(str(spec)) for spec in raw_specs]


def group_label(value: str) -> str:
    label = " ".join(value.split())
    if not label:
        label = "(empty)"
    if len(label) > GROUP_LABEL_LIMIT:
        label = label[: GROUP_LABEL_LIMIT - 3].rstrip() + "..."
    return label


def stringify_meta_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True)


def artifact_group_value(artifact: Artifact, spec: ArtifactGroupSpec) -> dict[str, str]:
    if spec.kind == "type":
        value = artifact.type.value
        return {"value": value, "label": value}

    argument = spec.argument or ""
    if spec.kind == "meta":
        if argument not in artifact.meta:
            value = f"missing {argument}"
        else:
            value = stringify_meta_value(artifact.meta[argument])
        return {"value": value, "label": group_label(value)}

    path = artifact.dir / argument
    if spec.kind == "exists":
        value = f"has {argument}" if path.exists() else f"missing {argument}"
        return {"value": value, "label": value}

    if spec.kind == "file":
        if not path.is_file():
            value = f"missing {argument}"
            return {"value": value, "label": value}
        with path.open("rb") as handle:
            value = handle.read(GROUP_FILE_READ_LIMIT).decode("utf-8", errors="replace")
        return {"value": value, "label": group_label(value)}

    raise ValueError(f"Unsupported grouping spec kind: {spec.kind}")


def artifact_payload(artifact: Artifact, group_specs: list[ArtifactGroupSpec] | None = None) -> dict[str, Any]:
    input_path = artifact.dir / "input.txt"
    return {
        "hash": artifact.hash,
        "type": artifact.type.value,
        "path": str(artifact.dir),
        "hasInput": input_path.exists(),
        "inputSize": input_path.stat().st_size if input_path.exists() else None,
        "groupValues": [
            artifact_group_value(artifact, spec)
            for spec in (group_specs or [])
        ],
    }


def read_artifact_files(artifact: Artifact, project: Project) -> list[dict[str, Any]]:
    project_root = artifact.dir.parents[1].resolve()
    files = []
    for path in sorted(artifact.dir.iterdir()):
        if path.name == "meta.json":
            continue
        if path.name.endswith(".marker"):
            continue
        if path.is_symlink():
            resolved = (path.parent / path.readlink()).resolve()
            try:
                display_target = str(resolved.relative_to(project_root))
            except ValueError:
                display_target = str(path.readlink())
            lldb_command = None
            if path.name == "core":
                core_rel = resolved.relative_to(project.path("cores"))
                lldb_command = f"lldb -c /pfm/cores/{core_rel} {project.fuzz_target}"
            files.append({
                "name": path.name,
                "symlink": display_target,
                "preview": None,
                "previewComplete": False,
                "isBinary": False,
                "lldbCommand": lldb_command,
            })
        elif path.suffix == ".txt":
            text = path.read_text("utf-8", errors="replace")
            lines = text.splitlines()
            files.append({
                "name": path.name,
                "symlink": None,
                "preview": "\n".join(lines[:10]),
                "previewComplete": len(lines) <= 10,
                "isBinary": False,
            })
        else:
            try:
                text = path.read_text("utf-8")
                lines = text.splitlines()
                files.append({
                    "name": path.name,
                    "symlink": None,
                    "preview": "\n".join(lines[:10]),
                    "previewComplete": len(lines) <= 10,
                    "isBinary": False,
                })
            except (UnicodeDecodeError, IsADirectoryError):
                files.append({
                    "name": path.name,
                    "symlink": None,
                    "preview": None,
                    "previewComplete": False,
                    "isBinary": True,
                })
    return files


def artifact_detail_payload(artifact: Artifact, project: Project) -> dict[str, Any]:
    return {
        "hash": artifact.hash,
        "type": artifact.type.value,
        "meta": artifact.meta,
        "files": read_artifact_files(artifact, project),
        "llmFiles": sorted(
            path.name
            for path in artifact.dir.iterdir()
            if not path.name.endswith(".marker")
            and (path.is_file() or path.is_symlink())
        ),
    }


async def artifacts_payload(project: Project, group_specs: list[ArtifactGroupSpec] | None = None) -> dict[str, Any]:
    artifacts = await list_artifacts(project)
    return {
        "artifacts": [
            artifact_payload(artifact, group_specs)
            for artifact in sorted(artifacts, key=lambda item: (item.type.value, item.hash))
        ]
    }


@handler("artifacts:list")
async def artifacts_list(socket: DashboardSocket, message: dict[str, Any]) -> Any:
    group_specs = parse_artifact_group_specs(message.get("groupSpecs", []))
    return await artifacts_payload(socket.project, group_specs)


@handler("artifacts:sync")
async def artifacts_sync(socket: DashboardSocket, message: dict[str, Any]) -> Any:
    # Lightweight, user-initiated refresh — not worth a tracked task / toast.
    created = await sync_artifacts(socket.project)
    return {"created": created}


@handler("artifact:get")
async def artifact_get(socket: DashboardSocket, message: dict[str, Any]) -> Any:
    artifact_hash = str(message.get("hash") or "")
    artifact = get_artifact(socket.project, artifact_hash)
    return artifact_detail_payload(artifact, socket.project)


@handler("artifact:run-lldb")
async def artifact_run_lldb(socket: DashboardSocket, message: dict[str, Any]) -> Any:
    artifact_hash = str(message.get("hash") or "")
    await socket.tasks.run_tracked(
        f"lldb {artifact_hash[:8]}", "lldb", socket.project.name,
        analyze_core(socket.project, artifact_hash),
    )
    artifact = get_artifact(socket.project, artifact_hash)
    return artifact_detail_payload(artifact, socket.project)


@handler("artifact:analyze-core")
async def artifact_analyze_core(socket: DashboardSocket, message: dict[str, Any]) -> Any:
    artifact_hash = str(message.get("hash") or "")
    await socket.tasks.run_tracked(
        f"analyze {artifact_hash[:8]}", "analyze-core", socket.project.name,
        analyze_core_artifact(socket.project, artifact_hash),
    )
    artifact = get_artifact(socket.project, artifact_hash)
    return artifact_detail_payload(artifact, socket.project)


@handler("artifacts:analyze-cores")
async def artifacts_analyze_cores(socket: DashboardSocket, message: dict[str, Any]) -> Any:
    async def analyze_all(project: Project) -> int:
        artifacts = await list_artifacts(project)
        cores = [a for a in artifacts if a.type == ArtifactType.CORE]
        for core in cores:
            await analyze_core_artifact(project, core.hash)
        return len(cores)

    analyzed = await socket.tasks.run_tracked(
        "analyze cores", "analyze-cores", socket.project.name,
        analyze_all(socket.project),
    )
    return {"analyzed": analyzed}


@handler("artifact:file")
async def artifact_file(socket: DashboardSocket, message: dict[str, Any]) -> Any:
    artifact_hash = str(message.get("hash") or "")
    filename = str(message.get("filename") or "")
    artifact = get_artifact(socket.project, artifact_hash)
    file_path = (artifact.dir / filename).resolve()
    if not str(file_path).startswith(str(artifact.dir.resolve())):
        raise ValueError("Invalid filename")
    return {"content": file_path.read_text("utf-8", errors="replace")}


@handler("artifact:ask-llm")
async def artifact_ask_llm(socket: DashboardSocket, message: dict[str, Any]) -> Any:
    artifact_hash = str(message.get("hash") or "")
    prompt = str(message.get("prompt") or "").strip()
    dest = validate_artifact_result_filename(str(message.get("dest") or "").strip())
    validate_group_filename(dest, "dest")
    raw_filenames = message.get("filenames")

    if not prompt:
        raise ValueError("Prompt cannot be empty")
    if dest.endswith(".marker"):
        raise ValueError("Response filename cannot end with .marker")
    if not isinstance(raw_filenames, list) or not all(isinstance(name, str) for name in raw_filenames):
        raise ValueError("filenames must be a list of artifact filenames")

    artifact = get_artifact(socket.project, artifact_hash)
    available_filenames = {
        path.name
        for path in artifact.dir.iterdir()
        if not path.name.endswith(".marker")
        and (path.is_file() or path.is_symlink())
    }
    filenames = set(raw_filenames)
    invalid_filenames = sorted(filenames - available_filenames)
    if invalid_filenames:
        raise ValueError(f"Unknown artifact filename: {invalid_filenames[0]}")

    dest_path = artifact.dir / dest
    if dest_path.exists():
        raise ValueError(f"Artifact file already exists: {dest}")

    async def ask() -> None:
        client = create_openai_client()
        await describe_artifact(
            client,
            socket.project,
            artifact_hash,
            prompt,
            dest,
            os.environ.get("PYFUZZ_OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
            include_filenames=filenames,
        )

    await socket.tasks.run_tracked(
        f"ask LLM {artifact_hash[:8]}",
        "ask-llm",
        socket.project.name,
        ask(),
        exclusive_key=f"ask-llm:{socket.project.name}:{artifact_hash}",
    )
    return artifact_detail_payload(get_artifact(socket.project, artifact_hash), socket.project)

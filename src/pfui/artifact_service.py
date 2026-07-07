from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyfuzz.analysis import Artifact, get_artifact, list_artifacts
from pyfuzz.project import Project


GROUP_FILE_READ_LIMIT = 4096
GROUP_LABEL_LIMIT = 100
FILE_PREVIEW_READ_LIMIT = 64 * 1024
FILE_PREVIEW_TEXT_LIMIT = 4096
# Files at or above this size are previewed (truncated) with a "Load full file"
# affordance; smaller files send their full content inline.
LARGE_FILE_THRESHOLD = 1024 * 1024


@dataclass(frozen=True)
class ArtifactGroupSpec:
    raw: str
    kind: str
    argument: str | None = None


def validate_local_filename(filename: str, label: str = "filename") -> str:
    if not filename:
        raise ValueError(f"{label} is required")
    path = Path(filename)
    if path.is_absolute() or filename in {".", ".."} or "/" in filename or "\\" in filename:
        raise ValueError(f"{label} must be local to the artifact directory")
    return filename


def parse_group_spec(raw: str) -> ArtifactGroupSpec:
    value = raw.strip()
    if not value:
        raise ValueError("Grouping spec cannot be empty")
    if value == "type":
        return ArtifactGroupSpec(value, "type")
    kind, separator, argument = value.partition(":")
    if not separator:
        raise ValueError(f"Unknown grouping spec: {value}")
    if kind in {"file", "exists"}:
        return ArtifactGroupSpec(value, kind, validate_local_filename(argument, f"{kind} filename"))
    if kind == "meta":
        if not argument:
            raise ValueError("meta grouping requires a key")
        return ArtifactGroupSpec(value, kind, argument)
    raise ValueError(f"Unknown grouping spec: {value}")


def _label(value: str) -> str:
    compact = " ".join(value.split()) or "(empty)"
    if len(compact) > GROUP_LABEL_LIMIT:
        return compact[: GROUP_LABEL_LIMIT - 3].rstrip() + "..."
    return compact


def _group_value(artifact: Artifact, spec: ArtifactGroupSpec) -> dict[str, str]:
    if spec.kind == "type":
        return {"value": artifact.type.value, "label": artifact.type.value}
    argument = spec.argument or ""
    if spec.kind == "meta":
        raw = artifact.meta.get(argument, f"missing {argument}")
        value = raw if isinstance(raw, str) else json.dumps(raw, sort_keys=True)
        return {"value": value, "label": _label(value)}
    path = artifact.dir / argument
    if spec.kind == "exists":
        value = f"has {argument}" if path.exists() else f"missing {argument}"
        return {"value": value, "label": value}
    if not path.is_file():
        value = f"missing {argument}"
    else:
        with path.open("rb") as handle:
            value = handle.read(GROUP_FILE_READ_LIMIT).decode("utf-8", errors="replace")
    return {"value": value, "label": _label(value)}


def artifact_summary(artifact: Artifact, specs: list[ArtifactGroupSpec]) -> dict[str, Any]:
    input_path = artifact.dir / "input.txt"
    return {
        "hash": artifact.hash,
        "type": artifact.type.value,
        "path": str(artifact.dir),
        "hasInput": input_path.exists(),
        "inputSize": input_path.stat().st_size if input_path.exists() else None,
        "groupValues": [_group_value(artifact, spec) for spec in specs],
    }


async def artifact_list_payload(project: Project, raw_specs: list[str]) -> dict[str, Any]:
    specs = [parse_group_spec(raw) for raw in raw_specs]
    artifacts = sorted(await list_artifacts(project), key=lambda item: (item.type.value, item.hash))
    return {"artifacts": [artifact_summary(artifact, specs) for artifact in artifacts]}


def contained_artifact_file(artifact: Artifact, filename: str) -> Path:
    validate_local_filename(filename)
    root = artifact.dir.resolve()
    candidate = (root / filename).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("Invalid artifact filename") from exc
    if not candidate.is_file():
        raise FileNotFoundError(f"Artifact file not found: {filename}")
    return candidate


def _file_payload(path: Path, artifact: Artifact, project: Project) -> dict[str, Any]:
    if path.is_symlink():
        resolved = path.resolve()
        try:
            target = str(resolved.relative_to(project.path()))
        except ValueError:
            target = str(path.readlink())
        command = None
        if path.name == "core":
            try:
                core_path = resolved.relative_to(project.path("cores"))
                command = f"lldb -c /pfm/cores/{core_path} {project.fuzz_target}"
            except ValueError:
                pass
        return {
            "name": path.name,
            "symlink": target,
            "preview": None,
            "previewComplete": False,
            "isBinary": False,
            "lldbCommand": command,
        }
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    is_large = size >= LARGE_FILE_THRESHOLD
    try:
        with path.open("rb") as handle:
            raw = handle.read(FILE_PREVIEW_READ_LIMIT + 1 if is_large else size)
        if is_large:
            raw = raw[:FILE_PREVIEW_READ_LIMIT]
    except OSError:
        return {
            "name": path.name,
            "symlink": None,
            "preview": None,
            "previewComplete": False,
            "isBinary": True,
            "lldbCommand": None,
        }
    control_count = sum(byte < 32 and byte not in {9, 10, 13} for byte in raw)
    is_binary = b"\x00" in raw or (bool(raw) and control_count / len(raw) > 0.01)
    text = raw.decode("utf-8", errors="replace")
    if is_large:
        preview = "\n".join(text.splitlines()[:10])[:FILE_PREVIEW_TEXT_LIMIT]
        return {
            "name": path.name,
            "symlink": None,
            "preview": preview,
            "previewComplete": False,
            "isBinary": is_binary,
            "lldbCommand": None,
        }
    return {
        "name": path.name,
        "symlink": None,
        "preview": text,
        "previewComplete": True,
        "isBinary": is_binary,
        "lldbCommand": None,
    }


def artifact_detail(project: Project, artifact_hash: str) -> dict[str, Any]:
    artifact = get_artifact(project, artifact_hash)
    visible_paths = [
        path
        for path in sorted(artifact.dir.iterdir())
        if path.name != "meta.json" and not path.name.endswith(".marker")
    ]
    return {
        "hash": artifact.hash,
        "type": artifact.type.value,
        "meta": artifact.meta,
        "files": [_file_payload(path, artifact, project) for path in visible_paths],
        "llmFiles": [path.name for path in visible_paths if path.is_file() or path.is_symlink()],
    }

from __future__ import annotations

import codecs
import os
from pathlib import Path, PurePosixPath
import tempfile
from typing import Any

from pyfuzz.project import Project


def _inputs_root(project: Project) -> Path:
    return project.path("inputs").resolve()


def _relative_path(value: str) -> PurePosixPath:
    if "\\" in value:
        raise ValueError("Input path must use forward slashes")
    relative = PurePosixPath(value)
    if relative.is_absolute() or not relative.parts:
        raise ValueError("Input path must be relative to the inputs directory")
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("Input path cannot contain '.', '..', or empty segments")
    return relative


def contained_input_file(project: Project, value: str) -> Path:
    root = _inputs_root(project)
    relative = _relative_path(value)
    candidate = root.joinpath(*relative.parts)
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("Input path escapes the inputs directory") from exc
    if candidate.is_symlink():
        raise ValueError("Input path must be a regular file")
    if not candidate.exists():
        raise FileNotFoundError(f"Input file not found: {value}")
    if not candidate.is_file():
        raise ValueError("Input path must be a regular file")
    return candidate


def escaped_input(raw: bytes) -> str:
    parts: list[str] = []
    for byte in raw:
        if byte in {9, 10, 13}:
            parts.append(chr(byte))
        elif byte == 92:
            parts.append("\\\\")
        elif 32 <= byte <= 126:
            parts.append(chr(byte))
        else:
            parts.append(f"\\x{byte:02x}")
    return "".join(parts)


def unescaped_input(text: str) -> bytes:
    try:
        encoded = text.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("Input content must be ASCII; use backslash escapes for bytes") from exc
    try:
        return codecs.escape_decode(encoded)[0]
    except ValueError as exc:
        raise ValueError("Input content contains an invalid backslash escape") from exc


def input_tree(project: Project) -> list[dict[str, Any]]:
    root = _inputs_root(project)
    if not root.exists():
        return []

    def build(path: Path) -> dict[str, Any]:
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            children = [
                build(child)
                for child in sorted(path.iterdir(), key=lambda item: (item.is_file(), item.name.lower(), item.name))
                if not child.is_symlink() and (child.is_dir() or child.is_file())
            ]
            return {"path": relative, "name": path.name, "kind": "directory", "children": children}
        return {"path": relative, "name": path.name, "kind": "file", "size": path.stat().st_size}

    return [
        build(child)
        for child in sorted(root.iterdir(), key=lambda item: (item.is_file(), item.name.lower(), item.name))
        if not child.is_symlink() and (child.is_dir() or child.is_file())
    ]


def input_file_payload(project: Project, value: str) -> dict[str, Any]:
    path = contained_input_file(project, value)
    raw = path.read_bytes()
    return {"path": value, "content": escaped_input(raw), "size": len(raw)}


def update_input_file(project: Project, value: str, content: str) -> dict[str, Any]:
    path = contained_input_file(project, value)
    raw = unescaped_input(content)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_bytes(raw)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return {"path": value, "content": escaped_input(raw), "size": len(raw)}


def delete_input_file(project: Project, value: str) -> list[dict[str, Any]]:
    path = contained_input_file(project, value)
    path.unlink()
    return input_tree(project)

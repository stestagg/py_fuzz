from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import mimetypes
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


REPO_ROOT = Path(__file__).resolve().parents[3]
PYTHON_SRC = REPO_ROOT / "src"
if str(PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(PYTHON_SRC))

from pyfuzz.project import Project  # noqa: E402
from pyfuzz.analysis import list_artifacts, sync_artifacts, get_artifact, Artifact  # noqa: E402
from pyfuzz.summary import summarize_fuzzing  # noqa: E402
from pyfuzz.lldb import analyze_core  # noqa: E402


WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
DIST_DIR = REPO_ROOT / "src" / "ui" / "dist"


class HttpError(Exception):
    def __init__(self, status: int, reason: str):
        self.status = status
        self.reason = reason
        super().__init__(f"{status} {reason}")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_bytes(data: Any) -> bytes:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


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
            "fuzzPeg": project.fuzz_peg,
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


def artifact_payload(artifact: Artifact) -> dict[str, Any]:
    input_path = artifact.dir / "input.txt"
    return {
        "hash": artifact.hash,
        "type": artifact.type.value,
        "path": str(artifact.dir),
        "hasInput": input_path.exists(),
        "inputSize": input_path.stat().st_size if input_path.exists() else None,
    }


def read_artifact_files(artifact: Artifact, project: Project) -> list[dict[str, Any]]:
    project_root = artifact.dir.parents[1].resolve()
    files = []
    for path in sorted(artifact.dir.iterdir()):
        if path.name == "meta.json":
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
    }


async def artifacts_payload(project: Project) -> dict[str, Any]:
    artifacts = await list_artifacts(project)
    return {
        "artifacts": [
            artifact_payload(artifact)
            for artifact in sorted(artifacts, key=lambda item: (item.type.value, item.hash))
        ]
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


def response_message(request_id: str | None, message_type: str, data: Any) -> dict[str, Any]:
    return {
        "type": f"{message_type}:result",
        "requestId": request_id,
        "ok": True,
        "data": data,
    }


def error_message(request_id: str | None, message_type: str, error: Exception) -> dict[str, Any]:
    return {
        "type": f"{message_type}:error",
        "requestId": request_id,
        "ok": False,
        "error": str(error),
    }


class DashboardSocket:
    def __init__(self, writer: asyncio.StreamWriter, initial_project: str | None):
        self.writer = writer
        self.project = load_project(initial_project)

    async def send(self, data: Any) -> None:
        self.writer.write(encode_ws_frame(json_bytes(data), opcode=0x1))
        await self.writer.drain()

    async def send_ready(self) -> None:
        await self.send(
            {
                "type": "connection:ready",
                "data": {
                    "projects": list_projects(),
                    **dashboard_payload(self.project),
                },
            }
        )

    async def dispatch(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("type") or "")
        request_id = message.get("requestId")
        try:
            data = await self.handle_message(message_type, message)
            await self.send(response_message(request_id, message_type, data))
        except Exception as exc:
            await self.send(error_message(request_id, message_type, exc))

    async def handle_message(self, message_type: str, message: dict[str, Any]) -> Any:
        if message_type == "projects:list":
            return {"projects": list_projects()}

        if message_type == "project:get":
            return {
                "projects": list_projects(),
                **dashboard_payload(self.project),
            }

        if message_type == "project:select":
            project_name = str(message.get("projectName") or "")
            self.project = load_project(project_name)
            return {
                "projects": list_projects(),
                **dashboard_payload(self.project),
            }

        if self.project is None:
            raise ValueError("No project selected")

        if message_type == "summary:refresh":
            return {"summary": await asyncio.to_thread(summary_payload, self.project)}

        if message_type == "artifacts:list":
            return await artifacts_payload(self.project)

        if message_type == "artifacts:sync":
            created = await sync_artifacts(self.project)
            return {"created": created}

        if message_type == "artifact:get":
            artifact_hash = str(message.get("hash") or "")
            artifact = get_artifact(self.project, artifact_hash)
            return artifact_detail_payload(artifact, self.project)

        if message_type == "artifact:run-lldb":
            artifact_hash = str(message.get("hash") or "")
            await analyze_core(self.project, artifact_hash)
            artifact = get_artifact(self.project, artifact_hash)
            return artifact_detail_payload(artifact, self.project)

        if message_type == "artifact:file":
            artifact_hash = str(message.get("hash") or "")
            filename = str(message.get("filename") or "")
            artifact = get_artifact(self.project, artifact_hash)
            file_path = (artifact.dir / filename).resolve()
            if not str(file_path).startswith(str(artifact.dir.resolve())):
                raise ValueError("Invalid filename")
            return {"content": file_path.read_text("utf-8", errors="replace")}

        raise ValueError(f"Unsupported message type: {message_type}")


async def read_http_request(reader: asyncio.StreamReader) -> tuple[str, str, dict[str, str]]:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = await reader.read(4096)
        if not chunk:
            raise HttpError(400, "Bad Request")
        data += chunk
        if len(data) > 65536:
            raise HttpError(431, "Request Header Fields Too Large")

    header_text = data.split(b"\r\n\r\n", 1)[0].decode("iso-8859-1")
    lines = header_text.split("\r\n")
    try:
        method, target, _version = lines[0].split(" ", 2)
    except ValueError as exc:
        raise HttpError(400, "Bad Request") from exc

    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.lower()] = value.strip()
    return method.upper(), target, headers


def websocket_accept(key: str) -> str:
    digest = hashlib.sha1((key + WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


async def handle_websocket(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    target: str,
    headers: dict[str, str],
    cli_project: str | None,
) -> None:
    key = headers.get("sec-websocket-key")
    if not key:
        raise HttpError(400, "Missing Sec-WebSocket-Key")

    writer.write(
        "\r\n".join(
            [
                "HTTP/1.1 101 Switching Protocols",
                "Upgrade: websocket",
                "Connection: Upgrade",
                f"Sec-WebSocket-Accept: {websocket_accept(key)}",
                "\r\n",
            ]
        ).encode("ascii")
    )
    await writer.drain()

    query_project = parse_qs(urlparse(target).query).get("project", [None])[0]
    socket = DashboardSocket(writer, query_project or cli_project)
    await socket.send_ready()

    while not reader.at_eof():
        opcode, payload = await read_ws_frame(reader)
        if opcode == 0x8:
            writer.write(encode_ws_frame(payload, opcode=0x8))
            await writer.drain()
            return
        if opcode == 0x9:
            writer.write(encode_ws_frame(payload, opcode=0xA))
            await writer.drain()
            continue
        if opcode != 0x1:
            continue

        message = json.loads(payload.decode("utf-8"))
        await socket.dispatch(message)


async def read_ws_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    first = await reader.readexactly(2)
    opcode = first[0] & 0x0F
    masked = bool(first[1] & 0x80)
    length = first[1] & 0x7F
    if length == 126:
        length = int.from_bytes(await reader.readexactly(2), "big")
    elif length == 127:
        length = int.from_bytes(await reader.readexactly(8), "big")

    mask = await reader.readexactly(4) if masked else b""
    payload = await reader.readexactly(length) if length else b""
    if masked:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return opcode, payload


def encode_ws_frame(payload: bytes, *, opcode: int) -> bytes:
    head = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        head.append(length)
    elif length < 65536:
        head.extend((126, *length.to_bytes(2, "big")))
    else:
        head.extend((127, *length.to_bytes(8, "big")))
    return bytes(head) + payload


async def send_http_response(
    writer: asyncio.StreamWriter,
    status: int,
    reason: str,
    body: bytes,
    *,
    content_type: str,
) -> None:
    writer.write(
        (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Content-Type: {content_type}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii")
        + body
    )
    await writer.drain()


def fallback_html(port: int, cli_project: str | None) -> bytes:
    project_arg = f" --project {cli_project}" if cli_project else ""
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>pyfuzz UI</title>
    <style>
      body {{ font: 15px/1.5 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 40px; color: #172026; background: #f5f7f8; }}
      main {{ max-width: 760px; }}
      code {{ background: #e8eef1; padding: 2px 5px; border-radius: 4px; }}
    </style>
  </head>
  <body>
    <main>
      <h1>pyfuzz UI backend is running</h1>
      <p>Build the React app with <code>cd src/ui && pnpm install && pnpm build</code>, then reload this page.</p>
      <p>For development, run <code>cd src/ui && VITE_PYFUZZ_WS_URL=ws://localhost:{port}/ws pnpm dev</code> while this backend stays up.</p>
      <p>Backend launch: <code>python src/ui/backend/server.py{project_arg}</code></p>
    </main>
  </body>
</html>
""".encode("utf-8")


def resolve_static_file(path: str) -> Path | None:
    if not DIST_DIR.exists():
        return None
    requested = "index.html" if path in ("", "/") else path.lstrip("/")
    candidate = (DIST_DIR / requested).resolve()
    if DIST_DIR.resolve() != candidate and DIST_DIR.resolve() not in candidate.parents:
        return None
    if candidate.is_dir():
        candidate = candidate / "index.html"
    if candidate.exists() and candidate.is_file():
        return candidate
    return DIST_DIR / "index.html"


def config_script(host_header: str, cli_project: str | None) -> bytes:
    host = host_header or "localhost:8767"
    config = {
        "wsUrl": f"ws://{host}/ws",
        "initialProject": cli_project,
    }
    return f"window.PYFUZZ_UI_CONFIG = {json.dumps(config)};\n".encode("utf-8")


async def handle_http(
    writer: asyncio.StreamWriter,
    target: str,
    headers: dict[str, str],
    port: int,
    cli_project: str | None,
) -> None:
    parsed = urlparse(target)
    if parsed.path == "/health":
        await send_http_response(
            writer,
            200,
            "OK",
            json_bytes({"ok": True, "service": "pyfuzz-ui", "updatedAt": utc_now()}),
            content_type="application/json; charset=utf-8",
        )
        return

    if parsed.path == "/config.js":
        await send_http_response(
            writer,
            200,
            "OK",
            config_script(headers.get("host", f"localhost:{port}"), cli_project),
            content_type="application/javascript; charset=utf-8",
        )
        return

    static_file = resolve_static_file(parsed.path)
    if static_file is None:
        await send_http_response(
            writer,
            200,
            "OK",
            fallback_html(port, cli_project),
            content_type="text/html; charset=utf-8",
        )
        return

    content_type = mimetypes.guess_type(static_file.name)[0] or "application/octet-stream"
    await send_http_response(writer, 200, "OK", static_file.read_bytes(), content_type=content_type)


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    port: int,
    cli_project: str | None,
) -> None:
    try:
        method, target, headers = await read_http_request(reader)
        if method != "GET":
            raise HttpError(405, "Method Not Allowed")
        if urlparse(target).path == "/ws":
            await handle_websocket(reader, writer, target, headers, cli_project)
        else:
            await handle_http(writer, target, headers, port, cli_project)
    except HttpError as exc:
        await send_http_response(
            writer,
            exc.status,
            exc.reason,
            f"{exc.status} {exc.reason}\n".encode("utf-8"),
            content_type="text/plain; charset=utf-8",
        )
    except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
        pass
    finally:
        writer.close()
        await writer.wait_closed()


async def run_server(host: str, port: int, project: str | None) -> None:
    server = await asyncio.start_server(
        lambda reader, writer: handle_client(reader, writer, port=port, cli_project=project),
        host,
        port,
    )
    urls = ", ".join(f"http://{sock.getsockname()[0]}:{sock.getsockname()[1]}" for sock in server.sockets or [])
    print(f"pyfuzz UI backend listening on {urls}")
    if project:
        print(f"Initial project: {project}")
    if not DIST_DIR.exists():
        print("React app is not built yet. Use: cd src/ui && pnpm install && pnpm dev")
    async with server:
        await server.serve_forever()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the pyfuzz UI websocket backend.")
    parser.add_argument("project_name", nargs="?", help="Project to select on connect.")
    parser.add_argument("--project", dest="project_option", help="Project to select on connect.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind.")
    parser.add_argument("--port", type=int, default=8767, help="Port to bind.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project = args.project_option or args.project_name
    asyncio.run(run_server(args.host, args.port, project))


if __name__ == "__main__":
    main()

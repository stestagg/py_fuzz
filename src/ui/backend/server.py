from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from common import REPO_ROOT, json_bytes, utc_now
from handlers import collect_handlers
from handlers.projects import dashboard_payload, list_projects, load_project

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
DIST_DIR = REPO_ROOT / "src" / "ui" / "dist"

HANDLERS = collect_handlers()


class HttpError(Exception):
    def __init__(self, status: int, reason: str):
        self.status = status
        self.reason = reason
        super().__init__(f"{status} {reason}")


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


FINISHED_TASK_TTL = 60.0


@dataclass
class TrackedTask:
    id: str
    name: str
    kind: str
    project: str | None
    started_at: str
    status: str = "running"  # running | done | error | cancelled
    error: str | None = None
    finished_at: str | None = None
    thread_backed: bool = False
    exclusive_key: str | None = None
    aio_task: asyncio.Task | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "project": self.project,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "status": self.status,
            "error": self.error,
            "stoppable": not self.thread_backed,
        }


class TaskManager:
    def __init__(self) -> None:
        self.tasks: dict[str, TrackedTask] = {}
        self.sockets: set[DashboardSocket] = set()
        self._counter = 0

    def register(self, socket: DashboardSocket) -> None:
        self.sockets.add(socket)

    def unregister(self, socket: DashboardSocket) -> None:
        self.sockets.discard(socket)

    def snapshot(self) -> list[dict[str, Any]]:
        return [task.payload() for task in self.tasks.values()]

    def running(self, kind: str, project: str | None = None) -> list[TrackedTask]:
        return [
            task for task in self.tasks.values()
            if task.status == "running" and task.kind == kind
            and (project is None or task.project == project)
        ]

    async def broadcast(self) -> None:
        message = {"type": "tasks:update", "data": {"tasks": self.snapshot()}}
        for socket in list(self.sockets):
            try:
                await socket.send(message)
            except Exception:
                self.sockets.discard(socket)

    def start(
        self,
        name: str,
        kind: str,
        project: str | None,
        coro: Any,
        *,
        thread_backed: bool = False,
        exclusive_key: str | None = None,
    ) -> TrackedTask:
        if exclusive_key:
            for task in self.tasks.values():
                if task.status == "running" and task.exclusive_key == exclusive_key:
                    coro.close()
                    raise ValueError(f"{task.name} is already running")
        self._counter += 1
        tracked = TrackedTask(
            id=f"task-{self._counter}",
            name=name,
            kind=kind,
            project=project,
            started_at=utc_now(),
            thread_backed=thread_backed,
            exclusive_key=exclusive_key,
        )
        self.tasks[tracked.id] = tracked
        tracked.aio_task = asyncio.create_task(coro, name=f"{tracked.id}:{name}")
        tracked.aio_task.add_done_callback(lambda task: self._on_done(tracked, task))
        asyncio.create_task(self.broadcast())
        return tracked

    def _on_done(self, tracked: TrackedTask, task: asyncio.Task) -> None:
        # A done callback (not a wrapper coroutine) so cancel-before-first-run
        # is handled too; statuses set by stop() (detached threads) win.
        if task.cancelled():
            if tracked.status == "running":
                tracked.status = "cancelled"
        elif task.exception() is not None:
            if tracked.status == "running":
                tracked.status = "error"
                tracked.error = str(task.exception())
        elif tracked.status == "running":
            tracked.status = "done"
        tracked.finished_at = utc_now()
        asyncio.create_task(self._finalize(tracked))

    async def _finalize(self, tracked: TrackedTask) -> None:
        await self.broadcast()
        await asyncio.sleep(FINISHED_TASK_TTL)
        if self.tasks.get(tracked.id) is tracked:
            del self.tasks[tracked.id]
            await self.broadcast()

    async def stop(self, task_id: str) -> dict[str, Any]:
        tracked = self.tasks.get(task_id)
        if tracked is None:
            raise ValueError(f"Unknown task: {task_id}")
        if tracked.status != "running":
            return {"stopped": False}
        if tracked.thread_backed:
            # Threads cannot be killed; mark cancelled and let it detach.
            tracked.status = "cancelled"
            await self.broadcast()
            return {"stopped": True, "detached": True}
        assert tracked.aio_task is not None
        tracked.aio_task.cancel()
        return {"stopped": True}

    async def run_tracked(
        self,
        name: str,
        kind: str,
        project: str | None,
        coro: Any,
        *,
        thread_backed: bool = False,
        exclusive_key: str | None = None,
    ) -> Any:
        tracked = self.start(name, kind, project, coro, thread_backed=thread_backed, exclusive_key=exclusive_key)
        assert tracked.aio_task is not None
        # asyncio.wait (not a bare await) so cancelling this dispatch does not
        # cancel the tracked task, and a stopped task surfaces as a plain error.
        await asyncio.wait([tracked.aio_task])
        if tracked.aio_task.cancelled():
            raise ValueError("Task was stopped")
        exc = tracked.aio_task.exception()
        if exc is not None:
            raise exc
        return tracked.aio_task.result()


class DashboardSocket:
    def __init__(self, writer: asyncio.StreamWriter, initial_project: str | None, tasks: TaskManager):
        self.writer = writer
        self.project = load_project(initial_project)
        self.tasks = tasks
        self._send_lock = asyncio.Lock()

    async def send_frame(self, payload: bytes, *, opcode: int) -> None:
        # Serialize frame writes: concurrent dispatches would interleave bytes.
        async with self._send_lock:
            self.writer.write(encode_ws_frame(payload, opcode=opcode))
            await self.writer.drain()

    async def send(self, data: Any) -> None:
        await self.send_frame(json_bytes(data), opcode=0x1)

    async def send_ready(self) -> None:
        await self.send(
            {
                "type": "connection:ready",
                "data": {
                    "projects": list_projects(),
                    "tasks": self.tasks.snapshot(),
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
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.send(error_message(request_id, message_type, exc))

    async def handle_message(self, message_type: str, message: dict[str, Any]) -> Any:
        handler = HANDLERS.get(message_type)
        if handler is None:
            raise ValueError(f"Unsupported message type: {message_type}")
        if handler.requires_project and self.project is None:
            raise ValueError("No project selected")
        return await handler(self, message)


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
    task_manager: TaskManager,
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
    socket = DashboardSocket(writer, query_project or cli_project, task_manager)
    await socket.send_ready()
    task_manager.register(socket)

    inflight: set[asyncio.Task] = set()
    try:
        while not reader.at_eof():
            opcode, payload = await read_ws_frame(reader)
            if opcode == 0x8:
                await socket.send_frame(payload, opcode=0x8)
                return
            if opcode == 0x9:
                await socket.send_frame(payload, opcode=0xA)
                continue
            if opcode != 0x1:
                continue

            message = json.loads(payload.decode("utf-8"))
            dispatch_task = asyncio.create_task(socket.dispatch(message))
            inflight.add(dispatch_task)
            dispatch_task.add_done_callback(inflight.discard)
    finally:
        task_manager.unregister(socket)
        for dispatch_task in inflight:
            dispatch_task.cancel()
        if inflight:
            await asyncio.gather(*inflight, return_exceptions=True)


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
    task_manager: TaskManager,
) -> None:
    try:
        method, target, headers = await read_http_request(reader)
        if method != "GET":
            raise HttpError(405, "Method Not Allowed")
        if urlparse(target).path == "/ws":
            await handle_websocket(reader, writer, target, headers, cli_project, task_manager)
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
    task_manager = TaskManager()
    server = await asyncio.start_server(
        lambda reader, writer: handle_client(reader, writer, port=port, cli_project=project, task_manager=task_manager),
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

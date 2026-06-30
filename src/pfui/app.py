from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from aiohttp import WSMsgType, web
from pydantic import ValidationError

from .handlers import router
from .project_service import list_projects
from .protocol import ProtocolError, RequestContext, RequestEnvelope, error_response, success_response
from .tasks import TaskManager, utc_now


LOGGER = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"


@dataclass(eq=False)
class ClientConnection:
    websocket: web.WebSocketResponse
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, payload: dict[str, Any]) -> None:
        async with self.send_lock:
            if not self.websocket.closed:
                await self.websocket.send_json(payload)


CLIENTS = web.AppKey("clients", set[ClientConnection])
TASKS = web.AppKey("tasks", TaskManager)
INITIAL_PROJECT = web.AppKey("initial_project", str | None)
DEFAULT_WARNING = web.AppKey("default_warning", str | None)
SHUTTING_DOWN = web.AppKey("shutting_down", bool)


async def close_clients(application: web.Application) -> None:
    application[SHUTTING_DOWN] = True
    clients = list(application[CLIENTS])
    if clients:
        await asyncio.gather(
            *(client.websocket.close(code=1001, message=b"PFUI shutting down") for client in clients),
            return_exceptions=True,
        )


def _valid_origin(request: web.Request) -> bool:
    origin = request.headers.get("Origin")
    if not origin:
        return True
    parsed = urlparse(origin)
    return parsed.scheme in {"http", "https"} and parsed.netloc == request.host


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "pfui", "updatedAt": utc_now()})


async def _process_request(connection: ClientConnection, tasks: TaskManager, raw: Any) -> None:
    request_id = raw.get("id") if isinstance(raw, dict) else None
    project = raw.get("project") if isinstance(raw, dict) else None
    try:
        request = RequestEnvelope.model_validate(raw)
        result = await router.dispatch(RequestContext(tasks=tasks), request)
        await connection.send(success_response(request, result))
    except ValidationError as exc:
        first = exc.errors(include_url=False)[0]
        await connection.send(error_response(request_id, project, "bad_request", first["msg"]))
    except ProtocolError as exc:
        await connection.send(error_response(request_id, project, exc.code, exc.message))
    except FileNotFoundError as exc:
        await connection.send(error_response(request_id, project, "not_found", str(exc)))
    except ValueError as exc:
        await connection.send(error_response(request_id, project, "bad_request", str(exc)))
    except asyncio.CancelledError:
        raise
    except Exception:
        LOGGER.exception("Unhandled PFUI request failure")
        await connection.send(error_response(request_id, project, "internal_error", "Request failed"))


async def websocket(request: web.Request) -> web.StreamResponse:
    if request.app[SHUTTING_DOWN]:
        raise web.HTTPServiceUnavailable(text="PFUI is shutting down")
    if not _valid_origin(request):
        raise web.HTTPForbidden(text="WebSocket origin does not match the PFUI host")
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    connection = ClientConnection(ws)
    request.app[CLIENTS].add(connection)
    await connection.send({
        "event": "session.ready",
        "data": {
            "projects": list_projects(),
            "defaultProject": request.app[INITIAL_PROJECT],
            "defaultWarning": request.app[DEFAULT_WARNING],
            "tasks": request.app[TASKS].snapshot(),
        },
    })

    inflight: set[asyncio.Task[None]] = set()
    try:
        async for message in ws:
            if message.type == WSMsgType.TEXT:
                try:
                    raw = message.json()
                except ValueError:
                    await connection.send(error_response(None, None, "bad_request", "Message must be valid JSON"))
                    continue
                task = asyncio.create_task(_process_request(connection, request.app[TASKS], raw))
                inflight.add(task)
                task.add_done_callback(inflight.discard)
            elif message.type == WSMsgType.ERROR:
                LOGGER.debug("PFUI websocket closed with %s", ws.exception())
                break
    finally:
        request.app[CLIENTS].discard(connection)
        for task in inflight:
            task.cancel()
        if inflight:
            await asyncio.gather(*inflight, return_exceptions=True)
    return ws


async def static_asset(request: web.Request) -> web.StreamResponse:
    assets = (STATIC_DIR / "assets").resolve()
    candidate = (assets / request.match_info["path"]).resolve()
    try:
        candidate.relative_to(assets)
    except ValueError as exc:
        raise web.HTTPNotFound() from exc
    if not candidate.is_file():
        raise web.HTTPNotFound()
    response = web.FileResponse(candidate)
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


async def spa(request: web.Request) -> web.StreamResponse:
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        return web.Response(
            status=503,
            content_type="text/plain",
            text="PFUI frontend is not built. Run: cd src/pfui/web && pnpm install && pnpm build",
        )
    response = web.FileResponse(index)
    response.headers["Cache-Control"] = "no-cache"
    return response


def create_app(*, initial_project: str | None = None, default_warning: str | None = None) -> web.Application:
    app = web.Application(client_max_size=1024 * 1024)
    app[CLIENTS] = set()
    app[INITIAL_PROJECT] = initial_project
    app[DEFAULT_WARNING] = default_warning
    app[SHUTTING_DOWN] = False

    async def broadcast(payload: dict[str, Any]) -> None:
        clients = list(app[CLIENTS])
        if clients:
            await asyncio.gather(*(client.send(payload) for client in clients), return_exceptions=True)

    app[TASKS] = TaskManager(broadcast)

    async def cleanup(application: web.Application) -> None:
        await close_clients(application)
        await application[TASKS].close()

    app.on_cleanup.append(cleanup)
    app.router.add_get("/health", health)
    app.router.add_get("/ws", websocket)
    app.router.add_get("/assets/{path:.*}", static_asset)
    app.router.add_get("/{path:.*}", spa)
    return app


async def serve(
    host: str,
    port: int,
    *,
    initial_project: str | None,
    default_warning: str | None,
    on_started: Any = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    runner = web.AppRunner(create_app(initial_project=initial_project, default_warning=default_warning))
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    try:
        if on_started is not None:
            on_started()
        await (stop_event or asyncio.Event()).wait()
        await close_clients(runner.app)
    finally:
        await runner.cleanup()

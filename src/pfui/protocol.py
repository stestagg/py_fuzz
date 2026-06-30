from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pyfuzz.project import Project


class RequestEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=200)
    method: str = Field(min_length=1, max_length=100)
    project: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class EmptyParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProtocolError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


ParamsT = TypeVar("ParamsT", bound=BaseModel)


@dataclass(frozen=True)
class RequestContext:
    tasks: Any


Handler = Callable[[RequestContext, Project | None, BaseModel], Awaitable[Any]]


@dataclass(frozen=True)
class HandlerSpec(Generic[ParamsT]):
    params_model: type[ParamsT]
    handler: Handler
    requires_project: bool


class Router:
    def __init__(self) -> None:
        self._handlers: dict[str, HandlerSpec[Any]] = {}

    def handler(
        self,
        method: str,
        params_model: type[ParamsT] = EmptyParams,
        *,
        requires_project: bool = True,
    ) -> Callable[[Callable[[RequestContext, Project | None, ParamsT], Awaitable[Any]]], Callable[[RequestContext, Project | None, ParamsT], Awaitable[Any]]]:
        def decorate(fn: Callable[[RequestContext, Project | None, ParamsT], Awaitable[Any]]):
            if method in self._handlers:
                raise RuntimeError(f"Duplicate PFUI method: {method}")
            self._handlers[method] = HandlerSpec(params_model, fn, requires_project)  # type: ignore[arg-type]
            return fn

        return decorate

    async def dispatch(self, context: RequestContext, request: RequestEnvelope) -> Any:
        spec = self._handlers.get(request.method)
        if spec is None:
            raise ProtocolError("method_not_found", f"Unknown method: {request.method}")

        project = None
        if spec.requires_project:
            if not request.project:
                raise ProtocolError("project_required", "This request requires a project")
            try:
                project = Project.load(request.project)
            except FileNotFoundError as exc:
                raise ProtocolError("project_not_found", str(exc)) from exc

        try:
            params = spec.params_model.model_validate(request.params)
        except ValidationError as exc:
            first = exc.errors(include_url=False)[0]
            location = ".".join(str(part) for part in first["loc"])
            prefix = f"{location}: " if location else ""
            raise ProtocolError("bad_request", prefix + first["msg"]) from exc
        return await spec.handler(context, project, params)


def success_response(request: RequestEnvelope, result: Any) -> dict[str, Any]:
    response: dict[str, Any] = {"id": request.id, "ok": True, "result": result}
    if request.project is not None:
        response["project"] = request.project
    return response


def error_response(
    request_id: str | None,
    project: str | None,
    code: str,
    message: str,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "id": request_id,
        "ok": False,
        "error": {"code": code, "message": message},
    }
    if project is not None:
        response["project"] = project
    return response

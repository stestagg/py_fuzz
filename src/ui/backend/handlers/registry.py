from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from server import DashboardSocket

Handler = Callable[["DashboardSocket", dict[str, Any]], Awaitable[Any]]


class Registry:
    """Per-module registry of websocket message handlers.

    Each handler module creates one instance and decorates its handlers with
    ``@registry.handler(name)``; server.py merges all module registries into a
    single dispatch table at startup.
    """

    def __init__(self) -> None:
        self.handlers: dict[str, Handler] = {}

    def handler(self, name: str, *, requires_project: bool = True) -> Callable[[Handler], Handler]:
        def decorate(fn: Handler) -> Handler:
            if name in self.handlers:
                raise ValueError(f"Duplicate handler registration: {name}")
            fn.requires_project = requires_project  # type: ignore[attr-defined]
            self.handlers[name] = fn
            return fn

        return decorate

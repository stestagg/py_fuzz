"""Message handlers for the pyfuzz UI websocket backend."""

from __future__ import annotations

import common  # noqa: F401  (sys.path bootstrap so handler modules can import pyfuzz)

from . import artifacts, plot, projects, tasks
from .registry import Handler


def collect_handlers() -> dict[str, Handler]:
    merged: dict[str, Handler] = {}
    for module in (projects, tasks, artifacts, plot):
        for name, fn in module.registry.handlers.items():
            if name in merged:
                raise RuntimeError(f"Duplicate message handler {name!r} in {module.__name__}")
            merged[name] = fn
    return merged

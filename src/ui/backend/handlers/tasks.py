from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from pyfuzz.build import build_helpers, build_python
from pyfuzz.clean import CleanComponent, clean
from pyfuzz.fuzz import run_fuzz
from pyfuzz.fuzzdict import make_dict
from pyfuzz.monitor import monitor_loop
from pyfuzz.project import Project

from .registry import Registry

if TYPE_CHECKING:
    from server import DashboardSocket

registry = Registry()
handler = registry.handler


async def fuzz_action(project: Project, instances: int, afl_debug: bool, monitor: bool) -> None:
    workers = [asyncio.create_task(run_fuzz(project, i, afl_debug=afl_debug)) for i in range(instances)]
    monitor_task = None
    if monitor:
        monitor_task = asyncio.create_task(
            monitor_loop(project, get_running_workers=lambda: sum(1 for t in workers if not t.done()))
        )
    try:
        await asyncio.gather(*workers, return_exceptions=True)
        for task in workers:
            if not task.cancelled() and task.exception():
                raise task.exception()
    except asyncio.CancelledError:
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        raise
    finally:
        if monitor_task is not None:
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)


async def build_action(project: Project, target: str) -> None:
    if target in ("all", "py"):
        await build_python(project)
    if target in ("all", "helpers"):
        await build_helpers(project)
    await make_dict(project)


def refuse_during_fuzz(socket: DashboardSocket, action: str) -> None:
    assert socket.project is not None
    if socket.tasks.running("fuzz", socket.project.name):
        raise ValueError(f"Cannot {action} while fuzzing is running on this project")


@handler("tasks:list", requires_project=False)
async def tasks_list(socket: DashboardSocket, message: dict[str, Any]) -> Any:
    return {"tasks": socket.tasks.snapshot()}


@handler("task:stop", requires_project=False)
async def task_stop(socket: DashboardSocket, message: dict[str, Any]) -> Any:
    task_id = str(message.get("taskId") or "")
    return await socket.tasks.stop(task_id)


@handler("task:start")
async def task_start(socket: DashboardSocket, message: dict[str, Any]) -> Any:
    project = socket.project
    assert project is not None
    action = str(message.get("action") or "")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError("params must be an object")

    if action == "fuzz":
        instances = int(params.get("instances") or 10)
        if not 1 <= instances <= 128:
            raise ValueError("instances must be between 1 and 128")
        afl_debug = bool(params.get("aflDebug"))
        monitor = bool(params.get("monitor", True))
        tracked = socket.tasks.start(
            f"fuzz (-j {instances})", "fuzz", project.name,
            fuzz_action(project, instances, afl_debug, monitor),
            exclusive_key=f"fuzz:{project.name}",
        )
    elif action == "build":
        target = str(params.get("target") or "all")
        if target not in ("all", "py", "helpers"):
            raise ValueError(f"Unknown build target: {target}")
        refuse_during_fuzz(socket, "build")
        tracked = socket.tasks.start(
            f"build: {target}", "build", project.name,
            build_action(project, target),
        )
    elif action == "clean":
        raw_components = params.get("components") or []
        if not isinstance(raw_components, list) or not raw_components:
            raise ValueError("clean requires at least one component")
        components = [CleanComponent(str(item)) for item in raw_components]
        refuse_during_fuzz(socket, "clean")
        label = "+".join(component.value for component in components)
        tracked = socket.tasks.start(
            f"clean: {label}", "clean", project.name,
            asyncio.to_thread(clean, project, components),
            thread_backed=True,
        )
    else:
        raise ValueError(f"Unknown action: {action}")

    return {"taskId": tracked.id}

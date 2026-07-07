from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


FINISHED_TASK_TTL = 60.0


@dataclass
class TrackedTask:
    id: str
    name: str
    kind: str
    project: str | None
    started_at: str
    status: str = "running"
    error: str | None = None
    finished_at: str | None = None
    thread_backed: bool = False
    exclusive_key: str | None = None
    aio_task: asyncio.Task[Any] | None = None
    progress: float | None = None
    eta_seconds: float | None = None
    phase: str | None = None

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
            "stoppable": self.status == "running" and not self.thread_backed,
            "progress": self.progress,
            "etaSeconds": self.eta_seconds,
            "phase": self.phase,
        }


class ProgressReporter:
    """Handle passed to a long task so it can report progress back to its
    TrackedTask. Bound to a task id once the task is started; a no-op until then."""

    def __init__(self) -> None:
        self._manager: TaskManager | None = None
        self._task_id: str | None = None

    def bind(self, manager: "TaskManager", task_id: str) -> None:
        self._manager = manager
        self._task_id = task_id

    def emit(self, progress: float, eta_seconds: float, phase: str) -> None:
        if self._manager is not None and self._task_id is not None:
            self._manager.set_progress(self._task_id, progress, eta_seconds, phase)


class TaskManager:
    def __init__(self, broadcast: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        self._broadcast = broadcast
        self._tasks: dict[str, TrackedTask] = {}
        self._counter = 0
        self._closing = False
        self._finalizers: set[asyncio.Task[None]] = set()

    def snapshot(self) -> list[dict[str, Any]]:
        return [task.payload() for task in self._tasks.values()]

    def running(self, kind: str, project: str | None = None) -> list[TrackedTask]:
        return [
            task
            for task in self._tasks.values()
            if task.status == "running"
            and task.kind == kind
            and (project is None or task.project == project)
        ]

    async def broadcast(self) -> None:
        if not self._closing:
            await self._broadcast({"event": "tasks.changed", "data": {"tasks": self.snapshot()}})

    def set_progress(
        self, task_id: str, progress: float | None, eta_seconds: float | None, phase: str | None
    ) -> None:
        tracked = self._tasks.get(task_id)
        if tracked is None or tracked.status != "running":
            return
        tracked.progress = progress
        tracked.eta_seconds = eta_seconds
        tracked.phase = phase
        asyncio.create_task(self.broadcast())

    def start(
        self,
        name: str,
        kind: str,
        project: str | None,
        awaitable: Awaitable[Any],
        *,
        thread_backed: bool = False,
        exclusive_key: str | None = None,
        progress_reporter: ProgressReporter | None = None,
    ) -> TrackedTask:
        if self._closing:
            if hasattr(awaitable, "close"):
                awaitable.close()  # type: ignore[union-attr]
            raise ValueError("PFUI is shutting down")
        if exclusive_key and any(
            task.status == "running" and task.exclusive_key == exclusive_key
            for task in self._tasks.values()
        ):
            if hasattr(awaitable, "close"):
                awaitable.close()  # type: ignore[union-attr]
            raise ValueError(f"A {kind} task is already running for this project")

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
        self._tasks[tracked.id] = tracked
        if progress_reporter is not None:
            progress_reporter.bind(self, tracked.id)
        tracked.aio_task = asyncio.create_task(awaitable, name=f"{tracked.id}:{name}")
        tracked.aio_task.add_done_callback(lambda task: self._on_done(tracked, task))
        asyncio.create_task(self.broadcast())
        return tracked

    def _on_done(self, tracked: TrackedTask, task: asyncio.Task[Any]) -> None:
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
        if not self._closing:
            finalizer = asyncio.create_task(self._finalize(tracked))
            self._finalizers.add(finalizer)
            finalizer.add_done_callback(self._finalizers.discard)

    async def _finalize(self, tracked: TrackedTask) -> None:
        await self.broadcast()
        await asyncio.sleep(FINISHED_TASK_TTL)
        if self._tasks.get(tracked.id) is tracked:
            del self._tasks[tracked.id]
            await self.broadcast()

    async def stop(self, task_id: str) -> dict[str, Any]:
        tracked = self._tasks.get(task_id)
        if tracked is None:
            raise ValueError(f"Unknown task: {task_id}")
        if tracked.status != "running":
            return {"stopped": False}
        if tracked.thread_backed:
            return {"stopped": False, "reason": "Task cannot be stopped safely"}
        assert tracked.aio_task is not None
        tracked.aio_task.cancel()
        await asyncio.sleep(0)
        return {"stopped": True}

    async def run_tracked(
        self,
        name: str,
        kind: str,
        project: str | None,
        awaitable: Awaitable[Any],
        *,
        exclusive_key: str | None = None,
    ) -> Any:
        tracked = self.start(name, kind, project, awaitable, exclusive_key=exclusive_key)
        assert tracked.aio_task is not None
        await asyncio.wait([tracked.aio_task])
        if tracked.aio_task.cancelled():
            raise ValueError("Task was stopped")
        error = tracked.aio_task.exception()
        if error is not None:
            raise error
        return tracked.aio_task.result()

    async def close(self) -> None:
        self._closing = True
        pending = [
            tracked.aio_task
            for tracked in self._tasks.values()
            if tracked.aio_task is not None and not tracked.aio_task.done() and not tracked.thread_backed
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for task in self._finalizers:
            task.cancel()
        if self._finalizers:
            await asyncio.gather(*self._finalizers, return_exceptions=True)

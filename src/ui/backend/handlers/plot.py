from __future__ import annotations

import bisect
from typing import TYPE_CHECKING, Any

from pyfuzz.project import Project

from .registry import Registry

if TYPE_CHECKING:
    from server import DashboardSocket

registry = Registry()
handler = registry.handler

_MAX_BUCKETS = 200


def _parse_plot_data(path: Any) -> list[tuple[int, int, float, int, int]]:
    """Return list of (relative_time, corpus_count, execs_per_sec, total_execs, edges_found)."""
    rows: list[tuple[int, int, float, int, int]] = []
    try:
        text = path.read_text()
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 13:
            continue
        try:
            rows.append((
                int(parts[0]),    # relative_time
                int(parts[3]),    # corpus_count
                float(parts[10]), # execs_per_sec
                int(parts[11]),   # total_execs
                int(parts[12]),   # edges_found
            ))
        except (ValueError, IndexError):
            continue
    return rows


def load_plot_data(project: Project) -> list[dict[str, Any]]:
    outputs_dir = project.path("outputs")
    if not outputs_dir.exists():
        return []

    # Collect rows per worker, sorted by relative_time
    workers: list[list[tuple[int, int, float, int, int]]] = []
    for worker_dir in sorted(outputs_dir.iterdir()):
        plot_file = worker_dir / "plot_data"
        if not plot_file.exists():
            continue
        rows = _parse_plot_data(plot_file)
        if rows:
            rows.sort(key=lambda r: r[0])
            workers.append(rows)

    if not workers:
        return []

    t_max = max(rows[-1][0] for rows in workers)
    if t_max == 0:
        return []

    n_buckets = min(_MAX_BUCKETS, max(r[0] for worker in workers for r in worker))
    step = t_max / n_buckets

    # Pre-extract relative_times per worker for bisect
    worker_times = [[r[0] for r in rows] for rows in workers]

    points: list[dict[str, Any]] = []
    for i in range(n_buckets + 1):
        t = int(i * step)
        execs_done = 0
        execs_per_sec = 0.0
        corpus_count = 0
        edges_found = 0
        for widx, rows in enumerate(workers):
            times = worker_times[widx]
            # Find the last row with relative_time <= t
            pos = bisect.bisect_right(times, t)
            if pos == 0:
                continue
            row = rows[pos - 1]
            execs_done += row[3]
            execs_per_sec += row[2]
            corpus_count += row[1]
            if row[4] > edges_found:
                edges_found = row[4]
        points.append({
            "time": t,
            "execs_done": execs_done,
            "execs_per_sec": round(execs_per_sec, 1),
            "corpus_count": corpus_count,
            "edges_found": edges_found,
        })

    return points


@handler("plot:data")
async def plot_data(socket: DashboardSocket, message: dict[str, Any]) -> Any:
    import asyncio
    points = await asyncio.to_thread(load_plot_data, socket.project)
    return {"points": points}

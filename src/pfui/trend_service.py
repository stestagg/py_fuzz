from __future__ import annotations

import bisect
from pathlib import Path
from typing import Any

from pyfuzz.project import Project


MAX_BUCKETS = 200


def _parse_plot_data(path: Path) -> list[tuple[int, int, float, int, int]]:
    rows: list[tuple[int, int, float, int, int]] = []
    try:
        text = path.read_text()
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 13:
            continue
        try:
            rows.append((int(parts[0]), int(parts[3]), float(parts[10]), int(parts[11]), int(parts[12])))
        except (ValueError, IndexError):
            continue
    return rows


def load_trend(project: Project) -> list[dict[str, Any]]:
    outputs = project.path("outputs")
    if not outputs.exists():
        return []
    workers: list[list[tuple[int, int, float, int, int]]] = []
    for worker in sorted(outputs.iterdir()):
        rows = _parse_plot_data(worker / "plot_data")
        if rows:
            workers.append(sorted(rows, key=lambda row: row[0]))
    if not workers:
        return []
    maximum = max(rows[-1][0] for rows in workers)
    if maximum <= 0:
        return []
    bucket_count = min(MAX_BUCKETS, maximum)
    step = maximum / bucket_count
    worker_times = [[row[0] for row in rows] for rows in workers]
    points: list[dict[str, Any]] = []
    for index in range(bucket_count + 1):
        timestamp = int(index * step)
        total_execs = 0
        execs_per_second = 0.0
        corpus = 0
        edges = 0
        for worker_index, rows in enumerate(workers):
            position = bisect.bisect_right(worker_times[worker_index], timestamp)
            if position == 0:
                continue
            row = rows[position - 1]
            corpus += row[1]
            execs_per_second += row[2]
            total_execs += row[3]
            edges = max(edges, row[4])
        points.append({
            "time": timestamp,
            "execs_done": total_execs,
            "execs_per_sec": round(execs_per_second, 1),
            "corpus_count": corpus,
            "edges_found": edges,
        })
    return points

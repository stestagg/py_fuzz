import time
from pathlib import Path
from .project import Project

# A worker is considered "live" if AFL rewrote its fuzzer_stats within this
# window. AFL refreshes fuzzer_stats at least every ~60s (STATS_UPDATE_SEC) while
# running, so a generous multiple of that reliably distinguishes a paused-but-alive
# fuzzer from a dead one without flapping during long calibration/trim stalls.
WORKER_STALE_SECONDS = 180


def parse_fuzzer_stats(path: Path) -> dict[str, str]:
    stats: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            stats[k.strip()] = v.strip()
    return stats


def summarize_fuzzing(project: Project) -> dict:
    outputs_dir = project.path("outputs")
    cores_dir = project.path("cores")

    # Aggregate fuzzer_stats across all worker dirs
    totals: dict[str, int] = {}
    float_fields: dict[str, float] = {}
    worker_count = 0  # dirs that ever ran (fuzzer_stats persists after a worker dies)
    live_worker_count = 0  # dirs whose fuzzer_stats is still being refreshed
    now = time.time()
    for worker_dir in outputs_dir.iterdir():
        stats_file = worker_dir / "fuzzer_stats"
        if not stats_file.exists():
            continue
        worker_count += 1
        stats = parse_fuzzer_stats(stats_file)
        try:
            last_update = int(stats.get("last_update", 0))
        except ValueError:
            last_update = 0
        if now - last_update <= WORKER_STALE_SECONDS:
            live_worker_count += 1
        for field in ("execs_done", "saved_crashes", "saved_hangs", "total_tmout",
                      "run_time", "corpus_count", "corpus_found", "edges_found"):
            totals[field] = totals.get(field, 0) + int(stats.get(field, 0))
        for field in ("execs_per_sec",):
            float_fields[field] = float_fields.get(field, 0.0) + float(stats.get(field, 0))

    # Count crash files (skip README)
    crashes = 0
    for worker_dir in outputs_dir.iterdir():
        crash_dir = worker_dir / "crashes"
        if crash_dir.exists():
            crashes += sum(1 for f in crash_dir.iterdir() if f.name != "README.txt")

    # Count core dumps
    core_dumps = sum(1 for f in cores_dir.rglob("*") if f.is_file()) if cores_dir.exists() else 0

    return {
        "project": project.name,
        "workers": live_worker_count,
        "workers_total": worker_count,
        "crashes": crashes,
        "core_dumps": core_dumps,
        **{k: v for k, v in totals.items()},
        **{k: round(v, 2) for k, v in float_fields.items()},
    }
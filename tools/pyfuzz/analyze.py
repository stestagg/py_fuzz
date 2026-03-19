from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import click

from tools.pyfuzz.console import detail, step, success, warn


def parse_memory_limit(readme: Path) -> int | None:
    if not readme.exists():
        return None
    match = re.search(r"limit used for this fuzzing session was (\d+) MB", readme.read_text())
    return int(match.group(1)) if match else None


def analyze_crash(harness: Path, crash_path: Path, analysis_dir: Path, memory_limit_mb: int | None) -> None:
    analysis_dir.mkdir(parents=True, exist_ok=True)
    cmds = ["-ex", "set pagination off", "-ex", "set confirm off"]
    if memory_limit_mb is not None:
        limit_bytes = memory_limit_mb * 1024 * 1024
        cmds += ["-ex", f"set exec-wrapper prlimit --as={limit_bytes}:{limit_bytes}"]
    cmds += ["-ex", f"run < {crash_path}", "-ex", "bt full", "-ex", "info registers"]
    result = subprocess.run(["gdb", "-batch", *cmds, "--args", str(harness)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
    (analysis_dir / "info.txt").write_text(result.stdout)
    shutil.copy(crash_path, analysis_dir / "input")
    success(f"Analyzed {crash_path.name}")
    detail("analysis", str(analysis_dir))


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--project-root", required=True, type=click.Path(path_type=Path))
@click.option("--worker")
@click.option("--crash")
@click.option("--no-memory-limit", is_flag=True)
def main(project_root: Path, worker: str | None, crash: str | None, no_memory_limit: bool) -> None:
    harness = project_root / "dist" / "fuzz_python"
    if not harness.exists():
        raise click.ClickException(f"Harness not found: {harness}")
    outputs = project_root / "outputs"
    step(f"Scanning crashes in {outputs}")
    for worker_dir in sorted(path for path in outputs.iterdir() if path.is_dir()):
        if worker and worker_dir.name != worker:
            continue
        crashes_dir = worker_dir / "crashes"
        if not crashes_dir.exists():
            continue
        memory_limit = None if no_memory_limit else parse_memory_limit(crashes_dir / "README.txt")
        if memory_limit is None and not no_memory_limit:
            warn(f"No memory limit metadata for {worker_dir.name}; running without exec-wrapper limit")
        for crash_path in sorted(path for path in crashes_dir.iterdir() if path.is_file() and path.name != "README.txt"):
            if crash and crash_path.name != crash:
                continue
            analysis_dir = project_root / "analysis" / f"{worker_dir.name}-{crash_path.name}"
            if (analysis_dir / "info.txt").exists():
                warn(f"Skipping {crash_path.name}; analysis already exists")
                continue
            analyze_crash(harness, crash_path, analysis_dir, memory_limit)


if __name__ == "__main__":
    main()

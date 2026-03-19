#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "click",
# ]
# ///
"""Crash analysis script for py-fuzz.

Outside Docker: builds py-fuzz-analyze:latest (py-fuzz + gdb) and launches itself inside it.
Inside Docker:  iterates crash files, runs each through gdb, and writes results to analysis/.
"""

import os
import re
import sys
import shutil
import subprocess
from pathlib import Path

import click

SCRIPT_DIR = Path(__file__).parent.resolve()
ANALYZE_IMAGE = "py-fuzz-debug:latest"
BASE_IMAGE = "py-fuzz:latest"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_memory_limit(crashes_dir: Path) -> int | None:
    readme = crashes_dir / "README.txt"
    if not readme.exists():
        return None
    m = re.search(r"limit used for this fuzzing session was (\d+) MB", readme.read_text())
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Host mode
# ---------------------------------------------------------------------------

def build_image(force: bool = False) -> None:
    if not force:
        result = subprocess.run(
            ["docker", "image", "inspect", ANALYZE_IMAGE],
            capture_output=True,
        )
        if result.returncode == 0:
            return
    print(f"==> Building Docker image {ANALYZE_IMAGE}...")
    subprocess.run(
        ["docker", "build", "-t", ANALYZE_IMAGE, "-f", "Dockerfile.debug", "."],
        cwd=SCRIPT_DIR,
        check=True,
    )


def run_in_docker(
    pr_id: str | None,
    worker_id: str | None,
    crash_name: str | None,
    no_memory_limit: bool,
) -> None:
    cmd = [
        "docker", "run", "--rm", "-it",
        "-v", f"{SCRIPT_DIR}:/src",
        "-v", f"{SCRIPT_DIR}/dist/{pr_id}:/dist",
        "-w", "/src",
        "-e", "ANALYZE_INSIDE_DOCKER=1",
        "--security-opt", "seccomp=unconfined",
        "--cap-add=SYS_PTRACE",
        ANALYZE_IMAGE,
        "/src/analyze.py",
    ]
    if pr_id:
        cmd.append(pr_id)
    if worker_id:
        cmd.extend(["--worker", worker_id])
    if crash_name:
        cmd.extend(["--crash", crash_name])
    if no_memory_limit:
        cmd.append("--no-memory-limit")
    os.execvp("docker", cmd)


# ---------------------------------------------------------------------------
# Docker (analysis) mode
# ---------------------------------------------------------------------------

def gdb_commands(crash_path: Path, memory_limit_mb: int | None) -> list[str]:
    cmds = [
        "-ex", "set pagination off",
        "-ex", "set confirm off",
    ]
    if memory_limit_mb is not None:
        limit_bytes = memory_limit_mb * 1024 * 1024
        cmds += ["-ex", f"set exec-wrapper prlimit --as={limit_bytes}:{limit_bytes}"]
    cmds += [
        "-ex", f"run < {crash_path} 2>&1",
        "-ex", "bt full",
        "-ex", "info registers",
    ]
    return cmds


def extract_signal(gdb_output: str) -> str:
    for line in gdb_output.splitlines():
        lower = line.lower()
        if "signal" in lower or "sigsegv" in lower or "sigabrt" in lower or "sigfpe" in lower:
            return line.strip()
        if "exited with code" in lower or "exited normally" in lower:
            return line.strip()
    return "unknown"


def analyze_single_crash(
    crash_path: Path,
    analysis_dir: Path,
    binary_path: Path,
    memory_limit_mb: int | None,
) -> None:
    analysis_dir.mkdir(parents=True, exist_ok=True)

    gdb_cmd = [
        "gdb", "-batch",
        *gdb_commands(crash_path, memory_limit_mb),
        "--args", str(binary_path),
    ]

    try:
        result = subprocess.run(
            gdb_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        print(f"  [warn] gdb timed out for {crash_path.name}", file=sys.stderr)
        shutil.rmtree(analysis_dir)
        return
    except Exception as exc:
        print(f"  [warn] gdb failed for {crash_path.name}: {exc}", file=sys.stderr)
        shutil.rmtree(analysis_dir)
        return

    output = result.stdout.decode(errors="replace")

    (analysis_dir / "info.txt").write_text(output)
    shutil.copy(crash_path, analysis_dir / "input")

    sig = extract_signal(output)
    pr_id = crash_path.parts[-4]
    worker_id = crash_path.parts[-3]
    print(f"  [ok] PR={pr_id} worker={worker_id} crash={crash_path.name} | {sig}")


def docker_main(
    pr_id: str | None,
    worker_id: str | None,
    crash_name: str | None,
    no_memory_limit: bool,
) -> None:
    output_dir = SCRIPT_DIR / "output"

    if pr_id:
        pr_dirs = [output_dir / pr_id]
    else:
        if not output_dir.exists():
            print("No output/ directory found.", file=sys.stderr)
            sys.exit(1)
        pr_dirs = [p for p in output_dir.iterdir() if p.is_dir()]

    for pr_dir in sorted(pr_dirs):
        if not pr_dir.exists():
            print(f"[warn] {pr_dir} not found", file=sys.stderr)
            continue

        binary_path = Path("/dist") / "fuzz_python"

        for worker_dir in sorted(pr_dir.iterdir()):
            if not worker_dir.is_dir():
                continue
            if worker_id and worker_dir.name != worker_id:
                continue
            crashes_dir = worker_dir / "crashes"
            if not crashes_dir.exists():
                continue

            if no_memory_limit:
                memory_limit_mb = None
            else:
                memory_limit_mb = parse_memory_limit(crashes_dir)
                if memory_limit_mb is not None:
                    print(f"  [mem] enforcing {memory_limit_mb} MB limit (from README.txt)")
                else:
                    print("  [mem] no memory limit found in README.txt", file=sys.stderr)

            for crash_file in sorted(crashes_dir.iterdir()):
                if crash_file.name == "README.txt" or not crash_file.is_file():
                    continue
                if crash_name and crash_file.name != crash_name:
                    continue

                analysis_dir = (
                    SCRIPT_DIR / "analysis"
                    / pr_dir.name
                    / f"{worker_dir.name}-{crash_file.name}"
                )

                if (analysis_dir / "info.txt").exists():
                    print(f"  [skip] {crash_file.name} (already analyzed)")
                    continue

                if not binary_path.exists():
                    print(
                        f"  [warn] binary not found for PR {pr_dir.name}: {binary_path}",
                        file=sys.stderr,
                    )
                    continue

                print(f"  [analyzing] {crash_file.name}")
                analyze_single_crash(crash_file, analysis_dir, binary_path, memory_limit_mb)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("pr_id", required=False, default=None)
@click.option("--worker", "worker_id", default=None, metavar="ID", help="Limit to a specific worker.")
@click.option("--crash", "crash_name", default=None, metavar="NAME", help="Limit to a specific crash file.")
@click.option("--build", "force_build", is_flag=True, help="Force rebuild of the Docker image.")
@click.option("--no-memory-limit", "no_memory_limit", is_flag=True, help="Skip memory limit enforcement parsed from README.txt.")
def main(
    pr_id: str | None,
    worker_id: str | None,
    crash_name: str | None,
    force_build: bool,
    no_memory_limit: bool,
) -> None:
    """Analyze AFL crash files using gdb."""
    if os.environ.get("ANALYZE_INSIDE_DOCKER") == "1":
        docker_main(pr_id, worker_id, crash_name, no_memory_limit)
    else:
        build_image(force=force_build)
        run_in_docker(pr_id, worker_id, crash_name, no_memory_limit)


if __name__ == "__main__":
    main()

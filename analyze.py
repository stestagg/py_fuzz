#!/usr/bin/env python3
"""Crash analysis script for py-fuzz.

Outside Docker: builds py-fuzz-analyze:latest (py-fuzz + gdb) and launches itself inside it.
Inside Docker:  iterates crash files, runs each through gdb, and writes results to analysis/.
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
ANALYZE_IMAGE = "py-fuzz-analyze:latest"
BASE_IMAGE = "py-fuzz:latest"

ANALYZE_DOCKERFILE = f"""\
FROM {BASE_IMAGE}
RUN apt-get update && apt-get install -y --no-install-recommends gdb \\
    && rm -rf /var/lib/apt/lists/*
"""


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
        ["docker", "build", "-t", ANALYZE_IMAGE, "-f", "-", "."],
        input=ANALYZE_DOCKERFILE.encode(),
        cwd=SCRIPT_DIR,
        check=True,
    )


def run_in_docker(pr_id: str | None) -> None:
    cmd = [
        "docker", "run", "--rm", "-it",
        "-v", f"{SCRIPT_DIR}:/src",
        "-w", "/src",
        "-e", "ANALYZE_INSIDE_DOCKER=1",
        ANALYZE_IMAGE,
        "python", "/src/analyze.py",
    ]
    if pr_id:
        cmd.append(pr_id)
    os.execvp("docker", cmd)


def host_main(args: list[str]) -> None:
    pr_id: str | None = None
    force_build = False

    i = 0
    while i < len(args):
        if args[i] == "--build":
            force_build = True
        elif args[i] in ("-h", "--help"):
            print("Usage: python analyze.py [--build] [PR_ID]")
            sys.exit(0)
        elif not args[i].startswith("-"):
            pr_id = args[i]
        else:
            print(f"Unknown option: {args[i]}", file=sys.stderr)
            sys.exit(1)
        i += 1

    build_image(force=force_build)
    run_in_docker(pr_id)


# ---------------------------------------------------------------------------
# Docker (analysis) mode
# ---------------------------------------------------------------------------

def gdb_commands(crash_path: Path) -> list[str]:
    return [
        "-ex", "set pagination off",
        "-ex", "set confirm off",
        "-ex", f"run < {crash_path}",
        "-ex", "bt full",
        "-ex", "info registers",
        "-ex", "info signals",
    ]


def extract_signal(gdb_output: str) -> str:
    for line in gdb_output.splitlines():
        lower = line.lower()
        if "signal" in lower or "sigsegv" in lower or "sigabrt" in lower or "sigfpe" in lower:
            return line.strip()
    return "unknown"


def analyze_single_crash(crash_path: Path, analysis_dir: Path, binary_path: Path) -> None:
    analysis_dir.mkdir(parents=True)

    gdb_cmd = [
        "gdb", "-batch",
        *gdb_commands(crash_path),
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


def docker_main(pr_id: str | None) -> None:
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

        binary_path = SCRIPT_DIR / "dist" / pr_dir.name / "fuzz_python"

        for worker_dir in sorted(pr_dir.iterdir()):
            if not worker_dir.is_dir():
                continue
            crashes_dir = worker_dir / "crashes"
            if not crashes_dir.exists():
                continue

            for crash_file in sorted(crashes_dir.iterdir()):
                if crash_file.name == "README.txt" or not crash_file.is_file():
                    continue

                analysis_dir = (
                    SCRIPT_DIR / "analysis"
                    / pr_dir.name
                    / f"{worker_dir.name}-{crash_file.name}"
                )

                if analysis_dir.exists():
                    print(f"  [skip] {crash_file.name} (already analyzed)")
                    continue

                if not binary_path.exists():
                    print(
                        f"  [warn] binary not found for PR {pr_dir.name}: {binary_path}",
                        file=sys.stderr,
                    )
                    continue

                print(f"  [analyzing] {crash_file.name}")
                analyze_single_crash(crash_file, analysis_dir, binary_path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = sys.argv[1:]

    if os.environ.get("ANALYZE_INSIDE_DOCKER") == "1":
        pr_id = args[0] if args else None
        docker_main(pr_id)
    else:
        host_main(args)


if __name__ == "__main__":
    main()

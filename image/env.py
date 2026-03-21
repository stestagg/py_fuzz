"""Canonical environment variables for image scripts."""
from __future__ import annotations

import os
from pathlib import Path

# Allow AFL-instrumented binaries to run outside afl-fuzz (needed during
# build/make and when running the harness directly for analysis).
AFL_IGNORE_PROBLEMS = "1"


def base_env(dist_dir: Path) -> dict[str, str]:
    """PYTHONHOME + LD_PRELOAD shim — for afl-fuzz (run.py). Not for GDB: set
    PYTHONHOME on the inferior via `set environment` instead, or GDB's own
    embedded Python will break."""
    env = dict(os.environ)
    env["PYTHONHOME"] = str(dist_dir / "install")
    shim = dist_dir / "nocorelimit.so"
    if shim.exists():
        env["LD_PRELOAD"] = str(shim)
    return env


def afl_env(dist_dir: Path) -> dict[str, str]:
    """base_env + AFL_IGNORE_PROBLEMS — for running the harness outside afl-fuzz."""
    env = base_env(dist_dir)
    env["AFL_IGNORE_PROBLEMS"] = AFL_IGNORE_PROBLEMS
    return env

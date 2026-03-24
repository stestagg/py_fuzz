"""Canonical environment variables for image scripts."""
from __future__ import annotations

import os
from pathlib import Path

# Allow AFL-instrumented binaries to run outside afl-fuzz (needed during
# build/make and when running the harness directly for analysis).
AFL_IGNORE_PROBLEMS = "1"


def _ext_sos(dist_dir: Path) -> list[str]:
    """Return paths to all AFL-instrumented Python extension .so files.

    These must be preloaded (via AFL_PRELOAD for afl-fuzz runs, or LD_PRELOAD
    for standalone runs) so the dynamic linker maps them into the process
    *before* the AFL forkserver constructor fires.

    Without preloading, Python's import machinery dlopen()s them after the
    forkserver is already up, which triggers AFL++'s fatal
    "instrumented dlopen() library loaded after forkserver" error — even
    without ASAN.  AFL_DEFER_FORKSRV=1 is the intended fix, but it has no
    effect here because the Python static library (compiled with
    afl-clang-fast and linked via --whole-archive) contributes its own AFL
    runtime constructor that fires regardless of the env var.
    """
    sos: list[str] = []
    for dynload_dir in sorted((dist_dir / "install").glob("lib/python*/lib-dynload")):
        sos.extend(str(p) for p in sorted(dynload_dir.glob("*.so")))
    return sos


def base_env(dist_dir: Path) -> dict[str, str]:
    """PYTHONHOME + AFL_PRELOAD + AFL_ALLOW_CORES — for afl-fuzz (run.py).

    AFL_PRELOAD is consumed by afl-fuzz and applied as LD_PRELOAD to the
    target process, ensuring all extension .so files are mapped before the
    AFL forkserver constructor runs.

    AFL_ALLOW_CORES prevents afl-fuzz from zeroing RLIMIT_CORE (both soft and
    hard limits) so that coredumps are actually written on crashes.

    Not for GDB: set PYTHONHOME on the inferior via `set environment` instead,
    or GDB's own embedded Python will break.
    """
    env = dict(os.environ)
    env["PYTHONHOME"] = str(dist_dir / "install")
    env["AFL_ALLOW_CORES"] = "1"
    preload = _ext_sos(dist_dir)
    if preload:
        env["AFL_PRELOAD"] = ":".join(preload)
    return env


def afl_env(dist_dir: Path) -> dict[str, str]:
    """PYTHONHOME + LD_PRELOAD + AFL_IGNORE_PROBLEMS — for running the harness
    outside afl-fuzz (debug / analysis / standalone).

    Uses LD_PRELOAD directly because AFL_PRELOAD is only processed by afl-fuzz.
    AFL_IGNORE_PROBLEMS is kept as a belt-and-suspenders fallback in case any
    .so is still dlopen'd after the forkserver (e.g. a newly linked extension
    not yet in lib-dynload).
    """
    env = dict(os.environ)
    env["PYTHONHOME"] = str(dist_dir / "install")
    env["AFL_IGNORE_PROBLEMS"] = AFL_IGNORE_PROBLEMS
    preload = _ext_sos(dist_dir)
    if preload:
        env["LD_PRELOAD"] = ":".join(preload)
    return env

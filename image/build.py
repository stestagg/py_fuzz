from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).parent.parent))

import click

from image.env import AFL_IGNORE_PROBLEMS
from tools.pyfuzz.console import run, step, success


def needs_rebuild(target: Path, deps: list[Path], force: bool) -> bool:
    if force or not target.exists():
        return True
    target_mtime = target.stat().st_mtime
    return any(dep.exists() and dep.stat().st_mtime > target_mtime for dep in deps)


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--force", is_flag=True)
@click.option("--jobs", "-j", type=click.IntRange(min=1), default=None)
def main(force: bool, jobs: int | None) -> None:
    repo = Path("/repo")
    project = Path("/project")
    source_dir = project / "cpython"
    dist_dir = project / "dist"
    prefix = dist_dir / "install"
    harness_src = repo / "helpers" / "fuzz_python.c"
    shim_src = repo / "helpers" / "nocorelimit.c"
    trace_src = repo / "helpers" / "trace_dlopen.c"
    harness = dist_dir / "fuzz_python"
    harness_cmplog = dist_dir / "fuzz_python_cmplog"
    shim_so = dist_dir / "nocorelimit.so"
    trace_so = dist_dir / "trace_dlopen.so"
    asan = os.environ.get("ASAN") == "1"

    dist_dir.mkdir(parents=True, exist_ok=True)
    if asan:
        (dist_dir / ".asan").write_text("")
    else:
        (dist_dir / ".asan").unlink(missing_ok=True)

    afl_cc = shutil.which("afl-clang-fast") or shutil.which("afl-clang-lto") or "afl-clang-fast"
    extra_cflags = ["-fsanitize=address", "-fno-omit-frame-pointer"] if asan else []

    base_env = dict(os.environ)
    if asan:
        base_env["AFL_USE_ASAN"] = "1"

    configure_env = {
        **base_env,
        "CC": afl_cc,
        "CFLAGS": " ".join(["-O2", "-g", *extra_cflags]),
        "LDFLAGS": " ".join(extra_cflags),
        "ax_cv_c_float_words_bigendian": "no",
    }
    make_env = {**base_env, "PYTHONPATH": str(source_dir / "Lib"), "AFL_IGNORE_PROBLEMS": AFL_IGNORE_PROBLEMS}
    cmplog_env = {**base_env, "AFL_LLVM_CMPLOG": "1"}

    python_bin = prefix / "bin" / "python3"
    if needs_rebuild(python_bin, [source_dir / "configure"], force):
        step(f"Configuring instrumented CPython in {source_dir}")
        run([
            "./configure",
            f"--prefix={prefix}",
            "--disable-shared",
            "--without-pymalloc",
            "--without-ensurepip",      # no pip bootstrapping
            "--disable-test-modules",   # no _testcapi, _testinternalcapi, etc.
            "--without-doc-strings",    # strip docstrings — saves space, irrelevant for fuzzing
        ], cwd=source_dir, env=configure_env)
        run(["make", f"-j{jobs or os.cpu_count() or 4}"], cwd=source_dir, env=make_env)
        # COMPILEALL_OPTS=-j0 disables .pyc precompilation during install — the ASAN-instrumented
        # Python uses enough memory during compileall to trigger OOM kills. pyc files are
        # unnecessary since the harness runs with write_bytecode=0.
        run(["make", "install", "COMPILEALL_OPTS=-j0"], cwd=source_dir, env={**base_env, "AFL_IGNORE_PROBLEMS": AFL_IGNORE_PROBLEMS})
    else:
        step("Instrumented CPython is up to date")

    pycfg = prefix / "bin" / "python3-config"
    include_flags = subprocess.check_output([str(pycfg), "--includes"], text=True).strip().split()
    ldflags = subprocess.check_output([str(pycfg), "--ldflags", "--embed"], text=True).strip().split()
    linker_flags: list[str] = []
    for flag in ldflags:
        if flag.startswith("-lpython"):
            linker_flags += ["-Wl,--whole-archive", flag, "-Wl,--no-whole-archive"]
        else:
            linker_flags.append(flag)

    harness_cmd = [afl_cc, "-O2", "-g", *extra_cflags, *include_flags, str(harness_src), *linker_flags, "-Wl,-export-dynamic", "-o"]
    # cmplog harness is built without ASAN: the cmplog instrumentation does 8-byte reads
    # on comparison operands which trips ASAN global redzones on short string literals.
    harness_cmplog_cmd = [afl_cc, "-O2", "-g", *include_flags, str(harness_src), *linker_flags, "-Wl,-export-dynamic", "-o"]
    if needs_rebuild(harness, [harness_src, pycfg], force):
        step(f"Building harness {harness.name}")
        run([*harness_cmd, str(harness)], env=base_env)
    if needs_rebuild(harness_cmplog, [harness_src, pycfg], force):
        step(f"Building harness {harness_cmplog.name}")
        run([*harness_cmplog_cmd, str(harness_cmplog)], env=cmplog_env)
    if needs_rebuild(shim_so, [shim_src], force):
        step(f"Building shim {shim_so.name}")
        run(["gcc", "-shared", "-fPIC", "-o", str(shim_so), str(shim_src), "-ldl"], env=base_env)
    if needs_rebuild(trace_so, [trace_src], force):
        step(f"Building shim {trace_so.name}")
        run(["gcc", "-shared", "-fPIC", "-o", str(trace_so), str(trace_src), "-ldl"], env=base_env)
    success(f"Build complete for {project.name}")


if __name__ == "__main__":
    main()

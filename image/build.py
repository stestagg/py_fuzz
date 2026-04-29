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
from tools.pyfuzz.project import load_project_config_from_root, resolve_harness_paths

FRAME_POINTER_CFLAGS = ["-fno-omit-frame-pointer", "-mno-omit-leaf-frame-pointer"]


def compiler_cflags() -> list[str]:
    asan = False
    cflags = ["-O2", "-g", *FRAME_POINTER_CFLAGS]
    if asan:
        cflags.append("-fsanitize=address")
    return cflags


def analysis_linker_flags() -> list[str]:
    asan = False
    return ["-fsanitize=address"] if asan else []


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
    config = load_project_config_from_root(project)
    source_dir = project / "cpython"
    dist_dir = project / "dist"
    prefix = dist_dir / "install"
    harness_src = repo / "helpers" / "fuzz_python.c"
    peg_harness_src = repo / "helpers" / "fuzz_peg.c"
    trace_src = repo / "helpers" / "trace_dlopen.c"
    mem_limit_src = repo / "helpers" / "mem_limit_exec.c"
    harness, harness_cmplog = resolve_harness_paths(project, config.harness)
    trace_so = dist_dir / "trace_dlopen.so"

    dist_dir.mkdir(parents=True, exist_ok=True)
    harness.parent.mkdir(parents=True, exist_ok=True)

    afl_cc = shutil.which("afl-clang-lto")
    common_cflags = compiler_cflags()
    common_linker_flags = analysis_linker_flags()
    cmplog_cflags = compiler_cflags()

    base_env = dict(os.environ)

    configure_env = {
        **base_env,
        "CC": afl_cc,
        "CXX": afl_cc + "++",
        "CFLAGS": " ".join(common_cflags),
        "LDFLAGS": " ".join(common_linker_flags),
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
    embed_linker_flags: list[str] = []
    for flag in ldflags:
        if flag.startswith("-lpython"):
            embed_linker_flags += ["-Wl,--whole-archive", flag, "-Wl,--no-whole-archive"]
        else:
            embed_linker_flags.append(flag)

    harness_cmd = [afl_cc, *common_cflags, *include_flags, str(harness_src), *embed_linker_flags, "-Wl,-export-dynamic", "-o"]
    # cmplog harness is built without ASAN: the cmplog instrumentation does 8-byte reads
    # on comparison operands which trips ASAN global redzones on short string literals.
    harness_cmplog_cmd = [afl_cc, *cmplog_cflags, *include_flags, str(harness_src), *embed_linker_flags, "-Wl,-export-dynamic", "-o"]
    if needs_rebuild(harness, [harness_src, pycfg], force):
        step(f"Building harness {harness.name}")
        run([*harness_cmd, str(harness)], env=base_env)
    if needs_rebuild(harness_cmplog, [harness_src, pycfg], force):
        step(f"Building harness {harness_cmplog.name}")
        run([*harness_cmplog_cmd, str(harness_cmplog)], env=cmplog_env)
    peg_harness = dist_dir / "fuzz_peg"
    peg_harness_cmplog = dist_dir / "fuzz_peg_cmplog"
    peg_cmd = [afl_cc, *common_cflags, *include_flags, str(peg_harness_src), *embed_linker_flags, "-Wl,-export-dynamic", "-o"]
    peg_cmplog_cmd = [afl_cc, *cmplog_cflags, *include_flags, str(peg_harness_src), *embed_linker_flags, "-Wl,-export-dynamic", "-o"]
    if needs_rebuild(peg_harness, [peg_harness_src, pycfg], force):
        step(f"Building harness {peg_harness.name}")
        run([*peg_cmd, str(peg_harness)], env=base_env)
    if needs_rebuild(peg_harness_cmplog, [peg_harness_src, pycfg], force):
        step(f"Building harness {peg_harness_cmplog.name}")
        run([*peg_cmplog_cmd, str(peg_harness_cmplog)], env=cmplog_env)
    if needs_rebuild(trace_so, [trace_src], force):
        step(f"Building shim {trace_so.name}")
        run(["clang", "-shared", "-fPIC", "-o", str(trace_so), str(trace_src), "-ldl"], env=base_env)
    mem_limit_exec = dist_dir / "mem_limit_exec"
    if needs_rebuild(mem_limit_exec, [mem_limit_src], force):
        step(f"Building helper {mem_limit_exec.name}")
        run(["clang", "-O2", "-o", str(mem_limit_exec), str(mem_limit_src)], env=base_env)
    success(f"Build complete for {project.name}")


if __name__ == "__main__":
    main()

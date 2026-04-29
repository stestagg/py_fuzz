from __future__ import annotations

import shlex
from pathlib import Path

from .project import REPO_ROOT, Project

PFRUN = "./pfrun"
AFL_IMAGE_DIR = "envs/afl"
LLDB_IMAGE_DIR = "envs/lldb"

GUEST_REPO = Path("/pfm/repo")
GUEST_PROJECT = Path("/pfm/project")
GUEST_TESTCASES = GUEST_PROJECT / "inputs"
GUEST_DICT = GUEST_REPO / "helpers" / "python.dict"
GUEST_OUTPUTS = Path("/pfm/outputs")
GUEST_CORES = Path("/pfm/cores")
GUEST_LOGS = Path("/pfm/logs")
UV_CACHE_DIR = REPO_ROOT / ".uv-pfrun-cache"

DEFAULT_DEBUG_MEM_MB = 1024
HANG_ANALYSIS_TIMEOUT = 180


def _repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return f"projects/{path.name}"


def project_guest_path(relative_path: str, *, project_root: Path = GUEST_PROJECT) -> str:
    return str(project_root / relative_path)


def guest_core_path(project_root: Path, core_path: Path) -> Path:
    core_path = core_path.resolve()
    project_root = project_root.resolve()
    try:
        relative = core_path.relative_to(project_root)
    except ValueError as exc:
        raise ValueError(f"Core path must be inside the project root: {core_path}") from exc
    return GUEST_PROJECT / relative


def shell_script(argv: list[str], env: dict[str, str] | None = None) -> str:
    lines = ["set -eu"]
    for key, value in sorted((env or {}).items()):
        lines.append(f"export {key}={shlex.quote(str(value))}")
    lines.append("exec " + shlex.join(str(part) for part in argv))
    return "\n".join(lines)


def pfrun_command(
    *,
    image_dir: str,
    project_root: Path,
    guest_argv: list[str],
    vm_mem: int,
    env: dict[str, str] | None = None,
    ncpu: int = 1,
    timeout: int | None = None,
    writable_dirs: list[str] = (),
) -> list[str]:
    UV_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (UV_CACHE_DIR / "tmp").mkdir(parents=True, exist_ok=True)
    
    proj_rel = _repo_relative(project_root)
    cmd = [
        PFRUN,
        "--imagedir", image_dir,
        "--ncpu", str(ncpu),
        "--mem", str(vm_mem),
        "--cmd", shell_script(guest_argv, env),
        "--mount", ".:repo",
    ]
    cmd += ["--mount", f"{proj_rel}:project"]

    always_mounts = {'outputs', 'cores', 'logs'}

    for dir in always_mounts - set(writable_dirs):
        cmd += ["--mount", f"{proj_rel}/{dir}:{dir}"]

    for writable in writable_dirs:
        cmd += ["--mount-rw", f"{proj_rel}/{writable}:{writable}"]
    cmd += ["--mount-rw", ".uv-pfrun-cache:uv-cache"]
    if timeout is not None:
        cmd += ["--timeout", str(timeout)]
    return cmd


def afl_command(
    *,
    project_root: Path,
    guest_argv: list[str],
    vm_mem: int,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
) -> list[str]:
    return pfrun_command(
        image_dir=AFL_IMAGE_DIR,
        project_root=project_root,
        guest_argv=guest_argv,
        env=env,
        ncpu=1,
        vm_mem=vm_mem,
        timeout=timeout,
        writable_dirs=["outputs", "cores", "logs"],
    )


def _afl_env(proj: Project, *, test_crash: bool = False, debug: bool = False) -> dict[str, str]:
    env: dict[str, str] = {
        "PYTHONPATH": str(GUEST_REPO),
        "PROJECT_ROOT": str(GUEST_PROJECT),
        "TESTCASES_DIR": str(GUEST_TESTCASES),
        "DICT_FILE": str(GUEST_DICT),
        "OUTPUT_DIR": str(GUEST_OUTPUTS),
        "LOGS_DIR": str(GUEST_LOGS),
        "PYTHONUNBUFFERED": "1",
    }
    if test_crash:
        env["FUZZ_TEST_CRASH"] = "1"
    if debug:
        env["AFL_DEBUG"] = "1"
    if proj.config.asan:
        env["ASAN_OPTIONS"] = proj.config.asan_options
        env["AFL_USE_ASAN"] = "1"
        env["AFL_IGNORE_PROBLEMS"] = "1"
    return env


def afl_run_command(
    *,
    proj: Project,
    runner_name: str,
    timeout: int | None = None,
    test_crash: bool = False,
    debug: bool = False,
) -> list[str]:
    guest_argv = [
        "uv", "run", str(GUEST_REPO / "image" / "run.py"),
        "--project-root", str(GUEST_PROJECT),
        "--runner-name", runner_name,
    ]
    if timeout is not None:
        guest_argv += ["--timeout", str(timeout)]
    return afl_command(
        project_root=proj.root,
        guest_argv=guest_argv,
        env=_afl_env(proj, test_crash=test_crash, debug=debug),
        vm_mem=proj.config.vm_mem,
    )


def afl_shell_command(
    *,
    proj: Project,
    test_crash: bool = False,
    debug: bool = False,
) -> list[str]:
    return afl_command(
        project_root=proj.root,
        guest_argv=["sh"],
        env=_afl_env(proj, test_crash=test_crash, debug=debug),
        vm_mem=proj.config.vm_mem,
    )


def afl_trace_command(
    *,
    proj: Project,
    test_crash: bool = False,
    debug: bool = False,
) -> list[str]:
    return afl_command(
        project_root=proj.root,
        guest_argv=["uv", "run", str(GUEST_REPO / "image" / "trace_inputs.py")],
        env=_afl_env(proj, test_crash=test_crash, debug=debug),
        vm_mem=proj.config.vm_mem,
    )


def debug_command(
    *,
    project_root: Path,
    guest_argv: list[str],
    vm_mem: int,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
) -> list[str]:
    return pfrun_command(
        image_dir=LLDB_IMAGE_DIR,
        project_root=project_root,
        guest_argv=guest_argv,
        env=env,
        ncpu=1,
        vm_mem=vm_mem,
        timeout=timeout,
        writable_dirs=["analysis", "core_analysis", "logs"],
    )


def lldb_core_command(*, proj: Project, project_root: Path, core_path: Path) -> list[str]:
    guest_core = guest_core_path(project_root, core_path)
    lldb_cmd = [
        "lldb",
        "--core", str(guest_core),
        "--file", project_guest_path(proj.config.harness),
        "-o",
        f"settings set target.env-vars PYTHONHOME={GUEST_PROJECT / 'dist' / 'install'} AFL_IGNORE_PROBLEMS=1",
    ]
    if proj.config.asan:
        lldb_cmd += ["-o", f"settings append target.env-vars ASAN_OPTIONS={proj.config.asan_options}"]
    return debug_command(
        project_root=project_root,
        guest_argv=lldb_cmd,
        env={"PYTHONPATH": str(GUEST_REPO)},
        vm_mem=proj.config.vm_mem,
    )


_ANALYSIS_ENV = {"PYTHONPATH": str(GUEST_REPO), "PYTHONUNBUFFERED": "1"}


def analyze_crash_command(
    *,
    proj: Project,
    worker: str | None = None,
    crash: str | None = None,
    cores: bool = False,
    no_memory_limit: bool = False,
    shell: bool = False,
    force: bool = False,
) -> list[str]:
    guest_argv = ["uv", "run", str(GUEST_REPO / "image" / "analyze.py"), "--project-root", str(GUEST_PROJECT)]
    if cores:
        guest_argv.append("--cores")
    else:
        if worker is not None:
            guest_argv += ["--worker", worker]
        if crash is not None:
            guest_argv += ["--crash", crash]
    if no_memory_limit:
        guest_argv.append("--no-memory-limit")
    if proj.config.asan:
        guest_argv += ["--asan-options", proj.config.asan_options]
    if shell:
        guest_argv += ["--shell"]
    if force:
        guest_argv.append("--force")
    return debug_command(
        project_root=proj.root,
        guest_argv=guest_argv,
        env=_ANALYSIS_ENV,
        vm_mem=proj.config.vm_mem,
    )


def analyze_hang_command(
    *,
    proj: Project,
    worker: str,
    hang: str,
    timeout: int | None = HANG_ANALYSIS_TIMEOUT,
    shell: bool = False,
) -> list[str]:
    guest_argv = [
        "uv", "run", str(GUEST_REPO / "image" / "analyze_hang.py"),
        "--project-root", str(GUEST_PROJECT),
        "--worker", worker,
        "--hang", hang,
    ]
    if timeout is not None:
        guest_argv += ["--timeout", str(timeout)]
    if shell:
        guest_argv += ["--shell"]
    return debug_command(
        project_root=proj.root,
        guest_argv=guest_argv,
        env=_ANALYSIS_ENV,
        vm_mem=proj.config.vm_mem,
    )

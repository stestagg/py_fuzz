from __future__ import annotations

import json
import mmap
import re
import shutil
import socket
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

import click

from .console import detail, run, step, success, warn
from .docker import BUILD_IMAGE, DEBUG_IMAGE, RUN_IMAGE, ensure_image, ensure_tool
from .project import DEFAULT_REPO, ProjectConfig, default_env_id, load_project, save_project

REPO_ROOT = Path(__file__).resolve().parents[2]
DICT_FILE = REPO_ROOT / "helpers" / "python.dict"
PYFUZZ_PROJECT_FILE = ".pyfuzz_project"


class DurationType(click.ParamType):
    name = "duration"

    def convert(self, value: str, param, ctx):
        match = re.fullmatch(r"(\d+)([hms]?)", value)
        if not match:
            self.fail("Expected duration like 30m, 1h, or 3600", param, ctx)
        amount = int(match.group(1))
        suffix = match.group(2) or "s"
        return amount * {"s": 1, "m": 60, "h": 3600}[suffix]


DURATION = DurationType()


def _project_root_alias_from_mapped_path(mapped_path: str, project_root: str) -> str | None:
    """Return the absolute project-root prefix embedded in a mapped file path.

    Core files record absolute file-backed mapping paths. When the debug
    container sees those files at a different absolute location, GDB emits
    warnings while processing the core. This helper trims a mapped file path
    back to the project root, preserving any prefix that was present when the
    core was created.
    """
    index = mapped_path.find(project_root)
    if index < 0:
        return None
    end = index + len(project_root)
    if end < len(mapped_path) and mapped_path[end] != "/":
        return None
    alias = mapped_path[:end]
    if not alias.startswith("/") or alias == project_root:
        return None
    return alias


def _infer_project_root_aliases_from_core(core_path: Path, project_root: Path, max_aliases: int = 4) -> list[str]:
    """Find alternate absolute project roots referenced inside a core file."""
    project_root_text = str(project_root)
    if not project_root_text.startswith("/"):
        return []
    needle = project_root_text.encode()
    aliases: list[str] = []
    try:
        with core_path.open("rb") as core_file, mmap.mmap(core_file.fileno(), 0, access=mmap.ACCESS_READ) as core_map:
            position = 0
            while len(aliases) < max_aliases:
                position = core_map.find(needle, position)
                if position < 0:
                    break
                start = core_map.rfind(b"\x00", 0, position) + 1
                end = core_map.find(b"\x00", position)
                if end < 0:
                    break
                try:
                    mapped_path = core_map[start:end].decode("utf-8")
                except UnicodeDecodeError:
                    position += 1
                    continue
                alias = _project_root_alias_from_mapped_path(mapped_path, project_root_text)
                if alias is not None and alias not in aliases:
                    aliases.append(alias)
                position += 1
    except (OSError, ValueError):
        return []
    return aliases


def _project_mount_targets_for_core(project_root: Path, core_path: Path) -> list[str]:
    """Return container paths where the project should be mounted for a core."""
    targets = ["/project"]
    project_root_text = str(project_root)
    if project_root_text.startswith("/"):
        targets.append(project_root_text)
        targets.extend(_infer_project_root_aliases_from_core(core_path, project_root))
    deduped: list[str] = []
    for target in targets:
        if target not in deduped:
            deduped.append(target)
    return deduped


def _github_url(repo: str) -> str:
    return f"https://github.com/{repo}.git"


def _find_pfx_project() -> str:
    """Walk up from cwd looking for a .pyfuzz_project file."""
    cwd = Path.cwd()
    for directory in [cwd, *cwd.parents]:
        marker = directory / PYFUZZ_PROJECT_FILE
        if marker.exists():
            name = marker.read_text().strip()
            if name:
                return name
    raise click.ClickException(
        f"No {PYFUZZ_PROJECT_FILE} file found. Run './pyfuzz <project> activate' to create one."
    )


def ensure_project_source(project) -> None:
    if project.source_dir.exists():
        return
    config = project.config
    clone_args = [] if config.commit else ["--depth=1"]
    step(f"Cloning {config.repo} into {project.source_dir}")
    run(["git", "clone", *clone_args, _github_url(config.repo), str(project.source_dir)])


def sync_project_checkout(project) -> None:
    ensure_project_source(project)
    config = project.config
    step(f"Checking out {config.repo} {config.display_target}")
    if config.pr_id is not None:
        ensure_tool("gh")
        run(["gh", "pr", "checkout", str(config.pr_id), "--repo", config.repo], cwd=project.source_dir)
    elif config.branch is not None:
        run(["git", "fetch", "origin", config.branch], cwd=project.source_dir)
        run(["git", "checkout", config.branch], cwd=project.source_dir)
        run(["git", "reset", "--hard", f"origin/{config.branch}"], cwd=project.source_dir)
    elif config.commit is not None:
        run(["git", "fetch", "origin"], cwd=project.source_dir)
        run(["git", "checkout", config.commit], cwd=project.source_dir)
    run(["git", "clean", "-fd"], cwd=project.source_dir)


@click.group(invoke_without_command=True, context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("project", required=False, default=None)
@click.option("--list", "list_projects", is_flag=True, help="List all projects.")
@click.pass_context
def cli(ctx: click.Context, project: str | None, list_projects: bool) -> None:
    """Unified pyfuzz project tooling."""
    ctx.ensure_object(dict)
    if list_projects:
        from datetime import datetime

        from .project import PROJECTS_DIR
        entries = []
        if PROJECTS_DIR.exists():
            for p in PROJECTS_DIR.iterdir():
                cfg_path = p / "project.json"
                if not (p.is_dir() and cfg_path.exists()):
                    continue
                try:
                    proj = load_project(p.name)
                except Exception as e:
                    warn(f"  {p.name}: failed to load ({e})")
                    continue
                entries.append((proj.config.created_at or "", p.name, proj.config))
        entries.sort(key=lambda e: e[0], reverse=True)
        if entries:
            for _, pname, config in entries:
                if config.created_at:
                    dt = datetime.fromisoformat(config.created_at).astimezone().strftime("%Y-%m-%d %H:%M")
                    date_str = click.style(dt, fg="bright_black")
                else:
                    date_str = click.style("unknown", fg="bright_black")
                repo_str = f"  {click.style(config.repo, fg='bright_black')}" if config.repo != DEFAULT_REPO else ""
                asan_str = click.style(" asan", fg="red") if config.asan else ""
                click.echo(f"  {click.style(pname, fg='cyan', bold=True)}  {click.style(config.display_target, fg='yellow')}{asan_str}{repo_str}  {date_str}")
        else:
            click.echo("No projects found.")
        ctx.exit()
        return
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        ctx.exit()
        return
    if project is None:
        raise click.UsageError("Missing argument 'PROJECT'.")
    ctx.obj["project_name"] = project


# ---------------------------------------------------------------------------
# Project management commands
# ---------------------------------------------------------------------------

def _parse_target(pr, branch, commit):
    """Validate mutual exclusion and return (pr_id, branch, commit) with default branch=main."""
    targets = [x for x in [pr, branch, commit] if x is not None]
    if len(targets) > 1:
        raise click.UsageError("Only one of --pr, --branch, or --commit may be set")
    if len(targets) == 0:
        branch = "main"
    return pr, branch, commit


@cli.command()
@click.option("--pr", type=int, default=None)
@click.option("--branch", default=None)
@click.option("--commit", default=None)
@click.option("--repo", default=None, help=f"GitHub repo (default: {DEFAULT_REPO})")
@click.option("--env-id", default=None)
@click.option("--asan", is_flag=True)
@click.option("--warmup-imports", default="", help="Comma-separated modules to import before AFL forkserver (prevents dlopen-after-fork errors for C extensions).")
@click.pass_context
def create(ctx: click.Context, pr: int | None, branch: str | None, commit: str | None, repo: str | None, env_id: str | None, asan: bool, warmup_imports: str) -> None:
    """Create an isolated pyfuzz project."""
    name = ctx.obj["project_name"]
    pr, branch, commit = _parse_target(pr, branch, commit)
    resolved_repo = repo or DEFAULT_REPO
    resolved_env_id = env_id or default_env_id(name, pr, branch, commit)
    proj = save_project(name, ProjectConfig(env_id=resolved_env_id, repo=resolved_repo, pr_id=pr, branch=branch, commit=commit, asan=asan, warmup_imports=warmup_imports))
    success(f"Created project {proj.name}")
    detail("root", str(proj.root))
    detail("repo", proj.config.repo)
    detail("target", proj.config.display_target)
    detail("env_id", proj.config.env_id)


@cli.command()
@click.pass_context
def info(ctx: click.Context) -> None:
    """Show config and paths for a project."""
    name = ctx.obj["project_name"]
    proj = load_project(name)
    detail("name", proj.name)
    detail("repo", proj.config.repo)
    detail("target", proj.config.display_target)
    detail("env_id", proj.config.env_id)
    detail("asan", str(proj.config.asan))
    detail("root", str(proj.root))
    detail("inputs_dir", str(proj.inputs_dir))
    detail("outputs_dir", str(proj.outputs_dir))


@cli.command(name="set")
@click.option("--pr", type=int, default=None)
@click.option("--branch", default=None)
@click.option("--commit", default=None)
@click.option("--repo", default=None, help=f"GitHub repo (default: {DEFAULT_REPO})")
@click.option("--env-id", default=None)
@click.option("--asan/--no-asan", default=None)
@click.option("--warmup-imports", default=None, help="Comma-separated modules to import before AFL forkserver.")
@click.pass_context
def project_set(ctx: click.Context, pr: int | None, branch: str | None, commit: str | None, repo: str | None, env_id: str | None, asan: bool | None, warmup_imports: str | None) -> None:
    """Update config fields for a project."""
    name = ctx.obj["project_name"]
    proj = load_project(name)
    config = proj.config
    targets = [x for x in [pr, branch, commit] if x is not None]
    if len(targets) > 1:
        raise click.UsageError("Only one of --pr, --branch, or --commit may be set")
    if len(targets) == 1:
        config.pr_id = pr
        config.branch = branch
        config.commit = commit
    if repo is not None:
        config.repo = repo
    if env_id is not None:
        config.env_id = env_id
    if asan is not None:
        config.asan = asan
    if warmup_imports is not None:
        config.warmup_imports = warmup_imports
    save_project(name, config)
    success(f"Updated project {name}")
    detail("repo", config.repo)
    detail("target", config.display_target)
    detail("env_id", config.env_id)
    detail("asan", str(config.asan))


@cli.command()
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def delete(ctx: click.Context, yes: bool) -> None:
    """Delete a project and all its data."""
    name = ctx.obj["project_name"]
    proj = load_project(name)
    if not yes:
        click.confirm(f"Delete project '{name}' and all data at {proj.root}?", abort=True)
    shutil.rmtree(proj.root)
    success(f"Deleted project {name}")


@cli.command()
@click.pass_context
def activate(ctx: click.Context) -> None:
    """Write a .pyfuzz_project file in the current directory."""
    name = ctx.obj["project_name"]
    load_project(name)  # verify it exists
    marker = Path.cwd() / PYFUZZ_PROJECT_FILE
    marker.write_text(name + "\n")
    success(f"Activated project {name} ({marker})")


# ---------------------------------------------------------------------------
# tests subgroup
# ---------------------------------------------------------------------------

TESTCASES_ROOT = REPO_ROOT / "testcases"


@cli.group(name="tests")
@click.pass_context
def tests_group(ctx: click.Context) -> None:
    """Manage testcase inputs for a project."""
    ctx.obj["project"] = load_project(ctx.obj["project_name"])


def _resolve_names(name: str | None, all_: bool, source_root: Path) -> list[str]:
    if all_:
        return [p.name for p in sorted(source_root.iterdir()) if p.is_dir()]
    if name is None:
        raise click.UsageError("Provide <name> or --all")
    return [name]


@tests_group.command(name="add")
@click.argument("name", required=False)
@click.option("--all", "all_", is_flag=True)
@click.pass_context
def tests_add(ctx: click.Context, name: str | None, all_: bool) -> None:
    """Copy testcases/<name>/ into the project inputs dir."""
    proj = ctx.obj["project"]
    names = _resolve_names(name, all_, TESTCASES_ROOT)
    for n in names:
        src = TESTCASES_ROOT / n
        dest = proj.inputs_dir / n
        if not src.exists():
            raise click.ClickException(f"Source does not exist: {src}")
        if dest.exists():
            raise click.ClickException(f"Destination already exists: {dest}. Use sync to overwrite.")
        shutil.copytree(src, dest)
        success(f"Added {n}")


@tests_group.command(name="remove")
@click.argument("name", required=False)
@click.option("--all", "all_", is_flag=True)
@click.pass_context
def tests_remove(ctx: click.Context, name: str | None, all_: bool) -> None:
    """Remove testcase inputs from the project inputs dir."""
    proj = ctx.obj["project"]
    if all_:
        names = [p.name for p in sorted(proj.inputs_dir.iterdir()) if p.is_dir()]
    else:
        if name is None:
            raise click.UsageError("Provide <name> or --all")
        names = [name]
    for n in names:
        dest = proj.inputs_dir / n
        if not dest.exists():
            raise click.ClickException(f"Not present: {dest}")
        shutil.rmtree(dest)
        success(f"Removed {n}")


@tests_group.command(name="sync")
@click.argument("name", required=False)
@click.option("--all", "all_", is_flag=True)
@click.pass_context
def tests_sync(ctx: click.Context, name: str | None, all_: bool) -> None:
    """Re-sync testcases/<name>/ into the project inputs dir (clean copy)."""
    proj = ctx.obj["project"]
    names = _resolve_names(name, all_, TESTCASES_ROOT)
    for n in names:
        src = TESTCASES_ROOT / n
        dest = proj.inputs_dir / n
        if not src.exists():
            raise click.ClickException(f"Source does not exist: {src}")
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        success(f"Synced {n}")


# ---------------------------------------------------------------------------
# Operational commands
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--build", "clean_build", is_flag=True, help="Clean build artifacts (dist/).")
@click.option("--outputs", "clean_outputs", is_flag=True, help="Clean fuzzer outputs (outputs/).")
@click.option("--cores", "clean_cores", is_flag=True, help="Clean core dumps (cores/).")
@click.option("--analysis", "clean_analysis", is_flag=True, help="Clean crash analyses (analysis/).")
@click.option("--logs", "clean_logs", is_flag=True, help="Clean logs (logs/).")
@click.option("--source", "clean_source", is_flag=True, help="Clean CPython source checkout (cpython/).")
@click.option("--all", "clean_all", is_flag=True, help="Clean everything above.")
@click.pass_context
def clean(ctx: click.Context, clean_build: bool, clean_outputs: bool, clean_cores: bool, clean_analysis: bool, clean_logs: bool, clean_source: bool, clean_all: bool) -> None:
    """Delete and recreate selected project directories."""
    proj = load_project(ctx.obj["project_name"])
    if not any([clean_build, clean_outputs, clean_cores, clean_analysis, clean_logs, clean_source, clean_all]):
        raise click.UsageError("Specify at least one target: --build, --outputs, --cores, --analysis, --logs, --source, or --all")
    targets: list[tuple[str, Path]] = [
        ("build",    proj.dist_dir),
        ("outputs",  proj.outputs_dir),
        ("cores",    proj.cores_dir),
        ("analysis", proj.analysis_dir),
        ("logs",     proj.logs_dir),
        ("source",   proj.source_dir),
    ]
    flags = [clean_build, clean_outputs, clean_cores, clean_analysis, clean_logs, clean_source]
    for (label, path), selected in zip(targets, flags):
        if clean_all or selected:
            if path.exists():
                shutil.rmtree(path)
            if label != "source":
                path.mkdir(parents=True)
            success(f"Cleaned {label} ({path.relative_to(proj.root)})")


@cli.command()
@click.option("--force", is_flag=True)
@click.option("--build-image", is_flag=True)
@click.option("-j", "--jobs", type=click.IntRange(min=1), default=None, help="Parallelism for make (default: cpu count).")
@click.pass_context
def build(ctx: click.Context, force: bool, build_image: bool, jobs: int | None) -> None:
    """Build a project's CPython and harness inside Docker."""
    proj = load_project(ctx.obj["project_name"])
    sync_project_checkout(proj)
    ensure_image(BUILD_IMAGE, "Dockerfile.build", force=build_image)
    proj.ensure_layout()
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{REPO_ROOT}:/repo",
        "-v", f"{proj.root}:/project",
        "-w", "/repo",
        "-e", "PYTHONPATH=/repo",
        "-e", f"PROJECT_NAME={proj.name}",
        "-e", f"ASAN={1 if proj.config.asan else 0}",
        BUILD_IMAGE,
        "uv", "run", "image/build.py",
    ]
    if force:
        cmd.append("--force")
    if jobs is not None:
        cmd += ["--jobs", str(jobs)]
    step(f"Building project {proj.name}")
    run(cmd)


@cli.command(name="run")
@click.option("-j", "--jobs", type=click.IntRange(min=1), default=1, show_default=True)
@click.option("-T", "--timeout", type=DURATION)
@click.option("--build-image", is_flag=True)
@click.option("--shell", is_flag=True)
@click.option("--test-crash", is_flag=True)
@click.option("--trace-dlopen", is_flag=True)
@click.option("--debug", is_flag=True, help="Set AFL_DEBUG=1.")
@click.pass_context
def run_cmd(ctx: click.Context, jobs: int, timeout: int | None, build_image: bool, shell: bool, test_crash: bool, trace_dlopen: bool, debug: bool) -> None:
    """Run AFL++ for a project inside Docker."""
    proj = load_project(ctx.obj["project_name"])
    if not proj.harness_path.exists():
        raise click.ClickException(f"Project {proj.name} is not built yet. Run ./pyfuzz {proj.name} build")
    ensure_image(RUN_IMAGE, "Dockerfile.run", force=build_image)
    if not any(proj.inputs_dir.iterdir()):
        warn(f"Warning: inputs dir is empty ({proj.inputs_dir}). Add testcases with: ./pyfuzz {proj.name} tests add <name>")
    proj.outputs_dir.mkdir(parents=True, exist_ok=True)
    cpu_args = ["--cpus", str(jobs)] if jobs > 1 else []
    extra_env: list[str] = []
    if test_crash:
        extra_env += ["-e", "FUZZ_TEST_CRASH=1"]
    if debug:
        extra_env += ["-e", "AFL_DEBUG=1"]
    if proj.config.asan:
        extra_env += ["-e", f"ASAN_OPTIONS={proj.config.asan_options}", "-e", "AFL_USE_ASAN=1", "-e", "AFL_IGNORE_PROBLEMS=1"]
    docker_cmd = [
        "docker", "run", "--rm", "-it", "--privileged",
        *cpu_args,
        "-v", f"{REPO_ROOT}:/repo:ro",
        "-v", f"{proj.root}:/project:ro",
        "-v", f"{proj.outputs_dir}:/project/outputs",
        "-v", f"{proj.cores_dir}:/project/outputs/cores",
        "-v", f"{proj.logs_dir}:/project/logs",
        "-v", f"{proj.inputs_dir}:/testcases:ro",
        "-v", f"{DICT_FILE}:/dicts/python.dict:ro",
        "-e", "PROJECT_ROOT=/project",
        "-e", "TESTCASES_DIR=/testcases",
        "-e", "DICT_FILE=/dicts/python.dict",
        "-e", "OUTPUT_DIR=/project/outputs",
        "-e", "PYTHONPATH=/repo",
        *extra_env,
        RUN_IMAGE,
    ]
    if shell:
        docker_cmd.append("bash")
    elif trace_dlopen:
        docker_cmd += ["uv", "run", "/repo/image/trace_inputs.py"]
    else:
        docker_cmd += ["uv", "run", "/repo/image/run.py", "--project-root", "/project", "--jobs", str(jobs)]
        if timeout is not None:
            docker_cmd += ["--timeout", str(timeout)]
    step(f"Running project {proj.name}")
    run(docker_cmd)


@cli.command(name="analyze")
@click.option("--worker")
@click.option("--crash")
@click.option("--core", "core_path", type=click.Path(exists=True, path_type=Path), default=None, help="Load a coredump into interactive gdb.")
@click.option("--no-memory-limit", is_flag=True)
@click.option("--build-image", is_flag=True)
@click.pass_context
def analyze_cmd(ctx: click.Context, worker: str | None, crash: str | None, core_path: Path | None, no_memory_limit: bool, build_image: bool) -> None:
    """Analyze project crashes with gdb inside Docker."""
    proj = load_project(ctx.obj["project_name"])
    ensure_image(DEBUG_IMAGE, "Dockerfile.debug", force=build_image)

    if core_path is not None:
        core_path = core_path.resolve()
        project_root = proj.root.resolve()
        project_mounts = [
            mount
            for target in _project_mount_targets_for_core(project_root, core_path)
            for mount in ["-v", f"{project_root}:{target}:ro"]
        ]
        gdb_setup = [
            "-ex", "set pagination off",
            "-ex", "set environment PYTHONHOME /project/dist/install",
            "-ex", "set environment AFL_IGNORE_PROBLEMS 1",
        ]
        if proj.config.asan:
            gdb_setup += ["-ex", f"set environment ASAN_OPTIONS {proj.config.asan_options}"]
        docker_cmd = [
            "docker", "run", "--rm", "-it",
            "--security-opt", "seccomp=unconfined",
            "--cap-add=SYS_PTRACE",
            "-v", f"{REPO_ROOT}:/repo:ro",
            *project_mounts,
            "-v", f"{core_path}:/corefile:ro",
            DEBUG_IMAGE,
            "gdb", "-q", *gdb_setup,
            "/project/dist/fuzz_python", "/corefile",
        ]
        step(f"Loading core {core_path.name} for project {proj.name}")
        run(docker_cmd)
        return

    docker_cmd = [
        "docker", "run", "--rm", "-it",
        "--security-opt", "seccomp=unconfined",
        "--cap-add=SYS_PTRACE",
        "-v", f"{REPO_ROOT}:/repo",
        "-v", f"{proj.root}:/project",
        DEBUG_IMAGE,
        "uv", "run", "/repo/image/analyze.py",
        "--project-root", "/project",
    ]
    if worker:
        docker_cmd += ["--worker", worker]
    if crash:
        docker_cmd += ["--crash", crash]
    if no_memory_limit:
        docker_cmd.append("--no-memory-limit")
    if proj.config.asan:
        docker_cmd += ["--asan-options", proj.config.asan_options]

    step(f"Analyzing project {proj.name}")
    run(docker_cmd)


@cli.command()
@click.pass_context
def tui(ctx: click.Context) -> None:
    """Browse a project's crashes and analyses."""
    proj = load_project(ctx.obj["project_name"])
    step(f"Launching TUI for {proj.name}")
    run(["uv", "run", "python", "-m", "tools.pyfuzz.tui", "--project-root", str(proj.root)], cwd=REPO_ROOT)


_LOG_URL = "https://logs.offd.es/fuzz/logs"
_LOG_SECRET = "XRAEtZ4E8qDNdDr7DOf2Wunj9shgZXSj"
_MONITOR_INTERVAL = 30


def _parse_fuzzer_stats(path: Path) -> dict[str, str]:
    stats: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            stats[k.strip()] = v.strip()
    return stats


def _collect_stats(proj) -> dict:
    outputs_dir = proj.outputs_dir
    cores_dir = proj.cores_dir

    # Aggregate fuzzer_stats across all worker dirs
    totals: dict[str, int] = {}
    float_fields: dict[str, float] = {}
    worker_count = 0
    for worker_dir in outputs_dir.iterdir():
        stats_file = worker_dir / "fuzzer_stats"
        if not stats_file.exists():
            continue
        worker_count += 1
        stats = _parse_fuzzer_stats(stats_file)
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
    core_dumps = sum(1 for f in cores_dir.iterdir()) if cores_dir.exists() else 0

    return {
        "project": proj.name,
        "workers": worker_count,
        "crashes": crashes,
        "core_dumps": core_dumps,
        **{k: v for k, v in totals.items()},
        **{k: round(v, 2) for k, v in float_fields.items()},
    }


def _post_log(log_data: dict, session_id: str) -> None:
    device = socket.gethostname()
    log_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = json.dumps([{
        "log_time": log_time,
        "device": device,
        "session": session_id,
        "log": log_data,
    }]).encode()
    req = urllib.request.Request(
        _LOG_URL,
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": _LOG_SECRET},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
        if status not in range(200, 300):
            click.echo(f"[monitor] warning: server returned {status}", err=True)
    except Exception as e:
        click.echo(f"[monitor] warning: post failed: {e}", err=True)


@cli.command()
@click.option("--interval", type=int, default=_MONITOR_INTERVAL, show_default=True, help="Seconds between log posts.")
@click.option("--once", is_flag=True, help="Post once and exit.")
@click.pass_context
def monitor(ctx: click.Context, interval: int, once: bool) -> None:
    """Periodically post fuzzer progress to the remote log server."""
    proj = load_project(ctx.obj["project_name"])
    session_id = str(uuid.uuid4())
    click.echo(f"[monitor] watching {proj.name} (interval={interval}s, session={session_id[:8]})")
    while True:
        stats = _collect_stats(proj)
        _post_log(stats, session_id)
        click.echo(f"[monitor] posted: crashes={stats['crashes']} cores={stats['core_dumps']} "
                   f"execs={stats.get('execs_done', 0)} hangs={stats.get('saved_hangs', 0)} "
                   f"tmouts={stats.get('total_tmout', 0)}")
        if once:
            break
        time.sleep(interval)


def main() -> None:
    prog_name = Path(sys.argv[0]).name
    if prog_name == "pfx":
        # In pfx mode there is no <project> argument — resolve it from .pyfuzz_project.
        # Skip injection only if the first arg is a flag (e.g. --help, --list).
        args = sys.argv[1:]
        if not args or not args[0].startswith("-"):
            project = _find_pfx_project()
            sys.argv.insert(1, project)
    cli.main()


if __name__ == "__main__":
    raise SystemExit(main())

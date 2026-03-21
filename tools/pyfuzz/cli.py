from __future__ import annotations

import re
import shutil
from pathlib import Path

import click

from .console import detail, run, step, success, warn
from .docker import BUILD_IMAGE, DEBUG_IMAGE, RUN_IMAGE, ensure_image, ensure_tool
from .project import DEFAULT_REPO, ProjectConfig, default_env_id, load_project, save_project

REPO_ROOT = Path(__file__).resolve().parents[2]
DICT_FILE = REPO_ROOT / "helpers" / "python.dict"


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


def _github_url(repo: str) -> str:
    return f"https://github.com/{repo}.git"


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


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def cli() -> None:
    """Unified pyfuzz project tooling."""


# ---------------------------------------------------------------------------
# project <name> group
# ---------------------------------------------------------------------------

@cli.group(invoke_without_command=True)
@click.argument("name", required=False, default=None)
@click.option("--list", "list_projects", is_flag=True, help="List all projects.")
@click.pass_context
def project(ctx: click.Context, name: str | None, list_projects: bool) -> None:
    """Manage a pyfuzz project's config and testcases."""
    ctx.ensure_object(dict)
    if list_projects:
        from .project import PROJECTS_DIR
        from datetime import datetime
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
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        ctx.exit()
    if name is None:
        raise click.UsageError("Missing argument 'NAME'.")
    ctx.obj["project_name"] = name


def _parse_target(pr, branch, commit):
    """Validate mutual exclusion and return (pr_id, branch, commit) with default branch=main."""
    targets = [x for x in [pr, branch, commit] if x is not None]
    if len(targets) > 1:
        raise click.UsageError("Only one of --pr, --branch, or --commit may be set")
    if len(targets) == 0:
        branch = "main"
    return pr, branch, commit


@project.command()
@click.option("--pr", type=int, default=None)
@click.option("--branch", default=None)
@click.option("--commit", default=None)
@click.option("--repo", default=None, help=f"GitHub repo (default: {DEFAULT_REPO})")
@click.option("--env-id", default=None)
@click.option("--asan", is_flag=True)
@click.pass_context
def create(ctx: click.Context, pr: int | None, branch: str | None, commit: str | None, repo: str | None, env_id: str | None, asan: bool) -> None:
    """Create an isolated pyfuzz project."""
    name = ctx.obj["project_name"]
    pr, branch, commit = _parse_target(pr, branch, commit)
    resolved_repo = repo or DEFAULT_REPO
    resolved_env_id = env_id or default_env_id(name, pr, branch, commit)
    proj = save_project(name, ProjectConfig(env_id=resolved_env_id, repo=resolved_repo, pr_id=pr, branch=branch, commit=commit, asan=asan))
    success(f"Created project {proj.name}")
    detail("root", str(proj.root))
    detail("repo", proj.config.repo)
    detail("target", proj.config.display_target)
    detail("env_id", proj.config.env_id)


@project.command()
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


@project.command(name="set")
@click.option("--pr", type=int, default=None)
@click.option("--branch", default=None)
@click.option("--commit", default=None)
@click.option("--repo", default=None, help=f"GitHub repo (default: {DEFAULT_REPO})")
@click.option("--env-id", default=None)
@click.option("--asan/--no-asan", default=None)
@click.pass_context
def project_set(ctx: click.Context, pr: int | None, branch: str | None, commit: str | None, repo: str | None, env_id: str | None, asan: bool | None) -> None:
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
    save_project(name, config)
    success(f"Updated project {name}")
    detail("repo", config.repo)
    detail("target", config.display_target)
    detail("env_id", config.env_id)
    detail("asan", str(config.asan))


@project.command()
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
@click.argument("project_name")
@click.option("--build", "clean_build", is_flag=True, help="Clean build artifacts (dist/).")
@click.option("--outputs", "clean_outputs", is_flag=True, help="Clean fuzzer outputs (outputs/).")
@click.option("--cores", "clean_cores", is_flag=True, help="Clean core dumps (cores/).")
@click.option("--analysis", "clean_analysis", is_flag=True, help="Clean crash analyses (analysis/).")
@click.option("--logs", "clean_logs", is_flag=True, help="Clean logs (logs/).")
@click.option("--source", "clean_source", is_flag=True, help="Clean CPython source checkout (cpython/).")
@click.option("--all", "clean_all", is_flag=True, help="Clean everything above.")
def clean(project_name: str, clean_build: bool, clean_outputs: bool, clean_cores: bool, clean_analysis: bool, clean_logs: bool, clean_source: bool, clean_all: bool) -> None:
    """Delete and recreate selected project directories."""
    proj = load_project(project_name)
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


# ---------------------------------------------------------------------------
# project <name> tests group
# ---------------------------------------------------------------------------

TESTCASES_ROOT = REPO_ROOT / "testcases"


@project.group(name="tests")
@click.pass_context
def tests_group(ctx: click.Context) -> None:
    """Manage testcase inputs for a project."""
    name = ctx.obj["project_name"]
    ctx.obj["project"] = load_project(name)


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
# Operational commands (unchanged)
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("project_name")
@click.option("--force", is_flag=True)
@click.option("--build-image", is_flag=True)
@click.option("-j", "--jobs", type=click.IntRange(min=1), default=None, help="Parallelism for make (default: cpu count).")
def build(project_name: str, force: bool, build_image: bool, jobs: int | None) -> None:
    """Build a project's CPython and harness inside Docker."""
    proj = load_project(project_name)
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


@cli.command()
@click.argument("project_name")
@click.option("-j", "--jobs", type=click.IntRange(min=1), default=1, show_default=True)
@click.option("-T", "--timeout", type=DURATION)
@click.option("--build-image", is_flag=True)
@click.option("--shell", is_flag=True)
@click.option("--test-crash", is_flag=True)
@click.option("--trace-dlopen", is_flag=True)
@click.option("--debug", is_flag=True, help="Set AFL_DEBUG=1.")
def run_cmd(project_name: str, jobs: int, timeout: int | None, build_image: bool, shell: bool, test_crash: bool, trace_dlopen: bool, debug: bool) -> None:
    """Run AFL++ for a project inside Docker."""
    proj = load_project(project_name)
    if not proj.harness_path.exists():
        raise click.ClickException(f"Project {proj.name} is not built yet. Run ./pyfuzz build {proj.name}")
    ensure_image(RUN_IMAGE, "Dockerfile.run", force=build_image)
    if not any(proj.inputs_dir.iterdir()):
        warn(f"Warning: inputs dir is empty ({proj.inputs_dir}). Add testcases with: ./pyfuzz project {proj.name} tests add <name>")
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
@click.argument("project_name")
@click.option("--worker")
@click.option("--crash")
@click.option("--no-memory-limit", is_flag=True)
@click.option("--build-image", is_flag=True)
def analyze_cmd(project_name: str, worker: str | None, crash: str | None, no_memory_limit: bool, build_image: bool) -> None:
    """Analyze project crashes with gdb inside Docker."""
    proj = load_project(project_name)
    ensure_image(DEBUG_IMAGE, "Dockerfile.debug", force=build_image)
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
@click.argument("project_name")
def tui(project_name: str) -> None:
    """Browse a project's crashes and analyses."""
    proj = load_project(project_name)
    step(f"Launching TUI for {proj.name}")
    run(["uv", "run", "python", "-m", "tools.pyfuzz.tui", "--project-root", str(proj.root)], cwd=REPO_ROOT)


def main() -> None:
    cli.main()


if __name__ == "__main__":
    raise SystemExit(main())

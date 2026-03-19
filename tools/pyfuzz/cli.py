from __future__ import annotations

import re
from pathlib import Path

import click

from .console import detail, run, step, success
from .docker import BUILD_IMAGE, DEBUG_IMAGE, RUN_IMAGE, ensure_image, ensure_tool
from .project import ProjectConfig, default_env_id, load_project, save_project

REPO_ROOT = Path(__file__).resolve().parents[2]
DICT_FILE = REPO_ROOT / "dicts" / "python.dict"
PYTHON_REPO = "https://github.com/python/cpython.git"


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


def ensure_project_source(project) -> None:
    if project.source_dir.exists():
        return
    step(f"Cloning CPython into {project.source_dir}")
    run(["git", "clone", "--depth=1", PYTHON_REPO, str(project.source_dir)])


def sync_project_checkout(project) -> None:
    ensure_project_source(project)
    if project.config.pr_id is not None:
        ensure_tool("gh")
        step(f"Checking out CPython {project.config.display_target} into {project.source_dir}")
        run(["gh", "pr", "checkout", str(project.config.pr_id), "--repo", "python/cpython"], cwd=project.source_dir)
    else:
        step("Resetting CPython checkout to origin/main")
        run(["git", "fetch", "origin", "main"], cwd=project.source_dir)
        run(["git", "checkout", "main"], cwd=project.source_dir)
        run(["git", "reset", "--hard", "origin/main"], cwd=project.source_dir)
    run(["git", "clean", "-fd"], cwd=project.source_dir)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def cli() -> None:
    """Unified pyfuzz project tooling."""


@cli.command()
@click.argument("name")
@click.option("--pr", type=int)
@click.option("--env-id")
@click.option("--asan", is_flag=True)
@click.option("--testcases", default="testcases", show_default=True, help="Repository-relative testcase directory or group")
def create(name: str, pr: int | None, env_id: str | None, asan: bool, testcases: str) -> None:
    """Create an isolated pyfuzz project."""
    resolved_env_id = env_id or default_env_id(name, pr)
    project = save_project(ProjectConfig(name=name, env_id=resolved_env_id, pr_id=pr, asan=asan, testcase_dir=testcases))
    success(f"Created project {project.config.name}")
    detail("root", str(project.root))
    detail("env_id", project.config.env_id)
    detail("target", project.config.display_target)
    detail("testcases", project.config.testcase_dir)


@cli.command()
@click.argument("project_name")
@click.option("--force", is_flag=True)
@click.option("--build-image", is_flag=True)
def build(project_name: str, force: bool, build_image: bool) -> None:
    """Build a project's CPython and harness inside Docker."""
    project = load_project(project_name)
    sync_project_checkout(project)
    ensure_image(BUILD_IMAGE, "Dockerfile.build", force=build_image)
    project.ensure_layout()
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{REPO_ROOT}:/repo",
        "-v", f"{project.root}:/project",
        "-w", "/repo",
        "-e", f"PROJECT_NAME={project.config.name}",
        "-e", f"ASAN={1 if project.config.asan else 0}",
        BUILD_IMAGE,
        "uv", "run", "python", "image/build.py",
    ]
    if force:
        cmd.append("--force")
    step(f"Building project {project.config.name}")
    run(cmd)


@cli.command()
@click.argument("project_name")
@click.option("-j", "--jobs", type=click.IntRange(min=1), default=1, show_default=True)
@click.option("-T", "--timeout", type=DURATION)
@click.option("--build-image", is_flag=True)
@click.option("--shell", is_flag=True)
@click.option("--test-crash", is_flag=True)
@click.option("--trace-dlopen", is_flag=True)
def run_cmd(project_name: str, jobs: int, timeout: int | None, build_image: bool, shell: bool, test_crash: bool, trace_dlopen: bool) -> None:
    """Run AFL++ for a project inside Docker."""
    project = load_project(project_name)
    if not project.harness_path.exists():
        raise click.ClickException(f"Project {project.config.name} is not built yet. Run ./pyfuzz build {project.config.name}")
    ensure_image(RUN_IMAGE, "Dockerfile.run", force=build_image)
    testcase_path = project.testcase_path()
    project.outputs_dir.mkdir(parents=True, exist_ok=True)
    cpu_args = ["--cpus", str(jobs)] if jobs > 1 else []
    extra_env: list[str] = []
    if test_crash:
        extra_env += ["-e", "FUZZ_TEST_CRASH=1"]
    if project.config.asan:
        extra_env += ["-e", "ASAN_OPTIONS=abort_on_error=1:detect_leaks=0:symbolize=0", "-e", "AFL_USE_ASAN=1"]
    docker_cmd = [
        "docker", "run", "--rm", "-it", "--privileged",
        *cpu_args,
        "-v", f"{REPO_ROOT}:/repo",
        "-v", f"{project.root}:/project",
        "-v", f"{testcase_path}:/testcases:ro",
        "-v", f"{DICT_FILE}:/dicts/python.dict:ro",
        "-e", "PROJECT_ROOT=/project",
        "-e", "TESTCASES_DIR=/testcases",
        "-e", "DICT_FILE=/dicts/python.dict",
        *extra_env,
        RUN_IMAGE,
    ]
    if shell:
        docker_cmd.append("bash")
    elif trace_dlopen:
        docker_cmd += ["uv", "run", "python", "/repo/image/trace_inputs.py"]
    else:
        docker_cmd += ["uv", "run", "python", "/repo/image/run.py", "--project-root", "/project", "--jobs", str(jobs)]
        if timeout is not None:
            docker_cmd += ["--timeout", str(timeout)]
    step(f"Running project {project.config.name}")
    run(docker_cmd)


@cli.command(name="analyze")
@click.argument("project_name")
@click.option("--worker")
@click.option("--crash")
@click.option("--no-memory-limit", is_flag=True)
@click.option("--build-image", is_flag=True)
def analyze_cmd(project_name: str, worker: str | None, crash: str | None, no_memory_limit: bool, build_image: bool) -> None:
    """Analyze project crashes with gdb inside Docker."""
    project = load_project(project_name)
    ensure_image(DEBUG_IMAGE, "Dockerfile.debug", force=build_image)
    docker_cmd = [
        "docker", "run", "--rm", "-it",
        "--security-opt", "seccomp=unconfined",
        "--cap-add=SYS_PTRACE",
        "-v", f"{REPO_ROOT}:/repo",
        "-v", f"{project.root}:/project",
        DEBUG_IMAGE,
        "uv", "run", "python", "/repo/tools/pyfuzz/analyze.py",
        "--project-root", "/project",
    ]
    if worker:
        docker_cmd += ["--worker", worker]
    if crash:
        docker_cmd += ["--crash", crash]
    if no_memory_limit:
        docker_cmd.append("--no-memory-limit")
    step(f"Analyzing project {project.config.name}")
    run(docker_cmd)


@cli.command()
@click.argument("project_name")
def tui(project_name: str) -> None:
    """Browse a project's crashes and analyses."""
    project = load_project(project_name)
    step(f"Launching TUI for {project.config.name}")
    run(["uv", "run", "python", "-m", "tools.pyfuzz.tui", "--project-root", str(project.root)], cwd=REPO_ROOT)


def main() -> int:
    cli.main(standalone_mode=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

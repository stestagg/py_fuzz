import asyncio
import json
from typing import Callable

from .project import Project
from .env import Env, Image, terminate_on_cancel
from .paths import root_path
from .build_progress import BuildProgressEstimator

ProgressCallback = Callable[[float, float, str], None]


async def _git(*args, cwd=None):
    proc = await asyncio.create_subprocess_exec("git", *args, cwd=cwd)
    await proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed (exit {proc.returncode})")


async def ensure_cpython_checkout(project: Project):
    cpython_dir = project.path("cpython")
    if (cpython_dir / ".git").exists():
        return
    repo_url = f"https://github.com/{project.repo}.git"
    kind, ref = project.clone_ref
    if kind == "branch":
        await _git("clone", "--depth", "1", "--branch", ref, repo_url, str(cpython_dir))
    else:
        await _git("clone", "--no-checkout", "--filter=blob:none", repo_url, str(cpython_dir))
        await _git("fetch", "--depth", "1", "origin", ref, cwd=cpython_dir)
        await _git("checkout", "FETCH_HEAD", cwd=cpython_dir)


async def _pump(stream, log_file, feed):
    """Read a piped stream line-by-line: tee each line to the log and the estimator."""
    buf = b""
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            log_file.write(line + b"\n")
            feed(line.decode("utf-8", "replace"))
    if buf:
        log_file.write(buf)
        feed(buf.decode("utf-8", "replace"))


def _tail(log_path, n: int = 40) -> str:
    try:
        lines = log_path.read_bytes().decode("utf-8", "replace").splitlines()
        return "\n".join(lines[-n:])
    except Exception as exc:
        return f"(could not read {log_path}: {exc})"


async def _build_run(env, script, log_path, target, on_progress: ProgressCallback | None, default_phase=None):
    estimator = BuildProgressEstimator(target, default_phase)

    def feed(line: str) -> None:
        reading = estimator.feed(line)
        if reading is not None and on_progress is not None:
            on_progress(*reading)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    proc = await env.run([script], vm_mem=8192)
    success = False
    with open(log_path, "wb", buffering=0) as log_file:
        try:
            async with terminate_on_cancel(proc):
                await asyncio.gather(
                    _pump(proc.stdout, log_file, feed),
                    _pump(proc.stderr, log_file, feed),
                    proc.wait(),
                )
            success = proc.returncode == 0
        finally:
            estimator.finish(success)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Build script {script} failed with return code {proc.returncode}\n"
            f"Last lines of {log_path}:\n{_tail(log_path)}"
        )


async def build_python(project: Project, on_progress: ProgressCallback | None = None):
    await ensure_cpython_checkout(project)
    env = Env(project, image=Image.BUILD)

    patches_dir = root_path("tactical-patches")
    patches_json_path = project.path("config", "patches.json")

    existing = {}
    if patches_json_path.exists():
        existing = json.loads(patches_json_path.read_text())

    all_patches = sorted(
        p.name for p in patches_dir.iterdir()
        if p.suffix in (".diff", ".patch")
    )

    skip_patches = [name for name in all_patches if existing.get(name) == "no"]
    env["PY_FUZZ_SKIP_PATCHES"] = ":".join(skip_patches)

    await _build_run(env, "/pfm/build_scripts/build.sh", project.path("logs", "build.log"), "py", on_progress)
    # pfrun always exits 0; verify the expected output actually exists
    if not any(project.path("py").glob("bin/python3*-config")):
        raise RuntimeError("Python build failed: python3-config not found in py/bin/")
    if not project.path("py", ".git-version-info").is_file():
        raise RuntimeError("Python build failed: Git version metadata was not generated")

    updated = {name: existing.get(name, "yes") for name in all_patches}
    patches_json_path.write_text(json.dumps(updated, indent=2, sort_keys=True) + "\n")


async def build_helpers(project: Project, on_progress: ProgressCallback | None = None):
    env = Env(project, image=Image.BUILD)
    await _build_run(env, "/pfm/build_scripts/build_helpers.sh", project.path("logs", "build_helpers.log"), "helpers", on_progress, default_phase="Building helpers")
    # pfrun always exits 0; verify at least one fuzz helper binary was produced
    tool_name = project.harness
    if not (project.path("tools") / tool_name).exists():
        raise RuntimeError(f"Helper build failed: {tool_name} not found in tools/")

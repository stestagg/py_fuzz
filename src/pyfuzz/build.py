import asyncio
import json

from .project import Project
from .env import Env, Image, terminate_on_cancel
from .paths import root_path


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


async def _build_run(env, script):
    proc = await env.run([script], console=True, vm_mem=8192)
    async with terminate_on_cancel(proc):
        await proc.wait()
    if proc.returncode != 0:
        stderr = (await proc.stderr.read()).decode() if proc.stderr else "<no stderr>"
        stdout = (await proc.stdout.read()).decode() if proc.stdout else "<no stdout>"
        raise RuntimeError(f"Build script {script} failed with return code {proc.returncode}\nStderr:\n{stderr}\nStdout:\n{stdout}")


async def build_python(project: Project):
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

    await _build_run(env, "/pfm/build_scripts/build.sh")
    # pfrun always exits 0; verify the expected output actually exists
    if not any(project.path("py").glob("bin/python3*-config")):
        raise RuntimeError("Python build failed: python3-config not found in py/bin/")
    if not project.path("py", ".git-version-info").is_file():
        raise RuntimeError("Python build failed: Git version metadata was not generated")

    updated = {name: existing.get(name, "yes") for name in all_patches}
    patches_json_path.write_text(json.dumps(updated, indent=2, sort_keys=True) + "\n")


async def build_helpers(project: Project):
    env = Env(project, image=Image.BUILD)
    await _build_run(env, "/pfm/build_scripts/build_helpers.sh")
    # pfrun always exits 0; verify at least one fuzz helper binary was produced
    tool_name = project.harness
    if not (project.path("tools") / tool_name).exists():
        raise RuntimeError(f"Helper build failed: {tool_name} not found in tools/")

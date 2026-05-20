import asyncio

from .project import Project
from .env import Env, Image


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


async def build_python(project: Project):
    await ensure_cpython_checkout(project)
    env = Env(project, image=Image.BUILD)
    proc = await env.run(["/pfm/build_scripts/build.sh"], console=True, vm_mem=8192)
    await proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"Python build failed with return code {proc.returncode}")
    # pfrun always exits 0; verify the expected output actually exists
    if not any(project.path("py").glob("bin/python3*-config")):
        raise RuntimeError("Python build failed: python3-config not found in py/bin/")


async def build_helpers(project: Project):
    env = Env(project, image=Image.BUILD)
    proc = await env.run(["/pfm/build_scripts/build_helpers.sh"], console=True, vm_mem=8192)
    await proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"Helper build failed with return code {proc.returncode}")
    # pfrun always exits 0; verify at least one fuzz helper binary was produced
    tool_name = "fuzz_peg" if project.fuzz_peg else "fuzz_python"
    if not (project.path("tools") / tool_name).exists():
        raise RuntimeError(f"Helper build failed: {tool_name} not found in tools/")
import shutil
from pathlib import Path

from .env import Env, Image
from .project import Project


async def run_bisect(project: Project, script_path: Path, *, ccache: bool = False, configure_args: str = "", mem_limit: int | None = None, log: bool = False) -> None:
    script_name = script_path.stem
    bisect_script_dir = project.path("scratch", "bisect")
    bisect_script_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(script_path, bisect_script_dir / f"{script_name}.py")

    env = Env(project, Image.BISECT)
    env['SCRIPT_NAME'] = script_name
    if ccache:
        env['USE_CCACHE'] = '1'
    all_configure_args = []
    if project.py_debug:
        all_configure_args.append("--with-pydebug")
    if project.py_configure_extra_args:
        all_configure_args.append(project.py_configure_extra_args)
    if configure_args:
        all_configure_args.append(configure_args)
    if all_configure_args:
        env['CONFIGURE_ARGS'] = " ".join(all_configure_args)
    if mem_limit is not None:
        env['MEM_LIMIT'] = str(mem_limit)
    if log:
        env['BISECT_LOG'] = '1'
    # Don't cap --cpus or --memory: docker errors if asked for more cpus than
    # the host has, and parallel make (-j with no number) can use a lot of
    # memory. Let docker use its own defaults (host cpus, no memory limit).
    proc = await env.run([], console=True, interactive=True, ncpu=0, vm_mem=0)
    await proc.wait()

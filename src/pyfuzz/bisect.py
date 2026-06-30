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
    if configure_args:
        env['CONFIGURE_ARGS'] = configure_args
    if mem_limit is not None:
        env['MEM_LIMIT'] = str(mem_limit)
    if log:
        env['BISECT_LOG'] = '1'
    proc = await env.run([], console=True, interactive=True)
    await proc.wait()

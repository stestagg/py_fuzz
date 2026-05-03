import asyncio
import shlex
import subprocess

from ..env import Env, Runner
from ..paths import root_path

PFRUN_DIR = root_path('pfrun')
PFRUN_BINARY = (PFRUN_DIR / "pfrun").resolve()

async def pf_run(
    env: Env,
    cmd: list[str],
    ncpu: int = None,
    vm_mem: int = None,
    vm_timeout: int = None,
    console: bool = False,
):
    assert env.runner == Runner.PFRUN
    
    image_dir = PFRUN_DIR / env.image.value
    assert image_dir.exists(), f"No pfrun image found for env type: {env.image}"

    ncpu = ncpu or env.project.ncpu
    vm_mem = vm_mem or env.project.vm_mem

    cmd_str = shlex.join(str(part) for part in cmd)

    pf_cmd = [
        str(PFRUN_BINARY),
        f'--imagedir', image_dir,
        f'--ncpu', str(ncpu),
        f'--mem', str(vm_mem),
        f'--cmd', cmd_str,
    ]
    if vm_timeout is not None:
        pf_cmd.extend([f'--timeout', str(vm_timeout)])
    
    for mount_dir, is_writable in env.mounts:
        mount_name = mount_dir.name
        if is_writable:
            pf_cmd.extend([f'--mount-rw', f'{mount_dir}:{mount_name}'])
        else:
            pf_cmd.extend([f'--mount', f'{mount_dir}:{mount_name}'])

    pipe_val = None if console else subprocess.PIPE 

    proc = await asyncio.create_subprocess_exec(
        *pf_cmd,
        stdout=pipe_val,
        stderr=pipe_val,
        stdin=pipe_val
    )
    return proc

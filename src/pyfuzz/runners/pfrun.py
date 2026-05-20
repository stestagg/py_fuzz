import asyncio
import os
import shlex
import subprocess
import odhash

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
    interactive: bool = False,
    dmesg_path: str | None = None,
):
    assert env.runner == Runner.PFRUN
    
    image_dir = PFRUN_DIR / "images" / env.image.value
    assert image_dir.exists(), f"No pfrun image found for env type: {env.image}"

    ncpu = ncpu or env.project.ncpu
    vm_mem = vm_mem or env.project.vm_mem

    export_script = env.shell_export

    cmd_str = shlex.join(str(part) for part in cmd)

    env_key = [cmd_str] + env.shell_sets
    env_hash = odhash.hash(".".join(env_key))
    
    (env.project.path("envs") / f'{env_hash}.env').write_text(export_script + "\n")

    env_file = f"/pfm/envs/{env_hash}.env"

    pf_cmd = [
        str(PFRUN_BINARY),
        f'--imagedir', image_dir,
        f'--ncpu', str(ncpu),
        f'--mem', str(vm_mem),
        f'--cmd', cmd_str,
        f'--env-file', env_file,
    ]
    if vm_timeout is not None:
        pf_cmd.extend([f'--timeout', str(vm_timeout)])
    if dmesg_path is not None:
        pf_cmd.extend(['--dmesg', str(dmesg_path)])

    for mount_dir, is_writable in env.mounts:
        mount_name = mount_dir.name
        if is_writable:
            pf_cmd.extend([f'--mount-rw', f'{mount_dir}:{mount_name}'])
        else:
            pf_cmd.extend([f'--mount', f'{mount_dir}:{mount_name}'])

    if interactive:
        console = True
        pf_cmd.append('--interactive')

    pipe_val = None if console else subprocess.PIPE 

    print(f'Running command in pfrun: {cmd_str}')

    proc = await asyncio.create_subprocess_exec(
        *pf_cmd,
        stdout=pipe_val,
        stderr=pipe_val,
        stdin=pipe_val
    )
    return proc

import asyncio
import os
import shlex
import subprocess
import tempfile

from ..env import Env, Runner
from ..paths import root_path


def docker_tag_name(image: Env.Image):
    return f"pyfuzz-{image.value}"


async def docker_build_image(image: Env.Image):
    image_dir = root_path("docker") / image.value
    assert image_dir.exists(), f"No docker image found for image: {image} at {image_dir}"
    build_cmd = [
        "docker", "build",
        "-t", docker_tag_name(image),
        str(image_dir)
    ]
    proc = await asyncio.create_subprocess_exec(
        *build_cmd,
    )
    await proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"Docker build failed for image {image} with return code {proc.returncode}")


async def docker_run(
    env: Env,
    cmd: list[str],
    ncpu: int = None,
    vm_mem: int = None,
    vm_timeout: int = None,
    console: bool = False,
    interactive: bool = False,
):
    assert env.runner == Runner.DOCKER
    assert vm_timeout is None, "Docker runner does not support vm_timeout"
    image_name = docker_tag_name(env.image)
    
    await docker_build_image(env.image)

    env_file = tempfile.NamedTemporaryFile(mode='w+', delete=False)
    env_file.writelines([f'{s}\n' for s in env.shell_sets])
    env_file.flush()

    ncpu = ncpu or env.project.ncpu
    vm_mem = vm_mem or env.project.vm_mem
    cmd_str = shlex.join(str(part) for part in cmd)
    docker_cmd = [
        "docker", "run", "--rm",
        "--cpus", str(ncpu),
        "--memory", f"{vm_mem}m",
        "--env-file", env_file.name,
    ]
    if interactive:
        docker_cmd.append("-it")
    for mount_dir, is_writable in env.mounts:
        mount_name = f'/pfm/{mount_dir.name}'
        if is_writable:
            docker_cmd.extend(["-v", f'{mount_dir}:{mount_name}'])
        else:
            docker_cmd.extend(["-v", f'{mount_dir}:{mount_name}:ro'])
    
    if cmd:
        docker_cmd += [image_name, "sh", "-c", cmd_str]
    else:
        docker_cmd.append(image_name)
    
    pipe_val = None if console else subprocess.PIPE
    print(f"Running docker command: {' '.join(shlex.quote(part) for part in docker_cmd)}")
    proc = await asyncio.create_subprocess_exec(
        *docker_cmd,
        stdout=pipe_val,
        stderr=pipe_val,
        stdin=pipe_val
    )

    async def _cleanup():
        try:
            await proc.wait()
        finally:
            try:
                os.unlink(env_file.name)
            except FileNotFoundError:
                pass
    asyncio.create_task(_cleanup())
    return proc
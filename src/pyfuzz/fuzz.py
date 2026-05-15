import asyncio
import shlex
from .project import Project
from .env import Env, Image


_CHUNK_SIZE = 1024 * 1024  # 1 MiB


async def _stream_to_file(stream, path):
    with open(path, 'wb', buffering=0) as f:
        while True:
            chunk = await stream.read(_CHUNK_SIZE)
            if not chunk:
                break
            f.write(chunk)


async def run_fuzz(project: Project, instance_num: int, afl_debug: bool = False):
    env = Env(project, image=Image.AFL)

    cmdline = [
        "afl-fuzz",
        "-i", '/pfm/inputs',
        "-o", '/pfm/outputs',
        "-t", str(project.fuzz_timeout_ms),
        "-m", str(project.actual_fuzz_mem_limit),
        "-x", '/pfm/py/combined.dict',
        '-L', '0',
        '-p', 'coe',
    ]

    if project.cmplog:
        cmdline.extend(['-c', f'{project.fuzz_target}.cmplog'])

    logs_root = project.path("logs")
    if instance_num == 0:
        logs_dir = logs_root / "main"
        cmdline.extend(['-M', 'main'])
        worker_id = 'main'
    else:
        worker_id = f'w{instance_num}'
        logs_dir = logs_root / worker_id
        cmdline.extend(['-S', worker_id])

    cmdline.append(project.fuzz_target)

    core_dir = f"/pfm/cores/{worker_id}"
    afl_cmd = shlex.join(str(p) for p in cmdline)
    setup = f"mkdir -p {core_dir} && echo {core_dir}/core.%p > /proc/sys/kernel/core_pattern"
    cmdline = ["sh", "-c", f"{setup} && {afl_cmd}"]

    if project.track_inputs:
        env['FUZZ_TRACK_INPUTS'] = f'/pfm/input_tracks/{worker_id}'
    if afl_debug:
        env['AFL_DEBUG'] = '1'


    logs_dir.mkdir(parents=True, exist_ok=True)
    proc = await env.run(cmdline, vm_mem=project.actual_vm_mem, ncpu=project.ncpu)

    await asyncio.gather(
        _stream_to_file(proc.stdout, logs_dir / "stdout.log"),
        _stream_to_file(proc.stderr, logs_dir / "stderr.log"),
        proc.wait(),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"fuzz failed with return code {proc.returncode}")
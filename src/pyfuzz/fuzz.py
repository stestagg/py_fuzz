import asyncio
import re
import shlex
import sys
from pathlib import Path
from .project import Project
from .env import Env, Image


_CHUNK_SIZE = 1024 * 1024  # 1 MiB
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[mK]')


async def _stream_to_file(stream, path):
    with open(path, 'wb', buffering=0) as f:
        while True:
            chunk = await stream.read(_CHUNK_SIZE)
            if not chunk:
                break
            f.write(chunk)


def _tail_log(path: Path, n: int = 30) -> str:
    try:
        lines = path.read_bytes().decode('utf-8', errors='replace').splitlines()
        tail = lines[-n:] if len(lines) > n else lines
        return '\n'.join(_ANSI_RE.sub('', line) for line in tail)
    except Exception as e:
        return f"(could not read {path}: {e})"


def _read_fuzzer_stats(stats_path: Path) -> str:
    try:
        raw = stats_path.read_text()
        keep = ('run_time', 'execs_done', 'execs_per_sec', 'corpus_count',
                 'stability', 'saved_crashes', 'saved_hangs', 'total_tmout',
                 'pending_favs', 'pending_total')
        lines = [l for l in raw.splitlines() if any(l.startswith(k) for k in keep)]
        return '\n'.join(lines) if lines else '(empty)'
    except Exception as e:
        return f"(could not read {stats_path}: {e})"


def _log_worker_exit(worker_id: str, returncode: int, logs_dir: Path, outputs_dir: Path) -> None:
    tag = f"[{worker_id}]"
    if returncode == 0:
        print(f"{tag} exited cleanly (code 0)")
        return
    print(f"{tag} exited with code {returncode}", file=sys.stderr)
    stats_path = outputs_dir / worker_id / "fuzzer_stats"
    if stats_path.exists():
        print(f"{tag} fuzzer_stats at exit:\n{_read_fuzzer_stats(stats_path)}", file=sys.stderr)
    stdout_log = logs_dir / "stdout.log"
    if stdout_log.exists():
        print(f"{tag} last lines of stdout.log:\n{_tail_log(stdout_log)}", file=sys.stderr)
    stderr_log = logs_dir / "stderr.log"
    if stderr_log.exists() and stderr_log.stat().st_size > 0:
        print(f"{tag} last lines of stderr.log:\n{_tail_log(stderr_log)}", file=sys.stderr)


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
    _log_worker_exit(worker_id, proc.returncode, logs_dir, project.path("outputs"))
    if proc.returncode != 0:
        raise RuntimeError(f"[{worker_id}] fuzz process exited with code {proc.returncode}")
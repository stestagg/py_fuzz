import struct
from pathlib import Path
from .project import Project

_MODULE_RESET_INTERVAL = 16
_GC_INTERVAL = 64


def _pid_and_worker_from_core_link(artifact_dir: Path) -> tuple[int | None, str | None]:
    core_link = artifact_dir / "core"
    if not core_link.is_symlink():
        print(f"Artifact {artifact_dir} has no core symlink, skipping")
        return None, None
    target = core_link.readlink()
    parts = target.name.split(".")
    if len(parts) >= 2 and parts[0] == "core":
        try:
            return int(parts[1]), target.parent.name
        except ValueError:
            pass
    raise ValueError(f"Unexpected core link target format: {target} in artifact {artifact_dir}")


_LOG_MAGIC = 0xF00D
_LOG_HEADER = 16   # magic(2) + pid(4) + ts_us(8) + input_len(2)
_IDX_RECORD = 20   # pid(4) + ts_us(8) + log_offset(8)


def _idx_start_offset(log_path: Path, pid: int) -> int:
    """Return the log offset for the first record of `pid` from the .idx file, or 0."""
    idx_path = log_path.with_suffix('.idx')
    if not idx_path.exists():
        return 0
    data = idx_path.read_bytes()
    n = len(data) // _IDX_RECORD
    for i in range(n):
        base = i * _IDX_RECORD
        (entry_pid,) = struct.unpack_from('<I', data, base)
        if entry_pid == pid:
            (log_off,) = struct.unpack_from('<Q', data, base + 12)
            return log_off
    return 0


def parse_inputs_file(path: Path, pid: int | None = None) -> list[bytes]:
    """Parse a .log file, returning raw bytes for each recorded input.

    Record layout (written by fuzz_python.c):
        u16 LE  – magic 0xF00D
        u32 LE  – pid
        u64 LE  – timestamp_us
        u16 LE  – input_len
        <input_len bytes>

    If `pid` is given, the .idx file is consulted for a seek hint and only
    records matching that pid are returned.  Reads to EOF for correctness.
    """
    start = _idx_start_offset(path, pid) if pid is not None else 0

    with path.open('rb') as f:
        if start:
            f.seek(start)
        data = f.read()

    inputs = []
    offset = 0
    while offset + _LOG_HEADER <= len(data):
        (magic,) = struct.unpack_from('<H', data, offset)
        if magic != _LOG_MAGIC:
            break
        (rec_pid,) = struct.unpack_from('<I', data, offset + 2)
        (input_len,) = struct.unpack_from('<H', data, offset + 14)
        end = offset + _LOG_HEADER + input_len
        if end > len(data):
            break
        if pid is None or rec_pid == pid:
            inputs.append(data[offset + _LOG_HEADER:end])
        offset = end
    return inputs


def get_pid_track_summary(path: Path, pid: int) -> tuple[int, bytes | None]:
    """Return (count, last_input) for a pid, streaming the log without buffering all inputs."""
    start = _idx_start_offset(path, pid)
    count = 0
    last_input: bytes | None = None
    with path.open('rb') as f:
        if start:
            f.seek(start)
        while True:
            header = f.read(_LOG_HEADER)
            if len(header) < _LOG_HEADER:
                break
            (magic,) = struct.unpack_from('<H', header, 0)
            if magic != _LOG_MAGIC:
                break
            (rec_pid,) = struct.unpack_from('<I', header, 2)
            (input_len,) = struct.unpack_from('<H', header, 14)
            payload = f.read(input_len)
            if len(payload) < input_len:
                break
            if rec_pid == pid:
                count += 1
                last_input = payload
    return count, last_input


def _pid_and_worker_for_artifact(artifact) -> tuple[int | None, str | None]:
    """Resolve (pid, worker) for an artifact.

    Cores derive these from the `core` symlink target; crashes read them from
    the `pid`/`worker` meta values (pid is enriched from worker logs).
    """
    from .analysis import ArtifactType

    if artifact.type == ArtifactType.CORE:
        return _pid_and_worker_from_core_link(artifact.dir)
    pid = artifact.meta.get("pid")
    worker_id = artifact.meta.get("worker")
    return pid, worker_id


def generate_all_track_scripts(project: Project, base: str) -> list[tuple[Path, bool]]:
    """Generate track scripts for all core and crash artifacts with matching input tracks.

    Returns list of (output_path, was_written) — was_written is False when
    the file already existed and was skipped.
    """
    from .analysis import ArtifactType, list_artifacts
    import asyncio

    artifacts = asyncio.run(list_artifacts(project))
    artifacts = [a for a in artifacts if a.type in (ArtifactType.CORE, ArtifactType.CRASH)]
    artifacts.sort(key=lambda a: a.meta.get("timestamp", 0))

    reproducers_dir = project.path("scratch", "reproducers")
    reproducers_dir.mkdir(parents=True, exist_ok=True)

    results = []
    num = 0
    for artifact in artifacts:
        print(f"Processing {artifact.type.value} artifact {artifact}")
        pid, worker_id = _pid_and_worker_for_artifact(artifact)
        if pid is None or worker_id is None:
            print(f"Warning: Could not determine pid/worker for artifact {artifact}, skipping")
            continue
        inputs_path = project.path("input_tracks", f"{worker_id}.log")
        if not inputs_path.exists():
            print(f"Warning: Input track not found for artifact {artifact}: {inputs_path}")
            continue
        num += 1
        out_path = reproducers_dir / f"{base}-{num}.py"
        if out_path.exists():
            results.append((out_path, False))
            continue
        script = build_track_script(inputs_path, worker_id=worker_id, pid=pid)
        out_path.write_text(script)
        results.append((out_path, True))

    return results


def build_track_script(inputs_path: Path, worker_id: str = "", pid: int | None = None) -> str:
    """Build a self-contained Python reproducer script from a .log file."""
    if not inputs_path.exists():
        raise FileNotFoundError(f"Track file not found: {inputs_path}")

    inputs = parse_inputs_file(inputs_path, pid=pid)
    if not inputs:
        raise FileNotFoundError(f"No inputs found in {inputs_path}")

    label = f"{worker_id}/{inputs_path.name}" if worker_id else str(inputs_path)

    lines = [
        "import sys, gc, unicodedata",
        "",
        f"# track-script: {label}  ({len(inputs)} inputs)",
        "",
        "_sysmodules = sys.modules",
        "_base_modules = set(_sysmodules)",
        "",
    ]

    for i, raw in enumerate(inputs):
        n = i + 1
        name = str(n)
        if b"\x00" in raw:
            continue
        data = raw
        lines.append(f"# FUZZ_MARKER: input_{name}")
        lines.append("try:")
        lines.append(f"    exec(compile({data!r}, '<input-{name}>', 'exec'))")
        lines.append("except:")
        lines.append("    pass")

        if n % _MODULE_RESET_INTERVAL == 0:
            lines.append(f"# FUZZ_MARKER: reset_{name}")
            lines.append("for _k in list(_sysmodules):")
            lines.append("    if _k not in _base_modules:")
            lines.append("        del _sysmodules[_k]")

        if n % _GC_INTERVAL == 0:
            lines.append(f"# FUZZ_MARKER: gc_{name}")
            lines.append("gc.collect()")
            lines.append("gc.collect()")
            lines.append("gc.collect()")

        lines.append("")

    return "\n".join(lines)

import re
from pathlib import Path
from .project import Project

_MODULE_RESET_INTERVAL = 16
_GC_INTERVAL = 64


def _extract_pid_from_lldb(lldb_text: str) -> int | None:
    m = re.search(r'^Process (\d+) stopped', lldb_text, re.MULTILINE)
    return int(m.group(1)) if m else None


def _find_input_track(project: Project, pid: int) -> tuple[str, str] | None:
    tracks_root = project.path("input_tracks")
    if not tracks_root.exists():
        return None
    prefix = f"{pid}."
    for worker_dir in sorted(tracks_root.iterdir()):
        if not worker_dir.is_dir():
            continue
        for entry in sorted(worker_dir.iterdir()):
            if entry.name.startswith(prefix):
                return worker_dir.name, entry.name
    return None


def generate_all_track_scripts(project: Project, base: str) -> list[tuple[Path, bool]]:
    """Generate track scripts for all core artifacts with matching input tracks.

    Returns list of (output_path, was_written) — was_written is False when file already existed.
    Cores are sorted by timestamp and numbered 1..N; existing files are skipped.
    """
    from .analysis import ArtifactType, list_artifacts
    import asyncio

    artifacts = asyncio.run(list_artifacts(project))
    cores = [a for a in artifacts if a.type == ArtifactType.CORE and a.lldb_output is not None]
    cores.sort(key=lambda a: a.meta.get("timestamp", 0))

    config_dir = project.path("config")
    config_dir.mkdir(exist_ok=True)

    results = []
    num = 0
    for core in cores:
        pid = _extract_pid_from_lldb(core.lldb_output)
        if pid is None:
            continue
        track = _find_input_track(project, pid)
        if track is None:
            continue
        num += 1
        out_path = config_dir / f"{base}-{num}.py"
        if out_path.exists():
            results.append((out_path, False))
            continue
        worker_id, pid_timestamp = track
        script = build_track_script(project, worker_id, pid_timestamp)
        out_path.write_text(script)
        results.append((out_path, True))

    return results


def build_track_script(project: Project, worker_id: str, pid_timestamp: str) -> str:
    track_dir = project.path("input_tracks") / worker_id / pid_timestamp
    if not track_dir.exists():
        raise FileNotFoundError(f"Track directory not found: {track_dir}")

    files = sorted(
        (f for f in track_dir.iterdir() if f.is_file() and f.name.isdigit()),
        key=lambda f: int(f.name),
    )
    if not files:
        raise FileNotFoundError(f"No numbered input files found in {track_dir}")

    lines = [
        "import sys, gc, unicodedata",
        "",
        f"# track-script: {worker_id} / {pid_timestamp}  ({len(files)} inputs)",
        "",
        "_base_modules = set(sys.modules)",
        "",
    ]

    for i, f in enumerate(files):
        n = i + 1
        raw = f.read_bytes()
        null_pos = raw.find(b"\x00")
        data = raw[:null_pos] if null_pos != -1 else raw
        lines.append(f"# FUZZ_MARKER: input_{f.name}")
        lines.append("try:")
        lines.append(f"    exec(compile({data!r}, '<input-{f.name}>', 'exec'))")
        lines.append("except Exception as _e:")
        lines.append(f"    print(f'input {f.name} failed: {{type(_e).__name__}}: {{_e}}', file=sys.stderr)")
        lines.append('else:')
        lines.append(f"    print(f'input {f.name} completed')")

        if n % _MODULE_RESET_INTERVAL == 0:
            lines.append(f"# FUZZ_MARKER: reset_{f.name}")
            lines.append("for _k in list(sys.modules):")
            lines.append("    if _k not in _base_modules:")
            lines.append("        del sys.modules[_k]")

        if n % _GC_INTERVAL == 0:
            lines.append(f"# FUZZ_MARKER: gc_{f.name}")
            lines.append("gc.collect()")
            lines.append("gc.collect()")
            lines.append("gc.collect()")

        lines.append("")

    return "\n".join(lines)

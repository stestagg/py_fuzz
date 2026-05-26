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


def parse_inputs_file(path: Path) -> list[bytes]:
    """Parse a .inputs file, returning raw bytes for each recorded input.

    Record layout (written by fuzz_python.c):
        u32 (LE)  – input length
        '\\n'
        <length bytes>
        '\\n'
        6 × 0x00
        '\\n'
    """
    data = path.read_bytes()
    inputs = []
    offset = 0
    while offset + 5 <= len(data):
        (size,) = struct.unpack_from('<I', data, offset)
        offset += 4
        if data[offset] != ord('\n'):
            break
        offset += 1
        end = offset + size
        if end > len(data):
            break
        raw = data[offset:end]
        offset = end
        # expect '\n' + 6 nulls + '\n'
        if offset + 8 > len(data):
            break
        if data[offset] != ord('\n'):
            break
        offset += 1
        offset += 6
        if data[offset] != ord('\n'):
            break
        offset += 1
        inputs.append(raw)
    return inputs


def generate_all_track_scripts(project: Project, base: str) -> list[tuple[Path, bool]]:
    """Generate track scripts for all core artifacts with matching input tracks.

    Returns list of (output_path, was_written) — was_written is False when
    the file already existed and was skipped.
    """
    from .analysis import ArtifactType, list_artifacts
    import asyncio

    artifacts = asyncio.run(list_artifacts(project))
    cores = [a for a in artifacts if a.type == ArtifactType.CORE]
    cores.sort(key=lambda a: a.meta.get("timestamp", 0))

    reproducers_dir = project.path("scratch", "reproducers")
    reproducers_dir.mkdir(parents=True, exist_ok=True)

    results = []
    num = 0
    for core in cores:
        print(f"Processing core artifact {core}")
        pid, worker_id = _pid_and_worker_from_core_link(core.dir)
        if pid is None:
            raise ValueError(f"Could not determine PID from core link in artifact {core}")
        inputs_path = project.path("input_tracks", worker_id, f"{pid}.inputs")
        if not inputs_path.exists():
            print(f"Warning: Input track not found for core artifact {core}: {inputs_path}")
            continue
        num += 1
        out_path = reproducers_dir / f"{base}-{num}.py"
        if out_path.exists():
            results.append((out_path, False))
            continue
        script = build_track_script(inputs_path, worker_id=worker_id)
        out_path.write_text(script)
        results.append((out_path, True))

    return results


def build_track_script(inputs_path: Path, worker_id: str = "") -> str:
    """Build a self-contained Python reproducer script from a .inputs file."""
    if not inputs_path.exists():
        raise FileNotFoundError(f"Track file not found: {inputs_path}")

    inputs = parse_inputs_file(inputs_path)
    if not inputs:
        raise FileNotFoundError(f"No inputs found in {inputs_path}")

    label = f"{worker_id}/{inputs_path.name}" if worker_id else str(inputs_path)

    lines = [
        "import sys, gc, unicodedata",
        "",
        f"# track-script: {label}  ({len(inputs)} inputs)",
        "",
        "_base_modules = set(sys.modules)",
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
        lines.append("except Exception as _e:")
        lines.append(f"    print(f'input {name} failed: {{type(_e).__name__}}: {{_e}}', file=sys.stderr)")
        lines.append("else:")
        lines.append(f"    print(f'input {name} completed')")

        if n % _MODULE_RESET_INTERVAL == 0:
            lines.append(f"# FUZZ_MARKER: reset_{name}")
            lines.append("for _k in list(sys.modules):")
            lines.append("    if _k not in _base_modules:")
            lines.append("        del sys.modules[_k]")

        if n % _GC_INTERVAL == 0:
            lines.append(f"# FUZZ_MARKER: gc_{name}")
            lines.append("gc.collect()")
            lines.append("gc.collect()")
            lines.append("gc.collect()")

        lines.append("")

    return "\n".join(lines)

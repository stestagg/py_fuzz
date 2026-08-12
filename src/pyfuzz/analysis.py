import asyncio
import json
import re
from enum import Enum
from pathlib import Path
from typing import Mapping

import odhash

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mK]")
_RECORD_RE = re.compile(
    r"\[RECORD\] child pid:\s*(\d+), result:\s*(\S+), status:\s*(\d+), file:\s*(.+)$"
)

from .project import Project


class ArtifactType(str, Enum):
    CRASH = "crash"
    CORE = "core"


class Artifact:
    def __init__(self, project: Project, hash: str):
        self.project = project
        self.hash = hash
        self._meta: dict | None = None

    @property
    def dir(self) -> Path:
        return self.project.path("artifacts", self.hash)

    @property
    def meta(self) -> dict:
        if self._meta is None:
            self._meta = json.loads((self.dir / "meta.json").read_text())
        return self._meta

    @property
    def type(self) -> ArtifactType:
        return ArtifactType(self.meta["type"])

    @property
    def input(self) -> bytes | None:
        p = self.dir / "input.txt"
        return p.read_bytes() if p.exists() else None

    @property
    def lldb_output(self) -> str | None:
        p = self.dir / "lldb.txt"
        return p.read_text(errors="replace") if p.exists() else None

    def __repr__(self) -> str:
        return f"Artifact({self.hash!r})"


DEFAULT_LLM_SECTION_LIMITS = {
    "metadata": 2_000,
    "input": 8_000,
    "lldb": 40_000,
    "analysis": 16_000,
}

TRANSIENT_META_KEYS = {
    "pid",
    "source_filename",
    "timestamp",
    "worker",
}


def _truncate_middle(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text

    marker = f"\n\n... [truncated {len(text) - max_chars:,} chars from middle] ...\n\n"
    if max_chars <= len(marker):
        return text[:max_chars]

    keep = max_chars - len(marker)
    head = keep // 2
    tail = keep - head
    return text[:head].rstrip() + marker + text[-tail:].lstrip()


def _format_llm_section(title: str, body: str, max_chars: int) -> str:
    return f"## {title}\n{_truncate_middle(body.strip(), max_chars)}"


def _bytes_for_llm(data: bytes) -> str:
    text = data.decode("utf-8", errors="backslashreplace")
    return "".join(
        ch
        if ch in "\n\r\t" or ord(ch) >= 32
        else f"\\x{ord(ch):02x}"
        for ch in text
    )


def _parse_afl_source_filename(name: str) -> dict[str, str]:
    fields = {}
    for part in name.split(","):
        key, sep, value = part.partition(":")
        if sep:
            fields[key] = value
    return fields


def _llm_metadata(artifact: Artifact) -> str:
    meta = {
        "artifact_hash": artifact.hash,
        "type": artifact.type.value,
    }
    for key, value in artifact.meta.items():
        if key in TRANSIENT_META_KEYS:
            continue
        meta[key] = value

    source_filename = artifact.meta.get("source_filename")
    if isinstance(source_filename, str):
        afl_fields = _parse_afl_source_filename(source_filename)
        if "sig" in afl_fields:
            meta["signal"] = afl_fields["sig"]
        if "op" in afl_fields:
            meta["fuzzer_operation"] = afl_fields["op"]

    return json.dumps(meta, indent=2, sort_keys=True)


def _iter_sources(project: Project):
    cores_dir = project.path("cores")
    if cores_dir.exists():
        for f in cores_dir.rglob("*"):
            if f.is_file():
                yield f, ArtifactType.CORE

    outputs_dir = project.path("outputs")
    if outputs_dir.exists():
        for worker_dir in outputs_dir.iterdir():
            crashes_dir = worker_dir / "crashes"
            if crashes_dir.exists():
                for f in crashes_dir.iterdir():
                    if f.is_file() and f.name != "README.txt":
                        yield f, ArtifactType.CRASH


def _iter_record_lines(logs_dir: Path):
    if not logs_dir.exists():
        return
    for worker_dir in logs_dir.iterdir():
        if not worker_dir.is_dir():
            continue
        stdout_log = worker_dir / "stdout.log"
        if not stdout_log.exists():
            continue
        try:
            with open(stdout_log, errors="replace") as f:
                for line in f:
                    clean = _ANSI_RE.sub("", line)
                    m = _RECORD_RE.search(clean)
                    if m:
                        yield {
                            "pid": int(m.group(1)),
                            "result": m.group(2),
                            "status": int(m.group(3)),
                            "file": m.group(4).strip(),
                        }
        except OSError:
            continue


def _enrich_from_logs(project: Project, artifacts_root: Path) -> int:
    logs_dir = project.path("logs")
    updated = 0
    for record in _iter_record_lines(logs_dir):
        vm_path = record["file"]
        if not vm_path.startswith("/pfm/"):
            continue
        rel = vm_path[len("/pfm/"):]
        artifact_dir = artifacts_root / odhash.hash(rel)
        if not artifact_dir.exists():
            continue
        meta_path = artifact_dir / "meta.json"
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        changed = False
        for key, val in (("pid", record["pid"]), ("result", record["result"]), ("status", record["status"])):
            if meta.get(key) != val:
                meta[key] = val
                changed = True
        if changed:
            meta_path.write_text(json.dumps(meta, indent=2))
            updated += 1
    return updated


def _parse_core_pid(name: str) -> int | None:
    # core.<pid>
    parts = name.split(".")
    if len(parts) == 2 and parts[0] == "core":
        try:
            return int(parts[1])
        except ValueError:
            pass
    return None


# The kernel's OOM killer logs two lines per victim, e.g.
#   [ 5669.837840] oom-kill:...,task=fuzz_python,pid=16747,uid=0
#   [ 5669.837893] Out of memory: Killed process 16747 (fuzz_python) total-vm:...
# The "Killed process" summary carries the RSS/vm figures, so we prefer it.
_OOM_KILL_RE = re.compile(r"oom-kill:.*\bpid=(\d+)")
_OOM_VICTIM_RE = re.compile(r"Out of memory: Killed process (\d+)\b")


def _find_oom_kill(kernel_log: Path, pid: int) -> str | None:
    """Return the kernel OOM-kill log line for ``pid`` if it was OOM-killed.

    Scans ``kernel_log`` for the worker and returns the "Out of memory: Killed
    process <pid> ..." summary line (which includes the victim's memory usage).
    Falls back to the terser "oom-kill:" line if the summary is absent, and
    returns None if the log is missing or the pid was never an OOM victim.
    """
    if not kernel_log.exists():
        return None
    try:
        text = kernel_log.read_text(errors="replace")
    except OSError:
        return None
    oom_line: str | None = None
    for line in text.splitlines():
        m = _OOM_VICTIM_RE.search(line)
        if m and int(m.group(1)) == pid:
            return line.strip()
        m = _OOM_KILL_RE.search(line)
        if m and int(m.group(1)) == pid:
            oom_line = line.strip()
    return oom_line


def _create_artifact(artifact_dir: Path, source: Path, atype: ArtifactType) -> None:
    artifact_dir.mkdir(exist_ok=True)
    meta: dict = {"type": atype.value}
    if atype == ArtifactType.CORE:
        pid = _parse_core_pid(source.name)
        if pid is not None:
            meta["pid"] = pid
        meta["worker"] = source.parent.name
        (artifact_dir / "core").symlink_to(source)
    elif atype == ArtifactType.CRASH:
        meta["timestamp"] = int(source.stat().st_ctime)
        meta["worker"] = source.parent.parent.name
        meta["source_filename"] = source.name
        (artifact_dir / "input.txt").write_bytes(source.read_bytes())
        up_to_first_null = source.read_bytes().split(b"\x00", 1)[0]
        (artifact_dir / "input_clean.txt").write_bytes(up_to_first_null)
    (artifact_dir / "meta.json").write_text(json.dumps(meta, indent=2))


async def sync_artifacts(project: Project, concurrency: int = 64) -> tuple[int, int]:
    """Create artifact dirs for any new sources and enrich with log metadata.

    Returns (new_artifact_count, enriched_meta_count).
    """
    artifacts_root = project.path("artifacts")
    artifacts_root.mkdir(exist_ok=True)

    project_root = project.path()
    sem = asyncio.Semaphore(concurrency)

    async def process(source: Path, atype: ArtifactType) -> bool:
        rel = str(source.relative_to(project_root))
        artifact_dir = artifacts_root / odhash.hash(rel)
        if artifact_dir.exists():
            return False
        async with sem:
            await asyncio.to_thread(_create_artifact, artifact_dir, source, atype)
        return True

    results = await asyncio.gather(*[process(src, atype) for src, atype in _iter_sources(project)])
    new_count = sum(results)
    enriched = await asyncio.to_thread(_enrich_from_logs, project, artifacts_root)
    return new_count, enriched


async def link_cores(project: Project) -> int:
    """Link each unlinked core to its matching crash by pid and worker. Returns count of new links."""
    artifacts = await list_artifacts(project)

    cores = [
        a for a in artifacts
        if a.type == ArtifactType.CORE and "pid" in a.meta and "worker" in a.meta and "linked_crash" not in a.meta
    ]
    crashes = [a for a in artifacts if a.type == ArtifactType.CRASH and "pid" in a.meta and "worker" in a.meta]

    linked = 0
    for core in cores:
        crash = next(
            (c for c in crashes if c.meta["pid"] == core.meta["pid"] and c.meta["worker"] == core.meta["worker"]),
            None,
        )
        if crash is None:
            continue

        core_meta = {**core.meta, "linked_crash": crash.hash}
        (core.dir / "meta.json").write_text(json.dumps(core_meta, indent=2))
        core._meta = core_meta

        crash_meta = {**crash.meta, "linked_core": core.hash}
        (crash.dir / "meta.json").write_text(json.dumps(crash_meta, indent=2))
        crash._meta = crash_meta

        linked += 1

    return linked


async def list_artifacts(project: Project) -> list[Artifact]:
    artifacts_root = project.path("artifacts")
    if not artifacts_root.exists():
        return []

    def _scan() -> list[Artifact]:
        return [
            Artifact(project, d.name)
            for d in artifacts_root.iterdir()
            if d.is_dir() and (d / "meta.json").exists()
        ]

    return await asyncio.to_thread(_scan)


def get_artifact(project: Project, hash: str) -> Artifact:
    artifact = Artifact(project, hash)
    if not artifact.dir.exists():
        raise FileNotFoundError(f"Artifact not found: {hash}")
    return artifact


# Marker filename is kept as "analyze-core.marker" for backwards compatibility
# with already-analyzed artifacts (renaming it would force a re-analysis of
# every existing core). It marks any artifact as analyzed, not just cores.
ANALYZE_MARKER_FILE = "analyze-core.marker"
ANALYZE_MARKER_VERSION = "v3"

def is_artifact_analyzed(artifact: Artifact) -> bool:
    """True if the artifact has already been analyzed at the current version."""
    marker_path = artifact.dir / ANALYZE_MARKER_FILE
    return marker_path.exists() and marker_path.read_text().strip() == ANALYZE_MARKER_VERSION


async def analyze_artifact(project: Project, artifact_hash: str, *, force: bool = False) -> None:
    """Run full analysis on any artifact: LLDB, core/crash linking, input tracking.

    Cores and crashes are both artifacts; they only differ in how LLDB is driven
    (handled by ``lldb.analyze_core``) and in which counterpart they link to.

    Idempotent: skips all work if the analyze marker already contains the current
    version. Pass force=True to re-run LLDB and reclassify even if already analyzed
    (e.g. after a change to the LLDB commands or the stack-fault classifier).
    """
    artifact = get_artifact(project, artifact_hash)
    marker_path = artifact.dir / ANALYZE_MARKER_FILE

    if not force and is_artifact_analyzed(artifact):
        return

    if force or not (artifact.dir / "lldb.txt").exists():
        from .lldb import analyze_core as _run_lldb
        await _run_lldb(project, artifact_hash)
        artifact._meta = None  # reload meta after lldb may enrich it

    lldb_text = artifact.lldb_output
    if lldb_text:
        from .stackfault import classify_lldb_stack_fault
        stack_fault = classify_lldb_stack_fault(lldb_text)
        current_meta = json.loads((artifact.dir / "meta.json").read_text())
        current_meta["stackalloc_score"] = stack_fault.score
        current_meta["stackalloc_factors"] = list(stack_fault.factors)
        (artifact.dir / "meta.json").write_text(json.dumps(current_meta, indent=2))
        artifact._meta = None

    pid = artifact.meta.get("pid")
    worker = artifact.meta.get("worker")

    # Link this artifact to its counterpart of the opposite type (core <-> crash)
    # sharing the same crashing pid/worker.
    if artifact.type == ArtifactType.CORE:
        counterpart_type, link_key, back_key = ArtifactType.CRASH, "linked_crash", "linked_core"
    else:
        counterpart_type, link_key, back_key = ArtifactType.CORE, "linked_core", "linked_crash"

    if pid is not None and worker is not None and link_key not in artifact.meta:
        all_artifacts = await list_artifacts(project)
        counterpart = next(
            (a for a in all_artifacts
             if a.type == counterpart_type
             and a.meta.get("pid") == pid
             and a.meta.get("worker") == worker),
            None,
        )
        if counterpart is not None:
            artifact_meta = {**artifact.meta, link_key: counterpart.hash}
            (artifact.dir / "meta.json").write_text(json.dumps(artifact_meta, indent=2))
            artifact._meta = artifact_meta

            counterpart_meta = {**counterpart.meta, back_key: artifact_hash}
            (counterpart.dir / "meta.json").write_text(json.dumps(counterpart_meta, indent=2))
            counterpart._meta = counterpart_meta

    if pid is not None and worker is not None:
        from .trackscript import get_pid_track_summary
        log_path = project.path("input_tracks") / f"{worker}.log"
        if log_path.exists():
            count, last_input = await asyncio.to_thread(get_pid_track_summary, log_path, pid)
            if count > 0:
                current_meta = json.loads((artifact.dir / "meta.json").read_text())
                current_meta["inputs_run"] = count
                (artifact.dir / "meta.json").write_text(json.dumps(current_meta, indent=2))
                artifact._meta = None
            if last_input is not None:
                (artifact.dir / "last_input.txt").write_bytes(last_input)

    if pid is not None and worker is not None:
        await _attach_harness_output(project, artifact, pid, worker)

    # Flag OOM-kills: a status-9 (SIGKILL) crash is often the kernel reaping the
    # worker for memory, not a genuine interpreter fault. Record the kernel line
    # so downstream triage can tell the two apart.
    if pid is not None and worker is not None:
        kernel_log = project.path("logs") / worker / "kernel.log"
        oom_line = await asyncio.to_thread(_find_oom_kill, kernel_log, pid)
        if oom_line is not None:
            current_meta = json.loads((artifact.dir / "meta.json").read_text())
            current_meta["oom_killed"] = True
            current_meta["oom_kill_line"] = oom_line
            (artifact.dir / "meta.json").write_text(json.dumps(current_meta, indent=2))
            artifact._meta = None

    marker_path.write_text(ANALYZE_MARKER_VERSION)


# Backwards-compatible alias for callers that predate the unified analyze API.
analyze_core_artifact = analyze_artifact


async def _attach_harness_output(project: Project, artifact: Artifact, pid: int, worker: str) -> None:
    """Slice the crashing pid's captured stdout/stderr into the artifact dir.

    Writes harness_stdout.txt / harness_stderr.txt, picked up by the LLM view
    via the *.txt glob.
    """
    from .trackscript import get_pid_output

    log_base = project.path("logs") / worker / "child"
    stdout_bytes, stderr_bytes = await asyncio.to_thread(get_pid_output, log_base, pid)

    if stdout_bytes:
        (artifact.dir / "harness_stdout.txt").write_bytes(stdout_bytes)
    if stderr_bytes:
        (artifact.dir / "harness_stderr.txt").write_bytes(stderr_bytes)


def render_artifact_llm_view(
    project: Project,
    artifact_hash: str,
    require_lldb: bool = True,
    section_limits: Mapping[str, int] | None = None,
    exclude_filenames: set[str] | None = None,
    include_filenames: set[str] | None = None,
) -> str:
    """Render an artifact as compact context suitable for an LLM prompt."""
    artifact = get_artifact(project, artifact_hash)
    limits = {**DEFAULT_LLM_SECTION_LIMITS, **(section_limits or {})}
    excluded = {"input.txt", "lldb.txt", *(exclude_filenames or set())}
    lldb_path = artifact.dir / "lldb.txt"

    if require_lldb and (include_filenames is None or "lldb.txt" in include_filenames) and not lldb_path.exists():
        raise FileNotFoundError(
            f"Artifact {artifact_hash} has no lldb.txt; run analysis first."
        )

    if include_filenames is not None:
        sections = []
        for filename in sorted(include_filenames):
            path = artifact.dir / filename
            if filename == "meta.json":
                sections.append(
                    _format_llm_section("Artifact Metadata", _llm_metadata(artifact), limits["metadata"])
                )
            elif filename == "input.txt" and path.is_file():
                data = path.read_bytes()
                sections.append(
                    _format_llm_section(
                        f"Crash Input (input.txt, {len(data):,} bytes)",
                        _bytes_for_llm(data),
                        limits["input"],
                    )
                )
            elif filename == "lldb.txt" and path.is_file():
                text = path.read_text(errors="replace")
                sections.append(
                    _format_llm_section(
                        f"LLDB Analysis (lldb.txt, {len(text):,} chars)",
                        text,
                        limits["lldb"],
                    )
                )
            elif path.is_symlink():
                sections.append(
                    _format_llm_section(
                        f"Artifact File ({filename})",
                        f"Symbolic link to {path.readlink()}",
                        limits["analysis"],
                    )
                )
            elif path.is_file():
                data = path.read_bytes()
                sections.append(
                    _format_llm_section(
                        f"Additional Analysis ({filename}, {len(data):,} bytes)",
                        _bytes_for_llm(data),
                        limits["analysis"],
                    )
                )
            elif path.is_dir():
                sections.append(
                    _format_llm_section(
                        f"Artifact Directory ({filename})",
                        "Directory contents are not included.",
                        limits["analysis"],
                    )
                )
        return "\n\n".join(sections) + ("\n" if sections else "")

    sections = [
        _format_llm_section("Artifact Metadata", _llm_metadata(artifact), limits["metadata"]),
    ]

    input_path = artifact.dir / "input.txt"
    if input_path.exists():
        input_bytes = input_path.read_bytes()
        input_text = _bytes_for_llm(input_bytes)
        sections.append(
            _format_llm_section(
                f"Crash Input (input.txt, {len(input_bytes):,} bytes)",
                input_text,
                limits["input"],
            )
        )

    if lldb_path.exists():
        lldb_text = lldb_path.read_text(errors="replace")
        sections.append(
            _format_llm_section(
                f"LLDB Analysis (lldb.txt, {len(lldb_text):,} chars)",
                lldb_text,
                limits["lldb"],
            )
        )

    for path in sorted(artifact.dir.glob("*.txt")):
        if path.name in excluded:
            continue
        text = path.read_text(errors="replace")
        sections.append(
            _format_llm_section(
                f"Additional Analysis ({path.name}, {len(text):,} chars)",
                text,
                limits["analysis"],
            )
        )

    return "\n\n".join(sections) + "\n"

import asyncio
import json
from enum import Enum
from pathlib import Path

import odhash

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


def _parse_core_name(name: str) -> tuple[int | None, int | None]:
    # core.<pid>.<timestamp>
    parts = name.split(".")
    if len(parts) == 3 and parts[0] == "core":
        try:
            return int(parts[1]), int(parts[2])
        except ValueError:
            pass
    return None, None


def _create_artifact(artifact_dir: Path, source: Path, atype: ArtifactType) -> None:
    artifact_dir.mkdir(exist_ok=True)
    meta: dict = {"type": atype.value}
    if atype == ArtifactType.CORE:
        pid, timestamp = _parse_core_name(source.name)
        if pid is not None:
            meta["pid"] = pid
        if timestamp is not None:
            meta["timestamp"] = timestamp
        (artifact_dir / "core").symlink_to(source)
    elif atype == ArtifactType.CRASH:
        meta["timestamp"] = int(source.stat().st_ctime)
        meta["worker"] = source.parent.parent.name
        meta["source_filename"] = source.name
        (artifact_dir / "input.txt").write_bytes(source.read_bytes())
    (artifact_dir / "meta.json").write_text(json.dumps(meta, indent=2))


async def sync_artifacts(project: Project, concurrency: int = 64) -> int:
    """Create artifact dirs for any new sources. Returns count of newly created artifacts."""
    artifacts_root = project.path("artifacts")
    artifacts_root.mkdir(exist_ok=True)

    project_root = project.path()
    sem = asyncio.Semaphore(concurrency)
    new_count = 0

    async def process(source: Path, atype: ArtifactType) -> bool:
        rel = str(source.relative_to(project_root))
        artifact_dir = artifacts_root / odhash.hash(rel)
        if artifact_dir.exists():
            return False
        async with sem:
            await asyncio.to_thread(_create_artifact, artifact_dir, source, atype)
        return True

    results = await asyncio.gather(*[process(src, atype) for src, atype in _iter_sources(project)])
    return sum(results)


async def link_cores(project: Project) -> int:
    """Link each unlinked core to its closest crash within ±1 second. Returns count of new links."""
    artifacts = await list_artifacts(project)

    cores = [
        a for a in artifacts
        if a.type == ArtifactType.CORE and "timestamp" in a.meta and "linked_crash" not in a.meta
    ]
    crashes = [a for a in artifacts if a.type == ArtifactType.CRASH and "timestamp" in a.meta]

    linked = 0
    for core in cores:
        core_ts = core.meta["timestamp"]
        candidates = [
            (abs(c.meta["timestamp"] - core_ts), c)
            for c in crashes
            if abs(c.meta["timestamp"] - core_ts) <= 1
        ]
        if not candidates:
            continue
        _, crash = min(candidates, key=lambda x: x[0])

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

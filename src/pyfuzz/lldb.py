import asyncio
import shlex
from .project import Project
from .env import Env, Image
from .analysis import get_artifact, ArtifactType


async def analyze_core(project: Project, artifact_hash: str) -> None:
    artifact = get_artifact(project, artifact_hash)
    if artifact.type != ArtifactType.CORE:
        raise ValueError(f"Artifact {artifact_hash} is not a core artifact")

    core_filename = (artifact.dir / "core").readlink().name

    env = Env(project, image=Image.LLDB)

    lldb_cmd = shlex.join([
        "lldb", "--batch",
        "-c", f"/pfm/cores/{core_filename}",
        "/pfm/py/bin/python3",
        "-o", "thread list",
        "-o", "bt all",
        "-o", "register read",
        "-o", "quit",
    ])
    output_path = f"/pfm/artifacts/{artifact.hash}/lldb.txt"
    cmdline = ["sh", "-c", f"{lldb_cmd} > {output_path} 2>&1"]

    proc = await env.run(cmdline, vm_mem=project.vm_mem, ncpu=1)

    await asyncio.gather(
        proc.stdout.read(),
        proc.stderr.read(),
        proc.wait(),
    )

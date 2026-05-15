import asyncio
import sys
from pathlib import Path
from .project import Project
from .env import Env, Image
from .analysis import get_artifact, ArtifactType


async def analyze_core(project: Project, artifact_hash: str) -> None:
    artifact = get_artifact(project, artifact_hash)

    env = Env(project, image=Image.LLDB)
    target = "/pfm/tools/fuzz_python" if not project.fuzz_peg else "/pfm/tools/fuzz_peg"
    output_path = f"/pfm/artifacts/{artifact.hash}/lldb.txt"

    if artifact.type == ArtifactType.CORE:
        core_rel = (artifact.dir / "core").readlink().relative_to(project.path("cores"))
        cmdline = [
            "python3", "/pfm/helpers/lldb_analyze.py",
            "--target", target,
            "--core", f"/pfm/cores/{core_rel}",
            "--output", output_path,
        ]
    elif artifact.type == ArtifactType.CRASH:
        input_path = f"/pfm/artifacts/{artifact.hash}/input.txt"
        cmdline = [
            "python3", "/pfm/helpers/lldb_analyze.py",
            "--target", target,
            "--crash-input", input_path,
            "--output", output_path,
        ]
        if project.actual_fuzz_mem_limit > 0:
            cmdline += ["--mem-limit-mb", str(project.actual_fuzz_mem_limit)]
    else:
        raise ValueError(f"Artifact {artifact_hash} has unsupported type {artifact.type}")

    proc = await env.run(cmdline, vm_mem=project.vm_mem, ncpu=1)

    _, stderr_data, _ = await asyncio.gather(
        proc.stdout.read(),
        proc.stderr.read(),
        proc.wait(),
    )
    if stderr_data:
        print(stderr_data.decode(errors="replace"), file=sys.stderr)


async def analyze_script_artifacts(project: Project, out_name: str, batch_file: Path, artifact_hashes: list[str]) -> None:
    for artifact_hash in artifact_hashes:
        artifact = get_artifact(project, artifact_hash)

        cmds_dst = artifact.dir / f"{out_name}.cmds"
        cmds_dst.write_text(batch_file.read_text())

        env = Env(project, image=Image.LLDB)
        output_path = f"/pfm/artifacts/{artifact.hash}/{out_name}.txt"
        commands_path = f"/pfm/artifacts/{artifact.hash}/{out_name}.cmds"

        if artifact.type == ArtifactType.CORE:
            core_rel = (artifact.dir / "core").readlink().relative_to(project.path("cores"))
            cmdline = [
                "python3", "/pfm/helpers/lldb_analyze.py",
                "--target", project.fuzz_target,
                "--core", f"/pfm/cores/{core_rel}",
                "--output", output_path,
                "--commands-file", commands_path,
            ]
        elif artifact.type == ArtifactType.CRASH:
            cmdline = [
                "python3", "/pfm/helpers/lldb_analyze.py",
                "--target", project.fuzz_target,
                "--crash-input", f"/pfm/artifacts/{artifact.hash}/input.txt",
                "--output", output_path,
                "--commands-file", commands_path,
            ]
            if project.actual_fuzz_mem_limit > 0:
                cmdline += ["--mem-limit-mb", str(project.actual_fuzz_mem_limit)]
        else:
            raise ValueError(f"Artifact {artifact_hash} has unsupported type {artifact.type}")

        proc = await env.run(cmdline, vm_mem=project.vm_mem, ncpu=1)
        _, stderr_data, _ = await asyncio.gather(
            proc.stdout.read(),
            proc.stderr.read(),
            proc.wait(),
        )
        if stderr_data:
            print(stderr_data.decode(errors="replace"), file=sys.stderr)

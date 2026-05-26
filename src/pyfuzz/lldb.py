import asyncio
import sys
from pathlib import Path
from .project import Project
from .env import Env, Image
from .analysis import get_artifact, ArtifactType


async def analyze_core(project: Project, artifact_hash: str, interactive: bool = False, output: str | None = None) -> None:
    artifact = get_artifact(project, artifact_hash)

    env = Env(project, image=Image.LLDB)
    target = "/pfm/tools/fuzz_python" if not project.fuzz_peg else "/pfm/tools/fuzz_peg"

    if interactive:
        if artifact.type == ArtifactType.CORE:
            core_rel = (artifact.dir / "core").readlink().relative_to(project.path("cores"))
            cmdline = [
                "python3", "/pfm/helpers/lldb_analyze.py",
                "--target", target,
                "--core", f"/pfm/cores/{core_rel}",
                "--interactive",
            ]
        elif artifact.type == ArtifactType.CRASH:
            input_path = f"/pfm/artifacts/{artifact.hash}/input.txt"
            cmdline = [
                "python3", "/pfm/helpers/lldb_analyze.py",
                "--target", target,
                "--crash-input", input_path,
                "--interactive",
            ]
            if project.actual_fuzz_mem_limit > 0:
                cmdline += ["--mem-limit-mb", str(project.actual_fuzz_mem_limit)]
        else:
            raise ValueError(f"Artifact {artifact_hash} has unsupported type {artifact.type}")

        proc = await env.run(cmdline, vm_mem=project.vm_mem, ncpu=1, interactive=True)
        await proc.wait()
        return

    output_path = f"/pfm/{output}" if output else f"/pfm/artifacts/{artifact.hash}/lldb.txt"

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


async def run_script_in_lldb(project: Project, script_path: Path, interactive: bool = False, output: str | None = None) -> None:
    script_path = script_path.resolve()
    scratch_dir = project.path("scratch")

    if script_path.is_relative_to(scratch_dir):
        rel = script_path.relative_to(scratch_dir)
        script_vm_path = f"/pfm/scratch/{rel}"
    else:
        scratch_lldb = scratch_dir / "lldb"
        scratch_lldb.mkdir(parents=True, exist_ok=True)
        (scratch_lldb / script_path.name).write_bytes(script_path.read_bytes())
        script_vm_path = f"/pfm/scratch/lldb/{script_path.name}"

    env = Env(project, image=Image.LLDB)

    cmdline = [
        "python3", "/pfm/helpers/lldb_analyze.py",
        "--target", "/pfm/py/bin/python3",
        "--script-path", script_vm_path,
    ]
    if project.actual_fuzz_mem_limit > 0:
        cmdline += ["--mem-limit-mb", str(project.actual_fuzz_mem_limit)]

    if interactive:
        cmdline.append("--interactive")
        proc = await env.run(cmdline, vm_mem=project.vm_mem, ncpu=1, interactive=True)
        await proc.wait()
        return

    cmdline += ["--output", f"/pfm/{output}"]

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

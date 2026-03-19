from __future__ import annotations

import os
import subprocess
from pathlib import Path

import click

from tools.pyfuzz.console import detail, step, success, warn


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--project-root", required=True, type=click.Path(path_type=Path))
@click.option("--jobs", type=click.IntRange(min=1), default=1, show_default=True)
@click.option("--timeout", type=int)
def main(project_root: Path, jobs: int, timeout: int | None) -> None:
    dist_dir = project_root / "dist"
    output_dir = project_root / "outputs"
    harness = dist_dir / "fuzz_python"
    harness_cmplog = dist_dir / "fuzz_python_cmplog"
    env = dict(os.environ)
    env["PYTHONHOME"] = str(dist_dir / "install")
    shim = dist_dir / "nocorelimit.so"
    if shim.exists():
        env["LD_PRELOAD"] = str(shim)
    afl_common = [
        "afl-fuzz",
        "-i", "-" if (output_dir / "main").exists() else os.environ["TESTCASES_DIR"],
        "-o", str(output_dir),
        "-t", "5000",
        "-m", "512",
        "-x", os.environ["DICT_FILE"],
    ]
    if timeout is not None:
        afl_common += ["-V", str(timeout)]
        detail("timeout", f"{timeout}s")
    step(f"Starting AFL++ for {project_root.name}")
    procs: list[tuple[subprocess.Popen[str], object]] = []
    for idx in range(1, jobs):
        log_path = project_root / "logs" / f"worker{idx}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "w")
        proc = subprocess.Popen([*afl_common, "-S", f"worker{idx}", "--", str(harness)], env=env, stdout=log_file, stderr=log_file, text=True)
        procs.append((proc, log_file))
        detail(f"worker{idx}", f"pid={proc.pid} log={log_path}")
    main_cmd = [*afl_common, "-M", "main"]
    if harness_cmplog.exists():
        main_cmd += ["-c", str(harness_cmplog)]
    main_cmd += ["--", str(harness)]
    try:
        code = subprocess.run(main_cmd, env=env, check=False).returncode
        if code == 0:
            success("AFL++ exited cleanly")
        else:
            warn(f"AFL++ exited with status {code}")
        raise SystemExit(code)
    finally:
        for proc, log_file in procs:
            proc.terminate()
            proc.wait()
            log_file.close()


if __name__ == "__main__":
    main()

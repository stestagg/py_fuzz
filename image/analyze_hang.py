#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "click>=8.1,<9",
#   "odhash",
# ]
# ///
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
import shutil
import signal
import subprocess
from pathlib import Path

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).parent.parent))

import click

from image.analyze import crash_analysis_dir, detail, step, success
from image.env import afl_env
from tools.pyfuzz.project import load_project_config_from_root, resolve_harness_paths, resolve_install_path

DEFAULT_HANG_TIMEOUT = 180
DEFAULT_SAMPLE_RATE = 99


@dataclass
class PerfRecordResult:
    returncode: int
    stdout: str
    timed_out: bool


def supports_perf_trampoline(python_bin: Path) -> bool:
    try:
        output = subprocess.check_output(
            [
                str(python_bin),
                "-c",
                "import sysconfig; print(int(bool(sysconfig.get_config_var('PY_HAVE_PERF_TRAMPOLINE'))))",
            ],
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return False
    return output == "1"


def build_perf_record_cmd(harness: Path, perf_data_path: Path, sample_rate: int) -> list[str]:
    return [
        "perf",
        "record",
        "-F",
        str(sample_rate),
        "-g",
        "--call-graph",
        "fp",
        "-o",
        str(perf_data_path),
        "--",
    ]


def build_target_env_cmd(harness: Path, env: dict[str, str]) -> list[str]:
    cmd = ["env"]
    for key in sorted(env):
        cmd.append(f"{key}={env[key]}")
    cmd.append(str(harness))
    return cmd


def build_perf_script_cmd(perf_data_path: Path) -> list[str]:
    return ["perf", "script", "-i", str(perf_data_path)]


def run_perf_record(
    cmd: list[str],
    *,
    stdin_path: Path,
    env: dict[str, str] | None,
    timeout: int,
) -> PerfRecordResult:
    with stdin_path.open("rb") as handle:
        proc = subprocess.Popen(
            cmd,
            stdin=handle,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        try:
            stdout, _ = proc.communicate(timeout=timeout)
            return PerfRecordResult(returncode=proc.returncode, stdout=stdout, timed_out=False)
        except subprocess.TimeoutExpired:
            proc.send_signal(signal.SIGINT)
            try:
                stdout, _ = proc.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, _ = proc.communicate()
            return PerfRecordResult(returncode=proc.returncode, stdout=stdout, timed_out=True)


def _normalize_frame(line: str) -> str | None:
    stripped = line.strip()
    if not stripped:
        return None
    parts = stripped.split(None, 1)
    if len(parts) == 2 and all(ch in "0123456789abcdefABCDEFx" for ch in parts[0]):
        return parts[1].strip()
    return stripped


def parse_perf_script(text: str) -> tuple[tuple[str, ...], int] | None:
    stack_counts: Counter[tuple[str, ...]] = Counter()
    current_frames: list[str] = []
    sample_started = False
    for line in text.splitlines():
        if not line.strip():
            if current_frames:
                stack_counts[tuple(current_frames)] += 1
                current_frames = []
            sample_started = False
            continue
        if not line[:1].isspace():
            if current_frames:
                stack_counts[tuple(current_frames)] += 1
                current_frames = []
            sample_started = True
            continue
        if not sample_started:
            continue
        frame = _normalize_frame(line)
        if frame:
            current_frames.append(frame)
    if current_frames:
        stack_counts[tuple(current_frames)] += 1
    if not stack_counts:
        return None
    stack, samples = stack_counts.most_common(1)[0]
    return stack, samples


def format_heaviest_stack(stack: tuple[str, ...] | None, samples: int | None) -> str:
    if not stack or samples is None:
        return "No samples captured.\n"
    lines = [f"samples: {samples}", ""]
    lines.extend(stack)
    return "\n".join(lines) + "\n"


def analyze_hang(
    project_root: Path,
    worker: str,
    hang: str,
    *,
    timeout: int,
    sample_rate: int,
) -> None:
    config = load_project_config_from_root(project_root)
    harness, _ = resolve_harness_paths(project_root, config.harness)
    pythonhome = resolve_install_path(project_root)
    python_bin = pythonhome / "bin" / "python3"
    if not harness.exists():
        raise click.ClickException(f"Harness not found: {harness}")
    if not supports_perf_trampoline(python_bin):
        raise click.ClickException("Perf trampoline is not available in the instrumented Python build")

    hang_path = project_root / "outputs" / worker / "hangs" / hang
    if not hang_path.exists():
        raise click.ClickException(f"Hang input not found: {hang_path}")

    analysis_dir = crash_analysis_dir(project_root, hang_path)
    analysis_dir.mkdir(parents=True, exist_ok=True)
    perf_data_path = analysis_dir / "perf.data"
    perf_script_path = analysis_dir / "perf.script.txt"
    heaviest_stack_path = analysis_dir / "heaviest_stack.txt"
    info_path = analysis_dir / "info.txt"
    l1_path = analysis_dir / "l1.json"

    target_env = afl_env(project_root / "dist")
    target_env["PYTHON_JIT"] = "0"
    target_env["FUZZ_PERF_TRAMPOLINE"] = "1"
    if config.warmup_imports:
        target_env["FUZZ_WARMUP_IMPORTS"] = config.warmup_imports

    perf_cmd = build_perf_record_cmd(harness, perf_data_path, sample_rate)
    perf_cmd.extend(build_target_env_cmd(harness, target_env))
    step(f"Profiling hang {hang}")
    record_result = run_perf_record(perf_cmd, stdin_path=hang_path, env=None, timeout=timeout)

    perf_script_stdout = ""
    perf_script_returncode: int | None = None
    if perf_data_path.exists():
        script_result = subprocess.run(
            build_perf_script_cmd(perf_data_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        perf_script_stdout = script_result.stdout
        perf_script_returncode = script_result.returncode
        perf_script_path.write_text(perf_script_stdout)

    heaviest = parse_perf_script(perf_script_stdout)
    if heaviest is None:
        heaviest_stack_path.write_text(format_heaviest_stack(None, None))
        heaviest_stack = None
        sample_count = None
    else:
        heaviest_stack, sample_count = heaviest
        heaviest_stack_path.write_text(format_heaviest_stack(heaviest_stack, sample_count))

    shutil.copy(hang_path, analysis_dir / "input")
    l1_path.write_text(json.dumps({"category": "profiled"}))

    info_lines = [
        f"kind: hang",
        f"worker: {worker}",
        f"hang: {hang}",
        f"timeout_seconds: {timeout}",
        f"sample_rate_hz: {sample_rate}",
        f"timed_out: {'yes' if record_result.timed_out else 'no'}",
        f"perf_returncode: {record_result.returncode}",
        f"perf_data: {perf_data_path}",
        f"perf_script: {perf_script_path}",
        f"heaviest_stack: {heaviest_stack_path}",
    ]
    if perf_script_returncode is not None:
        info_lines.append(f"perf_script_returncode: {perf_script_returncode}")
    if sample_count is None:
        info_lines += ["heaviest_stack_samples: none", "", "No samples captured."]
    else:
        info_lines += ["heaviest_stack_samples: " + str(sample_count), "", "Heaviest stack:"]
        info_lines.extend(heaviest_stack)
    if record_result.stdout.strip():
        info_lines += ["", "=== perf record output ===", record_result.stdout.strip()]
    info_path.write_text("\n".join(info_lines) + "\n")

    success(f"Profiled hang {hang}")
    detail("analysis", str(analysis_dir))
    detail("category", "profiled")


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--project-root", required=True, type=click.Path(path_type=Path))
@click.option("--worker", required=True)
@click.option("--hang", required=True)
@click.option("--timeout", type=int, default=DEFAULT_HANG_TIMEOUT, show_default=True)
@click.option("--sample-rate", type=int, default=DEFAULT_SAMPLE_RATE, show_default=True)
def main(project_root: Path, worker: str, hang: str, timeout: int, sample_rate: int) -> None:
    analyze_hang(project_root, worker, hang, timeout=timeout, sample_rate=sample_rate)


if __name__ == "__main__":
    main()

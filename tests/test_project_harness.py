from __future__ import annotations

import json
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import click

from image import analyze as analyze_mod
from image import analyze_hang as analyze_hang_mod
from image import run as run_mod
from image import build as build_mod
from tools.pyfuzz import project as project_mod
from tools.pyfuzz.cli import FindingMatch, _interactive_core_gdb_command, _resolve_finding_hash, cli
from tools.pyfuzz.pfrun import GUEST_PROJECT, GUEST_REPO, afl_command, shell_script
from tools.pyfuzz.project import (
    DEFAULT_HARNESS,
    Project,
    ProjectConfig,
    load_project,
    resolve_harness_paths,
    resolve_install_path,
)


class ProjectHarnessTests(unittest.TestCase):
    def test_load_project_defaults_harness_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir)
            project_root = projects_dir / "demo"
            project_root.mkdir()
            (project_root / "project.json").write_text(
                json.dumps(
                    {
                        "asan": False,
                        "branch": "main",
                        "created_at": "2026-04-08T09:00:00+00:00",
                        "env_id": "demo",
                    }
                )
            )
            with patch.object(project_mod, "PROJECTS_DIR", projects_dir):
                project = load_project("demo")
            self.assertEqual(project.config.harness, DEFAULT_HARNESS)
            self.assertEqual(project.harness_path, project_root / "dist" / "fuzz_python")
            self.assertEqual(project.cmplog_path, project_root / "dist" / "fuzz_python_cmplog")

    def test_resolve_custom_harness_and_install_paths(self) -> None:
        project_root = Path("/tmp/demo")
        harness, harness_cmplog = resolve_harness_paths(project_root, "bin/fuzzers/python_harness")
        self.assertEqual(harness, project_root / "bin" / "fuzzers" / "python_harness")
        self.assertEqual(harness_cmplog, project_root / "bin" / "fuzzers" / "python_harness_cmplog")
        self.assertEqual(resolve_install_path(project_root), project_root / "dist" / "install")

    def test_interactive_core_command_uses_configured_harness(self) -> None:
        project = Project(
            Path("/Users/alice/src/py_fuzz/projects/demo"),
            ProjectConfig(
                env_id="demo",
                branch="main",
                harness="bin/fuzzers/python_harness",
                asan=True,
                asan_options="detect_leaks=0",
            ),
        )
        cmd = _interactive_core_gdb_command(project, project.root, project.root / "cores" / "corefile")
        self.assertEqual(cmd[0], "./pfrun")
        self.assertIn("--interactive", cmd)
        cmd_text = " ".join(cmd)
        self.assertIn("/pfm/project/bin/fuzzers/python_harness", cmd_text)
        self.assertNotIn("/pfm/project/dist/fuzz_python", cmd_text)
        self.assertIn("PYTHONHOME=/pfm/project/dist/install", cmd_text)

    def test_pfrun_command_uses_project_mount_and_quoted_env(self) -> None:
        project_root = Path("/Users/alice/src/py_fuzz/projects/demo")
        cmd = afl_command(
            project_root=project_root,
            guest_argv=["uv", "run", "/pfm/repo/image/run.py", "--project-root", str(GUEST_PROJECT)],
            env={"PYTHONPATH": str(GUEST_REPO), "ASAN_OPTIONS": "symbolize=0:detect_leaks=0"},
            jobs=2,
        )
        self.assertEqual(cmd[:5], ["./pfrun", "--imagedir", "envs/afl", "--ncpu", "2"])
        self.assertIn("--mount", cmd)
        self.assertIn(".:repo", cmd)
        self.assertIn("--mount-rw", cmd)
        self.assertIn("projects/demo:project", cmd)
        self.assertIn(".uv-pfrun-cache:uv-cache", cmd)
        self.assertIn("export ASAN_OPTIONS=symbolize=0:detect_leaks=0", cmd[cmd.index("--cmd") + 1])

    def test_shell_script_quotes_env_values(self) -> None:
        script = shell_script(["echo", "ok"], {"A": "value with spaces"})
        self.assertIn("export A='value with spaces'", script)
        self.assertIn("exec echo ok", script)

    def test_image_run_uses_configured_harness(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "demo"
            project_root.mkdir()
            (project_root / "project.json").write_text(
                json.dumps(
                    {
                        "asan": False,
                        "branch": "main",
                        "env_id": "demo",
                        "harness": "bin/custom_harness",
                    }
                )
            )
            with patch.dict("os.environ", {"TESTCASES_DIR": "/tmp/testcases", "DICT_FILE": "/tmp/python.dict"}, clear=False):
                with patch.object(run_mod.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as mock_run:
                    with self.assertRaises(SystemExit) as exc:
                        run_mod.main.callback(project_root=project_root, jobs=1, timeout=None)
            self.assertEqual(exc.exception.code, 0)
            self.assertIn(str(project_root / "bin" / "custom_harness"), mock_run.call_args.args[0])

    def test_image_analyze_uses_configured_harness_and_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            project_root = tmp_path / "demo"
            project_root.mkdir()
            crash_path = tmp_path / "crash"
            crash_path.write_bytes(b"boom")
            harness = project_root / "bin" / "custom_harness"
            pythonhome = resolve_install_path(project_root)
            with patch.object(
                analyze_mod.subprocess,
                "run",
                return_value=SimpleNamespace(stdout="Process 1 exited with status = 0\n"),
            ) as mock_run:
                analyze_mod.analyze_crash(
                    harness,
                    pythonhome,
                    crash_path,
                    project_root / "analysis" / "sample",
                    memory_limit_mb=None,
                    asan_options=None,
                )
            cmd = mock_run.call_args.args[0]
            self.assertEqual(cmd[0], "lldb")
            self.assertIn(str(harness), cmd)
            self.assertIn(f"settings set target.env-vars PYTHONHOME={pythonhome}", cmd)

    def test_lldb_output_classification(self) -> None:
        self.assertEqual(analyze_mod.classify_exit("Process 1 exited with status = 0\n", ""), "non-reproducible")
        self.assertEqual(analyze_mod.classify_exit("stop reason = signal SIGSEGV\n", ""), "segfault")
        self.assertEqual(analyze_mod.classify_exit("AddressSanitizer: heap-buffer-overflow\n", ""), "heap_buffer_overflow")

    def test_build_uses_frame_pointer_flags(self) -> None:
        self.assertIn("-fno-omit-frame-pointer", build_mod.compiler_cflags(asan=False))
        self.assertIn("-mno-omit-leaf-frame-pointer", build_mod.compiler_cflags(asan=False))
        self.assertIn("-fsanitize=address", build_mod.compiler_cflags(asan=True))

    def test_perf_record_command_uses_harness_and_output(self) -> None:
        cmd = analyze_hang_mod.build_perf_record_cmd(Path("/tmp/fuzz_python"), Path("/tmp/perf.data"), 99)
        self.assertEqual(
            cmd,
            [
                "perf",
                "record",
                "-F",
                "99",
                "-g",
                "--call-graph",
                "fp",
                "-o",
                "/tmp/perf.data",
                "--",
            ],
        )

    def test_target_env_command_scopes_env_to_harness(self) -> None:
        cmd = analyze_hang_mod.build_target_env_cmd(
            Path("/tmp/fuzz_python"),
            {
                "PYTHONHOME": "/tmp/install",
                "LD_PRELOAD": "/tmp/a.so:/tmp/b.so",
            },
        )
        self.assertEqual(
            cmd,
            [
                "env",
                "LD_PRELOAD=/tmp/a.so:/tmp/b.so",
                "PYTHONHOME=/tmp/install",
                "/tmp/fuzz_python",
            ],
        )

    def test_parse_perf_script_picks_heaviest_stack(self) -> None:
        perf_script = """
python 123 [001] 1.000: cycles:
\t7f001 py::spam:/tmp/hang.py (/tmp/perf.map)
\t7f002 _PyEval_EvalFrameDefault (/tmp/python)

python 123 [001] 2.000: cycles:
\t7f001 py::spam:/tmp/hang.py (/tmp/perf.map)
\t7f002 _PyEval_EvalFrameDefault (/tmp/python)

python 123 [001] 3.000: cycles:
\t7f010 py::other:/tmp/hang.py (/tmp/perf.map)
"""
        stack, samples = analyze_hang_mod.parse_perf_script(perf_script)
        self.assertEqual(samples, 2)
        self.assertEqual(
            stack,
            (
                "py::spam:/tmp/hang.py (/tmp/perf.map)",
                "_PyEval_EvalFrameDefault (/tmp/python)",
            ),
        )

    def test_run_perf_record_marks_timeout(self) -> None:
        class FakeProc:
            def __init__(self) -> None:
                self.returncode = 130
                self.sent: list[int] = []
                self.communicate_calls = 0

            def communicate(self, timeout=None):
                self.communicate_calls += 1
                if self.communicate_calls == 1:
                    raise subprocess.TimeoutExpired(cmd=["perf"], timeout=timeout)
                return ("flushed", None)

            def send_signal(self, signum: int) -> None:
                self.sent.append(signum)

            def kill(self) -> None:
                raise AssertionError("kill should not be needed")

        with tempfile.TemporaryDirectory() as tmpdir:
            stdin_path = Path(tmpdir) / "hang"
            stdin_path.write_text("hang")
            fake_proc = FakeProc()
            with patch.object(analyze_hang_mod.subprocess, "Popen", return_value=fake_proc):
                result = analyze_hang_mod.run_perf_record(
                    ["perf"],
                    stdin_path=stdin_path,
                    env={},
                    timeout=1,
                )
        self.assertTrue(result.timed_out)
        self.assertEqual(result.stdout, "flushed")
        self.assertEqual(fake_proc.sent, [signal.SIGINT])

    def test_analyze_hang_writes_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            project_root = tmp_path / "demo"
            hang_dir = project_root / "outputs" / "worker1" / "hangs"
            hang_dir.mkdir(parents=True)
            hang_path = hang_dir / "id:000001"
            hang_path.write_text("hang input")
            python_bin = resolve_install_path(project_root) / "bin" / "python3"
            python_bin.parent.mkdir(parents=True, exist_ok=True)
            python_bin.write_text("")
            harness = project_root / "dist" / "fuzz_python"
            harness.parent.mkdir(parents=True, exist_ok=True)
            harness.write_text("")
            (project_root / "project.json").write_text(
                json.dumps(
                    {
                        "asan": False,
                        "branch": "main",
                        "env_id": "demo",
                        "harness": "dist/fuzz_python",
                    }
                )
            )
            def fake_run_perf_record(cmd, *, stdin_path, env, timeout):
                perf_data_path = Path(cmd[cmd.index("-o") + 1])
                perf_data_path.write_bytes(b"perf")
                return analyze_hang_mod.PerfRecordResult(returncode=0, stdout="recorded", timed_out=False)
            with patch.object(analyze_hang_mod, "supports_perf_trampoline", return_value=True):
                with patch.object(analyze_hang_mod, "run_perf_record", side_effect=fake_run_perf_record):
                    with patch.object(
                        analyze_hang_mod.subprocess,
                        "run",
                        return_value=SimpleNamespace(
                            stdout=(
                                "python 1 [001] 1.00: cycles:\n"
                                "\t7f001 py::spam:/tmp/hang.py (/tmp/perf.map)\n"
                                "\t7f002 _PyEval_EvalFrameDefault (/tmp/python)\n"
                            ),
                            returncode=0,
                        ),
                    ):
                        analyze_hang_mod.analyze_hang(project_root, "worker1", "id:000001", timeout=180, sample_rate=99)
            analysis_dir = analyze_mod.crash_analysis_dir(project_root, hang_path)
            self.assertTrue((analysis_dir / "info.txt").exists())
            self.assertTrue((analysis_dir / "heaviest_stack.txt").exists())
            self.assertTrue((analysis_dir / "l1.json").exists())
            self.assertTrue((analysis_dir / "input").exists())
            self.assertEqual(json.loads((analysis_dir / "l1.json").read_text())["category"], "profiled")


if __name__ == "__main__":
    unittest.main()

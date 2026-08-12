from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


_HELPER_PATH = Path(__file__).resolve().parents[1] / "helpers" / "minimize_crash.py"
_SPEC = importlib.util.spec_from_file_location("minimize_crash", _HELPER_PATH)
assert _SPEC is not None
minimize_crash = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(minimize_crash)


class MinimizeCrashUnwrapTests(unittest.TestCase):
    def test_unwrap_compile_exec_inside_generated_try(self) -> None:
        section = (
            "# FUZZ_MARKER: input_1\n"
            "try:\n"
            "    exec(compile(b'x = 1\\nprint(x)\\n', '<input-1>', 'exec'))\n"
            "except:\n"
            "    pass\n"
        )

        self.assertEqual(
            minimize_crash.unwrap_compile_exec_section(section),
            (
                "# FUZZ_MARKER: input_1\n"
                "try:\n"
                "    x = 1\n"
                "    print(x)\n"
                "except:\n"
                "    pass\n"
            ),
        )

    def test_unwrap_try_except_preserves_marker_and_body(self) -> None:
        section = (
            "# FUZZ_MARKER: input_1\n"
            "try:\n"
            "    x = 1\n"
            "    print(x)\n"
            "except:\n"
            "    pass\n"
        )

        self.assertEqual(
            minimize_crash.unwrap_try_except_section(section),
            (
                "# FUZZ_MARKER: input_1\n"
                "x = 1\n"
                "print(x)\n"
            ),
        )

    def test_unwrap_compile_exec_rejects_syntax_error_source(self) -> None:
        section = (
            "# FUZZ_MARKER: input_1\n"
            "try:\n"
            "    exec(compile(b'if True print(1)\\n', '<input-1>', 'exec'))\n"
            "except:\n"
            "    pass\n"
        )

        self.assertIsNone(minimize_crash.unwrap_compile_exec_section(section))


class MinimizeCrashOutputTests(unittest.TestCase):
    def test_find_artifact_name(self) -> None:
        self.assertEqual(
            minimize_crash.find_artifact_name("# track-script: x\n# artifact: abc123\n"),
            "abc123",
        )
        self.assertIsNone(minimize_crash.find_artifact_name("# artifact: ../abc123\n"))

    def test_write_minimized_script_to_artifact_dir(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            project = Path(d) / "project"
            reproducer_dir = project / "scratch" / "reproducers"
            artifact_dir = project / "artifacts" / "abc123"
            reproducer_dir.mkdir(parents=True)
            artifact_dir.mkdir(parents=True)
            script_path = reproducer_dir / "track.py"
            script_path.write_text("# original\n")
            result = "# track-script: x\n# artifact: abc123\nprint('min')\n"

            written = minimize_crash.write_minimized_script(script_path, result)

            self.assertEqual(written.resolve(), (artifact_dir / "reproducer.py").resolve())
            self.assertEqual(written.read_text(), result)
            self.assertFalse(script_path.with_suffix(".min.py").exists())

    def test_write_minimized_script_falls_back_to_min_path(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            script_path = Path(d) / "track.py"
            script_path.write_text("# original\n")
            result = "# track-script: x\n# artifact: missing\nprint('min')\n"

            written = minimize_crash.write_minimized_script(script_path, result)

            self.assertEqual(written, script_path.with_suffix(".min.py"))
            self.assertEqual(written.read_text(), result)

    def test_write_uses_artifact_source_after_comments_are_stripped(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            project = Path(d) / "project"
            reproducer_dir = project / "scratch" / "reproducers"
            artifact_dir = project / "artifacts" / "abc123"
            reproducer_dir.mkdir(parents=True)
            artifact_dir.mkdir(parents=True)
            script_path = reproducer_dir / "track.py"
            script_path.write_text("# original\n")
            result = "print('min')\n"
            artifact_source = "# artifact: abc123\nprint('min')\n"

            written = minimize_crash.write_minimized_script(
                script_path,
                result,
                artifact_source_script=artifact_source,
            )

            self.assertEqual(written.resolve(), (artifact_dir / "reproducer.py").resolve())
            self.assertEqual(written.read_text(), result)


class MinimizeCrashCommentStripTests(unittest.TestCase):
    def test_strip_python_comments_removes_comments_but_not_string_hashes(self) -> None:
        script = (
            "# artifact: abc123\n"
            "\n"
            "value = '# not a comment'    # inline\n"
            "if value:    \n"
            "    print(value)    \n"
            "\n"
            "print(value)\n"
        )

        stripped = minimize_crash.strip_python_comments(script)

        self.assertNotIn("# artifact", stripped)
        self.assertNotIn("# inline", stripped)
        self.assertIn("'# not a comment'", stripped)
        self.assertNotIn("\n\n", stripped)
        self.assertEqual(
            stripped,
            "value = '# not a comment'\nif value:\n    print(value)\nprint(value)\n",
        )


class MinimizeCrashAstReduceTests(unittest.TestCase):
    def test_ast_reduce_removes_statement_when_candidate_still_crashes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            script_path = Path(d) / "track.py"
            script_path.write_text("# original\n")
            writes: list[str] = []
            original_run_script = minimize_crash.run_script
            original_persist = minimize_crash.persist_minimized
            try:
                minimize_crash.run_script = (
                    lambda script, process_mem_limit, script_dir=None:
                    -11 if "remove_me" not in script and "keep_me" in script else 0
                )
                minimize_crash.persist_minimized = (
                    lambda _script_path, script, _stage: writes.append(script)
                )

                reduced, reductions = minimize_crash.ast_reduce_script(
                    "remove_me = 1\nkeep_me = 2\n",
                    script_path,
                    0,
                )
            finally:
                minimize_crash.run_script = original_run_script
                minimize_crash.persist_minimized = original_persist

            self.assertEqual(reductions, 1)
            self.assertEqual(reduced, "keep_me = 2\n")
            self.assertEqual(writes, ["keep_me = 2\n"])

    def test_ast_reduce_skips_unparseable_script(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            script_path = Path(d) / "track.py"
            reduced, reductions = minimize_crash.ast_reduce_script(
                "if True print(1)\n",
                script_path,
                0,
            )

            self.assertEqual(reductions, 0)
            self.assertEqual(reduced, "if True print(1)\n")


if __name__ == "__main__":
    unittest.main()

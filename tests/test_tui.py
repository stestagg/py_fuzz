from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner

from tools.pyfuzz.cli import cli
from tools.pyfuzz.tui import CrashBrowser


class TuiTests(unittest.TestCase):
    def test_cli_tui_forwards_hangs_flag(self) -> None:
        runner = CliRunner()
        project = SimpleNamespace(name="demo", root=Path("/tmp/demo"))
        with patch("tools.pyfuzz.cli.load_project", return_value=project):
            with patch("tools.pyfuzz.cli.step"):
                with patch("tools.pyfuzz.cli.run") as mock_run:
                    result = runner.invoke(cli, ["demo", "tui", "--hangs"])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        cmd = mock_run.call_args.args[0]
        self.assertIn("--hangs", cmd)
        self.assertEqual(cmd[-1], "--hangs")

    def test_load_findings_ignores_hangs_without_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            crash_path = self._write_finding(project_root, "worker1", "crashes", "id:000001", "crash", mtime=1)
            self._write_finding(project_root, "worker1", "hangs", "id:000002", "hang", mtime=2)
            browser = CrashBrowser(project_root)

            findings = browser.load_findings()

        self.assertEqual([finding.kind for finding in findings], ["crash"])
        self.assertEqual([finding.finding_path for finding in findings], [crash_path])

    def test_load_findings_includes_hangs_with_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            crash_path = self._write_finding(project_root, "worker1", "crashes", "id:000001", "crash", mtime=1)
            hang_path = self._write_finding(project_root, "worker1", "hangs", "id:000002", "hang", mtime=2)
            browser = CrashBrowser(project_root, include_hangs=True)

            findings = browser.load_findings()

        self.assertEqual([finding.kind for finding in findings], ["hang", "crash"])
        self.assertEqual([finding.finding_path for finding in findings], [hang_path, crash_path])

    def test_grouped_findings_separates_crashes_and_hangs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            self._write_finding(project_root, "worker1", "crashes", "id:000001", "crash", mtime=1)
            self._write_finding(project_root, "worker1", "hangs", "id:000002", "hang", mtime=2)
            browser = CrashBrowser(project_root, include_hangs=True)
            browser.findings = browser.load_findings()

            grouped = browser.grouped_findings()

        self.assertEqual(list(grouped), ["Crashes", "Hangs"])
        self.assertEqual(list(grouped["Crashes"]), ["unanalyzed"])
        self.assertEqual(list(grouped["Hangs"]), ["unanalyzed"])

    def test_cursor_key_includes_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            self._write_finding(project_root, "worker1", "crashes", "id:000001", "crash", mtime=1)
            self._write_finding(project_root, "worker1", "hangs", "id:000001", "hang", mtime=2)
            browser = CrashBrowser(project_root, include_hangs=True)
            crash = next(finding for finding in browser.load_findings() if finding.kind == "crash")
            hang = next(finding for finding in browser.load_findings() if finding.kind == "hang")

        self.assertNotEqual(browser.cursor_key_for(crash), browser.cursor_key_for(hang))

    def test_grouped_findings_uses_analysis_category_for_hangs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            browser = CrashBrowser(project_root, include_hangs=True)
            self._write_finding(project_root, "worker1", "hangs", "id:000001", "hang", mtime=1)
            browser.findings = browser.load_findings()
            hang = next(finding for finding in browser.findings if finding.kind == "hang")
            hang.analysis_dir.mkdir(parents=True, exist_ok=True)
            (hang.analysis_dir / "l1.json").write_text(json.dumps({"category": "timeout"}))

            grouped = browser.grouped_findings()

        self.assertEqual(list(grouped["Hangs"]), ["timeout"])

    def test_command_for_selected_hang_builds_hang_analysis_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            self._write_finding(project_root, "worker1", "hangs", "id:000001", "hang", mtime=1)
            browser = CrashBrowser(project_root, include_hangs=True)
            hang = next(finding for finding in browser.load_findings() if finding.kind == "hang")

        cmd = browser.command_for_selected_hang(hang)

        self.assertIsNotNone(cmd)
        cmd_text = " ".join(cmd)
        self.assertEqual(cmd[0], "./pfrun")
        self.assertIn("/pfm/repo/image/analyze_hang.py", cmd_text)
        self.assertIn("--hang", cmd_text)
        self.assertIn("id:000001", cmd_text)
        self.assertNotIn("docker", cmd)

    def test_command_for_selected_hang_returns_none_for_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            self._write_finding(project_root, "worker1", "crashes", "id:000001", "crash", mtime=1)
            browser = CrashBrowser(project_root)
            crash = next(finding for finding in browser.load_findings() if finding.kind == "crash")

            cmd = browser.command_for_selected_hang(crash)

        self.assertIsNone(cmd)

    def test_detail_text_for_hang_includes_heaviest_stack(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            self._write_finding(project_root, "worker1", "hangs", "id:000001", "hang", mtime=1)
            browser = CrashBrowser(project_root, include_hangs=True)
            hang = next(finding for finding in browser.load_findings() if finding.kind == "hang")
            hang.analysis_dir.mkdir(parents=True, exist_ok=True)
            (hang.analysis_dir / "heaviest_stack.txt").write_text("samples: 10\n\npy::spam\n")
            (hang.analysis_dir / "info.txt").write_text("kind: hang\n")

            detail = browser.detail_text_for(hang)

        self.assertIn("=== Heaviest Stack ===", detail)
        self.assertIn("samples: 10", detail)
        self.assertIn("kind: hang", detail)

    def _write_finding(
        self,
        project_root: Path,
        worker_id: str,
        dirname: str,
        filename: str,
        contents: str,
        *,
        mtime: int,
    ) -> Path:
        finding_dir = project_root / "outputs" / worker_id / dirname
        finding_dir.mkdir(parents=True, exist_ok=True)
        (finding_dir / "README.txt").write_text("metadata\n")
        finding_path = finding_dir / filename
        finding_path.write_text(contents)
        os.utime(finding_path, (mtime, mtime))
        return finding_path


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.pyfuzz.cli import (
    _infer_project_root_aliases_from_core,
    _project_mount_targets_for_core,
    _project_root_alias_from_mapped_path,
)


class CoreMountTests(unittest.TestCase):
    def test_project_root_alias_from_mapped_path(self) -> None:
        project_root = "/Users/alice/src/py_fuzz/projects/demo"
        mapped_path = f"/run/host_virtiofs{project_root}/dist/fuzz_python"
        self.assertEqual(
            _project_root_alias_from_mapped_path(mapped_path, project_root),
            f"/run/host_virtiofs{project_root}",
        )
        self.assertIsNone(_project_root_alias_from_mapped_path(f"{project_root}-backup/file", project_root))
        self.assertIsNone(_project_root_alias_from_mapped_path("/tmp/other-project/file", project_root))

    def test_infer_project_root_aliases_from_core(self) -> None:
        project_root = Path("/Users/alice/src/py_fuzz/projects/demo")
        virtiofs_root = f"/run/host_virtiofs{project_root}"
        payload = b"\x00".join(
            [
                b"noise",
                f"{virtiofs_root}/dist/fuzz_python".encode(),
                f"{virtiofs_root}/dist/install/lib/python3.15/lib-dynload/_bz2.so".encode(),
                f"{project_root}/dist/fuzz_python".encode(),
                b"",
            ]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            core_path = Path(tmpdir) / "core"
            core_path.write_bytes(payload)
            self.assertEqual(
                _infer_project_root_aliases_from_core(core_path, project_root),
                [virtiofs_root],
            )

    def test_project_mount_targets_for_core(self) -> None:
        project_root = Path("/Users/alice/src/py_fuzz/projects/demo")
        virtiofs_root = f"/run/host_virtiofs{project_root}"
        payload = b"\x00".join(
            [
                f"{virtiofs_root}/dist/fuzz_python".encode(),
                b"",
            ]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            core_path = Path(tmpdir) / "core"
            core_path.write_bytes(payload)
            self.assertEqual(
                _project_mount_targets_for_core(project_root, core_path),
                ["/project", str(project_root), virtiofs_root],
            )


if __name__ == "__main__":
    unittest.main()

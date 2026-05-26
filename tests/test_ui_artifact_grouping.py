import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from pyfuzz.analysis import ArtifactType  # noqa: E402
from ui.backend.server import (  # noqa: E402
    GROUP_FILE_READ_LIMIT,
    GROUP_LABEL_LIMIT,
    artifact_group_value,
    parse_artifact_group_spec,
)


class FakeArtifact:
    def __init__(self, artifact_dir: Path, meta: dict):
        self.hash = "fakehash"
        self.dir = artifact_dir
        self.meta = meta

    @property
    def type(self) -> ArtifactType:
        return ArtifactType(self.meta["type"])


class ArtifactGroupingTests(unittest.TestCase):
    def test_parse_group_specs(self):
        self.assertEqual(parse_artifact_group_spec("type").kind, "type")
        self.assertEqual(parse_artifact_group_spec("file:simpleclass").argument, "simpleclass")
        self.assertEqual(parse_artifact_group_spec("meta:worker").argument, "worker")
        self.assertEqual(parse_artifact_group_spec("exists:lldb.txt").argument, "lldb.txt")

    def test_rejects_invalid_group_specs(self):
        invalid_specs = [
            "",
            "type:crash",
            "file:",
            "file:../secret",
            "file:subdir/name",
            "exists:..",
            "nope:value",
        ]
        for spec in invalid_specs:
            with self.subTest(spec=spec):
                with self.assertRaises(ValueError):
                    parse_artifact_group_spec(spec)

    def test_group_values_from_type_meta_file_and_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact_dir = Path(tmp)
            (artifact_dir / "simpleclass").write_text("alpha\nbeta\n", encoding="utf-8")
            artifact = FakeArtifact(
                artifact_dir,
                {
                    "type": "crash",
                    "worker": "worker-1",
                    "details": {"b": 2, "a": 1},
                },
            )

            self.assertEqual(
                artifact_group_value(artifact, parse_artifact_group_spec("type")),
                {"value": "crash", "label": "crash"},
            )
            self.assertEqual(
                artifact_group_value(artifact, parse_artifact_group_spec("meta:worker")),
                {"value": "worker-1", "label": "worker-1"},
            )
            self.assertEqual(
                artifact_group_value(artifact, parse_artifact_group_spec("meta:missing")),
                {"value": "missing missing", "label": "missing missing"},
            )
            self.assertEqual(
                artifact_group_value(artifact, parse_artifact_group_spec("file:simpleclass")),
                {"value": "alpha\nbeta\n", "label": "alpha beta"},
            )
            self.assertEqual(
                artifact_group_value(artifact, parse_artifact_group_spec("exists:simpleclass")),
                {"value": "has simpleclass", "label": "has simpleclass"},
            )
            self.assertEqual(
                artifact_group_value(artifact, parse_artifact_group_spec("exists:lldb.txt")),
                {"value": "missing lldb.txt", "label": "missing lldb.txt"},
            )

    def test_file_group_value_is_read_limit_with_short_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact_dir = Path(tmp)
            (artifact_dir / "long.txt").write_text("x" * (GROUP_FILE_READ_LIMIT + 10), encoding="utf-8")
            artifact = FakeArtifact(artifact_dir, {"type": "core"})

            group_value = artifact_group_value(artifact, parse_artifact_group_spec("file:long.txt"))

            self.assertEqual(len(group_value["value"]), GROUP_FILE_READ_LIMIT)
            self.assertEqual(len(group_value["label"]), GROUP_LABEL_LIMIT)
            self.assertTrue(group_value["label"].endswith("..."))


if __name__ == "__main__":
    unittest.main()

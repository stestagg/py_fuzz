import asyncio
import importlib.util
import tempfile
import unittest
from pathlib import Path

from pyfuzz.fuzzdict import make_dict


class _FakeProject:
    def __init__(self, root):
        self.root = root

    def path(self, *parts):
        return self.root.joinpath(*parts)


class FuzzDictTests(unittest.TestCase):
    def test_includes_pymutate_name_file(self):
        root = Path(tempfile.mkdtemp())
        (root / "py/afl_dicts").mkdir(parents=True)
        (root / "py/pymutate_names.txt").write_text("nextfile\nDataFrame\n")

        asyncio.run(make_dict(_FakeProject(root)))

        entries = (root / "py/combined.dict").read_bytes().splitlines()
        self.assertIn(b'"nextfile"', entries)
        self.assertIn(b'"DataFrame"', entries)


REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATOR = REPO_ROOT / "pfrun/images/build/build_scripts/generate_pymutate_names.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_pymutate_names", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NameExtractionTests(unittest.TestCase):
    def test_collects_module_member_and_class_member_names(self):
        generator = _load_generator()
        names = generator.collect_names(["builtins", "fileinput", "pathlib"])
        for expected in ("print", "fileinput", "nextfile", "pathlib", "Path", "absolute"):
            self.assertIn(expected, names)
        self.assertEqual(names, sorted(names))

    def test_skips_modules_with_import_side_effects(self):
        generator = _load_generator()
        self.assertEqual(generator.collect_names(["antigravity", "venv.__main__"]), [])

    def test_expands_subpackages(self):
        generator = _load_generator()
        names = generator.collect_names(["xml"])
        self.assertIn("etree", names)
        self.assertIn("ElementTree", names)

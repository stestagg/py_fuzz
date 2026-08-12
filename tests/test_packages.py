import importlib.util
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace

from pyfuzz.packages import (
    REGISTRY,
    UnknownPackageError,
    resolve_packages,
    pymutate_name_candidates,
    write_pymutate_name_file,
    warmup_import_names,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
WRAPPER = REPO_ROOT / "pfrun/images/build/build_scripts/wrap-afl-clang-lto"


class _FakeProject:
    def __init__(self, packages=None, warmup_imports="", root=None):
        self.packages = packages or {}
        self.warmup_imports = warmup_imports
        self._root = root

    def path(self, *parts):
        return self._root.joinpath(*parts)


class ResolvePackagesTests(unittest.TestCase):
    def test_dependency_built_before_dependent(self):
        # pandas depends on numpy; listing only pandas pulls numpy in first.
        self.assertEqual(
            resolve_packages({"pandas": "main"}),
            [("numpy", "main"), ("pandas", "main")],
        )

    def test_explicit_ref_overrides_default(self):
        plan = resolve_packages({"pandas": "v2.2.3", "numpy": "v2.1.0"})
        self.assertEqual(plan, [("numpy", "v2.1.0"), ("pandas", "v2.2.3")])

    def test_pyarrow_pulls_numpy_first(self):
        # pyarrow depends on numpy; listing only pyarrow builds numpy first.
        self.assertEqual(
            resolve_packages({"pyarrow": "main"}),
            [("numpy", "main"), ("pyarrow", "main")],
        )

    def test_pandas_and_pyarrow_resolve_numpy_once(self):
        plan = resolve_packages({"pandas": "main", "pyarrow": "main"})
        self.assertEqual([n for n, _ in plan].count("numpy"), 1)
        # numpy precedes both dependents.
        names = [n for n, _ in plan]
        self.assertLess(names.index("numpy"), names.index("pandas"))
        self.assertLess(names.index("numpy"), names.index("pyarrow"))

    def test_pyarrow_uses_cmake_profile(self):
        self.assertEqual(REGISTRY["pyarrow"].profile, "cmake")

    def test_implicit_dep_uses_default_ref(self):
        (_numpy, numpy_ref), _ = resolve_packages({"pandas": "somebranch"})
        self.assertEqual(numpy_ref, REGISTRY["numpy"].default_ref)

    def test_empty(self):
        self.assertEqual(resolve_packages({}), [])

    def test_unknown_package_raises(self):
        with self.assertRaises(UnknownPackageError):
            resolve_packages({"scipy": "main"})


class WarmupImportTests(unittest.TestCase):
    def test_includes_packages_and_legacy_field_deduped(self):
        proj = _FakeProject(packages={"pandas": "main"}, warmup_imports="gc, numpy")
        # numpy comes from pandas' deps; the duplicate in warmup_imports is dropped.
        self.assertEqual(warmup_import_names(proj), ["numpy", "pandas", "gc"])

    def test_pyarrow_warms_core_and_parquet(self):
        proj = _FakeProject(packages={"pyarrow": "main"})
        # numpy (dep) first, then pyarrow's core + parquet submodule.
        self.assertEqual(
            warmup_import_names(proj), ["numpy", "pyarrow", "pyarrow.parquet"]
        )

    def test_empty(self):
        self.assertEqual(warmup_import_names(_FakeProject()), [])


class PyMutateNameCandidatesTests(unittest.TestCase):
    def test_includes_selected_packages_and_dependencies(self):
        names = pymutate_name_candidates(_FakeProject(packages={"pandas": "main"}))
        self.assertIn("ndarray", names)
        self.assertIn("DataFrame", names)
        self.assertEqual(names.count("astype"), 1)

    def test_includes_pyarrow_parquet_surface(self):
        names = pymutate_name_candidates(_FakeProject(packages={"pyarrow": "main"}))
        self.assertIn("read_table", names)
        self.assertIn("ParquetFile", names)
        self.assertIn("ndarray", names)  # from the numpy dep

    def test_empty(self):
        self.assertEqual(pymutate_name_candidates(_FakeProject()), [])

    def test_build_name_file_contains_package_and_dependency_names(self):
        root = Path(tempfile.mkdtemp())
        (root / "py").mkdir()
        (root / "py/pymutate_names.txt").write_text("nextfile\n")
        project = _FakeProject(packages={"pandas": "main"}, root=root)
        write_pymutate_name_file(project)
        names = (root / "py/pymutate_names.txt").read_text().splitlines()
        self.assertIn("nextfile", names)
        self.assertIn("ndarray", names)
        self.assertIn("DataFrame", names)

    def test_afl_environment_passes_name_file_paths_to_pymutate(self):
        from pyfuzz import env as envmod

        project = _FakeProject(
            packages={"pandas": "main"}, root=Path(tempfile.mkdtemp())
        )
        project.asan = False
        project.harness = "fuzz_python"
        project.crash_on_memory_error = False
        project.fuzz_env = ()
        env = SimpleNamespace(
            runner=envmod.Runner.PFRUN,
            image=envmod.Image.AFL,
            project=project,
        )
        vars = envmod.load_image_vars(env)
        self.assertEqual(
            vars["PYMUTATE_NAME_FILES"],
            "/pfm/config/pymutate_names.txt:/pfm/py/pymutate_names.txt",
        )


class MesonWrapDownloadsTests(unittest.TestCase):
    # pandas' real fast_float.wrap, minus the [provide] section noise.
    FAST_FLOAT = (
        "[wrap-file]\n"
        "directory = fast_float-8.2.3\n"
        "source_url = https://github.com/fastfloat/fast_float/archive/refs/tags/v8.2.3.tar.gz\n"
        "source_filename = fast_float-8.2.3.tar.gz\n"
        "source_hash = fa811076bad7b7151ce826005a7213971c879b192ee4505a7016c8413038c2d0\n"
        "patch_directory = fast_float\n"
    )

    def test_source_archive_extracted(self):
        from pyfuzz.build import meson_wrap_downloads

        self.assertEqual(
            meson_wrap_downloads(self.FAST_FLOAT),
            [(
                "https://github.com/fastfloat/fast_float/archive/refs/tags/v8.2.3.tar.gz",
                "fast_float-8.2.3.tar.gz",
                "fa811076bad7b7151ce826005a7213971c879b192ee4505a7016c8413038c2d0",
            )],
        )

    def test_patch_archive_included(self):
        from pyfuzz.build import meson_wrap_downloads

        wrap = (
            "[wrap-file]\n"
            "source_url = https://example.com/src.tar.gz\n"
            "source_filename = src.tar.gz\n"
            "patch_url = https://example.com/patch.zip\n"
            "patch_filename = patch.zip\n"
            "patch_hash = abc123\n"
        )
        self.assertEqual(
            meson_wrap_downloads(wrap),
            [
                ("https://example.com/src.tar.gz", "src.tar.gz", None),
                ("https://example.com/patch.zip", "patch.zip", "abc123"),
            ],
        )

    def test_wrap_git_yields_nothing(self):
        from pyfuzz.build import meson_wrap_downloads

        wrap = "[wrap-git]\nurl = https://example.com/repo.git\nrevision = main\n"
        self.assertEqual(meson_wrap_downloads(wrap), [])


class SoFilesTests(unittest.TestCase):
    def test_collects_site_packages_and_skips_blacklist(self):
        from pyfuzz import env as envmod

        root = Path(tempfile.mkdtemp())
        dyn = root / "py/lib/python3.13/lib-dynload"
        dyn.mkdir(parents=True)
        (dyn / "_json.cpython-313d.so").touch()
        (dyn / "_interpreters.cpython-313d.so").touch()  # blacklisted
        sp = root / "py/lib/python3.13/site-packages/pandas/_libs"
        sp.mkdir(parents=True)
        (sp / "hashtable.cpython-313d.so").touch()

        # Versioned soname bundled next to a package (pyarrow's Arrow C++ libs).
        pa = root / "py/lib/python3.13/site-packages/pyarrow"
        pa.mkdir(parents=True)
        (pa / "libarrow.so.1800.0.0").touch()

        out = envmod.so_files(_FakeProject(root=root))
        self.assertIn("lib-dynload/_json", out)
        self.assertIn("site-packages/pandas/_libs/hashtable", out)
        self.assertIn("site-packages/pyarrow/libarrow.so.1800.0.0", out)
        self.assertNotIn("_interpreters", out)


class ArrowThirdpartyEnvTests(unittest.TestCase):
    def test_rewrites_host_prefix_to_vm_and_forces_bundled(self):
        from pyfuzz.build import _arrow_thirdparty_env

        stdout = (
            "export ARROW_THRIFT_URL=/host/pkgs/pyarrow/.arrow_thirdparty/thrift-0.16.0.tar.gz\n"
            "export ARROW_SNAPPY_URL=/host/pkgs/pyarrow/.arrow_thirdparty/snappy-1.1.10.tar.gz\n"
        )
        out = _arrow_thirdparty_env(stdout, "/host/pkgs", "/pfm/packages")
        self.assertIn("export ARROW_DEPENDENCY_SOURCE=BUNDLED", out)
        self.assertIn(
            "ARROW_THRIFT_URL=/pfm/packages/pyarrow/.arrow_thirdparty/thrift-0.16.0.tar.gz",
            out,
        )
        # The host prefix must not leak into a path the offline VM will read.
        self.assertNotIn("/host/pkgs", out)


def _load_wrapper():
    loader = SourceFileLoader("ltowrap", str(WRAPPER))
    spec = importlib.util.spec_from_loader("ltowrap", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class WrapperProfileTests(unittest.TestCase):
    def setUp(self):
        self.w = _load_wrapper()
        self.w.load_state = lambda: {"start_id": 100}

    def classify(self, argv, profile):
        import os

        os.environ["LTOWRAP_PROFILE"] = profile
        env = {"AFL_LLVM_LTO_DONTWRITEID": "1"}
        res = self.w.prepare("afl-clang-lto", list(argv), env)
        if res is self.w.CAPTURE_EDGES:
            return "INSTRUMENT_SO"
        if isinstance(res, self.w.AlternateBinary):
            return "PLAIN_CLANG"
        if res is None:
            return "INSTRUMENT_EXE" if "AFL_LLVM_LTO_DONTWRITEID" not in env else "PASSTHROUGH"
        return "?"

    def test_cpython_profile_matches_legacy_classification(self):
        self.assertEqual(self.classify(["-c", "f.c", "-o", "f.o"], "cpython"), "PASSTHROUGH")
        self.assertEqual(self.classify(["f.o", "-o", "b.so", "-shared"], "cpython"), "INSTRUMENT_SO")
        self.assertEqual(self.classify(["a.o", "-o", "python.exe"], "cpython"), "INSTRUMENT_EXE")
        self.assertEqual(
            self.classify(["a.o", "-o", "/pfm/tools/fuzz_python"], "cpython"), "INSTRUMENT_EXE"
        )
        self.assertEqual(
            self.classify(["x.o", "-o", "Programs/_freeze_module"], "cpython"), "PLAIN_CLANG"
        )

    def test_cpython_profile_errors_on_unknown_output(self):
        with self.assertRaises(ValueError):
            self.classify(["x.o", "-o", "surprise_binary"], "cpython")

    def test_meson_python_profile(self):
        self.assertEqual(self.classify(["-c", "x.c", "-o", "x.o"], "meson_python"), "PASSTHROUGH")
        self.assertEqual(
            self.classify(["x.o", "-o", "pd/_libs/h.cpython-313d-x86_64-linux-gnu.so", "-shared"], "meson_python"),
            "INSTRUMENT_SO",
        )
        self.assertEqual(self.classify(["--version"], "meson_python"), "PLAIN_CLANG")
        self.assertEqual(self.classify(["-E", "conftest.c"], "meson_python"), "PLAIN_CLANG")
        self.assertEqual(self.classify(["-dumpmachine"], "meson_python"), "PLAIN_CLANG")
        self.assertEqual(self.classify(["conftest.c", "-o", "conftest"], "meson_python"), "PLAIN_CLANG")
        # meson's linker detection probe.
        self.assertEqual(self.classify(["-Wl,--version"], "meson_python"), "PLAIN_CLANG")

    def test_meson_python_errors_on_ambiguous(self):
        # A compile with neither an object output nor any probe signal must not be
        # silently passed to plain clang.
        with self.assertRaises(ValueError):
            self.classify(["-c", "x.c"], "meson_python")

    def test_cmake_profile(self):
        # Object compiles -> bitcode passthrough.
        self.assertEqual(
            self.classify(["-c", "x.cc", "-o", "CMakeFiles/a.dir/x.cc.o"], "cmake"),
            "PASSTHROUGH",
        )
        # A plain .so and a *versioned* soname (libarrow.so.1800.0.0) both instrument.
        self.assertEqual(
            self.classify(["a.o", "-o", "pyarrow/_parquet.cpython-313d.so", "-shared"], "cmake"),
            "INSTRUMENT_SO",
        )
        self.assertEqual(
            self.classify(["a.o", "-o", "libarrow.so.1800.0.0", "-shared"], "cmake"),
            "INSTRUMENT_SO",
        )
        # CMake compiler-id / feature-test probes -> plain clang.
        self.assertEqual(
            self.classify(["CMakeCCompilerId.c", "-o", "CMakeCCompilerId"], "cmake"),
            "PLAIN_CLANG",
        )
        self.assertEqual(self.classify(["-E", "conftest.c"], "cmake"), "PLAIN_CLANG")
        self.assertEqual(self.classify(["--version"], "cmake"), "PLAIN_CLANG")
        self.assertEqual(self.classify(["-Wl,--version"], "cmake"), "PLAIN_CLANG")

    def test_cmake_errors_on_ambiguous(self):
        with self.assertRaises(ValueError):
            self.classify(["-c", "x.cc"], "cmake")

    def test_unknown_profile_errors(self):
        with self.assertRaises(ValueError):
            self.classify(["--version"], "does_not_exist")


if __name__ == "__main__":
    unittest.main()

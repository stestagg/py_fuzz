"""Registry of third-party compiled Python packages the fuzzer can build.

Each package is built from source as a debug, AFL-instrumented build and installed
into the project's interpreter so fuzz inputs can import and exercise it. The actual
compilation lives in shell recipes under
``pfrun/images/build/build_scripts/packages/<name>.sh``; this module holds the
testable orchestration: which packages exist, their build order, and their warmup
imports.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PackageSpec:
    name: str
    imports: tuple[str, ...]          # module names for FUZZ_WARMUP_IMPORTS
    pymutate_names: tuple[str, ...]   # identifiers likely to reach this package's C paths
    repo: str                         # GitHub "org/repo"; cloned host-side (VM is offline)
    deps: tuple[str, ...] = ()        # other package names, built first
    default_ref: str = "main"         # git ref used when not pinned in config
    profile: str = "meson_python"     # wrapper rule set to activate (LTOWRAP_PROFILE)


REGISTRY: dict[str, PackageSpec] = {
    "numpy": PackageSpec(
        "numpy",
        imports=("numpy",),
        pymutate_names=(
            "array", "ndarray", "dtype", "zeros", "ones", "empty", "arange",
            "reshape", "transpose", "sum", "mean", "dot", "matmul", "concatenate",
            "stack", "where", "take", "astype", "shape", "strides", "ndim", "size",
            "item", "itemsize", "nbytes", "data", "flat", "real", "imag", "T",
            "__array_interface__", "__array_ufunc__", "__array_function__",
            "__array_finalize__",
        ),
        repo="numpy/numpy",
    ),
    "pandas": PackageSpec(
        "pandas",
        imports=("pandas",),
        pymutate_names=(
            "DataFrame", "Series", "Index", "read_csv", "concat", "merge", "groupby",
            "loc", "iloc", "at", "iat", "columns", "index", "values", "to_numpy",
            "to_dict", "to_csv", "fillna", "dropna", "astype", "sort_values", "apply",
            "agg", "describe", "value_counts", "isna", "notna",
        ),
        repo="pandas-dev/pandas",
        deps=("numpy",),
    ),
    "pyarrow": PackageSpec(
        "pyarrow",
        # Warm both so the Parquet extension .so is resident before the forkserver.
        imports=("pyarrow", "pyarrow.parquet"),
        pymutate_names=(
            "Table", "Array", "ChunkedArray", "RecordBatch", "RecordBatchReader",
            "Schema", "Field", "DataType", "field", "schema", "array", "table",
            "chunked_array", "record_batch", "concat_tables", "concat_arrays",
            "from_pandas", "to_pandas", "from_pylist", "to_pylist", "from_arrays",
            "BufferReader", "BufferOutputStream", "py_buffer", "ipc", "compute",
            # Parquet reader/writer surface (pyarrow.parquet):
            "parquet", "read_table", "write_table", "ParquetFile", "ParquetReader",
            "ParquetWriter", "ParquetSchema", "read_schema", "read_metadata",
        ),
        # pyarrow lives in the Apache Arrow monorepo (cpp/ core + python/ bindings).
        repo="apache/arrow",
        deps=("numpy",),
        # Two-stage CMake build (Arrow C++ then the bindings); not meson-python.
        profile="cmake",
    ),
}


class UnknownPackageError(ValueError):
    pass


def _spec(name: str) -> PackageSpec:
    try:
        return REGISTRY[name]
    except KeyError:
        raise UnknownPackageError(
            f"Unknown package '{name}'. Known packages: {', '.join(sorted(REGISTRY))}"
        ) from None


def resolve_packages(packages: dict[str, str]) -> list[tuple[str, str]]:
    """Expand configured packages into a deps-first build plan.

    ``packages`` maps package name -> git ref (branch/tag/commit). Declared
    dependencies are pulled in automatically at their ``default_ref`` unless the
    caller pinned them explicitly. Returns ``(name, ref)`` pairs in topological
    order (each package after all of its dependencies). Raises
    ``UnknownPackageError`` for a name not in the registry.
    """
    ordered: list[str] = []
    seen: set[str] = set()
    visiting: set[str] = set()

    def visit(name: str) -> None:
        if name in seen:
            return
        if name in visiting:
            raise ValueError(f"Circular package dependency involving '{name}'")
        visiting.add(name)
        spec = _spec(name)
        for dep in spec.deps:
            visit(dep)
        visiting.discard(name)
        seen.add(name)
        ordered.append(name)

    for name in packages:
        visit(name)

    return [(name, packages.get(name, _spec(name).default_ref)) for name in ordered]


def warmup_import_names(project) -> list[str]:
    """Module names to pre-import before the AFL forkserver starts.

    Combines every resolved package's declared imports with the legacy
    comma-separated ``warmup_imports`` config field, de-duplicated in order.
    """
    names: list[str] = []
    for name, _ref in resolve_packages(project.packages):
        names.extend(_spec(name).imports)
    for raw in project.warmup_imports.split(","):
        stripped = raw.strip()
        if stripped:
            names.append(stripped)

    deduped: list[str] = []
    for name in names:
        if name not in deduped:
            deduped.append(name)
    return deduped


def pymutate_name_candidates(project) -> list[str]:
    """Identifiers that bias name-based mutations toward configured packages.

    Dependencies are included because they are built alongside the requested
    package. The order follows the build plan and duplicates are removed, so the
    value is deterministic and is written to pymutate's package-name file.
    """
    names: list[str] = []
    for name, _ref in resolve_packages(project.packages):
        names.extend(_spec(name).pymutate_names)
    return list(dict.fromkeys(names))


def write_pymutate_name_file(project) -> None:
    """Merge package-derived mutation names into the interpreter name file."""
    path = project.path("py", "pymutate_names.txt")
    names = []
    if path.exists():
        names.extend(line.strip() for line in path.read_text().splitlines() if line.strip())
    names.extend(pymutate_name_candidates(project))
    names = list(dict.fromkeys(names))
    path.write_text("\n".join(names) + ("\n" if names else ""))

#!/usr/bin/env python3
"""Extract usable Python identifiers from the running interpreter's stdlib.

Run this under the freshly-built interpreter. It imports every standard
library module (and submodule) and collects the identifier components of
module names, module members, and members of any classes those modules
expose. This replaces the earlier Sphinx-inventory approach, which required
network access to build the documentation venv.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
import warnings
from pathlib import Path

# Modules whose import has side effects beyond definition: ``antigravity``
# opens a web browser, ``this`` prints, ``idlelib.idle`` and ``turtledemo``
# launch GUIs. ``__main__`` submodules (e.g. ``venv.__main__``) run their
# program on import.
SKIP_TOP_LEVEL = frozenset({"antigravity", "this", "idlelib", "turtledemo"})


def _import(name: str):
    parts = name.split(".")
    if parts[0] in SKIP_TOP_LEVEL or "__main__" in parts:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return importlib.import_module(name)
    except (Exception, SystemExit):
        # Not built on this platform (tkinter without Tk, winreg, ...) or
        # broken at import time. Either way it contributes no names.
        return None


def _walk_modules(top_level_names):
    for name in top_level_names:
        module = _import(name)
        if module is None:
            continue
        yield name, module
        path = getattr(module, "__path__", None)
        if path is None:
            continue
        for info in pkgutil.walk_packages(path, prefix=name + ".", onerror=lambda _: None):
            submodule = _import(info.name)
            if submodule is not None:
                yield info.name, submodule


def collect_names(top_level_names) -> list[str]:
    """Return every identifier component reachable from the given modules.

    Pymutate replaces a single identifier, so dotted module names contribute
    each component (``xml.etree.ElementTree`` -> ``xml``, ``etree``,
    ``ElementTree``). Keep ASCII Python identifiers: they are valid in both
    the Python source mutators and AFL's quoted dictionary format.
    """
    names: set[str] = set()

    def add(name: str) -> None:
        if name.isascii() and name.isidentifier():
            names.add(name)

    for dotted_name, module in _walk_modules(top_level_names):
        for segment in dotted_name.split("."):
            add(segment)
        for attr in dir(module):
            add(attr)
            try:
                value = getattr(module, attr)
            except Exception:
                continue
            if isinstance(value, type):
                for member in dir(value):
                    add(member)
    return sorted(names)


def write_name_files(names: list[str], names_path: Path, combined_dict_path: Path) -> None:
    names_path.write_text("\n".join(names) + ("\n" if names else ""))
    combined_dict_path.write_bytes(
        b"\n".join(b'"' + name.encode("ascii") + b'"' for name in names)
        + (b"\n" if names else b"")
    )


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} NAMES_FILE COMBINED_DICT", file=sys.stderr)
        return 2

    names_path, combined_dict_path = map(Path, argv[1:])
    # ``builtins`` is in stdlib_module_names, so this covers builtin
    # functions and types (and their methods, via the class-member walk).
    names = collect_names(sorted(sys.stdlib_module_names))
    write_name_files(names, names_path, combined_dict_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

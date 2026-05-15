#!/usr/bin/env python3
"""Minimize a track-script reproducer to the smallest set of sections that still crashes."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

PYTHON = "/pfm/py/bin/python3"
MARKER_PREFIX = "# FUZZ_MARKER: "
TIMEOUT = 30


def is_crash(returncode: int) -> bool:
    return returncode < 0


def run_script(script: str) -> int:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(script)
        tmp = f.name
    try:
        result = subprocess.run(
            [PYTHON, tmp],
            timeout=TIMEOUT,
            capture_output=True,
        )
        return result.returncode
    except subprocess.TimeoutExpired:
        return 0
    finally:
        os.unlink(tmp)


def parse_sections(script: str) -> tuple[str, list[tuple[str, str]]]:
    """Split into header (pre-first-marker) and [(name, text), ...] sections."""
    lines = script.splitlines(keepends=True)
    header: list[str] = []
    sections: list[tuple[str, str]] = []
    cur_name: str | None = None
    cur_lines: list[str] = []

    for line in lines:
        if line.startswith(MARKER_PREFIX):
            if cur_name is not None:
                sections.append((cur_name, "".join(cur_lines)))
            else:
                header = cur_lines
            cur_name = line[len(MARKER_PREFIX):].strip()
            cur_lines = [line]
        else:
            cur_lines.append(line)

    if cur_name is not None:
        sections.append((cur_name, "".join(cur_lines)))
    elif not sections:
        header = cur_lines

    return "".join(header), sections


def assemble(header: str, sections: list[tuple[str, str]]) -> str:
    return header + "".join(text for _, text in sections)


def minimize(script_path: Path) -> str:
    script = script_path.read_text()

    rc = run_script(script)
    if not is_crash(rc):
        print(f"error: original script did not crash (exit {rc})", file=sys.stderr)
        sys.exit(1)
    print(f"confirmed crash (exit {rc})", file=sys.stderr)

    header, sections = parse_sections(script)
    print(f"{len(sections)} sections to minimize", file=sys.stderr)

    changed = True
    while changed:
        changed = False
        i = 0
        removed_this_pass = 0
        while i < len(sections):
            candidate = sections[:i] + sections[i + 1:]
            rc = run_script(assemble(header, candidate))
            if is_crash(rc):
                print(f"  removed {sections[i][0]}", file=sys.stderr)
                sections = candidate
                removed_this_pass += 1
                changed = True
            else:
                i += 1
        print(
            f"pass done: {removed_this_pass} removed, {len(sections)} remaining",
            file=sys.stderr,
        )

    return assemble(header, sections)


def main() -> None:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <reproducer.py>", file=sys.stderr)
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"error: not found: {path}", file=sys.stderr)
        sys.exit(1)

    print(minimize(path))


if __name__ == "__main__":
    main()

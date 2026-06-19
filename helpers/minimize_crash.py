#!/usr/bin/env python3
"""Minimize a track-script reproducer to the smallest set of sections that still crashes."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

PYTHON = "/pfm/py/bin/python3"
MARKER_PREFIX = "# FUZZ_MARKER: "
TIMEOUT = 480
TIMEOUT_RETURNCODE = None


def is_crash(returncode) -> bool:
    return isinstance(returncode, int) and returncode < 0


def _build_cmdline(path: str, process_mem_limit: int) -> tuple[list[str], dict]:
    cmdline = [PYTHON, path]
    env = os.environ.copy()
    if process_mem_limit > 0:
        env['MEM_LIMIT_MB'] = str(process_mem_limit)
        env['MEM_LIMIT_EXEC'] = PYTHON
        cmdline[0] = '/pfm/tools/mem_limit_exec'
    return cmdline, env


def run_file(path: Path, process_mem_limit: int):
    """Run a script at its actual path (no tmpfile copy)."""
    cmdline, env = _build_cmdline(str(path), process_mem_limit)
    print(f"running: {' '.join(cmdline)}", file=sys.stderr)
    try:
        result = subprocess.run(cmdline, timeout=TIMEOUT, capture_output=True, env=env)
        return result.returncode
    except subprocess.TimeoutExpired:
        return TIMEOUT_RETURNCODE


def run_script(script: str, process_mem_limit: int, script_dir: Path | None = None):
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, dir=script_dir
    ) as f:
        f.write(script)
        tmp = f.name
    try:
        cmdline, env = _build_cmdline(tmp, process_mem_limit)
        print(f"running: {' '.join(cmdline)}", file=sys.stderr)
        result = subprocess.run(cmdline, timeout=TIMEOUT, capture_output=True, env=env)
        return result.returncode
    except subprocess.TimeoutExpired:
        return TIMEOUT_RETURNCODE
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


def bisect_sections(
    header: str,
    sections: list[tuple[str, str]],
    process_mem_limit: int,
    script_dir: Path,
) -> list[tuple[str, str]]:
    n = len(sections)
    if n == 0:
        return sections

    def try_keeping(indices: set[int]) -> bool:
        kept = [sections[i] for i in sorted(indices)]
        return is_crash(run_script(assemble(header, kept), process_mem_limit, script_dir))

    def reduce(candidates: set[int], required: set[int]) -> set[int]:
        if not candidates:
            return required
        if try_keeping(required):
            n_removed = len(candidates)
            print(f"bisect: removed {n_removed} sections ({len(required)} remaining)", file=sys.stderr)
            return required
        if len(candidates) == 1:
            return required | candidates
        cands = sorted(candidates)
        mid = len(cands) // 2
        left, right = set(cands[:mid]), set(cands[mid:])
        if try_keeping(required | left):
            print(f"bisect: removed {len(right)} sections", file=sys.stderr)
            return reduce(left, required)
        if try_keeping(required | right):
            print(f"bisect: removed {len(left)} sections", file=sys.stderr)
            return reduce(right, required)
        req2 = reduce(left, required)
        return reduce(right, req2)

    required = {n - 1}
    candidates = set(range(n - 1))
    final = reduce(candidates, required)
    return [sections[i] for i in sorted(final)]


def minimize(script_path: Path, bisect_mode: bool = False) -> str:
    process_mem_limit = int(os.environ.get('PFM_EXEC_MEM_LIMIT', 0) or 0)
    print(f"process memory limit: {process_mem_limit} MB" if process_mem_limit > 0 else "no process memory limit", file=sys.stderr)
    script = script_path.read_text()

    rc = run_file(script_path, process_mem_limit)
    if rc is TIMEOUT_RETURNCODE:
        print("error: original script timed out (not a crash)", file=sys.stderr)
        sys.exit(1)
    if not is_crash(rc):
        print(f"error: original script did not crash (exit {rc})", file=sys.stderr)
        sys.exit(1)
    print(f"confirmed crash (exit {rc})", file=sys.stderr)

    header, sections = parse_sections(script)
    print(f"{len(sections)} sections to minimize", file=sys.stderr)

    if bisect_mode:
        sections = bisect_sections(header, sections, process_mem_limit, script_path.parent)
        print(f"bisect done: {len(sections)} sections remaining", file=sys.stderr)
    else:
        changed = True
        while changed:
            changed = False
            i = 0
            removed_this_pass = 0
            while i < len(sections):
                candidate = sections[:i] + sections[i + 1:]
                rc = run_script(assemble(header, candidate), process_mem_limit, script_dir=script_path.parent)
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
    args = sys.argv[1:]
    bisect_mode = '--bisect' in args
    args = [a for a in args if a != '--bisect']

    if len(args) != 1:
        print(f"usage: {sys.argv[0]} [--bisect] <reproducer.py>", file=sys.stderr)
        sys.exit(1)

    path = Path(args[0])
    if not path.exists():
        print(f"error: not found: {path}", file=sys.stderr)
        sys.exit(1)

    result = minimize(path, bisect_mode=bisect_mode)
    print(result)

    min_path = path.with_suffix(".min.py")
    try:
        min_path.write_text(result)
        print(f"wrote minimized script to {min_path}", file=sys.stderr)
    except OSError as e:
        print(f"warning: could not write {min_path}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()

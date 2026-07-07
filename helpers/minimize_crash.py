#!/usr/bin/env python3
"""Minimize a track-script reproducer to the smallest set of sections that still crashes."""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PYTHON = "/pfm/py/bin/python3"
MARKER_PREFIX = "# FUZZ_MARKER: "
TIMEOUT = 800
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


def chunkify(items: list[int], n_chunks: int) -> list[set[int]]:
    """Split items into n_chunks near-equal, contiguous groups."""
    n_chunks = max(1, min(n_chunks, len(items)))
    size, remainder = divmod(len(items), n_chunks)
    chunks = []
    start = 0
    for i in range(n_chunks):
        end = start + size + (1 if i < remainder else 0)
        chunks.append(set(items[start:end]))
        start = end
    return chunks


def bisect_sections(
    header: str,
    sections: list[tuple[str, str]],
    process_mem_limit: int,
    script_dir: Path,
    bisect_factor: int = 2,
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
            print(f"bisect: removed {len(candidates)} sections ({len(required)} remaining)", file=sys.stderr)
            return required
        if len(candidates) == 1:
            return required | candidates

        cands = sorted(candidates)
        chunks = chunkify(cands, bisect_factor)

        # try keeping just one chunk first: most aggressive cut, drops
        # (bisect_factor - 1) / bisect_factor of the candidates in one go.
        # Try the chunk nearest `required` (latest sections) first, so we
        # test dropping the earlier sections before the later ones.
        for chunk in reversed(chunks):
            if len(chunk) < len(cands) and try_keeping(required | chunk):
                print(f"bisect: reduced to {len(chunk)} sections", file=sys.stderr)
                return reduce(chunk, required)

        if len(chunks) > 2:
            # milder cut: drop just one chunk, keep everything else
            for i, chunk in enumerate(chunks):
                complement = set().union(*(c for j, c in enumerate(chunks) if j != i))
                if try_keeping(required | complement):
                    print(f"bisect: removed {len(chunk)} sections", file=sys.stderr)
                    return reduce(complement, required)

        # crash needs sections from multiple chunks; recurse into each independently
        req = required
        for chunk in chunks:
            req = reduce(chunk, req)
        return req

    required = {n - 1}
    candidates = set(range(n - 1))
    final = reduce(candidates, required)
    return [sections[i] for i in sorted(final)]


def minimize(
    script_path: Path,
    bisect_mode: bool = False,
    skip_initial_run: bool = False,
    bisect_factor: int = 2,
) -> str:
    process_mem_limit = int(os.environ.get('PFM_EXEC_MEM_LIMIT', 0) or 0)
    print(f"process memory limit: {process_mem_limit} MB" if process_mem_limit > 0 else "no process memory limit", file=sys.stderr)
    script = script_path.read_text()

    if skip_initial_run:
        print("skipping initial verification run", file=sys.stderr)
    else:
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
        sections = bisect_sections(
            header, sections, process_mem_limit, script_path.parent, bisect_factor=bisect_factor
        )
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
    global TIMEOUT

    parser = argparse.ArgumentParser(
        description="Minimize a track-script reproducer to the smallest set of sections that still crashes."
    )
    parser.add_argument("path", type=Path, help="path to the reproducer script")
    parser.add_argument(
        "--no-bisect", action="store_true", help="disable bisection, remove sections one at a time"
    )
    parser.add_argument(
        "--no-initial-run",
        action="store_true",
        help="skip the initial verification run (useful for very slow track scripts)",
    )
    parser.add_argument(
        "--timeout", type=int, default=TIMEOUT, help=f"per-run timeout in seconds (default: {TIMEOUT})"
    )
    parser.add_argument(
        "--bisect-factor",
        type=int,
        default=2,
        help="number of chunks to split candidates into per bisect round (default: 2)",
    )
    args = parser.parse_args()

    if args.bisect_factor < 2:
        parser.error("--bisect-factor must be at least 2")

    TIMEOUT = args.timeout

    if not args.path.exists():
        print(f"error: not found: {args.path}", file=sys.stderr)
        sys.exit(1)

    result = minimize(
        args.path,
        bisect_mode=not args.no_bisect,
        skip_initial_run=args.no_initial_run,
        bisect_factor=args.bisect_factor,
    )
    print(result)

    min_path = args.path.with_suffix(".min.py")
    try:
        min_path.write_text(result)
        print(f"wrote minimized script to {min_path}", file=sys.stderr)
    except OSError as e:
        print(f"warning: could not write {min_path}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()

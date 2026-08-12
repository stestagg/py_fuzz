#!/usr/bin/env python3
"""Minimize a track-script reproducer to the smallest set of sections that still crashes."""

import argparse
import ast
import io
import os
import subprocess
import sys
import tempfile
import tokenize
from collections import Counter
from pathlib import Path

PYTHON = "/pfm/py/bin/python3"
MARKER_PREFIX = "# FUZZ_MARKER: "
ARTIFACT_PREFIX = "# artifact: "
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


def find_artifact_name(script: str) -> str | None:
    for line in script.splitlines():
        if line.startswith(ARTIFACT_PREFIX):
            name = line[len(ARTIFACT_PREFIX):].strip()
            if name and name not in (".", "..") and not Path(name).is_absolute() and Path(name).name == name:
                return name
    return None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def find_artifact_dir(script_path: Path, script: str) -> Path | None:
    artifact_name = find_artifact_name(script)
    if artifact_name is None:
        return None

    resolved_script_path = script_path.resolve()
    for parent in (resolved_script_path.parent, *resolved_script_path.parents):
        candidate = parent / "artifacts" / artifact_name
        if candidate.is_dir():
            return candidate

    root = _repo_root()
    current_project_path = root / ".pyfuzz_project"
    if current_project_path.exists():
        try:
            project_name = current_project_path.read_text().strip()
            candidate = root / "projects" / project_name / "artifacts" / artifact_name
            if candidate.is_dir():
                return candidate
        except OSError:
            pass

    projects_dir = root / "projects"
    matches = [
        candidate
        for project_dir in projects_dir.iterdir()
        if project_dir.is_dir() and (candidate := project_dir / "artifacts" / artifact_name).is_dir()
    ] if projects_dir.is_dir() else []
    if len(matches) == 1:
        return matches[0]
    return None


def write_minimized_script(
    script_path: Path,
    script: str,
    artifact_source_script: str | None = None,
) -> Path | None:
    artifact_dir = find_artifact_dir(script_path, artifact_source_script or script)
    if artifact_dir is not None:
        artifact_path = artifact_dir / "reproducer.py"
        try:
            artifact_path.write_text(script)
            print(f"wrote minimized script to {artifact_path}", file=sys.stderr)
            return artifact_path
        except OSError as e:
            print(f"warning: could not write {artifact_path}: {e}", file=sys.stderr)

    min_path = script_path.with_suffix(".min.py")
    try:
        min_path.write_text(script)
        print(f"wrote minimized script to {min_path}", file=sys.stderr)
        return min_path
    except OSError as e:
        print(f"warning: could not write {min_path}: {e}", file=sys.stderr)
        return None


def persist_minimized(
    script_path: Path,
    script: str,
    stage: str,
    artifact_source_script: str | None = None,
) -> None:
    print(f"writing current minimized script after {stage}", file=sys.stderr)
    write_minimized_script(script_path, script, artifact_source_script=artifact_source_script)


def strip_python_comments(script: str) -> str:
    try:
        tokens = tokenize.generate_tokens(io.StringIO(script).readline)
        kept = [
            token
            for token in tokens
            if token.type != tokenize.COMMENT
        ]
        stripped = tokenize.untokenize(kept)
    except tokenize.TokenError:
        return script
    return strip_blank_lines_and_trailing_whitespace(stripped)


def strip_blank_lines_and_trailing_whitespace(script: str) -> str:
    lines = [
        line.rstrip()
        for line in script.splitlines()
        if line.strip()
    ]
    return "\n".join(lines) + ("\n" if lines else "")


def _split_section_marker(text: str) -> tuple[str, str]:
    lines = text.splitlines(keepends=True)
    if lines and lines[0].startswith(MARKER_PREFIX):
        return lines[0], "".join(lines[1:])
    return "", text


def _normalize_source(source: str) -> str:
    return source if source.endswith("\n") else source + "\n"


def _decode_python_source(raw: bytes) -> str | None:
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        return raw.decode(encoding)
    except (SyntaxError, UnicodeDecodeError):
        return None


def _compile_exec_source(stmt: ast.stmt) -> str | None:
    if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
        return None
    exec_call = stmt.value
    if not isinstance(exec_call.func, ast.Name) or exec_call.func.id not in {"exec", "eval"}:
        return None
    if len(exec_call.args) != 1 or exec_call.keywords:
        return None

    wrapper_name = exec_call.func.id
    compile_mode = "eval" if wrapper_name == "eval" else "exec"
    arg = exec_call.args[0]
    if isinstance(arg, ast.Call):
        if not isinstance(arg.func, ast.Name) or arg.func.id != "compile":
            return None
        if len(arg.args) < 3:
            return None
        mode = arg.args[2]
        if not isinstance(mode, ast.Constant) or mode.value not in {"exec", "eval"}:
            return None
        compile_mode = mode.value
        arg = arg.args[0]

    if not isinstance(arg, ast.Constant):
        return None
    if isinstance(arg.value, bytes):
        source = _decode_python_source(arg.value)
    elif isinstance(arg.value, str):
        source = arg.value
    else:
        return None

    if source is None:
        return None
    try:
        compile(source, "<unwrapped-input>", compile_mode)
        compile(_normalize_source(source), "<unwrapped-input>", "exec")
    except SyntaxError:
        return None
    return _normalize_source(source)


def _is_pass_only_handlers(try_node: ast.Try) -> bool:
    return (
        bool(try_node.handlers)
        and not try_node.orelse
        and not try_node.finalbody
        and all(len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass) for handler in try_node.handlers)
    )


def _indent_source(source: str) -> str:
    return "".join(f"    {line}" if line.strip() else line for line in source.splitlines(keepends=True))


def unwrap_compile_exec_section(text: str) -> str | None:
    marker, body = _split_section_marker(text)
    try:
        module = ast.parse(body)
    except SyntaxError:
        return None

    if len(module.body) != 1:
        return None
    stmt = module.body[0]

    source = _compile_exec_source(stmt)
    if source is not None:
        return marker + source

    if not isinstance(stmt, ast.Try) or not _is_pass_only_handlers(stmt) or len(stmt.body) != 1:
        return None

    source = _compile_exec_source(stmt.body[0])
    if source is None:
        return None
    return marker + "try:\n" + _indent_source(source) + "except:\n    pass\n"


def _dedent_one_level(lines: list[str]) -> str:
    dedented = []
    for line in lines:
        if line.startswith("    "):
            dedented.append(line[4:])
        elif line.startswith("\t"):
            dedented.append(line[1:])
        else:
            dedented.append(line)
    return "".join(dedented)


def unwrap_try_except_section(text: str) -> str | None:
    marker, body = _split_section_marker(text)
    try:
        module = ast.parse(body)
    except SyntaxError:
        return None

    if len(module.body) != 1 or not isinstance(module.body[0], ast.Try):
        return None
    try_node = module.body[0]
    if not _is_pass_only_handlers(try_node) or not try_node.body:
        return None

    lines = body.splitlines(keepends=True)
    start = try_node.body[0].lineno - 1
    end = try_node.body[-1].end_lineno
    unwrapped = _normalize_source(_dedent_one_level(lines[start:end]))
    try:
        compile(unwrapped, "<unwrapped-input>", "exec")
    except SyntaxError:
        return None
    return marker + unwrapped


def unwrap_wrappers(
    header: str,
    sections: list[tuple[str, str]],
    process_mem_limit: int,
    script_dir: Path,
) -> tuple[list[tuple[str, str]], Counter[str]]:
    transforms = [
        ("exec/eval", unwrap_compile_exec_section),
        ("try/except", unwrap_try_except_section),
    ]
    accepted_counts: Counter[str] = Counter()
    changed = True
    while changed:
        changed = False
        i = 0
        unwrapped_this_pass = 0
        while i < len(sections):
            name, text = sections[i]
            accepted = False
            for label, transform in transforms:
                transformed = transform(text)
                if transformed is None or transformed == text:
                    continue
                candidate = sections[:i] + [(name, transformed)] + sections[i + 1:]
                rc = run_script(assemble(header, candidate), process_mem_limit, script_dir=script_dir)
                if is_crash(rc):
                    print(f"  unwrapped {label} in {name}", file=sys.stderr)
                    sections = candidate
                    accepted_counts[label] += 1
                    changed = True
                    accepted = True
                    unwrapped_this_pass += 1
                    break
            if not accepted:
                i += 1
        print(
            f"unwrap pass done: {unwrapped_this_pass} accepted, {len(sections)} sections remaining",
            file=sys.stderr,
        )
    return sections, accepted_counts


def _has_compile_exec_wrapper(sections: list[tuple[str, str]]) -> bool:
    return any(unwrap_compile_exec_section(text) is not None for _, text in sections)


def _line_offsets(lines: list[str]) -> list[int]:
    offsets = [0]
    total = 0
    for line in lines:
        total += len(line)
        offsets.append(total)
    return offsets


def _statement_spans(tree: ast.AST) -> list[tuple[int, int]]:
    spans: set[tuple[int, int]] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.stmt)
            and not isinstance(node, ast.Pass)
            and getattr(node, "lineno", None) is not None
            and getattr(node, "end_lineno", None) is not None
        ):
            spans.add((node.lineno, node.end_lineno))
    return sorted(spans, key=lambda span: (span[1] - span[0], span[0]), reverse=True)


def _remove_line_span(script: str, start_line: int, end_line: int) -> str:
    lines = script.splitlines(keepends=True)
    offsets = _line_offsets(lines)
    start = offsets[start_line - 1]
    end = offsets[end_line]
    return script[:start] + script[end:]


def _valid_python(script: str) -> bool:
    try:
        compile(script, "<ast-reduced>", "exec")
    except SyntaxError:
        return False
    return True


def ast_reduce_script(
    script: str,
    script_path: Path,
    process_mem_limit: int,
) -> tuple[str, int]:
    try:
        ast.parse(script)
    except SyntaxError as e:
        print(f"ast reduce skipped: script does not parse: {e}", file=sys.stderr)
        return script, 0

    reductions = 0
    changed = True
    while changed:
        changed = False
        try:
            tree = ast.parse(script)
        except SyntaxError as e:
            print(f"ast reduce stopped: current script does not parse: {e}", file=sys.stderr)
            break

        for start_line, end_line in _statement_spans(tree):
            candidate = _remove_line_span(script, start_line, end_line)
            if candidate == script or not _valid_python(candidate):
                continue
            rc = run_script(candidate, process_mem_limit, script_dir=script_path.parent)
            if is_crash(rc):
                removed = end_line - start_line + 1
                print(
                    f"  ast removed lines {start_line}-{end_line} ({removed} lines)",
                    file=sys.stderr,
                )
                script = candidate
                reductions += 1
                changed = True
                persist_minimized(script_path, script, f"ast reduction {reductions}")
                break

        print(
            f"ast reduce pass done: {'1 accepted' if changed else '0 accepted'}",
            file=sys.stderr,
        )

    return script, reductions


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

    result = assemble(header, sections)
    persist_minimized(script_path, result, "input minimization")

    print("trying wrapper unwrap pass", file=sys.stderr)
    sections, unwrap_counts = unwrap_wrappers(header, sections, process_mem_limit, script_path.parent)
    result = assemble(header, sections)
    if sum(unwrap_counts.values()):
        persist_minimized(script_path, result, "wrapper unwrapping")

    if unwrap_counts["exec/eval"] and not _has_compile_exec_wrapper(sections):
        print("trying AST reduction pass", file=sys.stderr)
        result, ast_reductions = ast_reduce_script(result, script_path, process_mem_limit)
        print(f"ast reduce done: {ast_reductions} removals accepted", file=sys.stderr)
        if ast_reductions == 0:
            persist_minimized(script_path, result, "ast reduction")
    elif unwrap_counts["exec/eval"]:
        print("ast reduce skipped: some exec/eval wrappers remain", file=sys.stderr)
    else:
        print("ast reduce skipped: exec/eval wrapper was not stripped", file=sys.stderr)

    comment_source = result
    result = strip_python_comments(result)
    persist_minimized(
        script_path,
        result,
        "comment stripping",
        artifact_source_script=comment_source,
    )

    return result


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


if __name__ == "__main__":
    main()

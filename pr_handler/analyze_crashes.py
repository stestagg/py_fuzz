#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["openai"]
# ///

import json
import os
from pathlib import Path

SCHEMA = {
    "type": "object",
    "properties": {
        "is_cpython_error": {
            "type": "boolean",
            "description": "True if the crash indicates a CPython interpreter bug; false if it's expected behavior (e.g., OOM from infinite loop, user code error).",
        },
        "error_category": {
            "type": "string",
            "description": "1-2 hyphen-separated word category for the crash cause, chosen to group similar crashes together, e.g. 'memory-exhaustion', 'stack-overflow', 'segfault', 'infinite-loop', 'bytecode-corruption'.",
        },
        "one_line_summary": {
            "type": "string",
            "description": "A single sentence describing the crash.",
        },
        "short_summary": {
            "type": "string",
            "description": "3-5 sentences explaining what the input does, what signal/fault was observed, and the evidence-based root cause conclusion.",
        },
    },
    "required": ["is_cpython_error", "error_category", "one_line_summary", "short_summary"],
    "additionalProperties": False,
}


def build_prompt(input_code: str, gdb_output: str) -> str:
    return (
        "--- Python Input ---\n"
        f"{input_code}\n\n"
        "--- GDB Output ---\n"
        f"{gdb_output}"
    )


def analyze_crash(crash_dir: Path, client, model: str) -> None:
    summary_path = crash_dir / "llm_summary.json"
    if summary_path.exists():
        print(f"  Skipping (already analyzed): {crash_dir.name}")
        return

    input_path = crash_dir / "input"
    info_path = crash_dir / "info.txt"

    if not input_path.exists() or not info_path.exists():
        print(f"  Skipping (missing input or info.txt): {crash_dir.name}")
        return

    input_code = input_path.read_text(errors="replace")
    gdb_output = info_path.read_text(errors="replace")

    print(f"  Analyzing: {crash_dir.name}")
    response = client.responses.create(
        model=model,
        reasoning={"effort": "high"},
        instructions=(
            "You are a CPython crash analyst. "
            "Reason strictly from the data provided — do not speculate beyond what the evidence shows. "
            "Determine whether this is a CPython interpreter bug or expected behavior, "
            "classify the error, and summarize the crash."
        ),
        input=build_prompt(input_code, gdb_output),
        text={
            "format": {
                "type": "json_schema",
                "name": "crash_summary",
                "schema": SCHEMA,
                "strict": True,
            }
        },
    )

    result = json.loads(response.output_text)
    summary_path.write_text(json.dumps(result, indent=2))
    print(f"    Wrote {summary_path}")


def main() -> None:
    import sys
    analysis_dir = Path(__file__).parent.parent / "analysis"
    if not analysis_dir.exists():
        print(f"Analysis directory not found: {analysis_dir}")
        return

    model = os.environ.get("OPENAI_MODEL", "gpt-5.4")
    print(f"Using model: {model}")

    from openai import OpenAI
    client = OpenAI()

    # Optional positional arg: path to a specific crash analysis dir
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
        analyze_crash(target, client, model)
        return

    for pr_dir in sorted(analysis_dir.iterdir()):
        if not pr_dir.is_dir():
            continue
        print(f"PR: {pr_dir.name}")
        for crash_dir in sorted(pr_dir.iterdir()):
            if not crash_dir.is_dir():
                continue
            analyze_crash(crash_dir, client, model)


if __name__ == "__main__":
    main()

#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["openai", "pandas", "tabulate"]
# ///

import json
import os
from pathlib import Path

import pandas as pd
from openai import OpenAI

MERGE_SCHEMA = {
    "type": "object",
    "properties": {
        "merges": {
            "type": "array",
            "description": "List of category merge decisions. Only include groups where merging is warranted.",
            "items": {
                "type": "object",
                "properties": {
                    "canonical_category": {
                        "type": "string",
                        "description": "The existing category name that best represents the group.",
                    },
                    "categories_to_merge": {
                        "type": "array",
                        "description": "Other category names that should be merged into canonical_category (or new_name if provided).",
                        "items": {"type": "string"},
                    },
                    "new_name": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "description": "Optional new hyphen-separated name for the merged group. If provided, all entries (including canonical_category) will be renamed to this. Use when none of the existing names is ideal.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Brief explanation of why these categories represent the same underlying issue.",
                    },
                },
                "required": ["canonical_category", "categories_to_merge", "new_name", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["merges"],
    "additionalProperties": False,
}


def collect_summaries(analysis_dir: Path) -> list[dict]:
    rows = []
    for summary_path in sorted(analysis_dir.rglob("llm_summary.json")):
        try:
            data = json.loads(summary_path.read_text())
        except Exception as e:
            print(f"  Warning: could not read {summary_path}: {e}")
            continue
        rows.append(
            {
                "path": str(summary_path),
                "error_category": data.get("error_category", ""),
                "one_line_summary": data.get("one_line_summary", ""),
            }
        )
    return rows


def build_markdown_table(df: pd.DataFrame) -> str:
    display = df[["error_category", "one_line_summary"]].copy()
    return display.to_markdown(index=False, tablefmt="github")


def get_merge_recommendations(table_md: str, client, model: str) -> dict:
    print("Asking model to review error categories...")
    response = client.responses.create(
        model=model,
        reasoning={"effort": "medium"},
        instructions=(
            "You are a crash analysis expert reviewing CPython fuzzing results. "
            "You will be given a table of crash error categories and their summaries. "
            "Identify any error categories that are duplicates or represent the same underlying bug class "
            "and should be merged under a single canonical name. "
            "Only suggest merges where there is clear overlap — do not over-consolidate distinct bug types. "
            "Use concise, hyphen-separated category names. "
            "If none of the existing names in a merge group is a good fit for the merged result, "
            "set new_name to a better hyphen-separated name; otherwise set new_name to null."
        ),
        input=f"Here are the current error categories and crash summaries:\n\n{table_md}",
        text={
            "format": {
                "type": "json_schema",
                "name": "category_merges",
                "schema": MERGE_SCHEMA,
                "strict": True,
            }
        },
    )
    return json.loads(response.output_text)


def apply_merges(df: pd.DataFrame, merges: list[dict]) -> int:
    # Build a mapping: old_category -> canonical_category
    remap = {}
    for merge in merges:
        canonical = merge["canonical_category"]
        target = merge.get("new_name") or canonical
        all_cats = merge["categories_to_merge"] + [canonical]
        for old_cat in all_cats:
            if old_cat != target:
                remap[old_cat] = target
        label = f"'{canonical}' + {merge['categories_to_merge']} -> '{target}'"
        print(f"  Merge: {label} ({merge['reason']})")

    if not remap:
        print("No merges to apply.")
        return 0

    updated = 0
    for _, row in df.iterrows():
        old_cat = row["error_category"]
        if old_cat not in remap:
            continue
        new_cat = remap[old_cat]
        path = Path(row["path"])
        try:
            data = json.loads(path.read_text())
            data["error_category"] = new_cat
            path.write_text(json.dumps(data, indent=2))
            print(f"  Updated {path}: '{old_cat}' -> '{new_cat}'")
            updated += 1
        except Exception as e:
            print(f"  Error updating {path}: {e}")

    return updated


def main() -> None:
    analysis_dir = Path(__file__).parent.parent / "analysis"
    if not analysis_dir.exists():
        print(f"Analysis directory not found: {analysis_dir}")
        return

    model = os.environ.get("OPENAI_MODEL", "gpt-5.4")
    print(f"Using model: {model}")

    print("Collecting llm_summary.json files...")
    rows = collect_summaries(analysis_dir)
    if not rows:
        print("No llm_summary.json files found.")
        return

    df = pd.DataFrame(rows)
    print(f"Found {len(df)} summaries across {df['error_category'].nunique()} categories.\n")

    table_md = build_markdown_table(df)
    print("Error category table:\n")
    print(table_md)
    print()

    client = OpenAI()
    result = get_merge_recommendations(table_md, client, model)

    merges = result.get("merges", [])
    if not merges:
        print("Model found no categories to merge.")
        return

    print(f"\nModel suggested {len(merges)} merge group(s):")
    updated = apply_merges(df, merges)
    print(f"\nDone. Updated {updated} file(s).")


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tui import Finding

MAIN_MODEL = "gpt-5.4"
MINI_MODEL = "gpt-5.4-mini"


INFO_MAX_CHARS = 8192
_HALF = INFO_MAX_CHARS // 2


def _truncate_middle(text: str) -> str:
    if len(text) <= INFO_MAX_CHARS:
        return text
    omitted = len(text) - INFO_MAX_CHARS
    return text[:_HALF] + f"\n...[{omitted} chars omitted]...\n" + text[-_HALF:]


async def classify_finding(
    finding: Finding,
    prompt_template: str,
    classifications: list[str],
) -> None:
    from pydantic import create_model
    from typing import Literal
    from openai import AsyncOpenAI

    info_path = finding.analysis_dir / "info.txt"
    if info_path.exists():
        raw = info_path.read_text(errors="replace")
    else:
        raw = finding.finding_path.read_text(errors="replace")

    info_content = _truncate_middle(raw)
    rendered = prompt_template.replace("<info>", f"<info>{info_content}</info>")

    ClassificationResult = create_model(
        "ClassificationResult",
        classification=(Literal[tuple(classifications)], ...),
        reasoning=(str, ...),
    )

    client = AsyncOpenAI()
    response = await client.responses.parse(
        model=MAIN_MODEL,
        input=rendered,
        text_format=ClassificationResult,
    )
    parsed = response.output_parsed
    if parsed is None:
        raise RuntimeError("OpenAI response did not contain parsed output.")

    finding.analysis_dir.mkdir(parents=True, exist_ok=True)
    (finding.analysis_dir / "classify.json").write_text(
        json.dumps({"classification": parsed.classification, "reasoning": parsed.reasoning})
    )

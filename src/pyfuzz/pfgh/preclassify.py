import asyncio
from tqdm.auto import tqdm
from pydantic import BaseModel

from .pr import load_pr, pr_add_value
from ..llm import create_openai_client

client = create_openai_client()

class PreclassificationResponse(BaseModel):
    risk_score: int


async def preclassify_pr_score(pr_number: int) -> tuple[int, int]:
    pr_data = load_pr(pr_number)
    if "risk_score" in pr_data:
        return None
    pr_title = pr_data["title"]
    pr_body = pr_data["body"] or ""

    prompt = f'''
Consider the following pr carefully, and decide if the nature of the change might have introduced
or altered interactions that may result in unhandled crashes (memory errors, segfaults, use after free, etc) in the Python interpreter.

Keep in mind that the PR has been merged already, so the normal test suites will have passed, we are concerned with
the inherent risk class of the change, and will be assessing if extensive fuzz testing should be carried out to 
identify complex edge cases that may not be covered by the normal test suite / peer review.

Likely candidates include:
 - changes to the C codebase, especially in the core interpreter, memory management, or built-in modules
 - changes to parsing or compiled encode/decode logic
 - Changes to c extensions, especially those that are complex or that interact with external libraries

Unlikely candidates include:
 - Pure python library changes, unless they involve complex calling of c extensions
 - Changes to documentation, build scripts, or other non-code files
 - Changes that are purely refactors or formatting, without changing logic

Actual PR Details:
Title: {pr_title}
Body:
{pr_body}
---

Assess the risk factor of this pr against the criteria above, producing a score from 0 to 10
where 0 means "extremely unlikely to have introduced a crash" and 10 means "extremely suitable for fuzz testing".
'''.strip()
    result = await client.responses.parse(
        model='gpt-5.4',
        input=prompt,
        text_format=PreclassificationResponse,
    )
    response = result.output_parsed
    return (pr_number, response.risk_score)

async def preclassify_prs(pr_ids: list[int]) -> dict[int, int]:
    from .pr import load_pr
    tasks = []

    for pr_id in pr_ids:
        tasks.append(preclassify_pr_score(pr_id))
    results = await tqdm.gather(*tasks, desc="Preclassifying PRs")        
    
    results = [r for r in results if r is not None]

    for pr_id, risk_score in results:
        if risk_score == 0:
            risk_class = "none"
        elif risk_score <= 3:
            risk_class = "low"
        elif risk_score <= 6:
            risk_class = "medium"
        else:
            risk_class = "high"
        print(f"PR #{pr_id} risk score: {risk_score} ({risk_class})")
        pr_add_value(pr_id, "risk_score", risk_score)
        pr_add_value(pr_id, "risk_class", risk_class)

from .api import get_repo, get_api_url

async def describe_pr(pr_data: dict) -> str:
    repo = get_repo()
    pr_title = pr_data["title"]
    pr_body = pr_data["body"] or ""
    pr = repo.get_pull(pr_data["number"])
    pr_diff_url = pr.diff_url
    pr_diff = get_api_url(pr_diff_url)
    if len(pr_diff) > 5000:
        pr_diff = pr_diff[:2500] + "\n\n--- Diff truncated due to length ---\n\n" + pr_diff[-2500:]

    parts = [
        f"PR #{pr_data['number']}",
        f"Title: {pr_title}",
    ]
    if pr_body:
        parts.append(f"Body:\n{pr_body}")
    
    if pr_diff:
        parts.append(f"Diff:\n{pr_diff}")
        
    return "\n\n".join(parts)
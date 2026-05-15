from .api import get_github, get_repo
from .paths import gh_path
import json
import pandas as pd


async def sync_prs():
    repo = get_repo()
    print(repo)
    pr_root = gh_path("prs")
    existing_prs = {int(p.stem) for p in pr_root.glob("*.json")}
    for pr in repo.get_pulls(state='closed', sort='created', direction='desc'):
        if pr.number in existing_prs:
            break
        pr_data = {
            "number": pr.number,
            "title": pr.title,
            "user": pr.user.login,
            "created_at": pr.created_at.isoformat(),
            "closed_at": pr.closed_at.isoformat() if pr.closed_at else None,
            "merged": pr.is_merged(),
            "merge_commit_sha": pr.merge_commit_sha,
            "body": pr.body,
            "labels": [label.name for label in pr.labels],
        }
        pr_path = pr_root / f"{pr.number}.json"
        pr_path.parent.mkdir(parents=True, exist_ok=True)
        pr_path.write_text(json.dumps(pr_data, indent=2))


def load_pr(pr_number: int) -> dict:
    pr_path = gh_path("prs", f"{pr_number}.json")
    if not pr_path.exists():
        raise FileNotFoundError(f"PR data not found for PR #{pr_number}")
    pr_data = json.loads(pr_path.read_text())
    return pr_data

def load_prs() -> pd.DataFrame:
    pr_root = gh_path("prs")
    pr_files = list(pr_root.glob("*.json"))
    pr_data = []
    for pr_file in pr_files:
        pr_json = json.loads(pr_file.read_text())
        if pr_json.get("merged") and pr_json.get("user") != "miss-islington":
            pr_data.append(pr_json)
    df = pd.DataFrame(pr_data).sort_values("created_at", ascending=False)
    return df

def pr_add_value(pr_number: int, key: str, value):
    pr_data = load_pr(pr_number)
    pr_data[key] = value
    pr_path = gh_path("prs", f"{pr_number}.json")
    pr_path.write_text(json.dumps(pr_data, indent=2))
from github import Github, Auth
from pathlib import Path
import requests

_github = None
OWNER = 'python'
REPO = 'cpython'

AUTH_TOKEN = Path("~/.gh_token").expanduser().read_text().strip()

def get_github() -> Github:
    global _github
    if _github is None:
        _github = Github(auth=Auth.Token(AUTH_TOKEN))
    return _github


def get_repo():
    gh = get_github()
    return gh.get_repo(f"{OWNER}/{REPO}")

def get_api_url(url):
    req = requests.get(url, headers={"Accept": "application/vnd.github.v3+json"}, auth=(AUTH_TOKEN, 'x-oauth-basic'))
    req.raise_for_status()
    return req.text
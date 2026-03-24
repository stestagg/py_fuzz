from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterator

from .models import PRComment, PullRequestCandidate

GITHUB_API_BASE = "https://api.github.com"
DEFAULT_OWNER = "python"
DEFAULT_REPO = "cpython"
DEFAULT_PAGE_SIZE = 25

COMPILED_SOURCE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hh",
    ".hpp",
    ".m",
    ".mm",
    ".pyx",
    ".pxd",
    ".s",
    ".S",
    ".asm",
}

OPEN_PRS_QUERY = """
query($owner: String!, $repo: String!, $pageSize: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequests(
      first: $pageSize,
      after: $cursor,
      states: [OPEN],
      orderBy: {field: UPDATED_AT, direction: DESC}
    ) {
      pageInfo {
        endCursor
        hasNextPage
      }
      nodes {
        number
        title
        bodyText
        url
        headRefOid
        updatedAt
        commits(last: 1) {
          nodes {
            commit {
              statusCheckRollup {
                state
              }
            }
          }
        }
      }
    }
  }
}
"""

COMMENTS_QUERY = """
query($owner: String!, $repo: String!, $number: Int!, $commentCount: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      comments(last: $commentCount) {
        nodes {
          author {
            login
          }
          bodyText
          createdAt
        }
      }
      reviews(last: $commentCount) {
        nodes {
          author {
            login
          }
          bodyText
          submittedAt
          state
        }
      }
    }
  }
}
"""


class GitHubAPIError(RuntimeError):
    """Raised when a GitHub API request fails."""


def is_compiled_source_path(path: str) -> bool:
    lowered = path.lower()
    return any(lowered.endswith(ext.lower()) for ext in COMPILED_SOURCE_EXTENSIONS)


class GitHubClient:
    def __init__(self, token: str, owner: str = DEFAULT_OWNER, repo: str = DEFAULT_REPO) -> None:
        self.token = token
        self.owner = owner
        self.repo = repo

    @classmethod
    def from_env(cls, owner: str = DEFAULT_OWNER, repo: str = DEFAULT_REPO) -> "GitHubClient":
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not token:
            raise GitHubAPIError("Set GITHUB_TOKEN or GH_TOKEN before running the PR picker.")
        return cls(token=token, owner=owner, repo=repo)

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "pyfuzz-pr-picker",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _request_json(self, method: str, url: str, *, data: dict | None = None) -> object:
        encoded_data = None
        headers = self._headers()
        if data is not None:
            encoded_data = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=encoded_data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise GitHubAPIError(f"GitHub API request failed: {exc.code} {body}") from exc
        except urllib.error.URLError as exc:
            raise GitHubAPIError(f"GitHub API request failed: {exc}") from exc
        return json.loads(payload)

    def _graphql(self, query: str, variables: dict[str, object]) -> dict:
        payload = self._request_json(
            "POST",
            f"{GITHUB_API_BASE}/graphql",
            data={"query": query, "variables": variables},
        )
        if not isinstance(payload, dict):
            raise GitHubAPIError("GitHub GraphQL response was not an object.")
        if payload.get("errors"):
            raise GitHubAPIError(f"GitHub GraphQL error: {payload['errors']}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise GitHubAPIError("GitHub GraphQL response was missing data.")
        return data

    def _rest(self, path: str, **query: object) -> object:
        encoded_query = urllib.parse.urlencode({key: value for key, value in query.items() if value is not None})
        url = f"{GITHUB_API_BASE}{path}"
        if encoded_query:
            url = f"{url}?{encoded_query}"
        return self._request_json("GET", url)

    def _rest_paginated(self, path: str, *, per_page: int = 100) -> Iterator[dict]:
        page = 1
        while True:
            payload = self._rest(path, per_page=per_page, page=page)
            if not isinstance(payload, list):
                raise GitHubAPIError(f"Expected a list response for {path}, got {type(payload)!r}")
            if not payload:
                return
            for item in payload:
                if not isinstance(item, dict):
                    raise GitHubAPIError(f"Expected object entries for {path}, got {type(item)!r}")
                yield item
            if len(payload) < per_page:
                return
            page += 1

    def iter_open_prs_with_passing_checks(self, *, page_size: int = DEFAULT_PAGE_SIZE) -> Iterator[dict]:
        cursor: str | None = None
        while True:
            data = self._graphql(
                OPEN_PRS_QUERY,
                {
                    "owner": self.owner,
                    "repo": self.repo,
                    "pageSize": page_size,
                    "cursor": cursor,
                },
            )
            pull_requests = data["repository"]["pullRequests"]
            for node in pull_requests["nodes"]:
                rollup = None
                commits = node.get("commits", {}).get("nodes", [])
                if commits:
                    rollup = commits[0].get("commit", {}).get("statusCheckRollup")
                if rollup and rollup.get("state") == "SUCCESS":
                    yield node
            if not pull_requests["pageInfo"]["hasNextPage"]:
                return
            cursor = pull_requests["pageInfo"]["endCursor"]

    def list_changed_files(self, pr_number: int) -> list[str]:
        files: list[str] = []
        for item in self._rest_paginated(f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}/files"):
            filename = item.get("filename")
            if isinstance(filename, str):
                files.append(filename)
        return files

    def list_recent_comments(self, pr_number: int, *, limit: int) -> list[PRComment]:
        data = self._graphql(
            COMMENTS_QUERY,
            {
                "owner": self.owner,
                "repo": self.repo,
                "number": pr_number,
                "commentCount": limit,
            },
        )
        pull_request = data["repository"]["pullRequest"]
        comments: list[PRComment] = []
        for node in pull_request["comments"]["nodes"]:
            body = (node.get("bodyText") or "").strip()
            if not body:
                continue
            comments.append(
                PRComment(
                    author=(node.get("author") or {}).get("login") or "unknown",
                    created_at=node.get("createdAt") or "",
                    body=body,
                    source="comment",
                )
            )
        for node in pull_request["reviews"]["nodes"]:
            body = (node.get("bodyText") or "").strip()
            if not body:
                continue
            state = node.get("state") or "REVIEW"
            comments.append(
                PRComment(
                    author=(node.get("author") or {}).get("login") or "unknown",
                    created_at=node.get("submittedAt") or "",
                    body=body,
                    source=f"review:{state.lower()}",
                )
            )
        comments.sort(key=lambda comment: comment.created_at, reverse=True)
        return comments[:limit]

    def iter_candidate_prs(
        self,
        *,
        comments_per_pr: int = 6,
        page_size: int = DEFAULT_PAGE_SIZE,
        exclude_pr_numbers: set[int] | None = None,
    ) -> Iterator[PullRequestCandidate]:
        excluded = exclude_pr_numbers or set()
        for node in self.iter_open_prs_with_passing_checks(page_size=page_size):
            if node["number"] in excluded:
                continue
            changed_files = self.list_changed_files(node["number"])
            if not any(is_compiled_source_path(path) for path in changed_files):
                continue
            comments = self.list_recent_comments(node["number"], limit=comments_per_pr)
            yield PullRequestCandidate(
                number=node["number"],
                title=node["title"],
                body=node.get("bodyText") or "",
                url=node["url"],
                head_sha=node["headRefOid"],
                changed_files=changed_files,
                comments=comments,
            )

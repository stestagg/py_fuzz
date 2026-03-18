#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""
Find the top N PRs from python/cpython that touch source files (.py, .c, .h, etc.).

Fetches PRs in pages of CHUNK_SIZE via GraphQL, interleaving open and merged PRs
so the output contains a mix of both states.

- Open PRs:   only included when GitHub's combined check rollup is SUCCESS.
- Merged PRs: always included.

Outputs one PR number per line to stdout; progress goes to stderr.

Usage: find_prs.py <N>
"""

import json
import subprocess
import sys

OWNER = "python"
REPO  = "cpython"

CHUNK_SIZE = 10

RELEVANT_EXTENSIONS = {".py", ".c", ".h", ".cpp", ".cxx", ".cc", ".hpp"}

# GraphQL query templates — {{ / }} are escaped braces in f-strings.
_OPEN_QUERY = """\
{{
  repository(owner: "{owner}", name: "{repo}") {{
    pullRequests(first: {chunk}, states: [OPEN],
                 orderBy: {{field: CREATED_AT, direction: DESC}}{after}) {{
      pageInfo {{ endCursor hasNextPage }}
      nodes {{
        number
        commits(last: 1) {{
          nodes {{ commit {{ statusCheckRollup {{ state }} }} }}
        }}
      }}
    }}
  }}
}}"""

_MERGED_QUERY = """\
{{
  repository(owner: "{owner}", name: "{repo}") {{
    pullRequests(first: {chunk}, states: [MERGED],
                 orderBy: {{field: CREATED_AT, direction: DESC}}{after}) {{
      pageInfo {{ endCursor hasNextPage }}
      nodes {{ number }}
    }}
  }}
}}"""


def gh_json(*args: str) -> object:
    result = subprocess.run(["gh", *args], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"gh error: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return json.loads(result.stdout)


def _graphql(query: str) -> object:
    return gh_json("api", "graphql", "-f", f"query={query}")


def _open_pr_numbers():
    """Yield open PR numbers whose combined check rollup is SUCCESS, CHUNK_SIZE at a time."""
    cursor = None
    while True:
        after = f', after: "{cursor}"' if cursor else ""
        query = _OPEN_QUERY.format(owner=OWNER, repo=REPO, chunk=CHUNK_SIZE, after=after)
        conn = _graphql(query)["data"]["repository"]["pullRequests"]
        for node in conn["nodes"]:
            rollup = node["commits"]["nodes"][0]["commit"].get("statusCheckRollup")
            if rollup and rollup.get("state") == "SUCCESS":
                yield node["number"]
        if not conn["pageInfo"]["hasNextPage"]:
            return
        cursor = conn["pageInfo"]["endCursor"]


def _merged_pr_numbers():
    """Yield merged PR numbers, CHUNK_SIZE at a time."""
    cursor = None
    while True:
        after = f', after: "{cursor}"' if cursor else ""
        query = _MERGED_QUERY.format(owner=OWNER, repo=REPO, chunk=CHUNK_SIZE, after=after)
        conn = _graphql(query)["data"]["repository"]["pullRequests"]
        for node in conn["nodes"]:
            yield node["number"]
        if not conn["pageInfo"]["hasNextPage"]:
            return
        cursor = conn["pageInfo"]["endCursor"]


def has_relevant_files(pr_number: int) -> bool:
    files = gh_json(
        "api", f"repos/{OWNER}/{REPO}/pulls/{pr_number}/files",
        "--jq", "[.[].filename]",
    )
    return any(any(f.endswith(ext) for ext in RELEVANT_EXTENSIONS) for f in files)


def main() -> None:
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        print(f"Usage: {sys.argv[0]} <N>", file=sys.stderr)
        sys.exit(1)

    n = int(sys.argv[1])
    results: list[int] = []
    seen: set[int] = set()

    # Round-robin across open and merged generators.
    # Each iteration picks one candidate from each live source;
    # sources are dropped when exhausted.
    sources = [("open  ", _open_pr_numbers()), ("merged", _merged_pr_numbers())]
    alive = list(range(len(sources)))

    while alive and len(results) < n:
        next_alive = []
        for i in alive:
            label, gen = sources[i]
            try:
                pr_num = next(gen)
                next_alive.append(i)  # source still has more
            except StopIteration:
                continue  # drop exhausted source

            if pr_num in seen:
                continue
            seen.add(pr_num)

            if has_relevant_files(pr_num):
                results.append(pr_num)
                print(f"[{len(results)}/{n}] {label} #{pr_num}", file=sys.stderr)
                if len(results) >= n:
                    break

        alive = next_alive

    for num in results:
        print(num)


if __name__ == "__main__":
    main()

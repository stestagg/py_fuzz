#!/usr/bin/env bash
set -euo pipefail

DIFF_LIMIT=100
PR_NUMBER="${1:?Usage: $0 <pr-number>}"
PR_URL="https://github.com/python/cpython/pull/$PR_NUMBER"

gh pr view -c "$PR_URL"

echo ""
echo "--- diff (capped at $DIFF_LIMIT lines) ---"

diff_output=$(gh pr diff "$PR_URL")
total_lines=$(echo "$diff_output" | wc -l | tr -d ' ')

echo "$diff_output" | head -n "$DIFF_LIMIT"

if [ "$total_lines" -gt "$DIFF_LIMIT" ]; then
  echo ""
  echo "[truncated: showing $DIFF_LIMIT of $total_lines lines]"
fi

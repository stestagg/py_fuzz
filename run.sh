#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS] [PR_ID]

Fuzz CPython using AFL++. With no PR_ID, fuzzes the current main HEAD.
With a PR_ID, generates targeted fuzz cases, checks out the PR branch,
performs a clean build, and keeps output isolated under output/<pr_id>/.

Arguments:
  PR_ID                 GitHub PR number from python/cpython to test

Options:
  -o, --output DIR      Override output directory (default: output/main or
                        output/<pr_id> when PR_ID is given)
  -j, --jobs N          Number of AFL++ workers (default: 1).
                        Spawns 1 main + N-1 secondary instances shown in a
                        tmux pane grid (requires tmux when N > 1).
  -T, --timeout DUR     Stop fuzzing after DUR (e.g. 30m, 1h, 3600s). By
                        default the session runs indefinitely.
      --skip-prep       Skip gen_fuzz_cases and git checkout (set automatically
                        by run_docker.sh after doing those steps on the host)
  -h, --help            Show this help and exit

Environment:
  SKIP_PREP=1           Same as --skip-prep (used by run_docker.sh)

Examples:
  $(basename "$0")                  # fuzz main HEAD, single worker
  $(basename "$0") -j4              # fuzz with 4 AFL workers in a tmux grid
  $(basename "$0") 132345           # fuzz PR #132345
  $(basename "$0") -j4 132345       # fuzz PR with 4 workers
  $(basename "$0") -T 30m            # fuzz for 30 minutes then stop
  $(basename "$0") -j4 -T 1h 132345 # 4 workers, 1-hour session, specific PR
  $(basename "$0") -o /tmp/out       # custom output dir
EOF
}

parse_duration() {
  local s="$1"
  if   [[ "$s" =~ ^([0-9]+)h$ ]]; then echo $(( BASH_REMATCH[1] * 3600 ))
  elif [[ "$s" =~ ^([0-9]+)m$ ]]; then echo $(( BASH_REMATCH[1] * 60 ))
  elif [[ "$s" =~ ^([0-9]+)s?$ ]]; then echo "${BASH_REMATCH[1]}"
  else echo "Invalid duration '$s' — use e.g. 30m, 1h, 3600 or 3600s" >&2; return 1
  fi
}

# Defaults
PR_ID=""
OUTPUT_DIR=""
AFL_WORKERS=1
SESSION_TIMEOUT=""
SKIP_PREP="${SKIP_PREP:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage; exit 0 ;;
    -o|--output)
      OUTPUT_DIR="$2"; shift 2 ;;
    -j[0-9]*)
      AFL_WORKERS="${1#-j}"; shift ;;
    -j|--jobs)
      AFL_WORKERS="$2"; shift 2 ;;
    -T|--timeout)
      SESSION_TIMEOUT="$2"; shift 2 ;;
    --skip-prep)
      SKIP_PREP=1; shift ;;
    -*)
      echo "Unknown option: $1" >&2; echo; usage >&2; exit 1 ;;
    *)
      if [[ -z "$PR_ID" ]]; then
        PR_ID="$1"; shift
      else
        echo "Unexpected argument: $1" >&2; echo; usage >&2; exit 1
      fi ;;
  esac
done

cd "$SCRIPT_DIR"

# Validate AFL_WORKERS
[[ "$AFL_WORKERS" =~ ^[0-9]+$ ]] && [[ "$AFL_WORKERS" -ge 1 ]] || {
  echo "-j requires a positive integer, got: $AFL_WORKERS" >&2; exit 1
}

# System checks
for cmd in afl-fuzz make git; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "Missing required tool: $cmd" >&2; exit 1; }
done
if [[ "$AFL_WORKERS" -gt 1 ]]; then
  command -v tmux >/dev/null 2>&1 || { echo "tmux required for multi-worker mode (-j > 1)" >&2; exit 1; }
fi

# Resolve OUTPUT_DIR and do PR/main prep
if [[ -n "$PR_ID" ]]; then
  [[ "$PR_ID" =~ ^[0-9]+$ ]] || { echo "PR_ID must be a number, got: $PR_ID" >&2; exit 1; }
  OUTPUT_DIR="${OUTPUT_DIR:-output/${PR_ID}}"

  if [[ -z "$SKIP_PREP" ]]; then
    for cmd in gh uv; do
      command -v "$cmd" >/dev/null 2>&1 || { echo "Missing tool for PR mode: $cmd" >&2; exit 1; }
    done

    echo "==> Generating fuzz cases for PR #${PR_ID}..."
    pr_handler/gen_fuzz_cases.py "$PR_ID"

    if [[ ! -d python ]]; then
      git clone --depth=1 https://github.com/python/cpython.git python
    fi
    echo "==> Checking out PR #${PR_ID} in python/..."
    cd python
    gh pr checkout "$PR_ID" --repo python/cpython
    cd "$SCRIPT_DIR"
  fi

  echo "==> Clean build for PR #${PR_ID}..."
  make clean

else
  OUTPUT_DIR="${OUTPUT_DIR:-output/main}"

  if [[ -d python ]] && [[ -z "$SKIP_PREP" ]]; then
    echo "==> Ensuring python/ is on the default branch..."
    cd python
    git checkout main 2>/dev/null || git checkout master 2>/dev/null || true
    cd "$SCRIPT_DIR"
  fi
fi

mkdir -p build "$OUTPUT_DIR"

# Build: always build harness; add cmplog harness for multi-worker
if [[ "$AFL_WORKERS" -gt 1 ]]; then
  make harness harness-cmplog "OUTPUT_DIR=${OUTPUT_DIR}"
else
  make harness "OUTPUT_DIR=${OUTPUT_DIR}"
fi

# --- Launch AFL++ ---
HARNESS="build/fuzz_python"
HARNESS_CMPLOG="build/fuzz_python_cmplog"
TESTCASES_DIR="testcases"
DICT_FILE="dicts/python.dict"

# Resolve optional session timeout to -V <seconds>
TIMEOUT_ARG=""
if [[ -n "$SESSION_TIMEOUT" ]]; then
  TIMEOUT_SECS="$(parse_duration "$SESSION_TIMEOUT")"
  TIMEOUT_ARG="-V ${TIMEOUT_SECS}"
  echo "==> Session timeout: ${SESSION_TIMEOUT} (${TIMEOUT_SECS}s)"
fi

AFL_COMMON="-i ${TESTCASES_DIR} -o ${OUTPUT_DIR} -t 2000 -m 512 -x ${DICT_FILE}${TIMEOUT_ARG:+ ${TIMEOUT_ARG}}"

if [[ "$AFL_WORKERS" -gt 1 ]]; then
  echo "==> Launching ${AFL_WORKERS} AFL++ workers (1 main + $((AFL_WORKERS - 1)) secondary)..."

  tmux kill-session -t fuzzing 2>/dev/null || true

  # Main worker (with cmplog for better coverage feedback)
  tmux new-session -d -s fuzzing \
    "afl-fuzz ${AFL_COMMON} -M main -c ${HARNESS_CMPLOG} -- ${HARNESS}; read"

  # Secondary workers
  for i in $(seq 1 $((AFL_WORKERS - 1))); do
    tmux split-window -t fuzzing \
      "afl-fuzz ${AFL_COMMON} -S worker${i} -- ${HARNESS}; read"
  done

  # Arrange all panes in a grid
  tmux select-layout -t fuzzing tiled

  tmux attach-session -t fuzzing
else
  # shellcheck disable=SC2086
  afl-fuzz $AFL_COMMON -- "$HARNESS"
fi

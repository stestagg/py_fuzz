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
      --main-only       With -j, show only the main worker in the terminal;
                        secondary workers run in background tmux windows.
                        Useful when the pane grid gets too cramped (e.g. -j6).
  -T, --timeout DUR     Stop fuzzing after DUR (e.g. 30m, 1h, 3600s). By
                        default the session runs indefinitely.
      --skip-prep       Skip gen_fuzz_cases and git checkout (set automatically
                        by run_docker.sh after doing those steps on the host)
  -h, --help            Show this help and exit

Environment:
  SKIP_PREP=1           Same as --skip-prep (used by run_docker.sh)

Examples:
  $(basename "$0")                       # fuzz main HEAD, single worker
  $(basename "$0") -j4                   # fuzz with 4 AFL workers in a tmux grid
  $(basename "$0") -j6 --main-only       # 6 workers, show only main (others in background)
  $(basename "$0") 132345                # fuzz PR #132345
  $(basename "$0") -j4 132345            # fuzz PR with 4 workers
  $(basename "$0") -T 30m                # fuzz for 30 minutes then stop
  $(basename "$0") -j4 -T 1h 132345     # 4 workers, 1-hour session, specific PR
  $(basename "$0") -o /tmp/out           # custom output dir
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

# Defaults — OUTPUT_DIR may be pre-set by run_docker.sh via environment
PR_ID=""
OUTPUT_DIR="${OUTPUT_DIR:-}"
AFL_WORKERS=1
SESSION_TIMEOUT=""
SKIP_PREP="${SKIP_PREP:-}"
MAIN_ONLY=""

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
    --main-only)
      MAIN_ONLY=1; shift ;;
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
for cmd in afl-fuzz git; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "Missing required tool: $cmd" >&2; exit 1; }
done
if [[ "$AFL_WORKERS" -gt 1 ]]; then
  command -v tmux >/dev/null 2>&1 || { echo "tmux required for multi-worker mode (-j > 1)" >&2; exit 1; }
fi

# Resolve OUTPUT_DIR, DIST_DIR and do PR/main prep.
# DIST_DIR and OUTPUT_DIR may be injected by run_docker.sh to point at the
# container's specific mount points; only fall back to defaults if not set.
if [[ -n "$PR_ID" ]]; then
  [[ "$PR_ID" =~ ^[0-9]+$ ]] || { echo "PR_ID must be a number, got: $PR_ID" >&2; exit 1; }
  OUTPUT_DIR="${OUTPUT_DIR:-output/${PR_ID}}"
  DIST_DIR="${DIST_DIR:-dist/${PR_ID}}"

  if [[ -z "$SKIP_PREP" ]]; then
    for cmd in gh uv; do
      command -v "$cmd" >/dev/null 2>&1 || { echo "Missing tool for PR mode: $cmd" >&2; exit 1; }
    done

    echo "==> Generating fuzz cases for PR #${PR_ID}..."
    pr_handler/gen_fuzz_cases.py "$PR_ID"
  fi

else
  OUTPUT_DIR="${OUTPUT_DIR:-output/main}"
  DIST_DIR="${DIST_DIR:-dist/main}"

  if [[ -d python ]] && [[ -z "$SKIP_PREP" ]]; then
    echo "==> Ensuring python/ is on the default branch..."
    cd python
    git checkout main 2>/dev/null || git checkout master 2>/dev/null || true
    cd "$SCRIPT_DIR"
  fi
fi

mkdir -p "$OUTPUT_DIR"

# Coredump setup — only meaningful on Linux (container) where /proc exists.
#
# core_pattern write: requires root + --privileged.  In Docker, entrypoint.sh
# handles this before dropping to the fuzzer user, so the check is skipped
# here when not root.  When running run.sh directly as root, it runs here.
#
# LD_PRELOAD shim: AFL++ calls setrlimit(RLIMIT_CORE, 0) on child processes,
# suppressing cores regardless of core_pattern.  The shim no-ops those calls
# to keep the limit at unlimited.  Applied for any user.
if [[ -f /proc/sys/kernel/core_pattern ]]; then
  if [[ "$EUID" -eq 0 ]]; then
    CORES_DIR="${SCRIPT_DIR}/${OUTPUT_DIR}/cores"
    mkdir -p "$CORES_DIR"
    ulimit -c unlimited
    HELPER="${SCRIPT_DIR}/helpers/coredump_helper.sh"
    echo "|${HELPER} ${CORES_DIR} %p %e %t" > /proc/sys/kernel/core_pattern
    echo "==> Coredumps enabled -> ${CORES_DIR}/"
  fi

  SHIM_SO="${DIST_DIR}/nocorelimit.so"
  [[ -f "$SHIM_SO" ]] && export LD_PRELOAD="${SHIM_SO}${LD_PRELOAD:+:${LD_PRELOAD}}"
fi

# --- Launch AFL++ ---
HARNESS="${DIST_DIR}/fuzz_python"
HARNESS_CMPLOG="${DIST_DIR}/fuzz_python_cmplog"
# Python was built with --prefix pointing at the install dir; PYTHONHOME lets
# the embedded interpreter find its stdlib if the binary has been relocated
# (e.g. mounted at a different path inside Docker).
export PYTHONHOME="${DIST_DIR}/install"
TESTCASES_DIR="${TESTCASES_DIR:-testcases}"
DICT_FILE="${DICT_FILE:-dicts/python.dict}"

# Resolve optional session timeout to -V <seconds>
TIMEOUT_ARG=""
if [[ -n "$SESSION_TIMEOUT" ]]; then
  TIMEOUT_SECS="$(parse_duration "$SESSION_TIMEOUT")"
  TIMEOUT_ARG="-V ${TIMEOUT_SECS}"
  echo "==> Session timeout: ${SESSION_TIMEOUT} (${TIMEOUT_SECS}s)"
fi

# export AFL_PERSISTENT_RECORD="${AFL_PERSISTENT_RECORD:-10}"

AFL_COMMON="-i ${TESTCASES_DIR} -o ${OUTPUT_DIR} -t 5000 -m 512 -x ${DICT_FILE}${TIMEOUT_ARG:+ ${TIMEOUT_ARG}}"

if [[ "$AFL_WORKERS" -gt 1 ]]; then
  echo "==> Launching ${AFL_WORKERS} AFL++ workers (1 main + $((AFL_WORKERS - 1)) secondary)..."

  tmux kill-session -t fuzzing &>/dev/null || true

  # Wrap afl-fuzz so the pane stays open on failure — otherwise the session
  # can die before attach-session runs and tmux exits "no server running".
  afl_wrap() { local cmd="$1"; printf '%s; echo; read -rp "press enter to close"\n' "$cmd"; }

  # Main worker in window 0 (with cmplog for better coverage feedback)
  tmux new-session -d -s fuzzing \
    "$(afl_wrap "afl-fuzz ${AFL_COMMON} -M main -c ${HARNESS_CMPLOG} -- ${HARNESS}")"

  if [[ -n "$MAIN_ONLY" ]]; then
    # Secondary workers each get their own background tmux window
    for i in $(seq 1 $((AFL_WORKERS - 1))); do
      tmux new-window -t fuzzing \
        "$(afl_wrap "afl-fuzz ${AFL_COMMON} -S worker${i} -- ${HARNESS}")"
    done
    # Return focus to the main worker's window before attaching
    tmux select-window -t fuzzing:0
    echo "==> Secondary workers running in background tmux windows (Ctrl-b n to cycle)."
  else
    # Secondary workers as split panes in the same window
    for i in $(seq 1 $((AFL_WORKERS - 1))); do
      tmux split-window -t fuzzing \
        "$(afl_wrap "afl-fuzz ${AFL_COMMON} -S worker${i} -- ${HARNESS}")"
    done
    # Arrange all panes in a grid
    tmux select-layout -t fuzzing tiled
  fi

  tmux attach-session -t fuzzing
else
  # shellcheck disable=SC2086
  afl-fuzz $AFL_COMMON -- "$HARNESS"
fi

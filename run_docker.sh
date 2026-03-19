#!/usr/bin/env bash
set -euo pipefail

# Runs the AFL++ fuzz loop inside a locked-down Docker container (Dockerfile.run).
# Assumes dist/<pr_id>/ has already been populated by build_docker.sh.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_IMAGE="py-fuzz:run"

usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS] [PR_ID]

Launches AFL++ inside a locked-down Docker container (${RUN_IMAGE}).
dist/<pr_id>/, testcases/, and dicts/ are mounted read-only; only
output/<pr_id>/ is writable.  Run build_docker.sh first to populate dist/.

Arguments:
  PR_ID                 GitHub PR number (must match what build_docker.sh built)

Options:
  -o, --output DIR      Override output directory
  -j, --jobs N          Number of AFL++ workers; also sets --cpus N on the
                        container when N > 1
  -T, --timeout DUR     Stop fuzzing after DUR (e.g. 30m, 1h, 3600s)
      --build           Force a rebuild of the ${RUN_IMAGE} Docker image
      --shell           Launch an interactive bash shell instead of fuzzing
      --test-crash      Set FUZZ_TEST_CRASH=1; harness aborts on input 'fuzztestcrash'
                        (use to verify end-to-end crash detection is working)
      --trace-dlopen    Replay all testcases through fuzz_python with an
                        LD_PRELOAD dlopen hook; writes dlopen_files.txt to the
                        project root
                        afl_preloads.txt at the project root lists container
                        paths of .so files to inject via AFL_PRELOAD (one per
                        line; # comments and blank lines ignored)
  -h, --help            Show this help and exit

Examples:
  $(basename "$0")                   # fuzz dist/main/
  $(basename "$0") 132345            # fuzz dist/132345/
  $(basename "$0") -j4 -T 30m 12345 # 4 workers, 30-minute session
  $(basename "$0") --shell 132345    # interactive shell with mounts in place
  $(basename "$0") --test-crash      # verify crash detection end-to-end
EOF
}

PR_ID=""
AFL_WORKERS=1
FORCE_IMAGE=0
SHELL_MODE=0
TEST_CRASH=0
TRACE_DLOPEN=0
FORWARD_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage; exit 0 ;;
    --build)
      FORCE_IMAGE=1; shift ;;
    --shell)
      SHELL_MODE=1; shift ;;
    --test-crash)
      TEST_CRASH=1; shift ;;
    --trace-dlopen)
      TRACE_DLOPEN=1; shift ;;
    -o|--output|-T|--timeout)
      FORWARD_ARGS+=("$1" "$2"); shift 2 ;;
    -j[0-9]*)
      AFL_WORKERS="${1#-j}"
      FORWARD_ARGS+=("$1"); shift ;;
    -j|--jobs)
      AFL_WORKERS="$2"
      FORWARD_ARGS+=("$1" "$2"); shift 2 ;;
    -*)
      echo "Unknown option: $1" >&2; echo; usage >&2; exit 1 ;;
    *)
      if [[ -z "$PR_ID" ]]; then
        PR_ID="$1"
        FORWARD_ARGS+=("$1")
        shift
      else
        echo "Unexpected argument: $1" >&2; echo; usage >&2; exit 1
      fi ;;
  esac
done

# Build the run image if missing or --build was passed
if [[ "$FORCE_IMAGE" -eq 1 ]] || ! docker image inspect "$RUN_IMAGE" >/dev/null 2>&1; then
  echo "==> Building Docker image ${RUN_IMAGE} (Dockerfile.run)..."
  docker build -t "$RUN_IMAGE" -f "$SCRIPT_DIR/Dockerfile.run" "$SCRIPT_DIR"
fi

DIST_ID="${PR_ID:-main}"

# Verify dist/ was built before trying to fuzz
[[ -f "$SCRIPT_DIR/dist/${DIST_ID}/fuzz_python" ]] || {
  echo "dist/${DIST_ID}/fuzz_python not found — run build_docker.sh ${PR_ID} first" >&2
  exit 1
}

# Auto-detect ASAN build from marker file left by build.sh
ASAN=0
[[ -f "$SCRIPT_DIR/dist/${DIST_ID}/.asan" ]] && ASAN=1
[[ "$ASAN" -eq 1 ]] && echo "==> ASAN build detected (dist/${DIST_ID}/.asan)"

# Ensure the output directory exists on the host before mounting it
OUTPUT_DIR="$SCRIPT_DIR/output/${DIST_ID}"
mkdir -p "$OUTPUT_DIR"

CPU_ARGS=()
[[ "$AFL_WORKERS" -gt 1 ]] && CPU_ARGS=(--cpus "$AFL_WORKERS")

TEST_CRASH_ARGS=()
[[ "$TEST_CRASH" -eq 1 ]] && TEST_CRASH_ARGS=(-e FUZZ_TEST_CRASH=1)

ASAN_ARGS=()
[[ "$ASAN" -eq 1 ]] && ASAN_ARGS=(
  -e "ASAN_OPTIONS=abort_on_error=1:detect_leaks=0:symbolize=0"
  -e "AFL_USE_ASAN=1"
)

AFL_PRELOAD_ARGS=()
if [[ -f "$SCRIPT_DIR/afl_preloads.txt" ]]; then
  preload_val=""
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    preload_val="${preload_val:+${preload_val}:}${line}"
  done < "$SCRIPT_DIR/afl_preloads.txt"
  if [[ -n "$preload_val" ]]; then
    AFL_PRELOAD_ARGS=(-e "AFL_PRELOAD=${preload_val}")
    echo "==> AFL_PRELOAD: ${preload_val}"
  fi
fi

TRACE_DLOPEN_ARGS=()
if [[ "$TRACE_DLOPEN" -eq 1 ]]; then
  touch "$SCRIPT_DIR/dlopen_files.txt"   # must exist for bind-mount
  TRACE_DLOPEN_ARGS=(
    -v "$SCRIPT_DIR/trace_inputs.sh:/src/trace_inputs.sh:ro"
    -v "$SCRIPT_DIR/dlopen_files.txt:/dlopen_files.txt"
  )
fi

# Mounts:
#   dist/{id}  — read-only  (harness binaries; must not be corrupted)
#   testcases  — read-only  (AFL++ corpus)
#   dicts      — read-only  (AFL++ dictionaries)
#   helpers    — read-only  (coredump_helper.sh, used by entrypoint.sh)
#   output/{id}— read-write (AFL++ findings; the only place the fuzzer writes)
#   run.py     — read-only  (fuzz loop script)
DOCKER_COMMON=(
  docker run --rm -it
  --privileged
  "${CPU_ARGS[@]+"${CPU_ARGS[@]}"}"
  -v "$SCRIPT_DIR/dist/${DIST_ID}:/dist:ro"
  -v "$SCRIPT_DIR/testcases:/testcases:ro"
  -v "$SCRIPT_DIR/dicts:/dicts:ro"
  -v "$SCRIPT_DIR/helpers:/helpers:ro"
  -v "$OUTPUT_DIR:/output"
  -v "$SCRIPT_DIR/run.py:/src/run.py:ro"
  -w /src
  -e DIST_DIR=/dist
  -e OUTPUT_DIR=/output
  -e TESTCASES_DIR=/testcases
  -e DICT_FILE=/dicts/python.dict
  "${TEST_CRASH_ARGS[@]+"${TEST_CRASH_ARGS[@]}"}"
  "${ASAN_ARGS[@]+"${ASAN_ARGS[@]}"}"
  "${TRACE_DLOPEN_ARGS[@]+"${TRACE_DLOPEN_ARGS[@]}"}"
  "${AFL_PRELOAD_ARGS[@]+"${AFL_PRELOAD_ARGS[@]}"}"
  "$RUN_IMAGE"
)

if [[ "$SHELL_MODE" -eq 1 ]]; then
  "${DOCKER_COMMON[@]}" bash
elif [[ "$TRACE_DLOPEN" -eq 1 ]]; then
  "${DOCKER_COMMON[@]}" /src/trace_inputs.sh
else
  "${DOCKER_COMMON[@]}" /src/run.py "${FORWARD_ARGS[@]+"${FORWARD_ARGS[@]}"}"
fi

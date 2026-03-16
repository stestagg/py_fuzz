#!/usr/bin/env bash
set -euo pipefail

# Docker wrapper — runs run.sh inside a custom AFL++ image built from the
# local Dockerfile.  PR-mode prep (gen_fuzz_cases and git checkout) runs on
# the host since the container lacks gh/uv.  The repo directory is
# bind-mounted so the in-container build picks up local changes immediately.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_IMAGE="py-fuzz:latest"

usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS] [PR_ID]

Docker wrapper for run.sh. All options are passed through to run.sh inside
the container. PR-mode prep (fuzz case generation and git checkout) runs on
the host before Docker starts.

The local Dockerfile is used by default; the image is built automatically on
first use and can be rebuilt with --build.

Arguments:
  PR_ID                 GitHub PR number from python/cpython to test

Options:
  -o, --output DIR      Override output directory
  -j, --jobs N          Number of AFL++ workers; also sets --cpus N on the
                        container when N > 1
  -T, --timeout DUR     Stop fuzzing after DUR (e.g. 30m, 1h, 3600s)
      --build           Force a rebuild of the local Docker image
  -i, --image IMAGE     Use a custom image instead of the local one
                        (skips auto-build; default: ${LOCAL_IMAGE})
  -h, --help            Show this help and exit

Environment:
  AFL_IMAGE             Same as --image

Examples:
  $(basename "$0")                   # fuzz main HEAD (builds image if needed)
  $(basename "$0") --build           # rebuild image then fuzz
  $(basename "$0") -j4 132345        # fuzz PR with 4 workers (4 CPUs exposed)
  $(basename "$0") -j4 -T 30m 12345  # 4 workers, stop after 30 minutes
  $(basename "$0") -i my/afl 12345   # use a custom image
EOF
}

PR_ID=""
IMAGE="${AFL_IMAGE:-${LOCAL_IMAGE}}"
AFL_WORKERS=1
FORCE_BUILD=0
FORWARD_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage; exit 0 ;;
    -i|--image)
      IMAGE="$2"; shift 2 ;;
    --build)
      FORCE_BUILD=1; shift ;;
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

# Build the local image if it's the one we're using and it's missing or stale
if [[ "$IMAGE" == "$LOCAL_IMAGE" ]]; then
  if [[ "$FORCE_BUILD" -eq 1 ]] || ! docker image inspect "$LOCAL_IMAGE" >/dev/null 2>&1; then
    echo "==> Building Docker image ${LOCAL_IMAGE}..."
    docker build -t "$LOCAL_IMAGE" "$SCRIPT_DIR"
  fi
fi

# PR-mode host prep — gh and uv are not available inside the container
if [[ -n "$PR_ID" ]]; then
  for cmd in gh uv; do
    command -v "$cmd" >/dev/null 2>&1 || { echo "Missing tool for PR mode: $cmd" >&2; exit 1; }
  done

  echo "==> Generating fuzz cases for PR #${PR_ID}..."
  "$SCRIPT_DIR/pr_handler/gen_fuzz_cases.py" "$PR_ID"

  if [[ ! -d "$SCRIPT_DIR/python" ]]; then
    git clone --depth=1 https://github.com/python/cpython.git "$SCRIPT_DIR/python"
  fi
  echo "==> Checking out PR #${PR_ID} in python/..."
  cd "$SCRIPT_DIR/python"
  gh pr checkout "$PR_ID" --repo python/cpython
  cd "$SCRIPT_DIR"
fi

echo "Using image: $IMAGE"
echo "Mounting:    $SCRIPT_DIR -> /src"

# Expose CPUs when running multiple workers
CPU_ARGS=()
if [[ "$AFL_WORKERS" -gt 1 ]]; then
  CPU_ARGS=(--cpus "$AFL_WORKERS")
fi

docker run --rm -it \
  --privileged \
  "${CPU_ARGS[@]+"${CPU_ARGS[@]}"}" \
  -v "$SCRIPT_DIR:/src" \
  -w /src \
  -e SKIP_PREP=1 \
  -e OPENAI_API_KEY \
  "$IMAGE" \
  bash ./run.sh "${FORWARD_ARGS[@]+"${FORWARD_ARGS[@]}"}"

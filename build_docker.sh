#!/usr/bin/env bash
set -euo pipefail

# Builds the fuzz harness and instrumented CPython inside a Docker container
# (Dockerfile.build), populating dist/<pr_id>/ on the host via a bind-mount.
# Host prep (fuzz case generation and git checkout) runs here since gh/uv are
# not available inside the container.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_IMAGE="py-fuzz:build"

usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS] [PR_ID]

Runs build.sh inside a Docker container (${BUILD_IMAGE}) to populate
dist/<pr_id>/ (or dist/main/) with the fuzz harness and instrumented CPython.
PR-mode prep (fuzz case generation and git checkout) runs on the host first.

Arguments:
  PR_ID           GitHub PR number from python/cpython to build against

Options:
  --force         Pass --force to build.sh (rebuild everything)
  --build         Force a rebuild of the ${BUILD_IMAGE} Docker image
  -h, --help      Show this help and exit

Examples:
  $(basename "$0")            # build from current HEAD -> dist/main/
  $(basename "$0") 132345     # prep + build for PR -> dist/132345/
  $(basename "$0") --force    # force full rebuild inside container
EOF
}

PR_ID=""
FORCE=0
FORCE_IMAGE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)    usage; exit 0 ;;
    --force)      FORCE=1; shift ;;
    --build)      FORCE_IMAGE=1; shift ;;
    -*)           echo "Unknown option: $1" >&2; echo; usage >&2; exit 1 ;;
    *)
      if [[ -z "$PR_ID" ]]; then
        PR_ID="$1"; shift
      else
        echo "Unexpected argument: $1" >&2; echo; usage >&2; exit 1
      fi ;;
  esac
done

# Build the Docker image if missing or --build was passed
if [[ "$FORCE_IMAGE" -eq 1 ]] || ! docker image inspect "$BUILD_IMAGE" >/dev/null 2>&1; then
  echo "==> Building Docker image ${BUILD_IMAGE} (Dockerfile.build)..."
  docker build -t "$BUILD_IMAGE" -f "$SCRIPT_DIR/Dockerfile.build" "$SCRIPT_DIR"
fi

# Host prep — gh and uv are not available inside the container
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

# Assemble build.sh args: PR_ID if given, --skip-checkout (done above), --force if requested
BUILD_ARGS=(--skip-checkout)
[[ -n "$PR_ID" ]] && BUILD_ARGS=("$PR_ID" "${BUILD_ARGS[@]}")
[[ "$FORCE" -eq 1 ]] && BUILD_ARGS+=(--force)

echo "==> Building dist/ inside container (image: ${BUILD_IMAGE})..."
docker run --rm \
  -v "$SCRIPT_DIR:/src" \
  -w /src \
  "$BUILD_IMAGE" \
  bash ./build.sh "${BUILD_ARGS[@]}"

#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_IMAGE="py-fuzz:latest"

usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS] <PR_ID|main> [CORE_FILE]

Load a core file into gdb inside the debug Docker image with the right
binary loaded automatically.

Arguments:
  PR_ID|main   PR number (uses dist/<pr_id>/ and output/<pr_id>/cores/)
               or "main" (uses dist/main/ and output/main/cores/)
  CORE_FILE    Path to a specific core file (optional).
               If omitted, the most recently modified core in
               output/<pr_id>/cores/ is used.

Options:
  -i, --image IMAGE   Use a custom Docker image (default: ${LOCAL_IMAGE})
  -h, --help          Show this help and exit

Examples:
  $(basename "$0") main                        # debug most recent core for main
  $(basename "$0") 132345                      # debug most recent core for PR
  $(basename "$0") 132345 output/132345/cores/core.fuzz_python.1234.1700000000
EOF
}

IMAGE="${LOCAL_IMAGE}"
PR_ID=""
CORE_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -i|--image) IMAGE="$2"; shift 2 ;;
    -*)
      echo "Unknown option: $1" >&2; echo; usage >&2; exit 1 ;;
    *)
      if [[ -z "$PR_ID" ]]; then
        PR_ID="$1"; shift
      elif [[ -z "$CORE_FILE" ]]; then
        CORE_FILE="$1"; shift
      else
        echo "Unexpected argument: $1" >&2; echo; usage >&2; exit 1
      fi ;;
  esac
done

if [[ -z "$PR_ID" ]]; then
  echo "Error: PR_ID or 'main' is required." >&2; echo; usage >&2; exit 1
fi

if [[ "$PR_ID" == "main" ]]; then
  DIST_DIR="dist/main"
  CORES_DIR="output/main/cores"
else
  [[ "$PR_ID" =~ ^[0-9]+$ ]] || { echo "PR_ID must be a number or 'main', got: $PR_ID" >&2; exit 1; }
  DIST_DIR="dist/${PR_ID}"
  CORES_DIR="output/${PR_ID}/cores"
fi

BINARY="${SCRIPT_DIR}/${DIST_DIR}/fuzz_python"

if [[ ! -f "$BINARY" ]]; then
  echo "Error: binary not found at ${BINARY}" >&2
  echo "Have you built it? (make harness DIST_DIR=${DIST_DIR})" >&2
  exit 1
fi

# Resolve core file
if [[ -z "$CORE_FILE" ]]; then
  CORES_ABS="${SCRIPT_DIR}/${CORES_DIR}"
  if [[ ! -d "$CORES_ABS" ]]; then
    echo "Error: cores directory not found: ${CORES_ABS}" >&2
    exit 1
  fi
  # Pick the most recently modified core file
  CORE_FILE="$(ls -t "${CORES_ABS}"/core.* 2>/dev/null | head -1)"
  if [[ -z "$CORE_FILE" ]]; then
    echo "Error: no core files found in ${CORES_ABS}" >&2
    exit 1
  fi
  echo "==> Using most recent core: ${CORE_FILE}"
fi

# Make CORE_FILE absolute
[[ "$CORE_FILE" = /* ]] || CORE_FILE="${SCRIPT_DIR}/${CORE_FILE}"

if [[ ! -f "$CORE_FILE" ]]; then
  echo "Error: core file not found: ${CORE_FILE}" >&2
  exit 1
fi

# Paths as seen from inside the container (/src is the bind-mount of SCRIPT_DIR)
CONTAINER_BINARY="/src/${DIST_DIR}/fuzz_python"
CONTAINER_CORE="/src/${CORE_FILE#"${SCRIPT_DIR}/"}"

# Core files record binary paths via Docker Desktop's virtioFS mount:
#   /run/host_virtiofs<host_path>  →  /src  (the bind-mount of SCRIPT_DIR)
# Tell gdb to substitute so it can find the binary, shared libs, and the
# nocorelimit.so shim that are referenced in the core's file-mapping notes.
VIRTIOFS_PREFIX="/run/host_virtiofs${SCRIPT_DIR}"

echo "==> Image:   ${IMAGE}"
echo "==> Binary:  ${CONTAINER_BINARY}"
echo "==> Core:    ${CONTAINER_CORE}"
echo ""

docker run --rm -it \
  -v "${SCRIPT_DIR}:/src" \
  -w /src \
  "$IMAGE" \
  bash -c "
    mkdir -p \"$(dirname "${VIRTIOFS_PREFIX}")\" && \
    ln -sfn /src \"${VIRTIOFS_PREFIX}\" && \
    gdb \"${CONTAINER_BINARY}\" \"${CONTAINER_CORE}\"
  "

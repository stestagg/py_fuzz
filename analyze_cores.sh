#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_IMAGE="py-fuzz:debug"

usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS] <PR_ID|main>

Run gdb in batch mode on every core file for a PR and write crash info +
stack traces to analysis/cores/<PR_ID>/core.*.txt.

Arguments:
  PR_ID|main   PR number (uses dist/<pr_id>/ and output/<pr_id>/cores/)
               or "main" (uses dist/main/ and output/main/cores/)

Options:
  -i, --image IMAGE   Use a custom Docker image (default: py-fuzz:debug)
  -h, --help          Show this help and exit

Examples:
  $(basename "$0") 132345
  $(basename "$0") main
  $(basename "$0") -i my-image:dev 132345
EOF
}

IMAGE="${LOCAL_IMAGE}"
PR_ID=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -i|--image) IMAGE="$2"; shift 2 ;;
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

CORES_ABS="${SCRIPT_DIR}/${CORES_DIR}"
if [[ ! -d "$CORES_ABS" ]]; then
  echo "Error: cores directory not found: ${CORES_ABS}" >&2
  exit 1
fi

CORE_FILES=("${CORES_ABS}"/core.*)
if [[ ! -f "${CORE_FILES[0]}" ]]; then
  echo "Error: no core files found in ${CORES_ABS}" >&2
  exit 1
fi

ANALYSIS_DIR="${SCRIPT_DIR}/analysis/cores/${PR_ID}"
mkdir -p "${ANALYSIS_DIR}"

CONTAINER_BINARY="/src/${DIST_DIR}/fuzz_python"
VIRTIOFS_PREFIX="/run/host_virtiofs${SCRIPT_DIR}"

echo "==> Image:        ${IMAGE}"
echo "==> Binary:       ${CONTAINER_BINARY}"
echo "==> Cores found:  ${#CORE_FILES[@]}"
echo "==> Output dir:   ${ANALYSIS_DIR}"
echo ""

for CORE_FILE in "${CORE_FILES[@]}"; do
  CORE_NAME="$(basename "${CORE_FILE}")"
  OUT_FILE="${ANALYSIS_DIR}/${CORE_NAME}.txt"
  CONTAINER_CORE="/src/${CORE_FILE#"${SCRIPT_DIR}/"}"

  echo "==> Analyzing ${CORE_NAME} ..."

  docker run --rm \
    -v "${SCRIPT_DIR}:/src" \
    -v "${SCRIPT_DIR}/${DIST_DIR}/:/dist" \
    -w /src \
    "$IMAGE" \
    bash -c "
      mkdir -p \"$(dirname "${VIRTIOFS_PREFIX}")\" && \
      ln -sfn /src \"${VIRTIOFS_PREFIX}\" && \
      gdb --batch \
        -ex 'set pagination off' \
        -ex 'echo === SIGNAL INFO ===\n' \
        -ex 'p $'"_siginfo" \
        -ex 'echo === INSTRUCTIONS AT CRASH ===\n' \
        -ex 'x/5i $'"pc" \
        -ex 'echo === REGISTERS ===\n' \
        -ex 'info registers' \
        -ex 'echo === BACKTRACE ===\n' \
        -ex 'bt full' \
        \"${CONTAINER_BINARY}\" \"${CONTAINER_CORE}\" 2>&1
    " > "${OUT_FILE}"

  echo "    Written: ${OUT_FILE}"
done

echo ""
echo "Done. Results in ${ANALYSIS_DIR}"

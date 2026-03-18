#!/usr/bin/env bash
set -euo pipefail

# Build script — compiles everything needed for a fuzz run and populates
# dist/<pr_id>/ (or dist/main/).  Acts like make: skips steps whose outputs
# are already up to date unless --force is passed.
#
# Outputs written to dist/<pr_id>/:
#   fuzz_python        — AFL++-instrumented harness
#   fuzz_python_cmplog — cmplog variant (needed for multi-worker runs)
#   nocorelimit.so     — LD_PRELOAD shim that prevents AFL++ from zeroing RLIMIT_CORE
#   install/           — instrumented CPython install

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS] [PR_ID]

Build the fuzz harness and an AFL++-instrumented CPython into dist/<pr_id>/
(or dist/main/ when no PR_ID is given).  Only rebuilds stale outputs unless
--force is passed.

Arguments:
  PR_ID         GitHub PR number (python/cpython).  Checks out that branch
                before building.  Omit to build from the current python/ HEAD.

Options:
  --force           Rebuild everything, even if outputs appear up to date
  --skip-checkout   Skip the 'gh pr checkout' step (use when the source tree
                    is already on the right branch, e.g. inside Docker)
  -h, --help        Show this help and exit

Examples:
  $(basename "$0")          # build from current HEAD -> dist/main/
  $(basename "$0") 132345   # check out PR, build -> dist/132345/
  $(basename "$0") --force  # force full rebuild
EOF
}

# ── Argument parsing ──────────────────────────────────────────────────────────
PR_ID=""
FORCE=0
SKIP_CHECKOUT=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)        usage; exit 0 ;;
    --force)          FORCE=1; shift ;;
    --skip-checkout)  SKIP_CHECKOUT=1; shift ;;
    -*)               echo "Unknown option: $1" >&2; echo; usage >&2; exit 1 ;;
    *)
      if [[ -z "$PR_ID" ]]; then
        PR_ID="$1"; shift
      else
        echo "Unexpected argument: $1" >&2; echo; usage >&2; exit 1
      fi ;;
  esac
done

if [[ -n "$PR_ID" ]]; then
  [[ "$PR_ID" =~ ^[0-9]+$ ]] || { echo "PR_ID must be a number, got: $PR_ID" >&2; exit 1; }
fi

# ── Paths ─────────────────────────────────────────────────────────────────────
DIST_DIR="${SCRIPT_DIR}/dist/${PR_ID:-main}"
PREFIX="${DIST_DIR}/install"
PYTHON_SRC="${SCRIPT_DIR}/python"
HARNESS_SRC="${SCRIPT_DIR}/harness/fuzz_python.c"
SHIM_SRC="${SCRIPT_DIR}/helpers/nocorelimit.c"

HARNESS="${DIST_DIR}/fuzz_python"
HARNESS_CMPLOG="${DIST_DIR}/fuzz_python_cmplog"
SHIM_SO="${DIST_DIR}/nocorelimit.so"

# ── Tooling ───────────────────────────────────────────────────────────────────
AFL_CC="$(command -v afl-clang-lto  2>/dev/null || \
          command -v afl-clang-fast 2>/dev/null || \
          command -v afl-gcc-fast   2>/dev/null || \
          echo afl-clang-fast)"
NPROC="$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)"

for cmd in make gcc "$AFL_CC"; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "Missing required tool: $cmd" >&2; exit 1; }
done

# ── Staleness helper ──────────────────────────────────────────────────────────
# needs_rebuild TARGET [DEP...] — returns 0 (true) if target should be rebuilt.
# Rebuilds when: --force is set, target is missing, or any dep is newer than target.
needs_rebuild() {
  local target="$1"; shift
  [[ "$FORCE" -eq 1 ]] && return 0
  [[ ! -e "$target" ]] && return 0
  for dep in "$@"; do
    [[ -e "$dep" && "$dep" -nt "$target" ]] && return 0
  done
  return 1
}

cd "$SCRIPT_DIR"
mkdir -p "$DIST_DIR"

# ── Ensure CPython source exists ──────────────────────────────────────────────
if [[ ! -d "$PYTHON_SRC" ]]; then
  echo "==> Cloning CPython..."
  git clone --depth=1 https://github.com/python/cpython.git "$PYTHON_SRC"
fi

# ── PR checkout ───────────────────────────────────────────────────────────────
if [[ -n "$PR_ID" ]] && [[ "$SKIP_CHECKOUT" -eq 0 ]]; then
  command -v gh >/dev/null 2>&1 || { echo "gh is required for PR mode (or pass --skip-checkout)" >&2; exit 1; }
  echo "==> Checking out PR #${PR_ID}..."
  cd "$PYTHON_SRC"
  gh pr checkout "$PR_ID" --repo python/cpython
  cd "$SCRIPT_DIR"
fi

# ── Build instrumented CPython ────────────────────────────────────────────────
if needs_rebuild "${PREFIX}/bin/python3"; then
  echo "==> Building instrumented CPython -> ${PREFIX}/"
  cd "$PYTHON_SRC"
  CC="$AFL_CC" CFLAGS="-O2 -g" \
    ax_cv_c_float_words_bigendian=no \
    ./configure \
      --prefix="$PREFIX" \
      --disable-shared \
      --without-pymalloc \
      2>&1 | tee "${DIST_DIR}/configure.log"
  # PYTHONPATH=Lib: the AFL-instrumented ./python can't find encodings when the
  # build system runs it to generate frozen modules (prefix not installed yet).
  # Pointing it at the in-tree Lib/ lets the partial interpreter bootstrap itself.
  PYTHONPATH="$(pwd)/Lib" make -j"$NPROC" 2>&1 | tee "${DIST_DIR}/build.log"
  make install     2>&1 | tee "${DIST_DIR}/install.log"
  cd "$SCRIPT_DIR"
else
  echo "==> Python already built — skipping (--force to rebuild)"
fi

# ── Build harness binaries ────────────────────────────────────────────────────
PYTHON_CONFIG="${PREFIX}/bin/python3-config"
# shellcheck disable=SC2046
PYTHON_CFLAGS="$("$PYTHON_CONFIG" --includes)"
# shellcheck disable=SC2046
PYTHON_LDFLAGS="$("$PYTHON_CONFIG" --ldflags --embed)"

if needs_rebuild "$HARNESS" "$HARNESS_SRC" "$PYTHON_CONFIG"; then
  echo "==> Building harness -> ${HARNESS}"
  # shellcheck disable=SC2086
  "$AFL_CC" -O2 -g $PYTHON_CFLAGS "$HARNESS_SRC" $PYTHON_LDFLAGS -o "$HARNESS"
else
  echo "==> Harness up to date — skipping"
fi

if needs_rebuild "$HARNESS_CMPLOG" "$HARNESS_SRC" "$PYTHON_CONFIG"; then
  echo "==> Building cmplog harness -> ${HARNESS_CMPLOG}"
  # shellcheck disable=SC2086
  AFL_LLVM_CMPLOG=1 "$AFL_CC" -O2 -g $PYTHON_CFLAGS "$HARNESS_SRC" $PYTHON_LDFLAGS -o "$HARNESS_CMPLOG"
else
  echo "==> Cmplog harness up to date — skipping"
fi

# ── Build coredump shim ───────────────────────────────────────────────────────
if needs_rebuild "$SHIM_SO" "$SHIM_SRC"; then
  echo "==> Building coredump shim -> ${SHIM_SO}"
  gcc -shared -fPIC -o "$SHIM_SO" "$SHIM_SRC" -ldl
else
  echo "==> Coredump shim up to date — skipping"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "==> Build complete: ${DIST_DIR}/"
ls -1 "$DIST_DIR"

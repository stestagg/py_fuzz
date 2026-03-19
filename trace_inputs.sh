#!/usr/bin/env bash
set -euo pipefail

# Replays all testcases through fuzz_python with an LD_PRELOAD shim that logs
# every dlopen() call.  Output is written to /dlopen_files.txt (bind-mounted
# from the project root on the host by run_docker.sh).

TESTCASES_DIR="${TESTCASES_DIR:-/testcases}"
DIST_DIR="${DIST_DIR:-/dist}"

HARNESS="${DIST_DIR}/fuzz_python"
TRACE_SO="${DIST_DIR}/trace_dlopen.so"
OUTPUT_FILE="/dlopen_files.txt"   # bind-mounted from host project root

export PYTHONHOME="${DIST_DIR}/install"
export DLOPEN_LOG="$OUTPUT_FILE"

: > "$OUTPUT_FILE"   # truncate (file exists via bind mount)

count=0
while IFS= read -r -d '' input; do
    LD_PRELOAD="$TRACE_SO" "$HARNESS" < "$input" 2>/dev/null || true
    count=$((count + 1))
    if (( count % 100 == 0 )); then
        echo "  ... ${count} inputs processed"
    fi
done < <(find "$TESTCASES_DIR" -type f -print0)

echo "==> ${count} inputs replayed.  Deduplicating..."
sort -u "$OUTPUT_FILE" -o "$OUTPUT_FILE"
echo "==> dlopen trace written to ${OUTPUT_FILE} ($(wc -l < "$OUTPUT_FILE") unique paths)"

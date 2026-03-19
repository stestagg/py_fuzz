#!/usr/bin/env bash
set -euo pipefail

# Runs as root. Does privileged setup, then drops to the fuzzer user.
#
# Coredump setup (requires --privileged + root):
#   1. Pipe-based core_pattern so the kernel forks an async helper — the
#      crashing process exits with its real signal immediately rather than
#      blocking on a synchronous core write, which would let AFL++'s timeout
#      fire and record the input as a hang instead of a crash.
#   2. The LD_PRELOAD shim (nocorelimit.so) is set later by run.sh so it
#      applies to AFL++ child processes; it no-ops the setrlimit(RLIMIT_CORE,0)
#      calls AFL++ makes on each child.

CORES_DIR="${OUTPUT_DIR}/cores"
mkdir -p "$CORES_DIR"

if [[ -f /proc/sys/kernel/core_pattern ]]; then
  ulimit -c unlimited
  # Use dd (always present on the host VM) + /proc/%P/root to cross the
  # container namespace boundary.  %P is the initial-namespace PID (what the
  # host /proc is indexed by); /proc/%P/root then resolves into the container's
  # filesystem from the host.  %p in the filename is the in-container PID.
  echo "|/usr/bin/dd bs=65536 of=/proc/%P/root${CORES_DIR}/core.%e.%p.%t" > /proc/sys/kernel/core_pattern
  echo "==> Coredumps enabled -> ${CORES_DIR}/"
fi

# Ensure the fuzzer user can write to the output directory.
chown -R fuzzer:fuzzer "${OUTPUT_DIR}"

exec gosu fuzzer "$@"

#!/bin/bash
# Invoked by the kernel as a core dump pipe handler.
# Usage (set via core_pattern): |/path/coredump_helper.sh <cores_dir> %p %e %t
# Kernel passes core data on stdin.
exec cat > "$1/core.$3.$2.$4"

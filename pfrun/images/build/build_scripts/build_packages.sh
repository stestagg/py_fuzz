#!/bin/sh
#
# Build third-party compiled Python packages into the fuzzed interpreter.
#
# Runs AFTER build.sh (stdlib .so's + python.exe are linked and the shared
# STARTID counter reflects them) and BEFORE build_helpers.sh (the harness link,
# which reserves a coverage map covering everything). Each package's extension
# .so's advance /pfm/py/lto-wrap-state.json further; we must NOT reset it here.
#
# Source is already checked out host-side into /pfm/packages/<name> (the VM is
# offline); recipes only build.
#
# PF_PACKAGES        colon-separated package names, in deps-first build order.
# PF_PKG_PROFILE_<name>  wrapper rule set (LTOWRAP_PROFILE) for each package.

set -xe

: "${PF_PACKAGES:?PF_PACKAGES not set}"

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/packages/_common.sh"

pf_bootstrap_buildenv

for _pkg in $(echo "$PF_PACKAGES" | tr ':' ' '); do
    eval _profile=\$PF_PKG_PROFILE_${_pkg}
    : "${_profile:=meson_python}"

    _recipe="$SCRIPT_DIR/packages/${_pkg}.sh"
    if [ ! -f "$_recipe" ]; then
        echo "No build recipe for package: $_pkg ($_recipe)" >&2
        exit 1
    fi

    echo "=== Building package $_pkg (profile: $_profile) ==="
    LTOWRAP_PROFILE="$_profile" sh "$_recipe"
done

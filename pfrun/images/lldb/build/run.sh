#!/bin/sh

set -xeuo pipefail

base_fs="${BASE_FS:-/base/fs.img}"
root_dir="${ROOT_DIR:-/work/root}"
out_fs="${OUT_FS:-/out/fs.img}"
tmp_fs="$out_fs.tmp"

if [ ! -f "$base_fs" ]; then
    echo "Missing base filesystem image: $base_fs" >&2
    exit 1
fi

rm -rf "$root_dir"
mkdir -p "$root_dir" /out

debugfs -R "rdump / $root_dir" "$base_fs"

sed -i 's/^fs\.file-max.*/fs.file-max = 4194304/' "$root_dir/etc/sysctl.conf"
grep -q 'fs\.nr_open' "$root_dir/etc/sysctl.conf" || printf 'fs.nr_open = 4194304\n' | tee -a "$root_dir/etc/sysctl.conf" >/dev/null

cat > /tmp/pacman-lean.conf << 'EOF'
[options]
HoldPkg     = pacman glibc
Architecture = auto
CheckSpace
SigLevel    = Required DatabaseOptional
LocalFileSigLevel = Optional
NoExtract   = usr/share/man/* usr/share/doc/* usr/share/info/*
NoExtract   = usr/share/locale/* usr/share/gtk-doc/*

[core]
Include = /etc/pacman.d/mirrorlist

[extra]
Include = /etc/pacman.d/mirrorlist
EOF

pacstrap -K -C /tmp/pacman-lean.conf "$root_dir" lldb

rm -rf "$root_dir"/var/cache/pacman/pkg/*
rm -rf "$root_dir"/var/lib/pacman/sync/*

rm -f "$tmp_fs" "$out_fs"
mke2fs -t ext4 -d "$root_dir" "$tmp_fs" 5G
resize2fs -f -M "$tmp_fs"
mv "$tmp_fs" "$out_fs"

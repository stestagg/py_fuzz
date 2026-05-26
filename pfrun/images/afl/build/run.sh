#!/bin/sh

set -xeuo pipefail

base_fs="/base/fs.img"
root_dir="/fs"
out_fs="/out/fs.img"
tmp_fs="$out_fs.tmp"

if [ ! -f "$base_fs" ]; then
    echo "Missing base filesystem image: $base_fs" >&2
    exit 1
fi

sudo rm -rf "$root_dir"
sudo mkdir -p "$root_dir" /out

sudo debugfs -R "rdump / $root_dir" "$base_fs"

sudo sed -i 's/^fs\.file-max.*/fs.file-max = 4194304/' "$root_dir/etc/sysctl.conf"
grep -q 'fs\.nr_open' "$root_dir/etc/sysctl.conf" || printf 'fs.nr_open = 4194304\n' | sudo tee -a "$root_dir/etc/sysctl.conf" >/dev/null

sudo rm -rf "$root_dir"/var/cache/pacman/pkg/*

sudo mkdir -p "$root_dir/etc/sysctl.d"
sudo cp /build/afl-coredumps "$root_dir/etc/sysctl.d/99-afl.conf"

rm -f "$tmp_fs" "$out_fs"
sudo mke2fs -t ext4 -d "$root_dir" "$tmp_fs" 5G
sudo resize2fs -f -M "$tmp_fs"
mv "$tmp_fs" "$out_fs"

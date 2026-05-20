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

mkdir -p "$root_dir" /out
rm -f "$tmp_fs"

# Extract base image content into a directory, then create a fresh 5G ext4
# image from that directory. Avoids sparse-file corruption that occurs when
# copying an APFS-backed image file through Docker volumes on macOS.
debugfs -R "rdump / $root_dir" "$base_fs"
mke2fs -t ext4 -d "$root_dir" "$tmp_fs" 5G

mount -o loop "$tmp_fs" "$root_dir"

rm -f "$root_dir/var/lib/pacman/db.lck"

cat > /tmp/pacman-build.conf << 'EOF'
[options]
HoldPkg     = pacman glibc
Architecture = auto
SigLevel    = Never
NoExtract   = usr/share/man/* usr/share/doc/* usr/share/info/*
NoExtract   = usr/share/locale/* usr/share/gtk-doc/*

[core]
Include = /etc/pacman.d/mirrorlist

[extra]
Include = /etc/pacman.d/mirrorlist
EOF

pacman -r "$root_dir" \
    --config /tmp/pacman-build.conf \
    --dbpath "$root_dir/var/lib/pacman" \
    --cachedir /var/cache/pacman/pkg \
    --noscriptlet \
    -Sy --noconfirm \
    bzip2 \
    ccache \
    clang \
    lld \
    gdbm \
    git \
    libffi \
    libxml2 \
    libxslt \
    linux-api-headers \
    llvm \
    make \
    ncurses \
    sed \
    openssl \
    readline \
    sqlite \
    tk \
    xz \
    zlib \
    zstd \
    python

rm -rf "$root_dir"/var/cache/pacman/pkg/*
rm -rf "$root_dir"/var/lib/pacman/sync/*
umount "$root_dir"

e2fsck -f -y "$tmp_fs" || true
resize2fs -f -M "$tmp_fs"
mv "$tmp_fs" "$out_fs"

#!/bin/sh

set -xeuo pipefail

echo "Running build script with args: $*"

should_run() {
    section="$1"
    shift

    echo "Checking if section '$section' should run with args: $*"
    [ "$#" -eq 0 ] && return 0

    for arg in "$@"; do
        [ "$arg" = "$section" ] && return 0
    done

    return 1
}

if should_run kernel "$@"; then
    KERNEL_VERSION=6.18.22
    wget -q "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-${KERNEL_VERSION}.tar.xz"
    tar xf "linux-${KERNEL_VERSION}.tar.xz"
    (
        cd "linux-${KERNEL_VERSION}"
        cp /build/virt.aarch64.config .config
        # olddefconfig updates compiler-specific options (CC_IS_GCC -> CC_IS_CLANG etc.)
        make ARCH=arm64 CC=clang LLVM=1 olddefconfig
        make ARCH=arm64 CC=clang LLVM=1 -j"$(nproc)" Image modules
        cp arch/arm64/boot/Image /out/vmlinux
        KVER=$(make ARCH=arm64 LLVM=1 -s kernelrelease)
        sudo make ARCH=arm64 LLVM=1 modules_install
        printf 'MODULES=()\nBINARIES=()\nFILES=()\nHOOKS=(base udev block filesystems)\n' \
            | sudo tee /tmp/mkinitcpio.conf >/dev/null
        sudo mkinitcpio -c /tmp/mkinitcpio.conf -k "$KVER" -g /out/initram
    )
fi

if should_run fs "$@"; then
    sudo mkdir -p /fs

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

    sudo pacstrap -K -C /tmp/pacman-lean.conf /fs \
        filesystem glibc gcc-libs busybox \
        python uv \
        bzip2 gdbm libffi xz ncurses openssl readline sqlite zlib zstd

    sudo rm -rf /fs/var/cache/pacman/pkg/*
    sudo rm -rf /fs/var/lib/pacman/sync/*

    git clone --branch dev --depth 1 https://github.com/AFLplusplus/AFLplusplus.git
    (
        cd AFLplusplus
        make PERFORMANCE=1 NO_PYTHON=1 NO_QEMU=1 AFL_NO_X86=1 NO_FRIDA=1 NO_UNICORN=1 \
            CC=clang CXX=clang++
        sudo make install DESTDIR=/fs PREFIX=/usr
    )

    sudo mkdir -p /fs/pfm
    sudo mkdir -p /fs/usr/local/bin

    clang -O2 -Wall -Wextra -o /build/pfm-pty /build/pfm-pty.c
    sudo cp /build/pfm-pty /fs/usr/local/bin/pfm-pty

    # Install busybox applets into /usr/local/bin so they don't conflict with
    # packages (coreutils, findutils, etc.) that downstream layers add via pacstrap
    sudo mkdir -p /fs/usr/local/bin
    sudo chroot /fs /usr/bin/busybox --install -s /usr/local/bin
    # sh: needed for #!/bin/sh shebangs (bash not installed, coreutils doesn't own sh)
    sudo ln -sf /usr/local/bin/sh   /fs/usr/bin/sh
    # init: kernel searches /sbin/init = /usr/bin/init on Arch's usr-merge
    sudo ln -sf /usr/local/bin/init /fs/usr/bin/init

    sudo cp /build/inittab /fs/etc/inittab
    sudo cp /build/pfm-run /fs/usr/local/bin/pfm-run
    sudo chmod +x /fs/usr/local/bin/pfm-run

    printf '%s\n' \
        'fs.file-max = 1048576' \
        'kernel.core_pattern = /pfm/cores/core.%p' \
        'kernel.core_uses_pid = 0' \
        'vm.overcommit_memory = 0' \
        'kernel.randomize_va_space = 0' \
        'kernel.sched_autogroup_enabled = 1' \
        | sudo tee -a /fs/etc/sysctl.conf >/dev/null

    sudo mke2fs -t ext4 -d /fs /out/fs.img 5G
    sudo resize2fs -f -M /out/fs.img
fi

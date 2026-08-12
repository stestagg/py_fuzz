FROM ogarcia/archlinux:devel

# Use nearby EU mirrors (RWTH Aachen, then dotsrc) instead of the default GeoIP
# rotation, which tends to route to US mirrors. This file is also pulled in by
# the pacstrap configs (Include = /etc/pacman.d/mirrorlist) in build/run.sh.
RUN printf 'Server = https://ftp.halifax.rwth-aachen.de/archlinux-arm/aarch64/$repo\nServer = https://mirrors.dotsrc.org/archlinuxarm/aarch64/$repo\n' > /etc/pacman.d/mirrorlist

RUN pacman -Syu --noconfirm && pacman -S --noconfirm \
    base-devel clang llvm lld compiler-rt e2fsprogs vim sudo bc \
    arch-install-scripts mkinitcpio wget git pahole rust

RUN pacman-key --init && pacman-key --populate archlinux

RUN useradd -m -s /bin/bash builder
RUN echo "builder ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers
RUN usermod -aG wheel builder

RUN mkdir /build

COPY build/ /build/

USER builder
WORKDIR /home/builder

ENTRYPOINT ["/build/run.sh"]

FROM ogarcia/archlinux:devel

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

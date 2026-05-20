FROM ogarcia/archlinux:devel

RUN pacman -Syu --noconfirm && pacman -S --noconfirm \
    e2fsprogs base-devel sudo clang llvm lld compiler-rt coreutils git

RUN useradd -m -s /bin/bash builder
RUN echo "builder ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers
RUN usermod -aG wheel builder

RUN mkdir /build /base /out

COPY build/ /build/

USER builder
WORKDIR /home/builder

ENTRYPOINT ["/build/run.sh"]

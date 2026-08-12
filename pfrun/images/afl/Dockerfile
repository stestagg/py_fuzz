FROM ogarcia/archlinux:devel

# Use nearby EU mirrors (RWTH Aachen, then dotsrc) instead of the default GeoIP
# rotation, which tends to route to US mirrors.
RUN printf 'Server = https://ftp.halifax.rwth-aachen.de/archlinux-arm/aarch64/$repo\nServer = https://mirrors.dotsrc.org/archlinuxarm/aarch64/$repo\n' > /etc/pacman.d/mirrorlist

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

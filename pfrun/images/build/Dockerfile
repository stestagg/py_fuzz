FROM ogarcia/archlinux:devel

# Use nearby EU mirrors (RWTH Aachen, then dotsrc) instead of the default GeoIP
# rotation, which tends to route to US mirrors. This file is also pulled in by
# the pacstrap config (Include = /etc/pacman.d/mirrorlist) in build/run.sh.
RUN printf 'Server = https://ftp.halifax.rwth-aachen.de/archlinux-arm/aarch64/$repo\nServer = https://mirrors.dotsrc.org/archlinuxarm/aarch64/$repo\n' > /etc/pacman.d/mirrorlist

RUN pacman -Syu --noconfirm && pacman -S --noconfirm e2fsprogs arch-install-scripts

RUN pacman-key --init && pacman-key --populate archlinux

RUN mkdir /build /base /out /work

COPY build/ /build/

ENTRYPOINT ["/build/run.sh"]

FROM ogarcia/archlinux:devel

RUN pacman -Syu --noconfirm && pacman -S --noconfirm e2fsprogs arch-install-scripts

RUN pacman-key --init && pacman-key --populate archlinux

RUN mkdir /build /base /out /work

COPY build/ /build/

ENTRYPOINT ["/build/run.sh"]

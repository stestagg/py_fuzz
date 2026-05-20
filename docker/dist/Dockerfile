FROM ogarcia/archlinux:devel

RUN pacman -Sy --noconfirm \
        ca-certificates \
        git \
        bzip2 \
        libffi \
        gdbm \
        xz \
        ncurses \
        readline \
        sqlite \
        openssl \
        libxml2 \
        libxslt \
        zstd \
        make \
        python \
        tk \
        zlib

RUN mkdir -p /src
WORKDIR /src

RUN git clone https://github.com/python/cpython /src/cpython

COPY run_dist.py /src/run_dist.py

ENTRYPOINT ["python", "/src/run_dist.py"]

FROM alpine:edge

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy

# Dev libs required to compile CPython from source using clang.
RUN apk add --no-cache \
      afl \
      bzip2-dev \
      ccache \
      clang-dev \
      gdbm-dev \
      git \
      libffi-dev \
      libxml2-dev \
      libxslt-dev \
      linux-headers \
      llvm \
      xz-dev \
      make \
      musl-dev \
      ncurses-dev \
      openssl-dev \
      readline-dev \
      sqlite-dev \
      tk-dev \
      xz-dev \
      zlib-dev \
      zstd-dev

RUN mkdir -p /src
COPY build.sh /src/build.sh
RUN chmod +x /src/build.sh

COPY build_helpers.sh /src/build_helpers.sh
RUN chmod +x /src/build_helpers.sh

WORKDIR /src

# CC=/usr/bin/afl-clang-lto
# CXX=/usr/bin/afl-clang-lto++

COPY lto-wrap.py /usr/bin/wrap-afl-clang-lto
RUN chmod +x /usr/bin/wrap-afl-clang-lto
COPY lto-wrap.py /usr/bin/wrap-afl-clang-lto++
RUN chmod +x /usr/bin/wrap-afl-clang-lto++
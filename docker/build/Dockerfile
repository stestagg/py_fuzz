FROM alpine:latest

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy

# Dev libs required to compile CPython from source using clang.
RUN apk add --no-cache \
      clang-dev \
      linux-headers \
      musl-dev \
      openssl-dev \
      bzip2-dev \
      readline-dev \
      sqlite-dev \
      xz-dev \
      afl \
      git \
      make \
      ccache \
      llvm

RUN mkdir -p /src
COPY build.sh /src/build.sh
RUN chmod +x /src/build.sh
WORKDIR /src

# CC=/usr/bin/afl-clang-lto
# CXX=/usr/bin/afl-clang-lto++

COPY lto-wrap.py /usr/bin/wrap-afl-clang-lto
RUN chmod +x /usr/bin/wrap-afl-clang-lto
COPY lto-wrap.py /usr/bin/wrap-afl-clang-lto++
RUN chmod +x /usr/bin/wrap-afl-clang-lto++
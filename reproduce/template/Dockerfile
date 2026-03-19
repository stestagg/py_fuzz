# Edit REPO and BRANCH to target any CPython fork/branch (e.g. raminfp:fix-pyexpat-extentity-error-paths)
ARG REPO=https://github.com/python/cpython
ARG BRANCH=main

FROM ubuntu:24.04

ARG REPO
ARG BRANCH
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    build-essential git pkg-config \
    libssl-dev libbz2-dev libreadline-dev libsqlite3-dev \
    liblzma-dev libffi-dev zlib1g-dev uuid-dev \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --branch ${BRANCH} --depth 1 \
    ${REPO} /cpython

RUN cd /cpython && \
    ./configure --prefix=/python --without-ensurepip && \
    make -j$(nproc) && \
    make install

# ── Edit your test case below ──────────────────────────────────────────────────
RUN cat > /test.py << 'EOF'
# paste your test case here
print("hello world")
EOF
# ──────────────────────────────────────────────────────────────────────────────

CMD ["/bin/sh", "-c", "uname -m && /python/bin/python3 -VV && /python/bin/python3 /test.py"]

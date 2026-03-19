ARG REPO=https://github.com/python/cpython.git
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

# ── TEST SCRIPT ──────────────────────────────────────────────────
RUN cat > /test.py << 'EOF'
from xml.parsers import expat

p = expat.ParserCreate(encoding="utf-16")

def start(name, attrs):
    p.CharacterDataHandler = lambda data: p.Parse(data, 0)

p.StartElementHandler = start

data = b"\xff\xfe<\x00a\x00>\x00x\x00"
for i in range(len(data)):
    try:
        p.Parse(data[i:i+1], i == len(data) - 1)
    except Exception:
        pass
EOF
# ──────────────────────────────────────────────────────────────────────────────

CMD ["/bin/sh", "-c", "uname -m && /python/bin/python3 -VV && /python/bin/python3 /test.py"]

FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential ca-certificates git gfortran libbz2-dev libexpat1-dev \
        libffi-dev libgdbm-compat-dev libgdbm-dev liblzma-dev libncursesw5-dev \
        libopenblas-dev libreadline-dev libsqlite3-dev libssl-dev libz-dev \
        ninja-build pkg-config \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth=1 --branch main https://github.com/python/cpython.git /src/cpython \
    && cd /src/cpython \
    && ./configure --prefix=/opt/cpython --with-pydebug --with-ensurepip=install \
    && make -j"$(nproc)" altinstall

RUN git clone --depth=1 --branch main --recurse-submodules \
        https://github.com/numpy/numpy.git /src/numpy \
    && /opt/cpython/bin/python3.16d -m pip install --no-cache-dir /src/numpy

# Expected result: a Py_DEBUG abort from _Py_CheckSlotResult, rather than
# the ValueError that an invalid integer mode should raise.
CMD ["/opt/cpython/bin/python3.16d", "-c", "import numpy as np; np.take(np.arange(1), [0], mode=3)"]

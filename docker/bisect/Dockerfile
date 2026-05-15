FROM ubuntu

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        clang \
        ccache \
        git \
        libbz2-dev \
        libffi-dev \
        libgdbm-dev \
        liblzma-dev \
        libncurses-dev \
        libreadline-dev \
        libsqlite3-dev \
        libssl-dev \
        libxml2-dev \
        libxslt1-dev \
        libzstd-dev \
        llvm \
        make \
        python3 \
        python3-pip \
        tk-dev \
        zlib1g-dev && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p /src
WORKDIR /src

RUN git clone https://github.com/python/cpython /src/cpython

COPY bisect.sh /bisect.sh
RUN chmod +x /bisect.sh

COPY run_bisect.py /src/run_bisect.py
RUN pip3 install --break-system-packages questionary rich

ENTRYPOINT ["python3", "/src/run_bisect.py"]

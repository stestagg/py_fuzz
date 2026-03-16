FROM aflplusplus/aflplusplus:latest

# tmux: needed for multi-worker mode (-j > 1)
# *-dev libs: needed for a full CPython build (base image has the runtime
# libs but not the headers/static archives required at compile time)
RUN apt-get update && apt-get install -y --no-install-recommends \
      tmux \
      libssl-dev \
      libbz2-dev \
      libreadline-dev \
      libsqlite3-dev \
      liblzma-dev \
      pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src

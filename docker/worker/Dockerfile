# Unified Assemblage worker image — coordinator | scraper | builder.
#
# One Dockerfile, two toolchain variants selected at build time:
#   docker build --build-arg TOOLCHAIN=gcc   -t assemblage-gcc:default   -f docker/worker/Dockerfile .
#   docker build --build-arg TOOLCHAIN=clang -t assemblage-clang:default -f docker/worker/Dockerfile .
#
# Both variants install the SAME union apt set, so gcc- and clang-built
# corpora stay comparable (everything below the compiler is identical). The
# clang variant exposes gcc/g++/cc/c++ via a PATH shim directory pointing at
# clang — NOT by overwriting /usr/bin/gcc — so build systems that hardcode
# `gcc`/`cc` route through clang while the real GNU toolchain stays intact.

FROM ubuntu:noble-20250910

# gcc   -> the real GNU toolchain (gcc-13/g++-13) is the default cc/gcc.
# clang -> clang-18 is exposed under the gcc/cc names via the shim dir below.
ARG TOOLCHAIN=gcc
ENV DEBIAN_FRONTEND=noninteractive
ENV PIP_USE_PEP517=1

# --- Union apt set --------------------------------------------------------
# The gcc "kitchen sink" (Qt/OpenGL/Java/OCaml/Erlang/OpenCV/HDF5/ctags/
# postgres headers …) merged with clang's build stack (boost, zmq, autotools,
# flex/bison, libc++), deduplicated. Compilers are pinned to versioned
# packages (gcc-13/g++-13, clang-18) for reproducibility. nodejs comes from
# noble's own archive — the old nodesource `curl | bash` line is gone. NO
# `timeout` script is installed: coreutils `timeout` (used by the build
# strategy's `timeout 10m make …`) already ships in the base image.
RUN apt-get update && apt-get -y upgrade && apt-get -y install \
        tzdata ca-certificates curl wget git tree unzip xz-utils \
        cmake make build-essential pkg-config \
        gcc g++ gcc-13 g++-13 \
        clang clang-18 llvm libc++-dev libc++abi-dev \
        autoconf automake libtool bundler flex bison swig \
        nodejs npm \
        python3-pip python3.12-venv python3-opencv \
        openjdk-11-jdk ocaml erlang rebar mono-xbuild \
        exuberant-ctags \
        postgresql postgresql-server-dev-all \
        qtbase5-dev qtchooser qt5-qmake qtbase5-dev-tools \
        libglu1-mesa-dev freeglut3-dev mesa-common-dev \
        libhdf5-dev libboost-all-dev libzmq3-dev \
        libssl-dev zlib1g-dev libbz2-dev liblzma-dev libreadline-dev \
        libsqlite3-dev libncurses-dev tk-dev libffi-dev \
        libxml2-dev libxmlsec1-dev libjpeg-dev \
        mecab-ipadic-utf8 \
    && rm -rf /var/lib/apt/lists/*

# Make gcc-13/g++-13 the default gcc/g++ (belt-and-braces; noble already maps them).
RUN update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-13 100 \
      --slave /usr/bin/g++ g++ /usr/bin/g++-13

# Expose the selected TOOLCHAIN under the gcc/g++/cc/c++/clang names via a
# PATH shim dir. On the clang variant these symlink to clang-18 so a build
# system that shells out to `gcc`/`cc` transparently uses clang, WITHOUT
# masquerading over /usr/bin (the real GNU toolchain remains available). On
# the gcc variant the shim dir stays empty and /usr/bin/gcc wins.
RUN mkdir -p /opt/assemblage/shims && \
    if [ "$TOOLCHAIN" = "clang" ]; then \
      _cc="$(command -v clang-18 || command -v clang)"; \
      _cxx="$(command -v clang++-18 || command -v clang++)"; \
      for n in gcc cc clang; do ln -sf "$_cc" /opt/assemblage/shims/$n; done; \
      for n in g++ c++ clang++; do ln -sf "$_cxx" /opt/assemblage/shims/$n; done; \
    fi
ENV PATH="/opt/assemblage/shims:/venv/bin:$PATH"

COPY ./backend /app
WORKDIR /app

# TODO(P11-followup): move the image to `uv sync --frozen` into /venv once a
# working local builder can validate the uv flow end-to-end. Until then the
# venv + `pip install -r requirements.txt` path stays, and requirements.txt is
# the image's dependency source of truth — do not delete it.
RUN python3 -m venv /venv && \
    /venv/bin/pip install --upgrade pip setuptools wheel && \
    /venv/bin/pip install -r requirements.txt
ENV PYTHONPATH=/app

# Legacy compiler env kept for continuity: COMPILER is an AliasChoices
# fallback for settings.compiler (the compose file's per-service `compiler:`
# var overrides it for builders); CC/CXX resolve through the shim/alternatives.
ENV COMPILER=${TOOLCHAIN}
ENV LANGUAGE=c++
ENV CC=cc
ENV CXX=c++

HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD pgrep -f start_worker.py > /dev/null || exit 1
ENTRYPOINT ["python3", "/app/scripts/start_worker.py"]

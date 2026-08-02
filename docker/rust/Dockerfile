# Assemblage Rust builder image — one pinned nightly, three codegen backends.
#
#   docker build --build-arg RUST_TOOLCHAIN=nightly-2026-06-15 \
#       -t assemblage-rust:default -f docker/rust/Dockerfile .
#
# Mirrors docker/worker/Dockerfile's structure (ubuntu:noble base, python venv +
# pip install -r requirements.txt, pgrep healthcheck, start_worker.py entrypoint)
# but swaps the C kitchen-sink apt set for a lean Rust toolchain. A single pinned
# nightly serves all three -Zcodegen-backend targets (llvm/cranelift/gcc) so the
# rustc frontend/MIR stays identical across backends and corpus builds reproduce.

FROM ubuntu:noble-20250910

# Single pin for ALL backends. -Zcodegen-backend is a nightly -Z flag, and the
# cranelift/gcc codegen components are only published for specific nightlies.
# nightly-2026-06-15 was chosen as the reference pin: it carries
# rustc-codegen-cranelift-preview and (best-effort) rustc-codegen-gcc-preview +
# gcc-x86_64-unknown-linux-gnu-preview via rustup. If a future rebuild needs to
# slide the pin, keep it in sync with build.rust.DEFAULT_RUST_TOOLCHAIN.
ARG RUST_TOOLCHAIN=nightly-2026-06-15

ENV DEBIAN_FRONTEND=noninteractive
ENV PIP_USE_PEP517=1

# Lean build stack: git (clone), build-essential/pkg-config (crates with build.rs
# that shell out to cc / pkg-config), and python3 for the worker itself.
RUN apt-get update && apt-get -y upgrade && apt-get -y install \
        tzdata ca-certificates curl wget git xz-utils zstd \
        build-essential pkg-config \
        python3-pip python3.12-venv \
    && rm -rf /var/lib/apt/lists/*

# --- Rust toolchain -------------------------------------------------------
# rustup + toolchain live under a fixed root, isolated from the per-task cargo
# registry cache. RUSTUP_HOME/CARGO_HOME here are the TOOLCHAIN's own home used
# only during image build (`cargo install rustfilt` lands in /opt/rust/cargo/bin);
# the per-task registry/download CARGO_HOME is a distinct runtime env (default
# /cargo, set below and mounted as a named volume by compose).
ENV RUSTUP_HOME=/opt/rust/rustup
ENV CARGO_HOME=/opt/rust/cargo
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | \
        sh -s -- -y --profile minimal --default-toolchain ${RUST_TOOLCHAIN} && \
    # cranelift is the gating experimental backend — a hard failure here is the
    # signal to slide the pin to a nightly where it installs.
    /opt/rust/cargo/bin/rustup component add rustc-codegen-cranelift-preview \
        --toolchain ${RUST_TOOLCHAIN} && \
    # cg_gcc is least mature and x86_64-linux-only; best-effort so the image is
    # still usable (llvm + cranelift) if this nightly lacks the component. R4
    # gates the gcc compose service on the empirical debuginfo probe.
    ( /opt/rust/cargo/bin/rustup component add rustc-codegen-gcc-preview \
          gcc-x86_64-unknown-linux-gnu-preview --toolchain ${RUST_TOOLCHAIN} || \
      echo "WARNING: rustc-codegen-gcc-preview unavailable on ${RUST_TOOLCHAIN}" ) && \
    /opt/rust/cargo/bin/cargo install rustfilt

# rustfilt + the rustup-managed cargo/rustc shims on PATH for every user.
ENV PATH=/opt/rust/cargo/bin:/venv/bin:$PATH

COPY ./backend /app
WORKDIR /app

# Same venv + requirements.txt path as the C worker image (pyelftools 0.32 in
# requirements.txt already reads rustc DWARF v4/v5).
RUN python3 -m venv /venv && \
    /venv/bin/pip install --upgrade pip setuptools wheel && \
    /venv/bin/pip install -r requirements.txt
ENV PYTHONPATH=/app

# Rust builder worker identity (compose overrides CODEGEN_BACKEND/BUILD_MODE/
# COMPILER_FLAG per service).
ENV COMPILER=rustc
ENV LANGUAGE=rust
# The nightly pin baked into the image; RustBuildStrategy reads RUST_TOOLCHAIN
# (falling back to build.rust.DEFAULT_RUST_TOOLCHAIN) and passes it as
# `cargo +<pin>` so a repo's rust-toolchain.toml can't divert the build off the
# backend-capable nightly.
ENV RUST_TOOLCHAIN=${RUST_TOOLCHAIN}
# Per-task cargo registry/cache home (distinct from the toolchain's own
# /opt/rust/cargo); compose mounts a shared named volume here.
ENV CARGO_HOME=/cargo

HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD pgrep -f start_worker.py > /dev/null || exit 1
ENTRYPOINT ["python3", "/app/scripts/start_worker.py"]

# Assemblage

Assemblage is a distributed binary-corpus generator. It discovers licensed C/C++
and Rust repositories on GitHub, builds them with multiple compilers and
optimization levels, and archives the resulting binaries with rich,
function-level metadata — producing labeled training data for machine-learning
approaches to binary analysis, and for static/dynamic analysis and reverse
engineering.

Paper: [arxiv.org/abs/2405.03991](https://arxiv.org/abs/2405.03991).
Code is MIT-licensed. The published dataset (permissively-licensed subset only)
is at [assemblage-dataset.net](https://assemblage-dataset.net); see the
[data sheet](https://assemblage-dataset.net/assets/total-datasheet.pdf).

**Rust dataset.** 18,450 compiled Rust binaries from 3,034 permissively licensed
repositories — each with DWARF function/line metadata, the source tree it was
built from, and compiler IR for a subset — are published on HuggingFace at
[`changliu8541/assemblage-rust`](https://huggingface.co/datasets/changliu8541/assemblage-rust).
It is an unprocessed crawl: raw build outputs, filtered only by license.

> **Provenance.** In July 2026 this codebase was deep-refactored and extended
> with the Rust worker by Claude (Fable 5, Anthropic) under the maintainer's
> direction — behavior-preserving by construction (frozen wire formats, live-DB
> schema parity, a golden-repo end-to-end gate) and verified against the
> production corpus. If you prefer the code as it was before that work, use the
> [`pre-refactor`](../../tree/pre-refactor) branch, whose HEAD is the last
> commit predating the refactor.

## Architecture

```
 GitHub ─▶ scraper ─▶ [scrape] ─▶ coordinator ─▶ [build_opt_{id}] ─▶ builders
                                      │                                  │
                                      ▼                                  ▼
                                 PostgreSQL  ◀── [clone|build|binary] ── MinIO
                                                                          │
                                                       export pipeline ◀──┘
                                                              │
                                                              ▼
                                                    published corpus
```

- **scraper** — date-windowed, license-filtered GitHub search; emits repo bundles.
- **coordinator** — records repos, fans each one out across the build matrix, and
  runs one dispatch thread per build option.
- **builders** — containers that clone or restore a repo, compile it, extract
  DWARF, and upload binaries plus `assemblage_meta.json`. Builders ack before
  building, so delivery is at-most-once by design.
- **export pipeline** — host-side; turns artifacts into a publishable corpus.

Stack: Python 3.12, RabbitMQ, PostgreSQL, MinIO, docker compose.

**Build matrix.** C/C++ builds run gcc and clang across `-O0 -O1 -O2 -O3 -Os`.
Rust builds run `rustc`'s three `-Zcodegen-backend` targets (LLVM, Cranelift,
GCC/cg_gcc) across Debug / RelWithDebInfo / Release and `-O0..-O3, -Os, -Oz`,
all from one pinned nightly. Rust binaries carry the same DWARF metadata as the C
corpus plus demangled names and per-function origin tags (in-repo, dependency,
stdlib). Quality varies by tier: LLVM and Cranelift give full source/line
mapping; cg_gcc is a reliable name/address corpus but its repo-level line info is
largely absent; Release binaries keep symbol tables rather than repo debug info.
The full matrix lives in `docker-compose.yml`.

## Getting started

**[INSTALL.md](INSTALL.md)** — prerequisites, configuration, image builds, first
boot, scaling, publishing a corpus, and the failure modes worth knowing before
you leave it running.

A minimal terminal UI wraps the common operations: `python assemblage_tui.py`.

## Further reading

| doc | covers |
|---|---|
| [INSTALL.md](INSTALL.md) | installation, deployment, operations |
| `backend/assemblage/README.md` | module map of the Python package |
| `backend/alembic/README.md` | schema policy — migrations are frozen to the live DB |
| `tests/README.md` | test markers and the end-to-end gate |
| `tests/fixtures/messages/README.md` | the frozen wire formats |

Windows/MSVC builds require a Windows host and are quarantined under
`backend/assemblage/legacy/`. DeepHistory, a standalone multi-version library
corpus built via Conan, lives in `deephistory/` and does not use the coordinator.

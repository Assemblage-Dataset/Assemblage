# Compose files

The canonical production stack stays at the repo root (`docker-compose.yml`,
found by a bare `docker compose up -d`). Everything else lives here; all paths
inside these files are relative to this directory (`../backend` etc.), so run
them from the repo root with `-f`:

- `e2e.yml` — the hermetic golden-repo E2E gate (`make e2e`); pinned to its own
  compose project `assemblage-e2e` so it can never touch production containers.
- `deephistory.yml` — the standalone DeepHistory/Conan batch corpus
  (`docker compose -f compose/deephistory.yml up --build`).
- `windows.yml` — legacy Windows/MSVC builders, run on a Windows host against
  a remote coordinator (see the README's distributed-builders section).

Test-only overlays live next to their harness (e.g.
`tests/e2e/docker-compose.parity-ports.yml`).

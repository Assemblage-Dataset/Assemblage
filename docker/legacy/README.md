# docker/legacy — quarantined Windows/MSVC images

These images build the frozen Windows/MSVC and Conan paths (see
`backend/assemblage/legacy/` and the re-architecture ADR). They are **not**
part of the Linux gcc/clang pipeline and are excluded from the standard gates.

- `windows/` — the MSVC builder image (`docker-compose-windows.yml`). Runs a
  Windows kernel; must be deployed on a Windows host, connected to the Linux
  coordinator over exposed RabbitMQ/MinIO ports.
- `conan/` — the DeepHistory Conan builder image (multi-version library corpus).

The Linux worker image lives at `docker/worker/Dockerfile` (unified gcc+clang,
`ARG TOOLCHAIN`). DeepHistory's Linux images are at `docker/deephistory/`.

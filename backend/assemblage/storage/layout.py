"""The single source of truth for S3 object keys.

The builder and the dataset pipeline historically derived S3 keys with
copy-pasted f-strings that could (and did) drift. Every key the system reads or
writes now comes from these functions, so a change to the layout happens in
exactly one place.

Layout (frozen — see ``tests/fixtures/messages/README.md``):

- ``project-archive`` bucket:
  ``{owner}/{project}/{sha12}.tar.gz`` (cloned source) and
  ``{owner}/{project}/latest.txt`` (a pointer to the latest sha12).
- ``artifacts`` bucket:
  ``{owner}_{project}_{sha12}_{compiler}_{flag}/<file>`` (built binaries and
  ``assemblage_meta.json``).
- IR dumps live *under* an artifact prefix, never beside it:
  ``{artifact_prefix}/ir/{stage}.tar.gz`` (one gzipped tarball per IR stage) plus
  ``{artifact_prefix}/ir/manifest.json``. Additive: a build with no IR simply has
  no ``ir/`` subtree, so every pre-IR artifact stays valid unread.
"""

PROJECT_ARCHIVE_BUCKET = "project-archive"
ARTIFACTS_BUCKET = "artifacts"

METADATA_FILENAME = "assemblage_meta.json"

IR_DIRNAME = "ir"
IR_MANIFEST_FILENAME = "manifest.json"

# --- v2: compressed, HuggingFace-aligned ------------------------------------
# The v1 layout above stores every object raw, which cost 2.3 TiB for 23k builds
# and forced the publish path to recompress the whole corpus on every release.
# v2 stores what the published dataset already stores, so exporting is a copy:
#
#   {build_dir}/binaries/{name}.zst
#   {build_dir}/metadata/assemblage_meta.json.zst
#   {build_dir}/ir/{stage}.tar.gz          (already gzipped; unchanged)
#   {build_dir}/export.json                (provenance + byte counts)
#
# v1 keys are NOT rewritten in place by the builder — readers try v2 first and
# fall back, so the two shapes coexist until the backfill finishes.
COMPRESSED_SUFFIX = ".zst"
BINARIES_DIRNAME = "binaries"
METADATA_DIRNAME = "metadata"
EXPORT_MANIFEST_FILENAME = "export.json"


def artifact_prefix(owner: str, project: str, sha12: str, compiler: str, flag: str) -> str:
    """The flat per-build prefix in the ``artifacts`` bucket."""
    return f"{owner}_{project}_{sha12}_{compiler}_{flag}"


def rust_artifact_prefix(
    owner: str, project: str, sha12: str, backend: str, mode: str, flag: str
) -> str:
    """The flat per-build prefix for a Rust build (frozen from day one).

    Unlike the C prefix, this carries the codegen backend AND the build mode, so
    llvm/cranelift/gcc and Debug/RelWithDebInfo/Release variants of the same commit
    never collide in the ``artifacts`` bucket (the latent C-side mode-collision bug
    is designed out for Rust).
    """
    return f"{owner}_{project}_{sha12}_rustc-{backend}_{mode}_{flag}"


def artifact_key(prefix: str, filename: str) -> str:
    """An object key inside an :func:`artifact_prefix` directory."""
    return f"{prefix}/{filename}"


def metadata_key(prefix: str) -> str:
    """The ``assemblage_meta.json`` key inside an :func:`artifact_prefix`."""
    return f"{prefix}/{METADATA_FILENAME}"


def ir_prefix(prefix: str) -> str:
    """The ``ir/`` subtree of an :func:`artifact_prefix`."""
    return f"{prefix}/{IR_DIRNAME}"


def ir_tarball_key(prefix: str, stage: str) -> str:
    """The gzipped tarball holding every kept dump for one IR ``stage``.

    One object per stage rather than per crate: IR is many small text files and
    S3 round-trips dominate at that granularity.
    """
    return f"{ir_prefix(prefix)}/{stage}.tar.gz"


def ir_manifest_key(prefix: str) -> str:
    """The IR manifest (what stages/crates/sizes are in the tarballs)."""
    return f"{ir_prefix(prefix)}/{IR_MANIFEST_FILENAME}"


def build_dir(owner: str, project: str, sha12: str, flag: str, backend: str, mode: str) -> str:
    """The v2 per-build directory: ``{owner}_{project}_{sha12}-{flag}-{backend}-{mode}``.

    Identical to the directory name inside a published HuggingFace repo tar, so a
    build exports without being renamed. ``backend`` is the bare codegen backend
    for Rust (``llvm``, not ``rustc-llvm``) and the compiler for C/C++
    (``gcc``/``clang``); ``flag`` is stored without its leading dash. Unlike the
    v1 C prefix this always carries ``mode``, which closes the C-side collision
    between Debug and Release builds of one commit.
    """
    return f"{owner}_{project}_{sha12}-{flag.lstrip('-')}-{backend}-{mode}"


def binary_key(prefix: str, filename: str) -> str:
    """A compressed binary inside a v2 build dir."""
    return f"{prefix}/{BINARIES_DIRNAME}/{filename}{COMPRESSED_SUFFIX}"


def compressed_metadata_key(prefix: str) -> str:
    """The compressed ``assemblage_meta.json`` inside a v2 build dir."""
    return f"{prefix}/{METADATA_DIRNAME}/{METADATA_FILENAME}{COMPRESSED_SUFFIX}"


def export_manifest_key(prefix: str) -> str:
    """The ``export.json`` provenance manifest inside a v2 build dir.

    Written by the builder with everything it knows (source prefix, per-object
    raw/stored bytes, repo URL, commit). ``license`` is added host-side from
    ``projects.license`` — builders have no database access.
    """
    return f"{prefix}/{EXPORT_MANIFEST_FILENAME}"


def archive_key(owner: str, project: str, sha12: str) -> str:
    """The source-archive key in the ``project-archive`` bucket."""
    return f"{owner}/{project}/{sha12}.tar.gz"


def pointer_key(owner: str, project: str) -> str:
    """The ``latest.txt`` pointer key in the ``project-archive`` bucket."""
    return f"{owner}/{project}/latest.txt"

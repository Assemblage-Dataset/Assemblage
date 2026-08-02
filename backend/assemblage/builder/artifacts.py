"""Metadata generation and artifact persistence.

Builds the ``assemblage_meta.json`` payload with the frozen key set and saves
binaries + metadata either to S3 (v2 layout: compressed, under a HuggingFace-
aligned build dir) or, with no S3 configured, to a local ``successes/`` tree.
The metadata *key set* is still pinned by the E2E golden; what changed in v2 is
only where the bytes land and that they are zstd-compressed on the way.
"""

import json
import logging
import os
import shutil
import stat
import tempfile
from dataclasses import asdict, dataclass

from assemblage.build.rust import RustBuildStrategy
from assemblage.build.strategy import BuildStrategy
from assemblage.messages import BuildTask
from assemblage.storage import compress
from assemblage.storage.layout import (
    COMPRESSED_SUFFIX,
    METADATA_FILENAME,
    binary_key,
    build_dir,
    compressed_metadata_key,
    export_manifest_key,
)
from assemblage.storage.s3 import S3Bucket

logger = logging.getLogger(__name__)

# Read/write owner, read group/other — built binaries are archived, not run.
NON_EXE_MODE = stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH


@dataclass(frozen=True)
class ObjectRecord:
    """One stored object's provenance, as it appears in ``export.json``."""

    file: str
    raw_bytes: int
    stored_bytes: int


def build_prefix(
    strategy: BuildStrategy, owner: str, project: str, commit_hexsha: str, compiler_flag: str
) -> str:
    """The v2 build directory for this build.

    Rust contributes its codegen backend (``llvm``/``cranelift``/``gcc``); C/C++
    contributes its compiler. Both carry the build mode, so Debug and Release of
    one commit can no longer collide.
    """
    backend = (
        strategy.codegen_backend
        if isinstance(strategy, RustBuildStrategy)
        else strategy.compiler
    )
    return build_dir(
        owner, project, commit_hexsha, compiler_flag, str(backend), strategy.build_mode
    )


def generate_metadata(
    *,
    strategy: BuildStrategy,
    library: str,
    task: BuildTask,
    commit_hexsha: str,
    compiler_flag: str,
) -> dict[str, object]:
    """Build the metadata JSON (the caller merges in ``Binary_info_list``).

    C/C++ builds emit exactly the frozen key set. Rust builds emit the same keys PLUS
    the additive Rust keys (``Codegen_backend``/``Toolchain``/``Mangling``/
    ``Backend_caps``/``Cargo_locked``); the lowercase ``language`` key already carries
    ``"rust"`` so no duplicate ``Language`` key is added.
    """
    metadata: dict[str, object] = {
        "Platform": strategy.platform,
        "Build_mode": strategy.build_mode,
        "Compiler": strategy.compiler,
        "Compiler_version": strategy.compiler_version,
        "URL": task.url,
        "Commit": commit_hexsha,
        "Optimization": compiler_flag,
        "Pushed_at": task.updated_at,
        "compiler_flag": compiler_flag,
        "language": strategy.language,
        "library": library,
    }
    if isinstance(strategy, RustBuildStrategy):
        metadata["Codegen_backend"] = strategy.codegen_backend
        metadata["Toolchain"] = strategy.toolchain_vv
        metadata["Mangling"] = "v0"
        metadata["Backend_caps"] = strategy.backend_caps
        metadata["Cargo_locked"] = strategy.cargo_locked
    return metadata


def save_metadata_locally(clone_dir: str, metadata: dict[str, object]) -> str | None:
    """Write ``assemblage_meta.json`` into the clone dir; return its path."""
    path = os.path.join(clone_dir, METADATA_FILENAME)
    try:
        with open(path, "w") as f:
            json.dump(metadata, f, indent=2)
        logger.info("Metadata saved to %s", path)
        return path
    except OSError as e:
        logger.warning("Failed to save metadata to %s: %s", path, e)
        return None


def save_metadata_to_s3(
    *, clone_dir: str, prefix: str, metadata: dict[str, object], artifact_bucket: S3Bucket
) -> ObjectRecord | None:
    """Compress and upload ``assemblage_meta.json`` under ``prefix``.

    Returns the stored object's record, or ``None`` on failure. This payload is
    the single biggest compression win in the corpus — measured 37.6x — because
    it is DWARF function/line records serialised as JSON.
    """
    path = os.path.join(clone_dir, METADATA_FILENAME)
    try:
        with open(path, "w") as f:
            json.dump(metadata, f, indent=2)
        raw_bytes = os.path.getsize(path)
    except OSError as e:
        logger.warning("Failed to write metadata %s: %s", path, e)
        return None

    key = compressed_metadata_key(prefix)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            packed = os.path.join(tmp, METADATA_FILENAME + COMPRESSED_SUFFIX)
            stored_bytes = compress.compress_file(path, packed)
            if not artifact_bucket.upload_file(packed, key):
                logger.warning("Failed to upload metadata to S3: %s", key)
                return None
    except OSError as e:
        logger.warning("Failed to compress metadata %s: %s", path, e)
        return None

    logger.info("Metadata uploaded to S3: %s (%d -> %d bytes)", key, raw_bytes, stored_bytes)
    try:
        os.remove(path)
    except OSError as e:
        logger.warning("Failed to clean up metadata file %s: %s", path, e)
    return ObjectRecord(
        file=f"{METADATA_FILENAME}{COMPRESSED_SUFFIX}",
        raw_bytes=raw_bytes,
        stored_bytes=stored_bytes,
    )


class SavedBinaries:
    """Result of :func:`save_binaries`."""

    def __init__(
        self,
        dest: str,
        all_saved: bool,
        file_names: list[str],
        records: list[ObjectRecord] | None = None,
    ) -> None:
        self.dest = dest
        self.all_saved = all_saved
        self.file_names = file_names
        self.records = records or []


def save_binaries(
    *,
    strategy: BuildStrategy,
    target_dir: str,
    original_files: list[str],
    commit_hexsha: str,
    compiler_flag: str,
    artifact_bucket: S3Bucket | None,
    binaries_root: str,
) -> SavedBinaries:
    """Store built binaries (S3 or local) and return the reportable file names."""
    logger.info("Saving binaries under %s", target_dir)
    strategy.own_dir(os.path.dirname(target_dir))

    bin_found = {
        f
        for f in strategy.find_binaries(target_dir)
        if os.path.exists(f) and f not in original_files
    }
    if not bin_found:
        logger.warning("No binaries found, build may have failed")
        return SavedBinaries(target_dir, False, [])

    logger.info("%d binaries found", len(bin_found))
    owner, project = target_dir.rstrip("/").split("/")[-2:]
    prefix = build_prefix(strategy, owner, project, commit_hexsha, compiler_flag)

    if artifact_bucket is not None:
        dest = f"{artifact_bucket}/{prefix}"
    else:
        dest = os.path.join(binaries_root, "successes", prefix)
        os.makedirs(dest, exist_ok=True)

    all_saved = True
    file_names: list[str] = []
    records: list[ObjectRecord] = []
    # One temp dir for the whole build: a compressed copy is ~1/6 the original,
    # so even a 250 MB Rust binary adds little beside the build tree already there.
    with tempfile.TemporaryDirectory() as tmp:
        for fpath in bin_found:
            base = os.path.basename(fpath)
            try:
                raw_bytes = os.path.getsize(fpath)
                packed = os.path.join(tmp, base + COMPRESSED_SUFFIX)
                stored_bytes = compress.compress_file(fpath, packed)
            except OSError as e:
                all_saved = False
                logger.warning("Failed to compress %s: %s", fpath, e)
                continue

            if artifact_bucket is not None:
                key = binary_key(prefix, base)
                if artifact_bucket.upload_file(packed, key):
                    logger.info("Uploaded %s -> %s (%d -> %d)", fpath, key, raw_bytes, stored_bytes)
                else:
                    all_saved = False
                    logger.warning("Failed to upload %s -> %s", fpath, key)
                # Reported to the coordinator as the build-tree path, unchanged:
                # binaries.file_name has always carried this, not the object key.
                file_names.append(fpath)
            else:
                dest_file = os.path.join(dest, base + COMPRESSED_SUFFIX)
                shutil.copy2(packed, dest_file)
                try:
                    os.chmod(dest_file, NON_EXE_MODE)
                except OSError:
                    logger.warning("Failed to change permissions on %s", dest_file)
                    all_saved = False
                file_names.append(dest_file)
            records.append(
                ObjectRecord(
                    file=base + COMPRESSED_SUFFIX,
                    raw_bytes=raw_bytes,
                    stored_bytes=stored_bytes,
                )
            )

    if artifact_bucket is None:
        strategy.own_dir(dest)
    return SavedBinaries(dest, all_saved, file_names, records)


def save_export_manifest(
    *,
    prefix: str,
    artifact_bucket: S3Bucket,
    task: BuildTask,
    commit_hexsha: str,
    metadata: ObjectRecord | None,
    binaries: list[ObjectRecord],
    ir: list[ObjectRecord],
) -> bool:
    """Write ``export.json`` — this build's provenance and byte accounting.

    The same document the published corpus ships, minus ``license``: builders
    have no database access, so the host-side export step fills that in from
    ``projects.license``. Never raises — a missing manifest costs provenance, not
    the build.
    """
    manifest = {
        "source_prefix": prefix,
        "compression": f"zstd -{compress.COMPRESS_LEVEL}",
        "binaries": [asdict(record) for record in binaries],
        "metadata": asdict(metadata) if metadata is not None else None,
        "ir": [asdict(record) for record in ir],
        "has_ir": bool(ir),
        "has_metadata": metadata is not None,
        "raw_bytes": sum(r.raw_bytes for r in [*binaries, *ir] + ([metadata] if metadata else [])),
        "stored_bytes": sum(
            r.stored_bytes for r in [*binaries, *ir] + ([metadata] if metadata else [])
        ),
        "repo_url": task.url,
        "commit": commit_hexsha,
    }
    key = export_manifest_key(prefix)
    try:
        blob = json.dumps(manifest, indent=2).encode()
    except (TypeError, ValueError) as e:  # pragma: no cover - defensive
        logger.warning("Failed to serialise export manifest for %s: %s", prefix, e)
        return False
    if not artifact_bucket.put_bytes(key, blob):
        logger.warning("Failed to upload export manifest: %s", key)
        return False
    logger.info("Export manifest uploaded: %s", key)
    return True

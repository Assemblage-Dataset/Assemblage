"""Metadata generation and artifact persistence.

Builds the ``assemblage_meta.json`` payload with the frozen key set and saves
binaries + metadata either to S3 (via :mod:`assemblage.storage.layout` keys) or,
with no S3 configured, to a local ``successes/`` tree. The metadata key set and
the flat artifact prefix are pinned by the E2E golden.
"""

import json
import logging
import os
import shutil
import stat

from assemblage.build.strategy import BuildStrategy
from assemblage.messages import BuildTask
from assemblage.storage.layout import METADATA_FILENAME, artifact_key, artifact_prefix, metadata_key
from assemblage.storage.s3 import S3Bucket

logger = logging.getLogger(__name__)

# Read/write owner, read group/other — built binaries are archived, not run.
NON_EXE_MODE = stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH


def generate_metadata(
    *,
    strategy: BuildStrategy,
    library: str,
    task: BuildTask,
    commit_hexsha: str,
    compiler_flag: str,
) -> dict[str, object]:
    """Build the metadata JSON (the caller merges in ``Binary_info_list``)."""
    return {
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
) -> bool:
    """Write and upload ``assemblage_meta.json`` under ``prefix``; return success."""
    path = os.path.join(clone_dir, METADATA_FILENAME)
    try:
        with open(path, "w") as f:
            json.dump(metadata, f, indent=2)
    except OSError as e:
        logger.warning("Failed to write metadata %s: %s", path, e)
        return False

    key = metadata_key(prefix)
    if not artifact_bucket.upload_file(path, key):
        logger.warning("Failed to upload metadata to S3: %s", key)
        return False
    logger.info("Metadata uploaded to S3: %s", key)
    try:
        os.remove(path)
    except OSError as e:
        logger.warning("Failed to clean up metadata file %s: %s", path, e)
    return True


class SavedBinaries:
    """Result of :func:`save_binaries`."""

    def __init__(self, dest: str, all_saved: bool, file_names: list[str]) -> None:
        self.dest = dest
        self.all_saved = all_saved
        self.file_names = file_names


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
    prefix = artifact_prefix(owner, project, commit_hexsha, strategy.compiler, compiler_flag)

    if artifact_bucket is not None:
        dest = f"{artifact_bucket}/{prefix}"
    else:
        dest = os.path.join(binaries_root, "successes", prefix)
        os.makedirs(dest, exist_ok=True)

    all_saved = True
    file_names: list[str] = []
    for fpath in bin_found:
        base = os.path.basename(fpath)
        if artifact_bucket is not None:
            key = artifact_key(prefix, base)
            if artifact_bucket.upload_file(fpath, key):
                logger.info("Uploaded %s -> %s", fpath, key)
            else:
                all_saved = False
                logger.warning("Failed to upload %s -> %s", fpath, key)
            file_names.append(fpath)
        else:
            dest_file = os.path.join(dest, base)
            shutil.copy2(fpath, dest_file)
            try:
                os.chmod(dest_file, NON_EXE_MODE)
            except OSError:
                logger.warning("Failed to change permissions on %s", dest_file)
                all_saved = False
            file_names.append(dest_file)

    if artifact_bucket is None:
        strategy.own_dir(dest)
    return SavedBinaries(dest, all_saved, file_names)

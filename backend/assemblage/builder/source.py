"""Source acquisition: restore-from-S3-archive or git-clone, then re-archive.

``acquire_source`` reproduces the pre-re-architecture builder's clone path
exactly: try to restore the project from ``project-archive`` (via the
``latest.txt`` pointer), else ``git clone --recursive`` (or ``git pull`` on an
existing checkout); snapshot the pre-build file set; derive the 12-char commit
sha; and — for the first builder to see a repo — upload the source archive and
write the pointer. All git subprocesses go through
:func:`assemblage.build.commands.run_command`.
"""

import glob
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from urllib.parse import urlparse

from assemblage.build.commands import run_command
from assemblage.build.strategy import BuildStrategy
from assemblage.enums import CloneStatus
from assemblage.messages import BuildTask
from assemblage.storage.layout import archive_key, pointer_key
from assemblage.storage.s3 import S3Bucket

logger = logging.getLogger(__name__)

_TEMP_DIR = tempfile.gettempdir()
_GIT_TIMEOUT_S = 600.0


@dataclass
class SourceResult:
    status: CloneStatus
    message: str
    clone_dir: str
    commit_hexsha: str
    restored_from_s3: bool
    original_files: list[str]
    save_path: str | None


def parse_github_name(url: str) -> tuple[str | None, str | None]:
    """Extract (owner, project) from a repo URL (https, git@, file://, .git)."""
    if url.endswith(".git"):
        url = url[:-4]
    path = url.split(":", 1)[1] if url.startswith("git@") else urlparse(url).path
    parts = path.strip("/").split("/")
    if len(parts) >= 2:
        return parts[0], parts[1]
    return None, None


def mark_dir_as_safe(path: str) -> None:
    result = run_command(f"git config --global --add safe.directory {path}", timeout=_GIT_TIMEOUT_S)
    if result.returncode != 0:
        logger.error(
            "Failed to mark %s as safe, later git commands may fail: %s",
            path,
            result.stderr.decode(errors="ignore"),
        )


def get_project_commit(clone_dir: str) -> str:
    """The repo's HEAD as a 12-char sha (the frozen key for DB/S3/metadata)."""
    result = run_command("git rev-parse --short=12 HEAD", timeout=_GIT_TIMEOUT_S, cwd=clone_dir)
    if result.returncode == 0:
        return result.stdout.decode().strip()
    logger.error("Failed to get commit hash: %s", result.stderr.decode().strip())
    return "Unknown"


def acquire_source(
    task: BuildTask, *, strategy: BuildStrategy, project_bucket: S3Bucket | None
) -> SourceResult:
    """Restore-or-clone ``task``'s repo and (first builder) archive it back to S3."""
    owner, project = parse_github_name(task.url)

    restored = False
    message = ""
    status = CloneStatus.FAILED
    clone_dir = ""

    if project_bucket is not None and owner and project:
        pointer_commit = task.commit_hexsha or _read_pointer(project_bucket, owner, project)
        if pointer_commit:
            archive = _download_archive(project_bucket, owner, project, pointer_commit)
            if archive:
                message, status, clone_dir = _restore_archive(archive, task.url, strategy)
                restored = status == CloneStatus.SUCCESS
                _remove_quietly(archive)

    if not restored:
        logger.info("Cloning %s ...", task.url)
        message, status, clone_dir = _clone(task.url, strategy)

    original_files = list(glob.iglob(clone_dir + "**/**", recursive=True))

    commit_hexsha = ""
    save_path: str | None = f"builder:{clone_dir}"
    if status == CloneStatus.SUCCESS:
        commit_hexsha = task.commit_hexsha or get_project_commit(clone_dir)
        if project_bucket is not None and not restored:
            up_owner, up_project = clone_dir.rstrip("/").split("/")[-2:]
            logger.info("Uploading %s to %s", clone_dir, project_bucket)
            if _upload_archive(project_bucket, clone_dir, up_owner, up_project, commit_hexsha):
                save_path = archive_key(up_owner, up_project, commit_hexsha)
                project_bucket.put_bytes(pointer_key(up_owner, up_project), commit_hexsha.encode())
                logger.info("Project saved to %s", save_path)

    return SourceResult(
        status=status,
        message=message,
        clone_dir=clone_dir,
        commit_hexsha=commit_hexsha,
        restored_from_s3=restored,
        original_files=original_files,
        save_path=save_path,
    )


# --- git ---------------------------------------------------------------------


def _clone(url: str, strategy: BuildStrategy) -> tuple[str, CloneStatus, str]:
    owner, project = parse_github_name(url)
    owner = owner or os.urandom(8).hex()
    project = project or os.urandom(8).hex()

    git_user_dir = f"{strategy.base_path}/projects/{owner}"
    clone_dir = f"{git_user_dir}/{project}"
    os.makedirs(git_user_dir, exist_ok=True)

    if os.path.isdir(clone_dir):
        logger.debug("Target %s already cloned: attempting to pull", clone_dir)
        result = run_command("git pull --recurse-submodules", timeout=_GIT_TIMEOUT_S, cwd=clone_dir)
    else:
        result = run_command(f"git clone --recursive {url} {clone_dir}/", timeout=_GIT_TIMEOUT_S)

    strategy.own_dir(git_user_dir)
    status = CloneStatus.SUCCESS if result.returncode == 0 else CloneStatus.FAILED
    if status == CloneStatus.FAILED:
        _removedirs_quietly(git_user_dir)
        _removedirs_quietly(clone_dir)
        logger.warning("Error cloning: %s", result.stderr.decode(errors="ignore"))

    mark_dir_as_safe(clone_dir)
    return result.stdout.decode(errors="ignore"), status, clone_dir


def _restore_archive(
    archive_path: str, url: str, strategy: BuildStrategy
) -> tuple[str, CloneStatus, str]:
    owner, project = parse_github_name(url)
    owner = owner or os.urandom(8).hex()
    project = project or os.urandom(8).hex()

    git_user_dir = f"{strategy.base_path}/projects/{owner}"
    clone_dir = f"{git_user_dir}/{project}"
    os.makedirs(clone_dir, exist_ok=True)

    try:
        shutil.unpack_archive(archive_path, clone_dir)
        strategy.own_dir(git_user_dir)
        mark_dir_as_safe(clone_dir)
        logger.info("Restored %s from S3 archive into %s", url, clone_dir)
        return "Restored from S3 archive", CloneStatus.SUCCESS, clone_dir
    except Exception as e:
        logger.warning("Failed to restore archive for %s: %s", url, e)
        shutil.rmtree(clone_dir, ignore_errors=True)
        return str(e), CloneStatus.FAILED, clone_dir


# --- S3 archive / pointer ----------------------------------------------------


def _read_pointer(bucket: S3Bucket, owner: str, project: str) -> str | None:
    key = pointer_key(owner, project)
    local = f"{_TEMP_DIR}/{owner}_{project}_latest.txt"
    try:
        if bucket.download_file(key, local):
            with open(local) as f:
                commit = f.read().strip()
            _remove_quietly(local)
            if commit:
                logger.info("Found cached commit %s for %s/%s", commit, owner, project)
                return commit
    except OSError:
        pass
    return None


def _download_archive(bucket: S3Bucket, owner: str, project: str, commit: str) -> str | None:
    key = archive_key(owner, project, commit)
    local = f"{_TEMP_DIR}/{owner}_{project}_{commit}.tar.gz"
    try:
        if bucket.object_exists(key) and bucket.download_file(key, local):
            logger.info("Downloaded project archive from S3: %s", key)
            return local
    except OSError as e:
        logger.debug("Failed to download project archive: %s", e)
    return None


def _upload_archive(
    bucket: S3Bucket, clone_dir: str, owner: str, project: str, commit: str
) -> bool:
    try:
        archive = shutil.make_archive(f"{_TEMP_DIR}/{commit}", "gztar", clone_dir)
        if not bucket.upload_file(archive, archive_key(owner, project, commit)):
            return False
        _remove_quietly(archive)
        return True
    except Exception as e:
        logger.warning("Failed to archive %s to S3: %s", clone_dir, e)
        return False


# --- fs helpers --------------------------------------------------------------


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _removedirs_quietly(path: str) -> None:
    try:
        os.removedirs(path)
    except OSError:
        pass

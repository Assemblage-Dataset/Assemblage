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
"""

PROJECT_ARCHIVE_BUCKET = "project-archive"
ARTIFACTS_BUCKET = "artifacts"

METADATA_FILENAME = "assemblage_meta.json"


def artifact_prefix(owner: str, project: str, sha12: str, compiler: str, flag: str) -> str:
    """The flat per-build prefix in the ``artifacts`` bucket."""
    return f"{owner}_{project}_{sha12}_{compiler}_{flag}"


def artifact_key(prefix: str, filename: str) -> str:
    """An object key inside an :func:`artifact_prefix` directory."""
    return f"{prefix}/{filename}"


def metadata_key(prefix: str) -> str:
    """The ``assemblage_meta.json`` key inside an :func:`artifact_prefix`."""
    return f"{prefix}/{METADATA_FILENAME}"


def archive_key(owner: str, project: str, sha12: str) -> str:
    """The source-archive key in the ``project-archive`` bucket."""
    return f"{owner}/{project}/{sha12}.tar.gz"


def pointer_key(owner: str, project: str) -> str:
    """The ``latest.txt`` pointer key in the ``project-archive`` bucket."""
    return f"{owner}/{project}/latest.txt"

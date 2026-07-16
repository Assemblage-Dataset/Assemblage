#!/usr/bin/env python3
"""Re-stage already-downloaded raw binaries into the SQLite dataset.

When a daily run downloaded binaries into ``{dataset}/{date}/raw/`` but the
staging / ``db_construct`` step did not complete (crash, DWARF timeout, etc.),
this re-runs phase 2 of the pipeline WITHOUT re-downloading from MinIO. It
reuses :func:`assemblage.dataset.pipeline.build_staging_entry` and
``db_construct`` — the exact staging path the daily runner takes.

    DWARF_TIMEOUT_SECS=0 DB_HOST=localhost MINIO_ENDPOINT=localhost:9010 \\
        python backend/scripts/restage_from_raw.py --date 2026-03-15 --since 2026-03-09

``--since`` selects the DB rows (same query as the daily run); ``--date`` names
the dataset day directory whose ``raw/`` files are reused. Set
``DWARF_TIMEOUT_SECS=0`` to skip DWARF extraction entirely for a fast rebuild.
"""

import argparse
import datetime
import logging
import os
import shutil
import sys
from pathlib import Path

from assemblage.dataset.construct import db_construct
from assemblage.dataset.pipeline import build_staging_entry, query_new_binaries

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
SECRETS_ENV = REPO_ROOT / "secrets.env"
DEFAULT_DATASET_DIR = REPO_ROOT / "assemblage_dataset"


def _load_secrets(path):
    env = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def _db_url(env):
    host = os.environ.get("DB_HOST") or env.get("DB_HOST", "localhost")
    port = os.environ.get("DB_PORT") or env.get("DB_PORT", "5432")
    user = os.environ.get("POSTGRES_USER") or env.get("POSTGRES_USER", "assemblage")
    pw = os.environ.get("POSTGRES_PASSWORD") or env.get("POSTGRES_PASSWORD", "")
    db = os.environ.get("POSTGRES_DB") or env.get("POSTGRES_DB", "assemblage")
    return f"postgresql://{user}:{pw}@{host}:{port}/{db}"


def main():
    parser = argparse.ArgumentParser(description="Re-stage downloaded raw binaries")
    parser.add_argument(
        "--date", required=True, help="Dataset day dir with a raw/ folder (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--since", required=True, help="Fetch DB rows built after this date (YYYY-MM-DD)"
    )
    parser.add_argument("--dataset-dir", default=str(DEFAULT_DATASET_DIR))
    args = parser.parse_args()

    dataset_dir = Path(args.dataset_dir)
    date_dir = dataset_dir / args.date
    raw_dir = date_dir / "raw"
    staging_dir = date_dir / "staging"
    binaries_dir = date_dir / "binaries"
    if not raw_dir.is_dir():
        logger.error("No raw/ directory at %s", raw_dir)
        sys.exit(1)
    staging_dir.mkdir(parents=True, exist_ok=True)
    binaries_dir.mkdir(parents=True, exist_ok=True)

    env = _load_secrets(SECRETS_ENV)
    since_dt = datetime.datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=datetime.UTC)
    rows = query_new_binaries(_db_url(env), since_dt)
    logger.info("Matched %d DB rows; re-staging from %s", len(rows), raw_dir)

    staged = 0
    for row in rows:
        filename = os.path.basename(row["file_name"])
        raw_path = raw_dir / f"{row['binary_id']}_{filename}"
        if not raw_path.exists():
            continue
        try:
            build_staging_entry(row, str(raw_path), str(staging_dir))
            staged += 1
        except Exception as e:
            logger.warning("staging failed for %s: %s", raw_path.name, e)

    logger.info("Re-staged %d binaries", staged)
    if staged == 0:
        shutil.rmtree(staging_dir, ignore_errors=True)
        logger.info("Nothing staged; skipping db_construct.")
        return

    sqlite_path = str(dataset_dir / "linux_licensed.sqlite")
    db_construct(
        dbfile=sqlite_path,
        target_dir=str(staging_dir),
        include_lines=True,
        include_functions=True,
        include_rvas=True,
        include_pdbs=False,
    )
    for item in staging_dir.rglob("*"):
        if item.is_file():
            dest = binaries_dir / item.relative_to(staging_dir)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(item), str(dest))
    shutil.rmtree(staging_dir, ignore_errors=True)
    logger.info("Done. Dataset updated at %s", sqlite_path)


if __name__ == "__main__":
    main()

"""Daily dataset runner.

Append newly-built licensed Linux binaries to the cumulative dataset. Reads
configuration from ``secrets.env`` at the repository root, computes
``since = yesterday`` by default, and delegates to
:func:`assemblage.dataset.pipeline.run_pipeline`.

Entry points:
    assemblage-daily                      (console script -> main())
    python backend/scripts/run_daily_dataset.py

Usage:
    assemblage-daily [--since YYYY-MM-DD] [--dataset-dir PATH]

    --since       Override the default "yesterday" start date.
    --dataset-dir Override the default <repo>/assemblage_dataset directory.
"""

import argparse
import datetime
import logging
import os
import sys
from pathlib import Path

from assemblage.dataset.pipeline import run_pipeline

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# daily.py lives at backend/assemblage/dataset/daily.py; the repository root
# (where secrets.env and assemblage_dataset/ live) is four levels up.
REPO_ROOT = Path(__file__).resolve().parents[3]
SECRETS_ENV = REPO_ROOT / "secrets.env"
DEFAULT_DATASET_DIR = REPO_ROOT / "assemblage_dataset"


def load_secrets_env(path):
    """Parse a secrets.env file into a dict (KEY=VALUE, ignoring comments)."""
    env = {}
    if not path.exists():
        logger.warning("secrets.env not found at %s — using environment variables", path)
        return env
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def build_db_url(env):
    # os.environ takes priority over secrets.env (allows host-side overrides)
    host = os.environ.get("DB_HOST") or env.get("DB_HOST", "localhost")
    port = os.environ.get("DB_PORT") or env.get("DB_PORT", "5432")
    user = os.environ.get("POSTGRES_USER") or env.get("POSTGRES_USER", "assemblage")
    password = os.environ.get("POSTGRES_PASSWORD") or env.get("POSTGRES_PASSWORD", "")
    db = os.environ.get("POSTGRES_DB") or env.get("POSTGRES_DB", "assemblage")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def main():
    parser = argparse.ArgumentParser(description="Run the daily Assemblage dataset pipeline")
    parser.add_argument(
        "--since",
        default=None,
        help="Fetch binaries built after this date (YYYY-MM-DD). Default: yesterday.",
    )
    parser.add_argument(
        "--dataset-dir",
        default=str(DEFAULT_DATASET_DIR),
        help="Root dataset directory. Default: <repo>/assemblage_dataset",
    )
    args = parser.parse_args()

    # Determine 'since' date
    if args.since:
        since_str = args.since
    else:
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        since_str = yesterday.isoformat()

    dataset_dir = Path(args.dataset_dir)
    today_str = datetime.date.today().isoformat()

    # Set up per-run log file
    log_dir = dataset_dir / today_str
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "pipeline.log"
    fh = logging.FileHandler(str(log_file))
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(fh)

    logger.info("=== Daily dataset pipeline starting ===")
    logger.info("Since date : %s", since_str)
    logger.info("Dataset dir: %s", dataset_dir)

    # Load config
    env = load_secrets_env(SECRETS_ENV)

    db_url = build_db_url(env)

    s3_endpoint = (
        os.environ.get("MINIO_ENDPOINT")
        or env.get("MINIO_ENDPOINT")
        or env.get("S3_HOST")
        or "localhost:9000"
    )
    # Strip http(s):// prefix if accidentally included
    s3_endpoint = s3_endpoint.replace("http://", "").replace("https://", "")

    s3_access_key = (
        os.environ.get("MINIO_ACCESS_KEY")
        or env.get("MINIO_ACCESS_KEY")
        or env.get("S3_ACCESS_KEY")
        or "minioadmin"
    )
    s3_secret_key = (
        os.environ.get("MINIO_SECRET_KEY")
        or env.get("MINIO_SECRET_KEY")
        or env.get("S3_SECRET_ACCESS_KEY")
        or "minioadmin"
    )
    s3_https = env.get("S3_HTTPS", "false").lower() in ("1", "true", "yes")
    bucket = env.get("S3_ARTIFACTS_BUCKET", "artifacts")

    try:
        run_pipeline(
            since_date_str=since_str,
            dataset_dir=str(dataset_dir),
            db_url=db_url,
            s3_endpoint=s3_endpoint,
            s3_access_key=s3_access_key,
            s3_secret_key=s3_secret_key,
            bucket=bucket,
            s3_https=s3_https,
        )
        logger.info("=== Pipeline completed successfully ===")
    except Exception as e:
        logger.exception("=== Pipeline failed: %s ===", e)
        sys.exit(1)


if __name__ == "__main__":
    main()

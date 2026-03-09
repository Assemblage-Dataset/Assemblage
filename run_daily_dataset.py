"""
run_daily_dataset.py

Call this script daily (manually or via cron) to append new licensed Linux
binaries to the cumulative dataset.

It reads configuration from secrets.env in the same directory, computes
since = yesterday, and delegates to minio_pipeline.run_pipeline().

Usage:
    python run_daily_dataset.py [--since YYYY-MM-DD] [--dataset-dir PATH]

    --since       Override the default "yesterday" start date.
    --dataset-dir Override the default ./assemblage_dataset directory.
"""

import argparse
import datetime
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent
SECRETS_ENV = SCRIPT_DIR / "secrets.env"
DEFAULT_DATASET_DIR = SCRIPT_DIR / "assemblage_dataset"
CLI_DIR = SCRIPT_DIR / "Assemblage_dataset_cli"


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
    host = env.get("DB_HOST") or os.environ.get("DB_HOST", "localhost")
    port = env.get("DB_PORT") or os.environ.get("DB_PORT", "5432")
    user = env.get("POSTGRES_USER") or os.environ.get("POSTGRES_USER", "assemblage")
    password = env.get("POSTGRES_PASSWORD") or os.environ.get("POSTGRES_PASSWORD", "")
    db = env.get("POSTGRES_DB") or os.environ.get("POSTGRES_DB", "assemblage")
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
        help="Root dataset directory. Default: ./assemblage_dataset",
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
        env.get("MINIO_ENDPOINT")
        or env.get("S3_HOST")
        or os.environ.get("MINIO_ENDPOINT", "localhost:9000")
    )
    # Strip http(s):// prefix if accidentally included
    s3_endpoint = s3_endpoint.replace("http://", "").replace("https://", "")

    s3_access_key = (
        env.get("MINIO_ACCESS_KEY")
        or env.get("S3_ACCESS_KEY")
        or os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
    )
    s3_secret_key = (
        env.get("MINIO_SECRET_KEY")
        or env.get("S3_SECRET_ACCESS_KEY")
        or os.environ.get("MINIO_SECRET_KEY", "minioadmin")
    )
    s3_https = (env.get("S3_HTTPS", "false").lower() in ("1", "true", "yes"))
    bucket = env.get("S3_ARTIFACTS_BUCKET", "artifacts")

    # Add the CLI directory to sys.path so we can import minio_pipeline
    if str(CLI_DIR) not in sys.path:
        sys.path.insert(0, str(CLI_DIR))

    from minio_pipeline import run_pipeline

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

"""Download all artifact folders from MinIO to a local directory."""
import json
import os
import sys
import logging
from multiprocessing import Pool

import boto3
import psycopg2

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

DEST = sys.argv[1] if len(sys.argv) > 1 else "dataset/staging"
ENDPOINT = os.environ.get("MINIO_ENDPOINT", "localhost:9010")
ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "minioadmin")
SECRET_KEY = os.environ.get("S3_SECRET_ACCESS_KEY", "minioadmin")
WORKERS = int(os.environ.get("WORKERS", "128"))
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_USER = os.environ.get("POSTGRES_USER", "assemblage")
DB_PASS = os.environ.get("POSTGRES_PASSWORD", "assemblage")
DB_NAME = os.environ.get("POSTGRES_DB", "assemblage")

_s3 = None

def get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", endpoint_url=f"http://{ENDPOINT}",
            aws_access_key_id=ACCESS_KEY, aws_secret_access_key=SECRET_KEY,
            region_name="us-east-1")
    return _s3

# Load licenses from PG: url -> license
licenses = {}
try:
    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS, dbname=DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT url, license FROM projects WHERE license != ''")
    for url, lic in cur.fetchall():
        slug = url.replace("https://github.com/", "").replace("https://api.github.com/repos/", "").strip("/")
        licenses[slug] = lic
    cur.close()
    conn.close()
    logger.info(f"Loaded {len(licenses)} licenses from PG")
except Exception as e:
    logger.warning(f"Could not load licenses from PG: {e}")

os.makedirs(DEST, exist_ok=True)

# List all prefixes (main thread)
logger.info("Listing prefixes...")
prefixes = []
s3 = get_s3()
for page in s3.get_paginator("list_objects_v2").paginate(Bucket="artifacts", Delimiter="/"):
    for p in page.get("CommonPrefixes", []):
        prefixes.append(p["Prefix"].rstrip("/"))
logger.info(f"{len(prefixes)} prefixes in MinIO")

# Skip already downloaded
existing = set(os.listdir(DEST))
todo = [p for p in prefixes if p not in existing]
logger.info(f"{len(prefixes) - len(todo)} already downloaded, {len(todo)} to download")


def download_one(prefix):
    try:
        client = get_s3()
        dest = os.path.join(DEST, prefix)
        os.makedirs(dest, exist_ok=True)
        for page in client.get_paginator("list_objects_v2").paginate(Bucket="artifacts", Prefix=f"{prefix}/"):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                fname = key[len(prefix) + 1:]
                if fname:
                    fpath = os.path.join(dest, fname)
                    os.makedirs(os.path.dirname(fpath), exist_ok=True)
                    client.download_file("artifacts", key, fpath)
        # Inject license from PG into meta
        meta_path = os.path.join(dest, "assemblage_meta.json")
        if licenses and os.path.isfile(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
            if not meta.get("License"):
                url = meta.get("URL", meta.get("url", ""))
                slug = url.replace("https://github.com/", "").replace(
                    "https://api.github.com/repos/", "").strip("/")
                if slug in licenses:
                    meta["License"] = licenses[slug]
                    with open(meta_path, "w") as f:
                        json.dump(meta, f, indent=2)
        return prefix, True, None
    except Exception as e:
        return prefix, False, str(e)


downloaded = 0
failed = 0
total = len(todo)

if __name__ == "__main__":
    errors = []
    with Pool(processes=WORKERS) as pool:
        for prefix, ok, err in pool.imap_unordered(download_one, todo):
            if ok:
                downloaded += 1
            else:
                failed += 1
                errors.append((prefix, err))
            done = downloaded + failed
            print(f"\r[{done}/{total}] ({downloaded} ok, {failed} fail)", end="", flush=True)
    print()
    for prefix, err in errors:
        logger.error(f"{prefix}: {err}")

    logger.info(f"Done: {downloaded} downloaded, {failed} failed")
    logger.info(f"Now run: cd Assemblage_dataset_cli && python cli.py -g --data ../{DEST} --dbfile ../dataset/linux_licensed.sqlite --lines --functions --rvas")

#!/usr/bin/env python3
"""Download all deephistory-artifacts from MinIO to deephistory/binaries/.

Each prefix in deephistory-artifacts is of form:
    <user>_<project>_<commit>_<compiler>_<flag>/

and contains binaries + assemblage_meta.json. Idempotent: skips prefixes
whose local dir already has an assemblage_meta.json (i.e. fully downloaded).
"""
import os
import sys
import logging
from multiprocessing import Pool

import boto3

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logger = logging.getLogger("dh-download")

DEST = sys.argv[1] if len(sys.argv) > 1 else "deephistory/binaries"
BUCKET = os.environ.get("BUCKET", "deephistory-artifacts")
ENDPOINT = os.environ.get("MINIO_ENDPOINT", "localhost:9010")
ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "minioadmin")
SECRET_KEY = os.environ.get("S3_SECRET_ACCESS_KEY", "minioadmin")
WORKERS = int(os.environ.get("WORKERS", "64"))

_s3 = None
def get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client(
            "s3", endpoint_url=f"http://{ENDPOINT}",
            aws_access_key_id=ACCESS_KEY, aws_secret_access_key=SECRET_KEY,
            region_name="us-east-1",
        )
    return _s3


def download_one(prefix):
    try:
        client = get_s3()
        dest = os.path.join(DEST, prefix)
        # Skip if meta file already present (complete prior download)
        if os.path.isfile(os.path.join(dest, "assemblage_meta.json")):
            return prefix, True, "skip"
        os.makedirs(dest, exist_ok=True)
        for page in client.get_paginator("list_objects_v2").paginate(
                Bucket=BUCKET, Prefix=f"{prefix}/"):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                fname = key[len(prefix) + 1:]
                if not fname:
                    continue
                fpath = os.path.join(dest, fname)
                os.makedirs(os.path.dirname(fpath), exist_ok=True)
                if os.path.isfile(fpath) and os.path.getsize(fpath) == obj["Size"]:
                    continue
                client.download_file(BUCKET, key, fpath)
        return prefix, True, "ok"
    except Exception as e:
        return prefix, False, str(e)


def main():
    os.makedirs(DEST, exist_ok=True)
    logger.info("Listing prefixes in %s ...", BUCKET)
    s3 = get_s3()
    prefixes = []
    for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=BUCKET, Delimiter="/"):
        for p in page.get("CommonPrefixes", []):
            prefixes.append(p["Prefix"].rstrip("/"))
    logger.info("Found %d prefixes", len(prefixes))

    downloaded = skipped = failed = 0
    errors = []
    with Pool(processes=WORKERS) as pool:
        for i, (prefix, ok, msg) in enumerate(
                pool.imap_unordered(download_one, prefixes), 1):
            if ok and msg == "skip":
                skipped += 1
            elif ok:
                downloaded += 1
            else:
                failed += 1
                errors.append((prefix, msg))
            if i % 50 == 0 or i == len(prefixes):
                print(f"\r[{i}/{len(prefixes)}] dl={downloaded} skip={skipped} fail={failed}",
                      end="", flush=True)
    print()
    for pfx, err in errors[:20]:
        logger.error("%s: %s", pfx, err)
    logger.info("Done. downloaded=%d skipped=%d failed=%d", downloaded, skipped, failed)


if __name__ == "__main__":
    main()

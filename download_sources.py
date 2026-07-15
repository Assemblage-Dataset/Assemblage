#!/usr/bin/env python3
"""Download project-archive bucket from MinIO to local disk."""

import argparse
import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.config import Config

_local = threading.local()

S3_CONFIG = Config(
    read_timeout=600,
    connect_timeout=30,
    retries={"max_attempts": 5, "mode": "adaptive"},
)


def make_s3(endpoint, ak, sk):
    return boto3.client("s3", endpoint_url=endpoint, aws_access_key_id=ak,
                        aws_secret_access_key=sk, config=S3_CONFIG)


def list_objects(s3, bucket, prefix=""):
    paginator = s3.get_paginator("list_objects_v2")
    params = {"Bucket": bucket}
    if prefix:
        params["Prefix"] = prefix
    for page in paginator.paginate(**params):
        for obj in page.get("Contents", []):
            yield obj["Key"], obj["Size"]


def download_one(endpoint, ak, sk, bucket, key, size, dest):
    if not hasattr(_local, "s3"):
        _local.s3 = make_s3(endpoint, ak, sk)

    local_path = os.path.join(dest, key)
    if os.path.exists(local_path) and os.path.getsize(local_path) == size:
        return key, size, "skipped"

    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    _local.s3.download_file(bucket, key, local_path)
    return key, size, "ok"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dest", required=True)
    p.add_argument("--endpoint", default="http://localhost:9010")
    p.add_argument("--access-key", default="minioadmin")
    p.add_argument("--secret-key", default="minioadmin")
    p.add_argument("--bucket", default="project-archive")
    p.add_argument("--prefix", default="")
    p.add_argument("--workers", type=int, default=32)
    p.add_argument("--batch-size", type=int, default=500)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    s3 = make_s3(args.endpoint, args.access_key, args.secret_key)
    print("Listing objects...", end="", flush=True)
    objects = []
    for key, size in list_objects(s3, args.bucket, args.prefix):
        objects.append((key, size))
        if len(objects) % 5000 == 0:
            print(f"\rListing objects... {len(objects)}", end="", flush=True)
    total_size = sum(s for _, s in objects)
    print(f"\rObjects: {len(objects)}, Total: {total_size / (1024**3):.2f} GB")

    if args.dry_run:
        return

    ok = skip = fail = 0
    bytes_done = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i in range(0, len(objects), args.batch_size):
            batch = objects[i:i + args.batch_size]
            futs = {
                pool.submit(download_one, args.endpoint, args.access_key, args.secret_key,
                            args.bucket, k, s, args.dest): (k, s)
                for k, s in batch
            }
            for fut in as_completed(futs):
                k, s = futs[fut]
                try:
                    _, _, st = fut.result()
                    bytes_done += s
                    if st == "skipped":
                        skip += 1
                    else:
                        ok += 1
                except Exception as e:
                    fail += 1
                    print(f"\n  FAIL {k}: {e}", file=sys.stderr)

            done = ok + skip + fail
            elapsed = time.time() - t0
            rate = bytes_done / (1024**2) / max(elapsed, 1)
            print(f"\r[{done}/{len(objects)}] {bytes_done/(1024**3):.1f}/{total_size/(1024**3):.1f} GB  {rate:.1f} MB/s  ok={ok} skip={skip} fail={fail}    ", end="", flush=True)

    elapsed = time.time() - t0
    print(f"\nDone: {ok} ok, {skip} skipped, {fail} failed in {elapsed:.0f}s")


if __name__ == "__main__":
    main()

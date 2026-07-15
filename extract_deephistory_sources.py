#!/usr/bin/env python3
"""Stream-download + extract deephistory-sources tarballs from MinIO.

Tarballs are laid out in the bucket as <owner>/<project>/<sha>.tar.gz and
extract into <DEST>/<owner>/<project>/<sha>/. A sentinel file
.extracted is written after a successful extract so reruns are idempotent.
"""

import argparse
import os
import sys
import tarfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.config import Config

S3_CONFIG = Config(
    read_timeout=600,
    connect_timeout=30,
    retries={"max_attempts": 5, "mode": "adaptive"},
)

_local = threading.local()


def make_s3(endpoint, ak, sk):
    return boto3.client(
        "s3", endpoint_url=endpoint,
        aws_access_key_id=ak, aws_secret_access_key=sk,
        config=S3_CONFIG,
    )


def list_tarballs(s3, bucket):
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".tar.gz"):
                yield key, obj["Size"]


def extract_one(endpoint, ak, sk, bucket, key, size, dest):
    if not hasattr(_local, "s3"):
        _local.s3 = make_s3(endpoint, ak, sk)

    rel = key[:-len(".tar.gz")]
    out_dir = os.path.join(dest, rel)
    marker = os.path.join(out_dir, ".extracted")
    if os.path.isfile(marker):
        return key, size, "skipped"

    os.makedirs(out_dir, exist_ok=True)
    obj = _local.s3.get_object(Bucket=bucket, Key=key)
    with tarfile.open(fileobj=obj["Body"], mode="r|gz") as tar:
        tar.extractall(out_dir, filter="data")
    with open(marker, "w") as f:
        f.write("ok\n")
    return key, size, "ok"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dest", default="/home/cliu57/research/deephistory/source_codes")
    p.add_argument("--endpoint", default="http://localhost:9010")
    p.add_argument("--access-key", default="minioadmin")
    p.add_argument("--secret-key", default="minioadmin")
    p.add_argument("--bucket", default="deephistory-sources")
    p.add_argument("--workers", type=int, default=16)
    args = p.parse_args()

    os.makedirs(args.dest, exist_ok=True)
    s3 = make_s3(args.endpoint, args.access_key, args.secret_key)

    print("Listing tarballs...", end="", flush=True)
    objects = list(list_tarballs(s3, args.bucket))
    total_bytes = sum(s for _, s in objects)
    print(f"\rObjects: {len(objects)}, Compressed: {total_bytes / (1024**3):.2f} GiB")

    ok = skip = fail = 0
    bytes_done = 0
    t0 = time.time()
    last_log = t0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {
            pool.submit(extract_one, args.endpoint, args.access_key, args.secret_key,
                        args.bucket, k, s, args.dest): (k, s)
            for k, s in objects
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
                print(f"\nFAIL {k}: {e}", file=sys.stderr, flush=True)

            done = ok + skip + fail
            now = time.time()
            if now - last_log >= 2 or done == len(objects):
                elapsed = max(now - t0, 1)
                rate = bytes_done / (1024**2) / elapsed
                print(
                    f"\r[{done}/{len(objects)}] "
                    f"{bytes_done/(1024**3):.1f}/{total_bytes/(1024**3):.1f} GiB  "
                    f"{rate:.1f} MiB/s  ok={ok} skip={skip} fail={fail}    ",
                    end="", flush=True,
                )
                last_log = now

    print(f"\nDone in {time.time() - t0:.0f}s: ok={ok} skip={skip} fail={fail}")


if __name__ == "__main__":
    main()

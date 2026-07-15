#!/usr/bin/env python3
"""Retry pass for deephistory-sources failures.

Reads keys from a file (one per line), downloads each tarball to a temp
file (boto3 retries the whole request on stream errors), then extracts
with the permissive "tar" filter which allows absolute symlinks while
still preventing path escape.
"""

import argparse
import os
import sys
import tarfile
import tempfile
import time

import boto3
from botocore.config import Config

S3_CONFIG = Config(
    read_timeout=1200,
    connect_timeout=30,
    retries={"max_attempts": 10, "mode": "adaptive"},
)


def read_keys(path):
    keys = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("FAIL "):
                line = line[5:]
            if ":" in line:
                line = line.split(":", 1)[0]
            keys.append(line)
    return keys


def process(s3, bucket, key, dest):
    rel = key[:-len(".tar.gz")]
    out_dir = os.path.join(dest, rel)
    marker = os.path.join(out_dir, ".extracted")
    if os.path.isfile(marker):
        return "skipped"
    os.makedirs(out_dir, exist_ok=True)

    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        s3.download_file(bucket, key, tmp_path)
        with tarfile.open(tmp_path, "r:gz") as tar:
            tar.extractall(out_dir, filter="tar")
        with open(marker, "w") as f:
            f.write("retry-ok\n")
        return "ok"
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--keys", required=True, help="file listing S3 keys")
    p.add_argument("--dest", default="/home/cliu57/research/deephistory/source_codes")
    p.add_argument("--endpoint", default="http://localhost:9010")
    p.add_argument("--access-key", default="minioadmin")
    p.add_argument("--secret-key", default="minioadmin")
    p.add_argument("--bucket", default="deephistory-sources")
    args = p.parse_args()

    s3 = boto3.client(
        "s3", endpoint_url=args.endpoint,
        aws_access_key_id=args.access_key, aws_secret_access_key=args.secret_key,
        config=S3_CONFIG,
    )

    keys = read_keys(args.keys)
    print(f"Retrying {len(keys)} keys")
    ok = skip = fail = 0
    t0 = time.time()
    for i, key in enumerate(keys, 1):
        try:
            st = process(s3, args.bucket, key, args.dest)
            if st == "skipped":
                skip += 1
            else:
                ok += 1
            print(f"[{i}/{len(keys)}] {st}: {key}", flush=True)
        except Exception as e:
            fail += 1
            print(f"[{i}/{len(keys)}] FAIL: {key}: {e}", file=sys.stderr, flush=True)
    print(f"Done in {time.time() - t0:.0f}s: ok={ok} skip={skip} fail={fail}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Transfer MinIO buckets to a remote machine over a persistent SSH pipe using a thread pool."""

import argparse
import os
import sys
import time
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3


def make_s3_client(endpoint, access_key, secret_key):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )


def list_objects(s3, bucket, prefix=""):
    """Yield all (key, size) pairs in a bucket."""
    paginator = s3.get_paginator("list_objects_v2")
    params = {"Bucket": bucket}
    if prefix:
        params["Prefix"] = prefix
    for page in paginator.paginate(**params):
        for obj in page.get("Contents", []):
            yield obj["Key"], obj["Size"]


_local = threading.local()


def transfer_one(s3_endpoint, access_key, secret_key, bucket, key, size,
                 ssh_dest, dest_base):
    """Download from MinIO, upload via single ssh command."""
    if not hasattr(_local, "s3"):
        _local.s3 = make_s3_client(s3_endpoint, access_key, secret_key)

    remote_path = f"{dest_base}/{bucket}/{key}"
    remote_dir = remote_path.rsplit("/", 1)[0]

    # Download from MinIO into memory
    buf = _local.s3.get_object(Bucket=bucket, Key=key)["Body"].read()

    # Single ssh call: mkdir + write file
    proc = subprocess.run(
        ["ssh", ssh_dest, f"mkdir -p '{remote_dir}' && cat > '{remote_path}'"],
        input=buf, capture_output=True, timeout=300
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode())

    return key, size, "ok"


def main():
    parser = argparse.ArgumentParser(description="Transfer MinIO buckets to remote via SSH")
    parser.add_argument("--ssh-dest", required=True,
                        help="SSH destination, e.g. cliu57@lcs-vc-kkmicin2.syr.edu")
    parser.add_argument("--dest", required=True, help="Remote base directory")
    parser.add_argument("--minio-endpoint", default="http://localhost:9010")
    parser.add_argument("--minio-access-key", default="minioadmin")
    parser.add_argument("--minio-secret-key", default="minioadmin")
    parser.add_argument("--buckets", nargs="+", default=["project-archive", "artifacts"])
    parser.add_argument("--prefix", default="", help="Only transfer keys under this prefix")
    parser.add_argument("--workers", type=int, default=16, help="Thread pool size")
    parser.add_argument("--batch-size", type=int, default=500, help="Submit futures in batches")
    parser.add_argument("--dry-run", action="store_true", help="List files without transferring")
    args = parser.parse_args()

    # Verify SSH works
    ret = subprocess.run(
        ["ssh", args.ssh_dest, "echo ok"],
        capture_output=True, text=True, timeout=15
    )
    if ret.returncode != 0:
        print(f"SSH connection failed: {ret.stderr}", file=sys.stderr)
        sys.exit(1)
    print("SSH connection verified.")

    s3 = make_s3_client(args.minio_endpoint, args.minio_access_key, args.minio_secret_key)

    for bucket in args.buckets:
        print(f"\n=== Bucket: {bucket} ===")
        objects = list(list_objects(s3, bucket, prefix=args.prefix))
        total_size = sum(s for _, s in objects)
        print(f"Objects: {len(objects)}, Total size: {total_size / (1024**3):.2f} GB")

        if args.dry_run:
            continue

        transferred = 0
        skipped = 0
        failed = 0
        bytes_done = 0
        t0 = time.time()

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for batch_start in range(0, len(objects), args.batch_size):
                batch = objects[batch_start:batch_start + args.batch_size]
                futures = {
                    pool.submit(
                        transfer_one,
                        args.minio_endpoint, args.minio_access_key, args.minio_secret_key,
                        bucket, key, size,
                        args.ssh_dest, args.dest,
                    ): (key, size)
                    for key, size in batch
                }

                for fut in as_completed(futures):
                    key, size = futures[fut]
                    try:
                        _, _, status = fut.result()
                        bytes_done += size
                        if status == "skipped":
                            skipped += 1
                        else:
                            transferred += 1
                    except Exception as e:
                        failed += 1
                        print(f"\n  FAIL {key}: {e}", file=sys.stderr)

                done = transferred + skipped + failed
                elapsed = time.time() - t0
                rate = bytes_done / (1024**2) / max(elapsed, 1)
                print(f"\r  [{done}/{len(objects)}] {bytes_done/(1024**3):.1f}/{total_size/(1024**3):.1f} GB  {rate:.1f} MB/s  ok={transferred} skip={skipped} fail={failed}    ", end="", flush=True)

        elapsed = time.time() - t0
        print(f"\nDone: {transferred} transferred, {skipped} skipped, {failed} failed "
              f"in {elapsed:.0f}s ({bytes_done/(1024**3):.2f} GB)")


if __name__ == "__main__":
    main()

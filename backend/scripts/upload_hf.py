"""Upload the permissive Rust corpus to the HuggingFace Hub.

Uses ``upload_large_folder``, which chunks, hashes and resumes on its own: it
keeps its bookkeeping in ``<folder>/.cache/huggingface/`` so an interrupted run
picks up where it stopped rather than re-hashing 318 GB. Re-run the same command
after any failure.
"""

from __future__ import annotations

import argparse
import sys

from huggingface_hub import HfApi


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--folder", default="/home/cliu57/research/Assemblage/assemblage-rust")
    ap.add_argument("--repo", default="changliu8541/assemblage-rust")
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()

    api = HfApi()
    print(f"uploading {args.folder} -> {args.repo} with {args.workers} workers", flush=True)
    api.upload_large_folder(
        folder_path=args.folder,
        repo_id=args.repo,
        repo_type="dataset",
        num_workers=args.workers,
        print_report=True,
    )
    print("upload complete", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

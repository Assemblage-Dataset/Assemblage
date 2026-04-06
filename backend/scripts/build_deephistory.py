#!/usr/bin/env python3
"""
DeepHistory Dataset Builder

Builds multi-version C/C++ libraries using Conan on Windows with MSVC.
Outputs a flat folder of build artifacts ready for the Assemblage dataset CLI.

Output structure:
    <output_dir>/
        <md5(url)>_x64_<mode>_<toolset>_<opt>/
            assemblage_meta.json
            <identifier>_package.dll
            <identifier>_package.exe
            <identifier>_package.pdb
            ...

To construct the SQLite database from the output, use the Assemblage dataset CLI:
    python Assemblage_dataset_cli/cli.py -g --data <output_dir> --dbfile deephistory.sqlite --functions --lines --rvas --pdbs

Usage:
    # Build all from manifest
    python scripts/build_deephistory.py --manifest assemblage/legacy/deephistory_manifest.json

    # Build specific packages
    python scripts/build_deephistory.py --packages sqlite3 fmt zlib

    # Dry run
    python scripts/build_deephistory.py --manifest assemblage/legacy/deephistory_manifest.json --dry-run

    # Resume (skip identifiers that already exist)
    python scripts/build_deephistory.py --manifest assemblage/legacy/deephistory_manifest.json --resume
"""

import argparse
import hashlib
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from assemblage.legacy.conan_strategy import (
    ConanBuildStrategy,
    DEEPHISTORY_BUILD_MODES,
    DEEPHISTORY_OPTIMIZATIONS,
)
from assemblage.consts import BINPATH

logger = logging.getLogger("deephistory")


def identifier_exists(output_base: str, github_url: str, package_name: str,
                      build_mode: str, toolset: str, optimization: str) -> bool:
    """Check if a build identifier folder already exists."""
    url_hash = hashlib.md5((github_url or package_name).encode()).hexdigest()
    identifier = f"{url_hash}_x64_{build_mode}_{toolset}_{optimization}"
    folder = os.path.join(output_base, identifier)
    return os.path.isdir(folder) and os.path.isfile(os.path.join(folder, "assemblage_meta.json"))


def load_manifest(manifest_path: str) -> dict:
    with open(manifest_path) as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(
        description="Build DeepHistory binary dataset using Conan"
    )
    parser.add_argument("--manifest", type=str, default=None,
                        help="Path to deephistory_manifest.json")
    parser.add_argument("--packages", nargs="+", default=None,
                        help="Specific packages to build")
    parser.add_argument("--versions", nargs="+", default=None,
                        help="Specific versions (only with single --package)")
    parser.add_argument("--build-modes", nargs="+", default=None,
                        choices=["Debug", "RelWithDebInfo", "Release"],
                        help=f"Build modes (default: {DEEPHISTORY_BUILD_MODES})")
    parser.add_argument("--optimizations", nargs="+", default=None,
                        choices=["Od", "O1", "O2", "Ox"],
                        help=f"Optimization levels (default: {DEEPHISTORY_OPTIMIZATIONS})")
    parser.add_argument("--output", type=str,
                        default=os.getenv("DEEPHISTORY_OUTPUT", os.path.join(BINPATH, "deephistory")),
                        help="Output base directory")
    parser.add_argument("--resume", action="store_true",
                        help="Skip builds whose output folder already exists")
    parser.add_argument("--dry-run", action="store_true",
                        help="List what would be built without building")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    build_modes = args.build_modes or DEEPHISTORY_BUILD_MODES
    optimizations = args.optimizations or DEEPHISTORY_OPTIMIZATIONS

    # Build work list
    work_items = []

    if args.manifest:
        manifest = load_manifest(args.manifest)
        filter_pkgs = set(args.packages) if args.packages else None

        for pkg_name, pkg_info in manifest.items():
            if filter_pkgs and pkg_name not in filter_pkgs:
                continue
            versions = args.versions or pkg_info.get("versions", [])
            github_url = pkg_info.get("github_url", "")

            for ver in versions:
                for bm in build_modes:
                    for opt in optimizations:
                        work_items.append((pkg_name, ver, bm, opt, github_url))

    elif args.packages:
        for pkg_name in args.packages:
            versions = args.versions or ["latest"]
            for ver in versions:
                for bm in build_modes:
                    for opt in optimizations:
                        work_items.append((pkg_name, ver, bm, opt, ""))
    else:
        parser.error("Must specify --manifest or --packages")

    logger.info(f"Total build configs: {len(work_items)}")

    if args.dry_run:
        print(f"\nDry run: {len(work_items)} configurations\n")
        for pkg, ver, bm, opt, gh in work_items[:50]:
            print(f"  {pkg}/{ver}  [{bm}] [/{opt}]")
        if len(work_items) > 50:
            print(f"  ... and {len(work_items) - 50} more")
        return

    os.makedirs(args.output, exist_ok=True)
    strategy = ConanBuildStrategy(output_base=args.output)

    total = len(work_items)
    success_count = 0
    fail_count = 0
    skip_count = 0
    start_time = time.time()

    for i, (pkg, ver, bm, opt, gh) in enumerate(work_items, 1):
        if args.resume:
            toolset = strategy._resolve_toolset()
            if identifier_exists(args.output, gh, pkg, bm, toolset, opt):
                skip_count += 1
                continue

        logger.info(f"[{i}/{total}] {pkg}/{ver} [{bm}] [/{opt}]")

        try:
            final_dir, status, meta = strategy.build_package(
                pkg, ver, bm, opt, gh
            )
            if status == "success":
                success_count += 1
            else:
                fail_count += 1
        except Exception as e:
            logger.error(f"Exception building {pkg}/{ver}: {e}", exc_info=True)
            fail_count += 1

        if i % 10 == 0:
            elapsed = time.time() - start_time
            logger.info(
                f"Progress: {i}/{total} | OK={success_count} FAIL={fail_count} SKIP={skip_count}"
            )

    elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"DeepHistory build complete")
    print(f"  Total:     {total}")
    print(f"  Succeeded: {success_count}")
    print(f"  Failed:    {fail_count}")
    print(f"  Skipped:   {skip_count}")
    print(f"  Time:      {elapsed:.0f}s ({elapsed/60:.1f}m)")
    print(f"  Output:    {args.output}")
    print(f"{'='*60}")
    print(f"\nTo build SQLite DB from output:")
    print(f"  python Assemblage_dataset_cli/cli.py -g --data {args.output} --dbfile deephistory.sqlite --functions --lines --rvas --pdbs")


if __name__ == "__main__":
    main()

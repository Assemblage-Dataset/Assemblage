"""Collect, scope and pack rustc's intermediate representations.

rustc lowers AST -> HIR -> THIR -> MIR -> LLVM-IR -> machine code. Two mechanisms
get those out, and they cost very differently:

- **ride-along** (``IrStage.rides_along``): ``--emit=link,llvm-ir,mir,asm`` makes the
  *normal* build drop ``.ll`` / ``.mir`` / ``.s`` next to the object files. One build.
- **separate pass**: ``-Zunpretty=hir-tree`` and friends *replace* codegen -- they
  print IR and produce no object -- so each needs its own
  ``cargo rustc -p <member> -- -Zunpretty=<mode>`` invocation.

Backend-native IRs (cg_gcc's GIMPLE, cranelift's CLIF) are declared per adapter in
:class:`assemblage.build.rust.IrCaps`, since a backend can only dump its own IR.

Scoping is the difference between feasible and not. Measured on a real 67-crate
repo at RelWithDebInfo (2026-07-17): ~350 MB of raw IR per build, of which **93% is
dependency crates** -- crates.io code identical across every repo that depends on
it. ``IrScope.REPO`` keeps only the repo's own crates (~19 MB raw, ~2 MB gzipped),
mirroring the ``origin: in_repo`` split the DWARF metadata already draws.

Everything is packed one gzipped tarball per stage: IR is many small text files and
per-file S3 round-trips would dominate. gzip (stdlib) gets ~8.7x; zstd is absent
from the worker image and xz costs 19s for 12.8x, which is not worth it here.
"""

from __future__ import annotations

import io
import json
import logging
import os
import shlex
import tarfile
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from assemblage.build.commands import run_command
from assemblage.build.rust import RustCodegenAdapter
from assemblage.enums import IrScope, IrStage

logger = logging.getLogger(__name__)

# cg_gcc's dumps land here: libgccjit's CG_GCCJIT_DUMP_TO_FILE writes to a fixed
# path, not one we can choose (verified nightly-2026-06-15, 2026-07-17).
GCCJIT_DUMP_DIR = "/tmp/gccjit_dumps"

_SUFFIX_TO_STAGE = {
    ".ll": IrStage.LLVM_IR,
    ".mir": IrStage.MIR,
    ".s": IrStage.ASM,
}


@dataclass(frozen=True)
class IrDump:
    """One IR file for one crate at one stage."""

    stage: IrStage
    crate: str
    scope: str  # "repo" | "dependency"
    path: str
    raw_bytes: int

    @property
    def arcname(self) -> str:
        """Path inside the stage tarball: ``{scope}/{crate}/{basename}``."""
        return f"{self.scope}/{self.crate}/{os.path.basename(self.path)}"


@dataclass
class IrBundle:
    """What a build produced: per-stage tarball bytes plus a manifest."""

    tarballs: dict[IrStage, bytes] = field(default_factory=dict)
    dumps: list[IrDump] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)

    @property
    def raw_bytes(self) -> int:
        return sum(d.raw_bytes for d in self.dumps)

    @property
    def stored_bytes(self) -> int:
        return sum(len(b) for b in self.tarballs.values())

    def manifest(self, *, toolchain: str, backend: str, scope: str) -> dict[str, object]:
        """The ``ir/manifest.json`` document."""
        per_stage: dict[str, object] = {}
        for stage, blob in self.tarballs.items():
            entries = [d for d in self.dumps if d.stage is stage]
            per_stage[stage.value] = {
                "tarball": f"{stage.value}.tar.gz",
                "files": len(entries),
                "raw_bytes": sum(d.raw_bytes for d in entries),
                "stored_bytes": len(blob),
                "crates": sorted({d.crate for d in entries}),
            }
        return {
            "Ir_version": 1,
            "Toolchain": toolchain,
            "Codegen_backend": backend,
            "Ir_scope": scope,
            "Generated_at": int(time.time()),
            "Stages": per_stage,
            "Skipped": self.skipped,
            "Raw_bytes": self.raw_bytes,
            "Stored_bytes": self.stored_bytes,
        }


def normalize_crate(name: str) -> str:
    """cargo package ``foo-bar`` compiles to crate ``foo_bar``; compare on one form."""
    return name.replace("-", "_")


def crate_of(path: str) -> str:
    """Crate name from a rustc output filename (``regex_syntax-1da8767.ll``).

    rustc appends ``-{metadata hash}`` to every dep filename. Split on the LAST
    dash so crates whose own names contain underscores/dashes survive intact.
    """
    stem = os.path.basename(path)
    for suffix in _SUFFIX_TO_STAGE:
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    head, sep, tail = stem.rpartition("-")
    # only strip when the tail looks like rustc's hex metadata hash
    if sep and tail and all(c in "0123456789abcdef" for c in tail):
        return normalize_crate(head)
    return normalize_crate(stem)


def classify_scope(crate: str, repo_crates: frozenset[str]) -> str:
    """``repo`` when the crate is a workspace member, else ``dependency``.

    Deliberately binary: cargo's output filenames carry no stdlib/registry marker,
    and the precompiled std is never recompiled, so it emits no IR at all.
    """
    return "repo" if normalize_crate(crate) in repo_crates else "dependency"


def discover_ride_along(
    target_dir: str,
    stages: Iterable[IrStage],
    repo_crates: frozenset[str],
    scope: IrScope,
) -> list[IrDump]:
    """Find ``.ll``/``.mir``/``.s`` dropped by ``--emit`` under ``target_dir``."""
    wanted = {s for s in stages if s.rides_along}
    if not wanted or not os.path.isdir(target_dir):
        return []
    found: list[IrDump] = []
    for root, _dirs, files in os.walk(target_dir):
        for name in files:
            stage = _SUFFIX_TO_STAGE.get(os.path.splitext(name)[1])
            if stage is None or stage not in wanted:
                continue
            path = os.path.join(root, name)
            crate = crate_of(path)
            crate_scope = classify_scope(crate, repo_crates)
            if scope is IrScope.REPO and crate_scope != "repo":
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            found.append(IrDump(stage, crate, crate_scope, path, size))
    return sorted(found, key=lambda d: (d.stage.value, d.crate, d.path))


def discover_gimple(repo_crates: frozenset[str], scope: IrScope) -> list[IrDump]:
    """Collect cg_gcc's GIMPLE dumps from libgccjit's fixed dump dir."""
    if not os.path.isdir(GCCJIT_DUMP_DIR):
        return []
    found: list[IrDump] = []
    for name in os.listdir(GCCJIT_DUMP_DIR):
        path = os.path.join(GCCJIT_DUMP_DIR, name)
        if not os.path.isfile(path):
            continue
        crate = crate_of(path.split(".")[0])
        crate_scope = classify_scope(crate, repo_crates)
        if scope is IrScope.REPO and crate_scope != "repo":
            continue
        try:
            found.append(IrDump(IrStage.GIMPLE, crate, crate_scope, path, os.path.getsize(path)))
        except OSError:
            continue
    return sorted(found, key=lambda d: (d.crate, d.path))


def run_unpretty(
    *,
    clone_dir: str,
    out_dir: str,
    toolchain: str,
    profile: str,
    env: dict[str, str],
    stages: Iterable[IrStage],
    members: Sequence[str],
    adapter: RustCodegenAdapter,
    timeout_s: int,
) -> list[IrDump]:
    """Run one ``cargo rustc -- -Zunpretty=<mode>`` pass per (stage, member).

    These stages produce no object file, so each pass is a whole extra compile of
    that crate. Cost is bounded by only ever running them for workspace members --
    never dependencies, which is also all ``IrScope.REPO`` would keep anyway.

    A failing pass is logged and skipped: IR is additive, and no IR stage is worth
    failing a build that already produced a binary.
    """
    modes = [s for s in stages if s.unpretty_mode and adapter.ir_caps.supports(s)]
    if not modes or not members:
        return []
    os.makedirs(out_dir, exist_ok=True)
    # run_command takes no env mapping; the strategy's convention is a shell prefix.
    # Drop the ride-along --emit from RUSTFLAGS here: -Zunpretty produces no object,
    # so asking for --emit=link as well makes rustc error instead of printing IR.
    ir_env = {k: v for k, v in env.items() if k != "RUSTFLAGS"}
    rustflags = " ".join(
        part for part in env.get("RUSTFLAGS", "").split() if not part.startswith("--emit=")
    )
    if rustflags:
        ir_env["RUSTFLAGS"] = rustflags
    env_prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in sorted(ir_env.items()))

    dumps: list[IrDump] = []
    for stage in modes:
        for member in members:
            out_path = os.path.join(out_dir, f"{normalize_crate(member)}.{stage.value}.txt")
            cmd = (
                f"{env_prefix} cargo +{toolchain} rustc -p {shlex.quote(member)} "
                f"--profile {profile} -- -Zunpretty={stage.unpretty_mode}"
            )
            result = run_command(cmd, timeout=timeout_s, cwd=clone_dir)
            if result.returncode != 0 or not result.stdout:
                logger.info(
                    "unpretty %s failed for %s (rc=%s), skipping",
                    stage.unpretty_mode,
                    member,
                    result.returncode,
                )
                continue
            try:
                with open(out_path, "wb") as fh:
                    fh.write(result.stdout)
            except OSError as e:
                logger.warning("could not write %s: %s", out_path, e)
                continue
            dumps.append(
                IrDump(stage, normalize_crate(member), "repo", out_path, len(result.stdout))
            )
    return dumps


def pack(dumps: Sequence[IrDump], *, max_bytes: int = 0) -> IrBundle:
    """Pack dumps into one in-memory gzipped tarball per stage.

    ``max_bytes`` (0 = unbounded) caps the *stored* size per stage; a stage over the
    cap is dropped whole and recorded in ``skipped`` rather than truncated, because a
    half-tarball would look like a complete one to every reader downstream.
    """
    bundle = IrBundle()
    by_stage: dict[IrStage, list[IrDump]] = {}
    for d in dumps:
        by_stage.setdefault(d.stage, []).append(d)

    for stage, entries in sorted(by_stage.items(), key=lambda kv: kv[0].value):
        buf = io.BytesIO()
        # mtime=0: a rebuild of the same source should give the same tarball bytes.
        with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=6) as tar:
            for d in entries:
                try:
                    info = tar.gettarinfo(d.path, arcname=d.arcname)
                except OSError as e:
                    logger.warning("skipping unreadable IR file %s: %s", d.path, e)
                    continue
                info.mtime = 0
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                try:
                    with open(d.path, "rb") as fh:
                        tar.addfile(info, fh)
                except OSError as e:
                    logger.warning("skipping unreadable IR file %s: %s", d.path, e)
        blob = buf.getvalue()
        if max_bytes and len(blob) > max_bytes:
            bundle.skipped[stage.value] = (
                f"stage tarball {len(blob)} bytes exceeds IR_MAX_BYTES={max_bytes}"
            )
            logger.warning(
                "dropping IR stage %s: %d bytes > IR_MAX_BYTES=%d",
                stage.value,
                len(blob),
                max_bytes,
            )
            continue
        bundle.tarballs[stage] = blob
        bundle.dumps.extend(entries)
    return bundle


def manifest_bytes(bundle: IrBundle, *, toolchain: str, backend: str, scope: str) -> bytes:
    """Serialize the manifest exactly as it is stored."""
    return json.dumps(
        bundle.manifest(toolchain=toolchain, backend=backend, scope=scope), indent=2
    ).encode()

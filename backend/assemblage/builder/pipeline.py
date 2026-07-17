"""The per-task build pipeline: clone -> report -> build -> extract -> save.

``run_task`` performs the exact step order the pre-re-architecture ``job_handler``
did, so the observable message sequence and S3 side effects are preserved:

1. acquire the source (S3 restore or git clone);
2. on clone failure: send a clone-status message and stop;
3. send the clone-SUCCESS message (uuid-prefixed), then a build-PROCESSING one;
4. prepare, build (timing the build only), extract DWARF;
5. on build success: write metadata, upload binaries (one binary message each);
6. send the final build message with the build time;
7. clean the clone tree only once everything is safely in S3.
"""

import logging
import os
import shutil
import time
from dataclasses import dataclass

from assemblage.build.strategy import BuildStrategy
from assemblage.builder.artifacts import (
    build_prefix,
    generate_metadata,
    save_binaries,
    save_metadata_locally,
    save_metadata_to_s3,
)
from assemblage.builder.ir import manifest_bytes
from assemblage.builder.report import BuildReporter
from assemblage.builder.source import SourceResult, acquire_source
from assemblage.enums import BuildStatus, CloneStatus, IrScope
from assemblage.messages import BuildTask, IrStageRecord
from assemblage.storage.layout import ir_manifest_key, ir_tarball_key
from assemblage.storage.s3 import S3Bucket

logger = logging.getLogger(__name__)


@dataclass
class BuildContext:
    """Everything :func:`run_task` needs, assembled once at builder startup."""

    strategy: BuildStrategy
    reporter: BuildReporter
    compiler_flag: str
    library: str
    uuid: str
    binaries_root: str
    project_bucket: S3Bucket | None = None
    artifact_bucket: S3Bucket | None = None

    @property
    def s3_enabled(self) -> bool:
        return self.project_bucket is not None or self.artifact_bucket is not None


def run_task(ctx: BuildContext, task: BuildTask) -> None:
    """Clone, build, extract and archive one repository for one build option."""
    strategy = ctx.strategy
    reporter = ctx.reporter
    started = time.time()

    logger.info("Received a task to build %s (buildsys: %s)", task.url, task.build_system)

    source = acquire_source(task, strategy=strategy, project_bucket=ctx.project_bucket)

    if source.status != CloneStatus.SUCCESS:
        reporter.clone_status(
            url=task.url,
            status=source.status,
            msg=ctx.uuid[:5] + source.message,
            task_id=task.task_id,
        )
        logger.info("Clone FAILURE %s: %s", task.url, source.message)
        return

    logger.info("Clone/restore SUCCESS for task %s", task.name)
    commit_hexsha = source.commit_hexsha
    clone_dir = source.clone_dir

    reporter.clone_status(
        url=task.url,
        status=CloneStatus.SUCCESS,
        msg=ctx.uuid[:5] + source.message,
        task_id=task.task_id,
    )
    reporter.build_processing(url=task.url, task_id=task.task_id, commit_hexsha=commit_hexsha)

    logger.info("Building %s with flag %s", task.name, ctx.compiler_flag)
    prepared = strategy.prepare(clone_dir, ctx.compiler_flag)
    before_build = int(time.time())
    build_output, build_status = strategy.build(clone_dir, ctx.compiler_flag, prepared)
    after_build = int(time.time())

    dwarf_list = strategy.debug_info(clone_dir, source.original_files)
    logger.info("Build %s for task %s with flag %s", build_status, task.name, ctx.compiler_flag)

    if build_status == BuildStatus.SUCCESS:
        all_saved = _persist_success(ctx, task, source, dwarf_list)
    else:
        all_saved = False
        logger.info("Build failed for %s: %s", task.name, build_output[:500])

    reporter.build_finished(
        url=task.url,
        task_id=task.task_id,
        status=build_status,
        build_time=after_build - before_build,
        commit_hexsha=commit_hexsha,
    )

    if ctx.s3_enabled and all_saved:
        _clean_folder(os.path.dirname(clone_dir))

    logger.info("Task %s finished in %.3fs", task.name, time.time() - started)


def _persist_success(
    ctx: BuildContext,
    task: BuildTask,
    source: SourceResult,
    dwarf_list: list[dict[str, object]],
) -> bool:
    """Write metadata + upload binaries; return whether everything was saved."""
    clone_dir = source.clone_dir
    commit_hexsha = source.commit_hexsha

    metadata = generate_metadata(
        strategy=ctx.strategy,
        library=ctx.library,
        task=task,
        commit_hexsha=commit_hexsha,
        compiler_flag=ctx.compiler_flag,
    )
    # Always present, even when extraction yielded nothing — Binary_info_list is
    # part of the frozen metadata key set, and an explicit [] distinguishes
    # "no debug info extracted" from a malformed document.
    metadata["Binary_info_list"] = dwarf_list

    owner, project = clone_dir.rstrip("/").split("/")[-2:]
    prefix = build_prefix(ctx.strategy, owner, project, commit_hexsha, ctx.compiler_flag)

    if ctx.artifact_bucket is not None:
        save_metadata_to_s3(
            clone_dir=clone_dir,
            prefix=prefix,
            metadata=metadata,
            artifact_bucket=ctx.artifact_bucket,
        )
    else:
        save_metadata_locally(clone_dir, metadata)

    saved = save_binaries(
        strategy=ctx.strategy,
        target_dir=clone_dir,
        original_files=source.original_files,
        commit_hexsha=commit_hexsha,
        compiler_flag=ctx.compiler_flag,
        artifact_bucket=ctx.artifact_bucket,
        binaries_root=ctx.binaries_root,
    )
    for file_name in saved.file_names:
        ctx.reporter.binary(task_id=task.task_id, file_name=file_name)
    logger.info("Binaries saved to %s", saved.dest)

    _persist_ir(ctx, task, prefix)
    return saved.all_saved


def _persist_ir(ctx: BuildContext, task: BuildTask, prefix: str) -> None:
    """Upload this build's IR tarballs + manifest and report them.

    A no-op for every strategy without a ``collect_ir`` hook (all the C/C++ ones) and
    for Rust tiers with ``IR_DUMP`` off. Duck-typed rather than isinstance-checked so
    this module never imports the Rust strategy — the factory stays the only place
    that knows which strategies exist.

    Failures are logged, never raised: the binary and its metadata are already saved,
    and IR is additive corpus data — it must not be able to fail a good build.
    """
    collect = getattr(ctx.strategy, "collect_ir", None)
    if collect is None or ctx.artifact_bucket is None:
        return
    try:
        bundle = collect()
        if bundle is None or not bundle.tarballs:
            return

        records: list[IrStageRecord] = []
        for stage, blob in bundle.tarballs.items():
            key = ir_tarball_key(prefix, stage.value)
            ctx.artifact_bucket.put_bytes(key, blob)
            entries = [d for d in bundle.dumps if d.stage is stage]
            records.append(
                IrStageRecord(
                    stage=stage,
                    s3_key=key,
                    file_count=len(entries),
                    crate_count=len({d.crate for d in entries}),
                    raw_bytes=sum(d.raw_bytes for d in entries),
                    stored_bytes=len(blob),
                )
            )

        scope = getattr(ctx.strategy, "ir_scope", IrScope.REPO)
        backend = str(getattr(ctx.strategy, "codegen_backend", ""))
        toolchain = str(getattr(ctx.strategy, "toolchain", ""))
        ctx.artifact_bucket.put_bytes(
            ir_manifest_key(prefix),
            manifest_bytes(bundle, toolchain=toolchain, backend=backend, scope=scope.value),
        )
        ctx.reporter.ir(
            task_id=task.task_id,
            scope=scope,
            toolchain=toolchain,
            codegen_backend=backend,
            stages=records,
        )
        logger.info(
            "IR stored for %s: %d stage(s), %d -> %d bytes",
            prefix,
            len(records),
            bundle.raw_bytes,
            bundle.stored_bytes,
        )
    except Exception:
        logger.exception("storing IR failed for %s; the build itself is unaffected", prefix)


def _clean_folder(path: str) -> None:
    """Best-effort recursive delete of a finished clone tree."""
    logger.info("Deleting %s", path)
    shutil.rmtree(path, ignore_errors=True)

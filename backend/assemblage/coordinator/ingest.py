"""Pure ingest handlers: ``(store, typed message[, deps]) -> AckDecision``.

No pika, no json, no threads — each handler takes an already-parsed typed
message and a :class:`CoordinatorStore`, mutates the database, and returns the
ack decision. This makes them directly unit-testable with a mock store, and it
is where several old bugs die:

- clone status is parsed via ``CloneStatus`` (the old code round-tripped it
  through ``BuildStatus`` and only survived on the shared value strings);
- the 10-second in-callback DB-sync sleep and the ``OUTDATED_MSG`` discard
  branch are gone (dead paths / approved delta).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from assemblage.db.store import CoordinatorStore
from assemblage.enums import BuildStatus
from assemblage.messages import (
    BinaryRecordMsg,
    BuildStatusMsg,
    CloneStatusMsg,
    IrRecordMsg,
    ScrapeBundle,
)
from assemblage.mq.consumer import AckDecision

logger = logging.getLogger(__name__)

_BUILD_MSG_LIMIT = 500
_CLONE_MSG_LIMIT = 200


@dataclass
class BuildStats:
    """Running build success/failure tallies (logged every 100 completions)."""

    successes: int = 0
    failures: int = 0


def handle_scrape(
    store: CoordinatorStore,
    bundle: ScrapeBundle,
    on_repos_received: Callable[[], None],
) -> AckDecision:
    """Insert every repo in the bundle; tolerate per-repo failures."""
    on_repos_received()
    saved = 0
    for repo in bundle:
        try:
            saved += store.insert_repos(repo.model_dump())
        except Exception as exc:  # a bad row must not sink the whole bundle
            logger.error("failed to insert scraped repo %s: %s", repo.url, exc)
    logger.info("scrape bundle: received %d, saved %d", len(bundle), saved)
    return AckDecision.ACK


def handle_clone_status(store: CoordinatorStore, msg: CloneStatusMsg) -> AckDecision:
    """Record a clone result (status parsed as a real ``CloneStatus``)."""
    store.update_repo_status(
        status_id=msg.task_id,
        clone_status=msg.status,
        clone_msg=msg.msg[-_CLONE_MSG_LIMIT:],
    )
    return AckDecision.ACK


def handle_build_status(
    store: CoordinatorStore, msg: BuildStatusMsg, stats: BuildStats
) -> AckDecision:
    """Record a build result; on FAILED, fail the repo's still-INIT siblings."""
    store.update_repo_status(
        status_id=msg.task_id,
        build_status=msg.status,
        build_time=msg.build_time,
        build_msg=msg.msg[-_BUILD_MSG_LIMIT:],
        commit_hexsha=msg.commit_hexsha,
    )
    if msg.status not in (BuildStatus.SUCCESS, BuildStatus.FAILED):
        return AckDecision.ACK

    if msg.status == BuildStatus.SUCCESS:
        stats.successes += 1
    else:
        stats.failures += 1
        row = store.get_status_row_by_id(msg.task_id)
        skipped = store.fail_sibling_statuses(row.repo_id, msg.task_id, msg="Sibling build failed")
        if skipped > 0:
            stats.failures += skipped
            logger.info("failed %d sibling tasks for repo %d", skipped, row.repo_id)

    if (stats.successes + stats.failures) % 100 == 0:
        logger.info(
            "builds completed: %d (%d ok, %d failed)",
            stats.successes + stats.failures,
            stats.successes,
            stats.failures,
        )
    return AckDecision.ACK


def handle_binary(store: CoordinatorStore, msg: BinaryRecordMsg) -> AckDecision:
    """Record one produced binary against its task."""
    store.insert_binary(file_name=msg.file_name, description="", status_id=msg.task_id)
    return AckDecision.ACK


def handle_ir(store: CoordinatorStore, msg: IrRecordMsg) -> AckDecision:
    """Record the IR stage tarballs a builder stored for one build.

    The DB keeps enum member NAMES (``LLVM_IR``) while the wire carries values
    (``llvm-ir``) — the same split the status columns already use.
    """
    rows = [
        {
            "stage": rec.stage.name,
            "scope": msg.scope.name,
            "s3_key": rec.s3_key,
            "file_count": rec.file_count,
            "crate_count": rec.crate_count,
            "raw_bytes": rec.raw_bytes,
            "stored_bytes": rec.stored_bytes,
        }
        for rec in msg.stages
    ]
    written = store.upsert_ir_artifacts(msg.task_id, rows)
    logger.info("recorded %d IR stage(s) for task %d", written, msg.task_id)
    return AckDecision.ACK

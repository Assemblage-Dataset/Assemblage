"""The coordinator's typed data store.

``CoordinatorStore`` ports only the ``DBManager`` methods the re-architected
coordinator actually uses; the ~250 commented lines and the dead helpers
(``find_*``, ``reset_timeout_status``, ``get_build_opt_language``,
``add_build_option``, disasm helpers, …) are gone. Every write goes through
``session_scope`` (commit-or-rollback-and-re-raise), so a failed write surfaces
to the ConsumerLoop, which requeues the delivery.

Comparisons wrap model attributes in :func:`sqlmodel.col` so the SQLAlchemy
column expression (not the field's Python type) is what ``.where`` sees.
"""

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Engine, func, inspect, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlmodel import col

from assemblage.db.engine import session_scope
from assemblage.db.models import BuildDO, BuildOpt, IrArtifactDO, RepoDO, ScraperData, Status
from assemblage.enums import BuildStatus, CloneStatus
from assemblage.messages import BuilderRegistration

logger = logging.getLogger(__name__)

# Column names of the projects table — used to drop unknown keys from a scraped
# repo dict before constructing a RepoDO.
_REPO_COLUMNS: frozenset[str] = frozenset(RepoDO.model_fields) - {"statuses"}

# Languages the corpus builds with the c++ buildopts: GitHub reports C and C++
# repos separately (the scraper lowercases them) and 1,594 legacy projects rows
# store 'CPP'; all of them belong to the C/C++ pipeline.
_C_FAMILY: frozenset[str] = frozenset({"c", "cpp", "c++"})


def languages_match(repo_language: str, opt_language: str) -> bool:
    """True when a repo of ``repo_language`` belongs on an ``opt_language`` buildopt.

    Both sides are normalized via ``lower()``; the C family ``{'c', 'cpp',
    'c++'}`` (including the legacy uppercase ``'CPP'`` rows) collapses to
    ``'c++'``. Anything else must match exactly (``'rust'`` only matches
    ``'rust'``).
    """

    def canon(language: str) -> str:
        lowered = language.lower()
        return "c++" if lowered in _C_FAMILY else lowered

    return canon(repo_language) == canon(opt_language)


@dataclass(frozen=True)
class DispatchCandidate:
    """A build/clone task ready to be dispatched to a builder.

    Carries the raw (un-patched) repo URL; the dispatcher applies ``patch_url``
    when it builds the wire ``BuildTask``.
    """

    task_id: int
    opt_id: int
    repo_id: int
    name: str
    url: str
    updated_at: str
    build_system: str
    compiler_flag: str


class CoordinatorStore:
    """All database access the coordinator performs."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def shutdown(self) -> None:
        """Dispose the engine's connection pool."""
        self._engine.dispose()

    # --- schema ---------------------------------------------------------------

    def tables_exist(self) -> bool:
        """True once the schema has been created (any table present)."""
        with self._engine.connect() as conn:
            return len(inspect(conn).get_table_names()) > 0

    # --- build options / registration ----------------------------------------

    def register_build_opt(self, reg: BuilderRegistration) -> int:
        """Match-or-create the build option identified by a builder registration.

        Identity is nine buildopt columns: the seven original ones (platform,
        language, compiler_name, compiler_flag, build_system, build_command,
        library) plus build_type and codegen_backend (Rust rollout; the wire
        defaults 'RelWithDebInfo' / '' make C builders re-match their existing
        live rows exactly). A matching row is re-enabled; a new row is created
        and back-filled with a ``b_status`` for every existing repo whose build
        system AND language it covers.
        """
        with session_scope(self._engine) as session:
            existing = (
                session.execute(
                    select(BuildOpt).where(
                        col(BuildOpt.platform) == reg.platform,
                        col(BuildOpt.language) == reg.language,
                        col(BuildOpt.compiler_name) == reg.compiler,
                        col(BuildOpt.compiler_flag) == reg.compiler_flag,
                        col(BuildOpt.build_system) == reg.build_system,
                        col(BuildOpt.build_command) == reg.build_command,
                        col(BuildOpt.library) == reg.library,
                        col(BuildOpt.build_type) == reg.build_mode,
                        col(BuildOpt.codegen_backend) == reg.codegen_backend,
                    )
                )
                .scalars()
                .first()
            )

            if existing is not None:
                if not existing.enable:
                    existing.enable = True
                if existing.id is None:  # pragma: no cover - defensive
                    raise ValueError("existing build option has no id")
                return existing.id

            opt = BuildOpt(
                platform=reg.platform,
                language=reg.language,
                compiler_name=reg.compiler,
                compiler_flag=reg.compiler_flag,
                build_system=reg.build_system,
                build_command=reg.build_command,
                library=reg.library,
                enable=True,
                build_type=reg.build_mode,
                codegen_backend=reg.codegen_backend,
            )
            session.add(opt)
            session.flush()

            new_statuses = [
                Status(repo_id=repo.id, build_opt_id=opt.id)
                for repo in session.execute(select(RepoDO)).scalars()
                if (reg.build_system in repo.build_system or reg.build_system == "all")
                and languages_match(repo.language, reg.language)
            ]
            session.bulk_save_objects(new_statuses)

            if opt.id is None:  # pragma: no cover - defensive
                raise ValueError("failed to create build option")
            return opt.id

    # --- dispatch -------------------------------------------------------------

    def next_dispatchable(self, opt_id: int) -> DispatchCandidate | None:
        """Return one un-started task for ``opt_id`` (clone NOT_STARTED, build INIT)."""
        with session_scope(self._engine) as session:
            row = (
                session.execute(
                    select(Status)
                    .where(
                        col(Status.clone_status) == CloneStatus.NOT_STARTED,
                        col(Status.build_status) == BuildStatus.INIT,
                        col(Status.build_opt_id) == opt_id,
                    )
                    .limit(1)
                )
                .scalars()
                .first()
            )
            if row is None:
                return None

            project = session.get(RepoDO, row.repo_id)
            buildopt = session.get(BuildOpt, row.build_opt_id)
            if project is None or buildopt is None:  # pragma: no cover - FK guarantees
                return None

            return DispatchCandidate(
                task_id=row.id,
                opt_id=buildopt.id if buildopt.id is not None else opt_id,
                repo_id=project.id if project.id is not None else row.repo_id,
                name=project.name,
                url=project.url,
                updated_at=project.updated_at.strftime("%m/%d/%Y, %H:%M:%S"),
                build_system=project.build_system,
                compiler_flag=buildopt.compiler_flag or "",
            )

    def count_pending(self, opt_id: int) -> int:
        """How many un-started tasks remain for ``opt_id``."""
        with session_scope(self._engine) as session:
            return int(
                session.execute(
                    select(func.count())
                    .select_from(Status)
                    .where(
                        col(Status.clone_status) == CloneStatus.NOT_STARTED,
                        col(Status.build_status) == BuildStatus.INIT,
                        col(Status.build_opt_id) == opt_id,
                    )
                ).scalar_one()
            )

    def mark_clone_processing(self, status_id: int) -> None:
        """Flag a task as PROCESSING (called only after a confirmed dispatch)."""
        with session_scope(self._engine) as session:
            session.execute(
                update(Status)
                .values(clone_status=CloneStatus.PROCESSING, mod_timestamp=int(time.time()))
                .where(col(Status.id) == status_id)
            )

    # --- status updates -------------------------------------------------------

    def get_status_row_by_id(self, status_id: int) -> Status:
        """Fetch a detached ``Status`` row by primary key."""
        with session_scope(self._engine) as session:
            status = session.get(Status, status_id)
            if status is None:
                raise ValueError(f"no b_status row with id {status_id}")
            session.expunge(status)
            return status

    def update_repo_status(
        self,
        *,
        status_id: int,
        build_status: BuildStatus | None = None,
        build_time: int | None = None,
        build_msg: str | None = None,
        clone_status: CloneStatus | None = None,
        clone_msg: str | None = None,
        commit_hexsha: str | None = None,
    ) -> None:
        """Update the build/clone status of one task.

        Only the provided fields are written; ``commit_hexsha`` is written only
        when non-empty, so a later status update that carries no sha cannot
        clobber the sha a build report already stored.
        """
        if build_status is None and clone_status is None:
            logger.info("update_repo_status(%s): nothing to update", status_id)
            return
        values: dict[str, object] = {"mod_timestamp": int(time.time())}
        if build_status is not None:
            values["build_status"] = build_status
            if build_time is not None:
                values["build_time"] = build_time
            if build_msg is not None:
                values["build_msg"] = build_msg
        if clone_status is not None:
            values["clone_status"] = clone_status
            if clone_msg is not None:
                values["clone_msg"] = clone_msg
        if commit_hexsha:
            values["commit_hexsha"] = commit_hexsha
        with session_scope(self._engine) as session:
            session.execute(update(Status).values(**values).where(col(Status.id) == status_id))

    def fail_sibling_statuses(
        self, repo_id: int, exclude_status_id: int, msg: str = "Sibling build failed"
    ) -> int:
        """Mark every still-INIT sibling of a failed build as FAILED; return the count."""
        with session_scope(self._engine) as session:
            result = session.execute(
                update(Status)
                .values(
                    build_status=BuildStatus.FAILED, build_msg=msg, mod_timestamp=int(time.time())
                )
                .where(
                    col(Status.repo_id) == repo_id,
                    col(Status.id) != exclude_status_id,
                    col(Status.build_status) == BuildStatus.INIT,
                )
            )
            return result.rowcount

    # --- repos / binaries -----------------------------------------------------

    def insert_repos(self, repo: dict[str, object]) -> int:
        """Insert one scraped repo plus a ``b_status`` per covering build option.

        A build option covers a repo when its build system matches AND
        :func:`languages_match` holds (a rust repo lands only on rust opts, a
        c/c++/CPP repo only on c++ opts).

        Returns 1 on success, 0 on a duplicate (IntegrityError) or any other
        failure — the corpus has many duplicate URLs, so a duplicate is a normal
        skip, not an error worth failing the whole bundle over.
        """
        filtered = {k: v for k, v in repo.items() if k in _REPO_COLUMNS}
        build_system = str(repo.get("build_system", ""))
        language = str(repo.get("language", ""))
        with session_scope(self._engine) as session:
            try:
                repo_row = RepoDO(**filtered)
                session.add(repo_row)
                session.flush()
                for opt in session.execute(select(BuildOpt)).scalars():
                    opt_bs = opt.build_system or ""
                    if (build_system in opt_bs or opt_bs == "all") and languages_match(
                        language, opt.language
                    ):
                        session.add(
                            Status(
                                clone_status=CloneStatus.NOT_STARTED,
                                clone_msg="",
                                build_status=BuildStatus.INIT,
                                build_msg="",
                                build_opt_id=opt.id,
                                mod_timestamp=int(time.time()),
                                repo_id=repo_row.id,
                            )
                        )
            except IntegrityError:
                session.rollback()
                logger.info("duplicate repo skipped: %s", repo.get("url"))
                return 0
            except Exception as exc:
                session.rollback()
                logger.error("failed to insert repo %s: %s", repo.get("url"), exc)
                return 0
        return 1

    def insert_binary(self, file_name: str, description: str, status_id: int) -> None:
        """Record one produced binary against its task's status row."""
        with session_scope(self._engine) as session:
            session.add(BuildDO(file_name=file_name, description=description, status_id=status_id))

    def upsert_ir_artifacts(self, status_id: int, rows: Sequence[dict[str, object]]) -> int:
        """Record a build's IR stage tarballs; returns the number written.

        Idempotent on (status_id, stage): a redelivered ``ir`` message updates the
        existing row instead of duplicating it. The builder acks its task *before*
        building, so a redelivery is a normal event, not an error.
        """
        if not rows:
            return 0
        written = 0
        with session_scope(self._engine) as session:
            for row in rows:
                stmt = pg_insert(IrArtifactDO).values(status_id=status_id, **row)
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_ir_artifacts_status_stage",
                    set_={k: stmt.excluded[k] for k in row if k != "stage"},
                )
                session.execute(stmt)
                written += 1
        return written

    # --- scrapers -------------------------------------------------------------

    def ready_scraper_table(self) -> None:
        """Clear all scraper ownership claims (run once at coordinator startup)."""
        with session_scope(self._engine) as session:
            session.execute(update(ScraperData).values(owner_uuid=""))

    def register_scraper(
        self, worker_uuid: str, fallback_start: int, fallback_end: int
    ) -> dict[str, int]:
        """Return a scraper's (start_time, end_time): claim an unclaimed row or create one."""
        with session_scope(self._engine) as session:
            owned = (
                session.execute(
                    select(ScraperData).where(col(ScraperData.owner_uuid) == worker_uuid)
                )
                .scalars()
                .first()
            )
            if owned is not None:
                return {"start_time": owned.start_time, "end_time": owned.end_time}

            unclaimed = (
                session.execute(select(ScraperData).where(col(ScraperData.owner_uuid) == ""))
                .scalars()
                .first()
            )
            if unclaimed is None:
                logger.info("creating scraper config for %s from defaults", worker_uuid)
                row = ScraperData(
                    start_time=fallback_start, end_time=fallback_end, owner_uuid=worker_uuid
                )
                session.add(row)
                return {"start_time": row.start_time, "end_time": row.end_time}

            unclaimed.owner_uuid = worker_uuid
            return {"start_time": unclaimed.start_time, "end_time": unclaimed.end_time}

    def update_scraper(self, worker_uuid: str, start_time: int, fallback_end: int) -> None:
        """Advance a scraper's stored start time (create the row if missing)."""
        with session_scope(self._engine) as session:
            owned = (
                session.execute(
                    select(ScraperData).where(col(ScraperData.owner_uuid) == worker_uuid)
                )
                .scalars()
                .first()
            )
            if owned is None:
                logger.warning("no scraper row for %s; creating one", worker_uuid)
                session.add(
                    ScraperData(
                        start_time=start_time, end_time=fallback_end, owner_uuid=worker_uuid
                    )
                )
            else:
                owned.start_time = start_time

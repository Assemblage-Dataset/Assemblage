"""Integration tests for :class:`CoordinatorStore` against the scratch DB."""

import logging
import unittest

import pytest
import sqlalchemy as sqla
from assemblage.db.models import Status
from assemblage.db.store import DispatchCandidate
from assemblage.enums import BuildStatus, CloneStatus
from assemblage.messages import BuilderRegistration, RepoRecord

import tests.integration_tests.integration_helper as helper
from tests.constants import TEST_MESSAGE_LEVEL

pytestmark = pytest.mark.integration  # needs the live test database
logging.basicConfig(
    format="%(asctime)s [TEST] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=TEST_MESSAGE_LEVEL,
)
logger = logging.getLogger(__name__)


def _registration(compiler: str, flag: str = "") -> BuilderRegistration:
    return BuilderRegistration(
        name=f"{compiler}-builder",
        uuid="0001",
        compiler=compiler,
        library="x64",
        language="c++",
        platform="linux",
        compiler_flag=flag,
        build_command="",
        build_system="all",
    )


class TestCoordinatorStore(unittest.TestCase):
    def test_get_status_row_by_id(self):
        helper.truncate_all()
        helper.seed_database_projects()
        helper.seed_database_buildopts()
        helper.seed_database_statuses_unstarted()

        store = helper.make_store()
        row = store.get_status_row_by_id(1)

        self.assertIsInstance(row, Status)
        self.assertEqual(row.id, 1)
        self.assertEqual(row.build_opt_id, 1)
        self.assertEqual(row.repo_id, 1)

    def test_register_build_opt_matches_or_creates(self):
        helper.truncate_all()
        store = helper.make_store()

        first = store.register_build_opt(_registration("clang", "comp_flag"))
        second = store.register_build_opt(_registration("gcc", "comp_flag"))
        again = store.register_build_opt(_registration("gcc", "comp_flag"))

        self.assertEqual(first, 1)
        self.assertEqual(second, 2)
        self.assertEqual(again, 2)  # identity match, not a new row

        with store._engine.connect() as conn:
            count = conn.execute(sqla.text("SELECT count(*) FROM buildopt")).scalar_one()
        self.assertEqual(count, 2)

    def test_register_build_opt_backfills_statuses(self):
        helper.truncate_all()
        helper.seed_database_projects()  # 3 repos
        store = helper.make_store()

        opt_id = store.register_build_opt(_registration("clang"))  # build_system 'all'

        with store._engine.connect() as conn:
            n = conn.execute(
                sqla.text("SELECT count(*) FROM b_status WHERE build_opt_id = :o"),
                {"o": opt_id},
            ).scalar_one()
        self.assertEqual(n, 3)  # one per existing repo

    def test_insert_repos_creates_repo_and_statuses(self):
        helper.truncate_all()
        helper.seed_database_buildopts()  # two 'all' build options
        store = helper.make_store()

        record = RepoRecord.model_validate_json(
            '{"name": "DOOM", "url": "https://github.com/id-Software/DOOM",'
            ' "language": "c++", "owner_id": 1395534, "description": "d",'
            ' "created_at": "2012-01-31 21:28:06", "updated_at": "2024-05-24 13:18:59",'
            ' "size": 149, "build_system": "make", "branch": "master"}'
        )
        self.assertEqual(store.insert_repos(record.model_dump()), 1)

        with store._engine.connect() as conn:
            repos = conn.execute(sqla.text("SELECT count(*) FROM projects")).scalar_one()
            statuses = conn.execute(sqla.text("SELECT count(*) FROM b_status")).scalar_one()
        self.assertEqual(repos, 1)
        self.assertEqual(statuses, 2)  # one per 'all' build option

    def test_next_dispatchable_returns_candidate(self):
        helper.truncate_all()
        helper.seed_database_projects()
        helper.seed_database_buildopts()
        helper.seed_database_statuses_unstarted()

        store = helper.make_store()
        candidate = store.next_dispatchable(1)

        self.assertIsInstance(candidate, DispatchCandidate)
        self.assertEqual(candidate.opt_id, 1)
        self.assertEqual(candidate.name, "PROJECT_1")
        self.assertEqual(candidate.url, "URL_1")  # raw, un-patched
        self.assertEqual(candidate.updated_at, "11/15/2025, 12:41:59")
        self.assertEqual(candidate.build_system, "BUILD_SYS")

    def test_mark_clone_processing(self):
        helper.truncate_all()
        helper.seed_database_projects()
        helper.seed_database_buildopts()
        helper.seed_database_statuses_unstarted()

        store = helper.make_store()
        store.mark_clone_processing(1)

        row = store.get_status_row_by_id(1)
        self.assertEqual(row.clone_status, CloneStatus.PROCESSING)
        self.assertEqual(row.build_status, BuildStatus.INIT)  # untouched

    def test_update_repo_status_variants(self):
        helper.truncate_all()
        helper.seed_database_projects()
        helper.seed_database_buildopts()
        helper.seed_database_statuses_unstarted()

        store = helper.make_store()
        store.update_repo_status(status_id=1, clone_status=CloneStatus.FAILED)
        store.update_repo_status(
            status_id=2,
            build_status=BuildStatus.FAILED,
            build_time=3,
            build_msg="test",
            commit_hexsha="000000",
        )

        row1 = store.get_status_row_by_id(1)
        row2 = store.get_status_row_by_id(2)
        self.assertEqual(row1.clone_status, CloneStatus.FAILED)
        self.assertEqual(row1.build_status, BuildStatus.INIT)
        self.assertEqual(row2.build_status, BuildStatus.FAILED)
        self.assertEqual(row2.build_time, 3)
        self.assertEqual(row2.build_msg, "test")
        self.assertEqual(row2.commit_hexsha, "000000")

    def test_update_repo_status_does_not_clobber_commit(self):
        helper.truncate_all()
        helper.seed_database_projects()
        helper.seed_database_buildopts()
        helper.seed_database_statuses_unstarted()

        store = helper.make_store()
        store.update_repo_status(
            status_id=1, build_status=BuildStatus.SUCCESS, commit_hexsha="abc123"
        )
        # a later clone update carries no sha; the stored sha must survive.
        store.update_repo_status(status_id=1, clone_status=CloneStatus.SUCCESS)

        self.assertEqual(store.get_status_row_by_id(1).commit_hexsha, "abc123")

    def test_fail_sibling_statuses(self):
        helper.truncate_all()
        helper.seed_database_projects()
        helper.seed_database_buildopts()
        helper.seed_database_statuses_unstarted()

        store = helper.make_store()
        # repo 1 has statuses 1 (opt 1) and 2 (opt 2); fail siblings of 1.
        skipped = store.fail_sibling_statuses(repo_id=1, exclude_status_id=1)

        self.assertEqual(skipped, 1)
        self.assertEqual(store.get_status_row_by_id(2).build_status, BuildStatus.FAILED)
        self.assertEqual(store.get_status_row_by_id(1).build_status, BuildStatus.INIT)

    def test_insert_binary(self):
        helper.truncate_all()
        helper.seed_database_projects()
        helper.seed_database_buildopts()
        helper.seed_database_statuses_unstarted()

        store = helper.make_store()
        store.insert_binary("FILENAME", "", 3)
        store.insert_binary("FILENAME2", "", 3)

        with store._engine.connect() as conn:
            rows = (
                conn.execute(
                    sqla.text("SELECT file_name FROM binaries WHERE status_id = 3 ORDER BY id")
                )
                .scalars()
                .all()
            )
        self.assertEqual(rows, ["FILENAME", "FILENAME2"])

    def test_count_pending(self):
        helper.truncate_all()
        helper.seed_database_projects()
        helper.seed_database_buildopts()
        helper.seed_database_statuses_unstarted()

        store = helper.make_store()
        self.assertEqual(store.count_pending(1), 3)
        store.mark_clone_processing(1)
        self.assertEqual(store.count_pending(1), 2)


if __name__ == "__main__":
    unittest.main()

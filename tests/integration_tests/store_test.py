"""Integration tests for :class:`CoordinatorStore` against the scratch DB."""

import logging
import unittest
from typing import ClassVar

import pytest
import sqlalchemy as sqla
from assemblage.blocklist import Blocklist
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


def _registration(
    compiler: str,
    flag: str = "",
    language: str = "c++",
    build_system: str = "all",
    **fields: str,
) -> BuilderRegistration:
    return BuilderRegistration(
        name=f"{compiler}-builder",
        uuid="0001",
        compiler=compiler,
        library="x64",
        language=language,
        platform="linux",
        compiler_flag=flag,
        build_command="",
        build_system=build_system,
        **fields,
    )


def _repo(name: str, language: str, build_system: str = "make") -> dict[str, object]:
    return {
        "name": name,
        "url": f"https://github.com/test/{name}",
        "language": language,
        "owner_id": 1,
        "description": "d",
        "size": 1,
        "build_system": build_system,
        "branch": "master",
    }


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

    def test_register_build_opt_identity_is_nine_columns(self):
        """build_type and codegen_backend joined the identity (Rust rollout)."""
        helper.truncate_all()
        store = helper.make_store()

        base = store.register_build_opt(_registration("gcc", "-O2"))
        same = store.register_build_opt(_registration("gcc", "-O2"))
        debug = store.register_build_opt(_registration("gcc", "-O2", build_mode="Debug"))
        rust = store.register_build_opt(
            _registration("rustc", "-O2", language="rust", codegen_backend="llvm")
        )

        self.assertEqual(same, base)  # defaults re-match
        self.assertNotEqual(debug, base)  # build_mode differentiates
        self.assertNotIn(rust, {base, debug})  # codegen_backend differentiates

        with store._engine.connect() as conn:
            rows = dict(
                conn.execute(
                    sqla.text("SELECT id, build_type || '|' || codegen_backend FROM buildopt")
                ).all()
            )
        self.assertEqual(rows[base], "RelWithDebInfo|")
        self.assertEqual(rows[debug], "Debug|")
        self.assertEqual(rows[rust], "RelWithDebInfo|llvm")

    def test_reregister_existing_c_identity_no_churn(self):
        """A current C builder re-registers onto its migrated live row.

        The row is seeded via raw SQL WITHOUT build_type/codegen_backend so the
        server defaults fill them — exactly what migration e9d4c1f2a3b5 leaves
        behind on the live database. A default registration (wire defaults
        'RelWithDebInfo' / '') must return the same id with zero new rows.
        """
        helper.truncate_all()
        helper.seed_database_projects()
        store = helper.make_store()

        with store._engine.begin() as conn:
            live_id = conn.execute(
                sqla.text(
                    "INSERT INTO buildopt (platform, language, compiler_name, compiler_flag,"
                    " build_system, build_command, library, enable)"
                    " VALUES ('linux', 'c++', 'gcc', '-O2', 'all', '', 'x64', true)"
                    " RETURNING id"
                )
            ).scalar_one()

        opt_id = store.register_build_opt(_registration("gcc", "-O2"))

        self.assertEqual(opt_id, live_id)
        with store._engine.connect() as conn:
            opts = conn.execute(sqla.text("SELECT count(*) FROM buildopt")).scalar_one()
            statuses = conn.execute(sqla.text("SELECT count(*) FROM b_status")).scalar_one()
        self.assertEqual(opts, 1)  # no duplicate buildopt row
        self.assertEqual(statuses, 0)  # identity match never back-fills

    def test_register_build_opt_backfill_is_language_aware(self):
        """A new rust buildopt back-fills b_status ONLY for rust repos; a new
        c++ buildopt covers the c/cpp/CPP family including legacy rows."""
        helper.truncate_all()
        store = helper.make_store()
        self.assertEqual(store.insert_repos(_repo("cpp-repo", "c++")), 1)
        self.assertEqual(store.insert_repos(_repo("legacy-repo", "CPP")), 1)
        self.assertEqual(store.insert_repos(_repo("c-repo", "c")), 1)
        self.assertEqual(store.insert_repos(_repo("rust-repo", "rust", "cargo")), 1)

        rust_opt = store.register_build_opt(
            _registration("rustc", "-O0", language="rust", codegen_backend="llvm")
        )
        cpp_opt = store.register_build_opt(_registration("gcc", "-O0"))

        with store._engine.connect() as conn:
            rows = conn.execute(
                sqla.text(
                    "SELECT s.build_opt_id, p.name FROM b_status s"
                    " JOIN projects p ON p.id = s.repo_id"
                )
            ).all()
        by_opt: dict[int, set[str]] = {}
        for opt_id, name in rows:
            by_opt.setdefault(opt_id, set()).add(name)
        self.assertEqual(by_opt.get(rust_opt), {"rust-repo"})
        self.assertEqual(by_opt.get(cpp_opt), {"cpp-repo", "legacy-repo", "c-repo"})

    def test_insert_repos_fanout_is_language_aware(self):
        """insert_repos creates b_status rows only on matching-language opts."""
        helper.truncate_all()
        store = helper.make_store()
        cpp_opt = store.register_build_opt(_registration("gcc", "-O1"))
        rust_opt = store.register_build_opt(
            _registration("rustc", "-O1", language="rust", codegen_backend="llvm")
        )

        self.assertEqual(store.insert_repos(_repo("cpp-repo", "c++")), 1)
        self.assertEqual(store.insert_repos(_repo("legacy-repo", "CPP")), 1)
        self.assertEqual(store.insert_repos(_repo("rust-repo", "rust", "cargo")), 1)

        with store._engine.connect() as conn:
            rows = conn.execute(
                sqla.text(
                    "SELECT s.build_opt_id, p.name FROM b_status s"
                    " JOIN projects p ON p.id = s.repo_id"
                )
            ).all()
        by_opt: dict[int, set[str]] = {}
        for opt_id, name in rows:
            by_opt.setdefault(opt_id, set()).add(name)
        self.assertEqual(by_opt.get(cpp_opt), {"cpp-repo", "legacy-repo"})
        self.assertEqual(by_opt.get(rust_opt), {"rust-repo"})

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


class TestBlocklistedDispatch(unittest.TestCase):
    """The blocklist has to be applied in SQL, not to the returned row.

    ``next_dispatchable`` selects one row; rejecting it afterwards would leave it
    NOT_STARTED/INIT for the next tick to select and reject again, wedging that
    build option's dispatcher forever. These tests pin the behaviour that matters:
    a blocked repo at the head of the queue is *skipped over*, not returned.
    """

    #: PROJECT_1/2 belong to the blocked owner and sort ahead of PROJECT_3.
    URLS: ClassVar[list[str]] = [
        "https://api.github.com/repos/Dicklesworthstone/frankenpandas",
        "https://api.github.com/repos/Dicklesworthstone/franken_numpy",
        "https://api.github.com/repos/id-Software/DOOM",
    ]

    def _seed(self):
        helper.truncate_all()
        helper.seed_database_projects_urls(self.URLS)
        helper.seed_database_buildopts()
        helper.seed_database_statuses_unstarted()

    @staticmethod
    def _provider(text: str):
        blocklist = Blocklist.parse(text)
        return lambda: blocklist

    def test_skips_blocked_owner_at_the_head_of_the_queue(self):
        self._seed()
        store = helper.make_store(self._provider("Dicklesworthstone"))

        candidate = store.next_dispatchable(1)

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.name, "PROJECT_3")
        self.assertEqual(candidate.url, self.URLS[2])

    def test_skips_a_single_blocked_repo_but_keeps_its_siblings(self):
        self._seed()
        store = helper.make_store(self._provider("Dicklesworthstone/frankenpandas"))

        candidate = store.next_dispatchable(1)

        self.assertEqual(candidate.name, "PROJECT_2")

    def test_underscore_in_a_repo_name_is_not_a_wildcard(self):
        # 'franken_numpy' as a raw LIKE pattern would also match 'frankenXnumpy';
        # blocking it must leave the *other* franken repo dispatchable.
        self._seed()
        store = helper.make_store(self._provider("Dicklesworthstone/franken_numpy"))

        candidate = store.next_dispatchable(1)

        self.assertEqual(candidate.name, "PROJECT_1")

    def test_returns_none_when_every_candidate_is_blocked(self):
        self._seed()
        store = helper.make_store(self._provider("Dicklesworthstone\nid-Software\n"))

        self.assertIsNone(store.next_dispatchable(1))

    def test_empty_blocklist_dispatches_normally(self):
        self._seed()
        store = helper.make_store(self._provider("# nothing blocked\n"))

        self.assertEqual(store.next_dispatchable(1).name, "PROJECT_1")

    def test_count_pending_excludes_blocked_repos(self):
        self._seed()
        store = helper.make_store(self._provider("Dicklesworthstone"))

        self.assertEqual(store.count_pending(1), 1)

    def test_a_live_edit_takes_effect_without_rebuilding_the_store(self):
        # The coordinator is never restarted to apply a blocklist change, so the
        # store must read the provider on every call rather than caching it.
        self._seed()
        blocklist = {"current": Blocklist.parse("")}
        store = helper.make_store(lambda: blocklist["current"])

        self.assertEqual(store.next_dispatchable(1).name, "PROJECT_1")
        blocklist["current"] = Blocklist.parse("Dicklesworthstone")
        self.assertEqual(store.next_dispatchable(1).name, "PROJECT_3")


if __name__ == "__main__":
    unittest.main()

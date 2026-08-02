"""Unit tests for the dispatch blocklist.

Two properties carry real weight here and are asserted deliberately:

- ``like_patterns`` must not over-match. GitHub names are full of ``_``, which is
  a LIKE wildcard, so an unescaped ``franken_numpy`` would also block
  ``frankenXnumpy``.
- a read failure must never *unblock*. If the file vanishes or cannot be read,
  the last good list stays in force; the alternative is silently re-admitting a
  repository that costs hundreds of gigabytes.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from assemblage.blocklist import Blocklist, FileBlocklist, split_repo_url

API_URL = "https://api.github.com/repos/{}/{}"
CLONE_URL = "https://github.com/{}/{}"


class TestSplitRepoUrl(unittest.TestCase):
    def test_reads_both_stored_url_shapes(self):
        # The scraper stores API URLs; patch_url rewrites them for the builder.
        self.assertEqual(split_repo_url(API_URL.format("Owner", "Name")), ("owner", "name"))
        self.assertEqual(split_repo_url(CLONE_URL.format("Owner", "Name")), ("owner", "name"))

    def test_accepts_bare_slug_and_strips_git_suffix(self):
        self.assertEqual(split_repo_url("owner/name"), ("owner", "name"))
        self.assertEqual(split_repo_url(CLONE_URL.format("a", "b.git")), ("a", "b"))

    def test_rejects_unusable_input(self):
        for bad in ("", "owner", "https://github.com/", "/"):
            self.assertIsNone(split_repo_url(bad), bad)


class TestParse(unittest.TestCase):
    def test_parses_owners_repos_comments_and_blanks(self):
        blocklist = Blocklist.parse(
            "\n".join(
                [
                    "# a comment",
                    "",
                    "Dicklesworthstone",
                    "someone/one-huge-repo   # trailing comment",
                    "https://github.com/Third/Repo",
                    "   ",
                ]
            )
        )
        self.assertEqual(blocklist.owners, frozenset({"dicklesworthstone"}))
        self.assertEqual(blocklist.repos, frozenset({"someone/one-huge-repo", "third/repo"}))

    def test_empty_text_is_falsey(self):
        self.assertFalse(Blocklist.parse("# nothing here\n\n"))
        self.assertTrue(Blocklist.parse("owner"))


class TestMatches(unittest.TestCase):
    def setUp(self):
        self.blocklist = Blocklist.parse("Dicklesworthstone\nsomeone/one-huge-repo\n")

    def test_blocks_every_repo_of_a_blocked_owner(self):
        for url in (
            API_URL.format("Dicklesworthstone", "frankenpandas"),
            CLONE_URL.format("dicklesworthstone", "anything"),
        ):
            self.assertTrue(self.blocklist.matches(url), url)

    def test_blocks_exactly_the_named_repo(self):
        self.assertTrue(self.blocklist.matches(API_URL.format("someone", "one-huge-repo")))
        self.assertFalse(self.blocklist.matches(API_URL.format("someone", "another-repo")))

    def test_does_not_block_an_owner_named_like_a_blocked_project(self):
        # 'one-huge-repo' is blocked only under 'someone', never as an owner.
        self.assertFalse(self.blocklist.matches(API_URL.format("one-huge-repo", "x")))

    def test_unparseable_url_is_not_blocked(self):
        self.assertFalse(self.blocklist.matches("URL_1"))


class TestLikePatterns(unittest.TestCase):
    def test_owner_pattern_requires_a_following_segment(self):
        self.assertEqual(Blocklist.parse("owner").like_patterns(), ("%/owner/%",))

    def test_repo_patterns_anchor_to_the_url_tail(self):
        self.assertEqual(
            Blocklist.parse("owner/name").like_patterns(),
            ("%/owner/name", "%/owner/name/%"),
        )

    def test_like_wildcards_in_names_are_escaped(self):
        # Unescaped, 'franken_numpy' would also match 'frankenXnumpy'.
        patterns = Blocklist.parse("Dicklesworthstone/franken_numpy").like_patterns()
        self.assertEqual(
            patterns,
            ("%/dicklesworthstone/franken\\_numpy", "%/dicklesworthstone/franken\\_numpy/%"),
        )

    def test_empty_blocklist_yields_no_patterns(self):
        self.assertEqual(Blocklist.parse("").like_patterns(), ())


class TestFileBlocklist(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "blocklist.txt"

    def test_missing_file_blocks_nothing(self):
        self.assertFalse(FileBlocklist(self.path).current())

    def test_reloads_when_the_file_changes(self):
        self.path.write_text("owner-a\n")
        # reload_interval_s=0 makes every call re-stat, as the test needs.
        source = FileBlocklist(self.path, reload_interval_s=0)
        self.assertEqual(source.current().owners, frozenset({"owner-a"}))

        self.path.write_text("owner-a\nowner-b\n")
        self.assertEqual(source.current().owners, frozenset({"owner-a", "owner-b"}))

    def test_does_not_re_stat_before_the_interval_elapses(self):
        self.path.write_text("owner-a\n")
        source = FileBlocklist(self.path, reload_interval_s=3600)
        self.path.write_text("owner-b\n")
        self.assertEqual(source.current().owners, frozenset({"owner-a"}))

    def test_a_deleted_file_keeps_the_loaded_list(self):
        self.path.write_text("owner-a\n")
        source = FileBlocklist(self.path, reload_interval_s=0)
        self.assertTrue(source.current())

        self.path.unlink()
        self.assertEqual(source.current().owners, frozenset({"owner-a"}))

    def test_an_unreadable_file_keeps_the_loaded_list(self):
        self.path.write_text("owner-a\n")
        source = FileBlocklist(self.path, reload_interval_s=0)
        self.assertTrue(source.current())

        self.path.write_text("owner-b\n")
        self.path.chmod(0o000)
        self.addCleanup(self.path.chmod, 0o644)
        if self.path.is_file() and _readable(self.path):
            self.skipTest("running as root: chmod cannot make the file unreadable")
        self.assertEqual(source.current().owners, frozenset({"owner-a"}))


def _readable(path: Path) -> bool:
    try:
        path.read_text()
    except OSError:
        return False
    return True


if __name__ == "__main__":
    unittest.main()

"""Unit tests for the language-aware fan-out helper (``assemblage.db.store``).

The predicate guards BOTH b_status fan-out sites (``insert_repos`` and the
``register_build_opt`` back-fill). The C-family rows — GitHub's "C" repos, the
scraper's lowercase 'c++', and the 1,594 legacy uppercase 'CPP' project rows —
must all keep matching the c++ buildopts; everything else is exact-match.
"""

import unittest

from assemblage.db.store import languages_match


class TestLanguagesMatch(unittest.TestCase):
    def test_c_family_matches_cpp_buildopts(self):
        for repo_language in ("c++", "C++", "CPP", "cpp", "c"):
            with self.subTest(repo_language=repo_language):
                self.assertTrue(languages_match(repo_language, "c++"))

    def test_c_family_does_not_match_rust_buildopts(self):
        for repo_language in ("c++", "C++", "CPP", "cpp", "c"):
            with self.subTest(repo_language=repo_language):
                self.assertFalse(languages_match(repo_language, "rust"))

    def test_rust_matches_only_rust_buildopts(self):
        for repo_language in ("rust", "Rust"):
            with self.subTest(repo_language=repo_language):
                self.assertTrue(languages_match(repo_language, "rust"))
                self.assertFalse(languages_match(repo_language, "c++"))

    def test_unknown_language_is_exact_match_only(self):
        self.assertFalse(languages_match("go", "c++"))
        self.assertFalse(languages_match("go", "rust"))
        self.assertTrue(languages_match("go", "go"))

    def test_opt_side_is_normalized_too(self):
        # Defensive symmetry: an opt registered as 'cpp' still covers c++ repos.
        self.assertTrue(languages_match("c++", "cpp"))
        self.assertTrue(languages_match("Rust", "RUST"))


if __name__ == "__main__":
    unittest.main()

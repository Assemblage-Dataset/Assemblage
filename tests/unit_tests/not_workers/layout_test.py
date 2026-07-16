"""Unit tests for storage.layout — the single source of S3-key truth.

The exact strings here are frozen: the E2E gate asserts these very keys, and the
dataset pipeline reads them.
"""

import unittest

from assemblage.storage import layout


class TestLayout(unittest.TestCase):
    def test_artifact_prefix(self):
        self.assertEqual(
            layout.artifact_prefix("e2e", "hello-make", "0123456789ab", "gcc", "-O0"),
            "e2e_hello-make_0123456789ab_gcc_-O0",
        )

    def test_artifact_and_metadata_keys(self):
        prefix = layout.artifact_prefix("e2e", "hello-make", "0123456789ab", "gcc", "-O0")
        self.assertEqual(layout.artifact_key(prefix, "hello"), f"{prefix}/hello")
        self.assertEqual(layout.metadata_key(prefix), f"{prefix}/assemblage_meta.json")

    def test_archive_and_pointer_keys(self):
        self.assertEqual(
            layout.archive_key("e2e", "hello-make", "0123456789ab"),
            "e2e/hello-make/0123456789ab.tar.gz",
        )
        self.assertEqual(layout.pointer_key("e2e", "hello-make"), "e2e/hello-make/latest.txt")

    def test_metadata_filename(self):
        self.assertEqual(layout.METADATA_FILENAME, "assemblage_meta.json")


if __name__ == "__main__":
    unittest.main()

"""Unit tests for storage.compress and the v2 (compressed) layout keys.

The level is pinned deliberately: it is the level the published HuggingFace
corpus already uses, and a build only exports without recompression if the two
agree. A change here silently re-costs every future release, so it is asserted.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from assemblage.storage import compress, layout


class TestCompressLevel(unittest.TestCase):
    def test_level_matches_the_published_corpus(self):
        self.assertEqual(compress.COMPRESS_LEVEL, 12)

    def test_suffix_matches_the_published_corpus(self):
        self.assertEqual(layout.COMPRESSED_SUFFIX, ".zst")


class TestRoundTrip(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_file_round_trip_is_byte_exact(self):
        payload = b"\x7fELF" + bytes(range(256)) * 400
        src = self.tmp / "binary"
        src.write_bytes(payload)

        packed = self.tmp / "binary.zst"
        stored = compress.compress_file(src, packed)
        self.assertEqual(stored, packed.stat().st_size)
        self.assertLess(stored, len(payload))

        restored = self.tmp / "restored"
        self.assertEqual(compress.decompress_file(packed, restored), len(payload))
        self.assertEqual(restored.read_bytes(), payload)

    def test_bytes_round_trip_is_byte_exact(self):
        payload = b'{"Binary_info_list": []}' * 500
        self.assertEqual(compress.decompress_bytes(compress.compress_bytes(payload)), payload)

    def test_decompresses_a_streamed_frame(self):
        # copy_stream writes a frame with no declared content size; the naive
        # zstandard.decompress() call rejects those, stream_reader does not.
        src = self.tmp / "src"
        src.write_bytes(b"x" * (8 << 20))
        packed = self.tmp / "src.zst"
        compress.compress_file(src, packed)

        self.assertEqual(compress.decompress_bytes(packed.read_bytes()), b"x" * (8 << 20))

    def test_empty_input_round_trips(self):
        src = self.tmp / "empty"
        src.write_bytes(b"")
        packed = self.tmp / "empty.zst"
        compress.compress_file(src, packed)
        restored = self.tmp / "restored"
        compress.decompress_file(packed, restored)
        self.assertEqual(restored.read_bytes(), b"")


class TestV2Layout(unittest.TestCase):
    #: Exactly the directory name inside a published HuggingFace repo tar.
    RUST_DIR = "0Albiere_LSM_Tree_Storage_Engine_3aa71c69557b-O2-llvm-Release"

    def test_rust_build_dir_matches_the_published_corpus(self):
        self.assertEqual(
            layout.build_dir(
                "0Albiere", "LSM_Tree_Storage_Engine", "3aa71c69557b", "-O2", "llvm", "Release"
            ),
            self.RUST_DIR,
        )

    def test_flag_leading_dash_is_stripped_either_way(self):
        with_dash = layout.build_dir("o", "p", "abc123456789", "-O2", "llvm", "Release")
        without = layout.build_dir("o", "p", "abc123456789", "O2", "llvm", "Release")
        self.assertEqual(with_dash, without)

    def test_c_builds_carry_the_mode_too(self):
        # v1's C prefix had no mode, so Debug and Release of one commit collided.
        debug = layout.build_dir("o", "p", "abc123456789", "-O0", "gcc", "Debug")
        release = layout.build_dir("o", "p", "abc123456789", "-O0", "gcc", "Release")
        self.assertNotEqual(debug, release)
        self.assertEqual(release, "o_p_abc123456789-O0-gcc-Release")

    def test_object_keys(self):
        self.assertEqual(
            layout.binary_key(self.RUST_DIR, "lsm-cli"), f"{self.RUST_DIR}/binaries/lsm-cli.zst"
        )
        self.assertEqual(
            layout.compressed_metadata_key(self.RUST_DIR),
            f"{self.RUST_DIR}/metadata/assemblage_meta.json.zst",
        )
        self.assertEqual(
            layout.export_manifest_key(self.RUST_DIR), f"{self.RUST_DIR}/export.json"
        )

    def test_ir_keys_land_inside_the_v2_build_dir(self):
        # IR is already gzipped, so v2 changes only where it sits, not its name.
        self.assertEqual(
            layout.ir_tarball_key(self.RUST_DIR, "llvm-ir"), f"{self.RUST_DIR}/ir/llvm-ir.tar.gz"
        )

    def test_v1_keys_are_untouched(self):
        # Readers still resolve them until the backfill completes.
        prefix = layout.artifact_prefix("e2e", "hello", "0123456789ab", "gcc", "-O0")
        self.assertEqual(layout.artifact_key(prefix, "hello"), f"{prefix}/hello")
        self.assertEqual(layout.metadata_key(prefix), f"{prefix}/assemblage_meta.json")


if __name__ == "__main__":
    unittest.main()

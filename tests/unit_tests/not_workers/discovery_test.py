"""Unit tests for build.discovery.find_binaries.

Uses the host cc (present in the builder image and on CI) to produce a real ELF;
skips if no compiler is available. Verifies ELF detection, the ``-save-temps``
``.s`` inclusion toggle, and the skip-directory list.
"""

import os
import shutil
import subprocess
import unittest

from assemblage.build.discovery import find_binaries

_CC = shutil.which("cc") or shutil.which("gcc")


@unittest.skipIf(_CC is None, "no C compiler available")
class TestFindBinaries(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        src = os.path.join(self.tmp, "prog.c")
        with open(src, "w") as f:
            f.write("int main(void){return 0;}\n")
        self.prog = os.path.join(self.tmp, "prog")
        subprocess.run([_CC, "-g", "-O0", "-o", self.prog, src], check=True)

        # A text assembly artifact (only found with save_assembly=True).
        self.asm = os.path.join(self.tmp, "prog.s")
        with open(self.asm, "w") as f:
            f.write("\t.text\n")

        # A plain text file that must never be picked up.
        with open(os.path.join(self.tmp, "notes.txt"), "w") as f:
            f.write("hello\n")

        # An ELF hidden inside a skip-dir must be ignored.
        skip = os.path.join(self.tmp, ".git")
        os.makedirs(skip)
        shutil.copy(self.prog, os.path.join(skip, "hidden"))

    def test_finds_elf_ignores_text(self):
        found = find_binaries(self.tmp, platform="linux", save_assembly=False)
        self.assertIn(self.prog, found)
        self.assertNotIn(self.asm, found)  # save_assembly off
        self.assertFalse(any(f.endswith("notes.txt") for f in found))

    def test_save_assembly_includes_s_files(self):
        found = find_binaries(self.tmp, platform="linux", save_assembly=True)
        self.assertIn(self.prog, found)
        self.assertIn(self.asm, found)

    def test_skip_dirs_excluded(self):
        found = find_binaries(self.tmp, platform="linux", save_assembly=True)
        self.assertFalse(any("/.git/" in f for f in found))


if __name__ == "__main__":
    unittest.main()

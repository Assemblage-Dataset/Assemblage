"""Unit tests for dwarf.extract.

The real E2E gate is the extractor's acceptance test; these cover what runs
offline: a full extraction against a locally-compiled ``-g`` binary (skipped
with no compiler), plus pure-function tests for range resolution, the intersect
ratio, the duplicate-function dedup, and the size-limit / alarm guards.
"""

import os
import shutil
import subprocess
import unittest
from types import SimpleNamespace

from assemblage.dwarf import extract

_CC = shutil.which("cc") or shutil.which("gcc")


@unittest.skipIf(_CC is None, "no C compiler available")
class TestExtractRealBinary(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        src = os.path.join(self.tmp, "prog.c")
        with open(src, "w") as f:
            f.write(
                "int add(int a, int b) {\n"
                "    return a + b;\n"
                "}\n"
                "int main(void) {\n"
                "    return add(1, 2);\n"
                "}\n"
            )
        self.prog = os.path.join(self.tmp, "prog")
        subprocess.run([_CC, "-g", "-O0", "-o", self.prog, src], check=True, cwd=self.tmp)

    def test_extracts_functions_and_lines(self):
        item = extract.extract_dwarf_info(self.prog)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item["file"], "prog")
        funcs = {f["function_name"]: f for f in item["functions"]}
        self.assertIn("add", funcs)
        self.assertIn("main", funcs)
        add = funcs["add"]
        self.assertTrue(add["function_info"])  # has RVA ranges
        self.assertTrue(add["source_file"].endswith("prog.c"))
        line_numbers = {ln["line_number"] for ln in add["lines"]}
        self.assertIn(2, line_numbers)  # the `return a + b;` line

    def test_size_limit_skips(self):
        # A 0-byte size limit skips every binary — and NEVER silently: the skip
        # must be attributable from the logs (senior-verification finding: real
        # >150 MB Rust binaries lost their whole Binary_info_list untraceably).
        with self.assertLogs("assemblage.dwarf.extract", level="WARNING") as cm:
            self.assertIsNone(extract.extract_dwarf_info(self.prog, size_limit=0))
        self.assertTrue(any("DWARF_SIZE_LIMIT" in line for line in cm.output))

    def test_alarm_armed_on_main_thread(self):
        # A generous timeout arms + disarms SIGALRM without tripping.
        item = extract.extract_dwarf_info(self.prog, timeout_secs=30)
        self.assertIsNotNone(item)


class TestResolveAddressRanges(unittest.TestCase):
    def _attr(self, value, form=""):
        return SimpleNamespace(value=value, form=form)

    def test_low_high_pc_offset_form(self):
        die = SimpleNamespace(
            attributes={
                "DW_AT_low_pc": self._attr(0x1000),
                "DW_AT_high_pc": self._attr(0x40, form="DW_FORM_data8"),
            }
        )
        ranges = extract._resolve_address_ranges(die, dwarf_info=None, cu_base_addr=0)
        self.assertEqual(ranges, [(0x1000, 0x1040)])

    def test_low_high_pc_addr_form(self):
        die = SimpleNamespace(
            attributes={
                "DW_AT_low_pc": self._attr(0x2000),
                "DW_AT_high_pc": self._attr(0x2100, form="DW_FORM_addr"),
            }
        )
        ranges = extract._resolve_address_ranges(die, dwarf_info=None, cu_base_addr=0)
        self.assertEqual(ranges, [(0x2000, 0x2100)])

    def test_no_pc_returns_none(self):
        die = SimpleNamespace(attributes={})
        self.assertIsNone(extract._resolve_address_ranges(die, dwarf_info=None, cu_base_addr=0))


class TestPureHelpers(unittest.TestCase):
    def test_intersect_ratio_single_range(self):
        self.assertEqual(extract._intersect_ratio([{"start_int": 0, "end_int": 10}]), "0%")

    def test_intersect_ratio_gap(self):
        # total span 0..100 = 100, gap between [0,40] and [60,100] = 20 -> 20%.
        ranges = [
            {"start_int": 0, "end_int": 40},
            {"start_int": 60, "end_int": 100},
        ]
        self.assertEqual(extract._intersect_ratio(ranges), "20.00%")

    def _func(self, name):
        return {
            "name": name,
            "source_file": "a.c",
            "ranges": [
                {
                    "rva_start": "0" * 16,
                    "rva_end": "f".rjust(16, "0"),
                    "start_int": 0,
                    "end_int": 15,
                }
            ],
            "lines": [],
        }

    def test_build_item_dedups_identical_functions(self):
        item = extract._build_item("/x/prog", [self._func("add"), self._func("add")])
        assert item is not None
        self.assertEqual(len(item["functions"]), 1)

    def test_build_item_keeps_distinct_functions(self):
        item = extract._build_item("/x/prog", [self._func("add"), self._func("sub")])
        assert item is not None
        self.assertEqual(len(item["functions"]), 2)

    def test_build_item_empty_returns_none(self):
        self.assertIsNone(extract._build_item("/x/prog", []))


if __name__ == "__main__":
    unittest.main()

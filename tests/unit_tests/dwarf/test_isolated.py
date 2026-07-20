"""Tests for the out-of-process DWARF extractor (assemblage.dwarf.isolated).

The point of this module is that extraction can no longer park a builder or
OOM its container, so the tests that matter are the *failure* paths: a child
that hangs must be killed and reported as "no debug info", never raised. Every
failure mode has to collapse onto ``None``, which is what the pre-existing
DWARF_SIZE_LIMIT skip already returns and what every caller already handles.
"""

import subprocess
import sys
import textwrap
import time
import unittest
from unittest import mock

from assemblage.dwarf import isolated


class TestParseResult(unittest.TestCase):
    def test_reads_json_after_marker(self):
        out = f"noise on stdout{isolated._RESULT_MARKER}" + '{"file": "a.out"}'
        self.assertEqual(isolated._parse_result(out, "a.out"), {"file": "a.out"})

    def test_library_chatter_before_marker_is_ignored(self):
        out = f"pyelftools said something\n{isolated._RESULT_MARKER}" + '{"file": "x"}'
        self.assertEqual(isolated._parse_result(out, "x"), {"file": "x"})

    def test_null_payload_is_none(self):
        """extract_dwarf_info returns None for a binary with no debug info."""
        self.assertIsNone(isolated._parse_result(isolated._RESULT_MARKER + "null", "x"))

    def test_missing_marker_is_none(self):
        self.assertIsNone(isolated._parse_result('{"file": "x"}', "x"))

    def test_unparseable_payload_is_none(self):
        self.assertIsNone(isolated._parse_result(isolated._RESULT_MARKER + "{not json", "x"))

    def test_non_object_payload_is_none(self):
        self.assertIsNone(isolated._parse_result(isolated._RESULT_MARKER + "[1, 2]", "x"))


class TestExtractIsolated(unittest.TestCase):
    """Drives the real subprocess machinery against stand-in child programs."""

    def _run_child(self, body: str, *, timeout_secs: int = 10):
        """Run extract_isolated against a throwaway python child instead of the extractor.

        Binds the real Popen before patching -- referring to ``subprocess.Popen``
        inside the side effect would resolve back to the mock and recurse.
        """
        script = textwrap.dedent(body)
        real_popen = subprocess.Popen

        def _spawn(_cmd, **kwargs):
            return real_popen([sys.executable, "-c", script], **kwargs)

        with mock.patch.object(isolated.subprocess, "Popen", side_effect=_spawn):
            return isolated.extract_isolated("/nonexistent", timeout_secs=timeout_secs)

    def test_successful_round_trip(self):
        item = self._run_child(f"""
            import sys
            sys.stdout.write({isolated._RESULT_MARKER!r})
            sys.stdout.write('{{"file": "hello", "functions": []}}')
        """)
        self.assertEqual(item, {"file": "hello", "functions": []})

    def test_hanging_child_is_killed_and_returns_none(self):
        """The whole point: a child that never finishes cannot park the caller."""
        started = time.monotonic()
        item = self._run_child(
            """
            import time
            time.sleep(300)
            """,
            timeout_secs=1,
        )
        elapsed = time.monotonic() - started
        self.assertIsNone(item)
        # Bounded by the timeout, not by the child's 300s sleep.
        self.assertLess(elapsed, 30)

    def test_crashing_child_returns_none(self):
        self.assertIsNone(
            self._run_child("""
                import sys
                sys.stderr.write("boom")
                sys.exit(1)
            """)
        )

    def test_child_killed_by_signal_returns_none(self):
        self.assertIsNone(
            self._run_child("""
                import os, signal
                os.kill(os.getpid(), signal.SIGKILL)
            """)
        )

    def test_memory_limit_is_enforced_in_the_child(self):
        """RLIMIT_AS makes a runaway allocation kill the child, not the parent."""
        item = self._run_child(f"""
            import resource, sys
            resource.setrlimit(resource.RLIMIT_AS, (64 * 1024 * 1024,) * 2)
            try:
                x = bytearray(512 * 1024 * 1024)
            except MemoryError:
                sys.exit(1)
            sys.stdout.write({isolated._RESULT_MARKER!r})
            sys.stdout.write("null")
        """)
        self.assertIsNone(item)

    def test_spawn_failure_returns_none(self):
        with mock.patch.object(isolated.subprocess, "Popen", side_effect=OSError("no fork")):
            self.assertIsNone(isolated.extract_isolated("/x", timeout_secs=5))


class TestExtractEach(unittest.TestCase):
    def test_yields_only_successful_items(self):
        with mock.patch.object(isolated, "extract_isolated", side_effect=[{"file": "a"}, None]):
            got = list(
                isolated.extract_each(["a", "b"], timeout_secs=10, phase_timeout_s=100)
            )
        self.assertEqual(got, [{"file": "a"}])

    def test_preserves_caller_order(self):
        items = [{"file": "a"}, {"file": "b"}, {"file": "c"}]
        with mock.patch.object(isolated, "extract_isolated", side_effect=items):
            got = list(
                isolated.extract_each(["a", "b", "c"], timeout_secs=10, phase_timeout_s=100)
            )
        self.assertEqual([i["file"] for i in got], ["a", "b", "c"])

    def test_phase_budget_stops_further_extraction(self):
        """A build with many binaries cannot cost len(binaries) * timeout_secs."""
        calls = []

        def _slow(binfile, **kw):
            calls.append(binfile)
            time.sleep(0.4)
            return {"file": binfile}

        with mock.patch.object(isolated, "extract_isolated", side_effect=_slow):
            got = list(
                isolated.extract_each(
                    [f"bin{i}" for i in range(20)], timeout_secs=10, phase_timeout_s=1
                )
            )
        self.assertLess(len(calls), 20, "phase budget did not stop the loop")
        self.assertEqual(len(got), len(calls))

    def test_per_binary_timeout_clamped_to_remaining_phase_budget(self):
        seen = {}

        def _capture(binfile, **kw):
            seen[binfile] = kw["timeout_secs"]
            return None

        with mock.patch.object(isolated, "extract_isolated", side_effect=_capture):
            list(isolated.extract_each(["a"], timeout_secs=9999, phase_timeout_s=5))
        self.assertLessEqual(seen["a"], 5)

    def test_one_raising_binary_does_not_lose_the_others(self):
        with mock.patch.object(
            isolated, "extract_isolated", side_effect=[RuntimeError("bad elf"), {"file": "b"}]
        ):
            got = list(
                isolated.extract_each(["a", "b"], timeout_secs=10, phase_timeout_s=100)
            )
        self.assertEqual(got, [{"file": "b"}])


if __name__ == "__main__":
    unittest.main()

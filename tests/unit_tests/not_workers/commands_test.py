"""Unit tests for build.commands.run_command.

The timeout test proves the killpg fix: a timed-out child's whole process group
is killed and the TEST PROCESS (standing in for the worker) survives — the
opposite of the old cmd_with_output, which could SIGTERM the worker itself.
"""

import os
import time
import unittest

from assemblage.build.commands import run_command


class TestRunCommand(unittest.TestCase):
    def test_success_captures_output(self):
        result = run_command("printf hello", timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"hello")

    def test_nonzero_exit(self):
        result = run_command("exit 3", timeout=10)
        self.assertEqual(result.returncode, 3)

    def test_timeout_kills_group_and_worker_survives(self):
        pidfile = os.path.join(
            os.environ.get("TMPDIR", "/tmp"), f"assemblage_cmd_{os.getpid()}.pid"
        )
        if os.path.exists(pidfile):
            os.remove(pidfile)
        # A backgrounded child in the same process group as the shell, plus a
        # foreground sleep so the command blocks past the timeout.
        cmd = f"sleep 300 & echo $! > {pidfile}; sleep 300"

        started = time.monotonic()
        result = run_command(cmd, timeout=1)
        elapsed = time.monotonic() - started

        # Returned promptly with the frozen timeout contract.
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, b"subprocess.TimeoutExpired")
        self.assertLess(elapsed, 20)

        # This process (the "worker") is still alive to run this assertion.
        os.kill(os.getpid(), 0)

        # The child's process group was killed: the backgrounded sleep is gone.
        with open(pidfile) as handle:
            child_pid = int(handle.read().strip())
        os.remove(pidfile)

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        with self.assertRaises(ProcessLookupError):
            os.kill(child_pid, 0)


if __name__ == "__main__":
    unittest.main()

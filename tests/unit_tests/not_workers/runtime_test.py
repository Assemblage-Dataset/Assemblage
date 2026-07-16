"""Unit tests for the runtime substrate (Backoff, Supervisor)."""

import random
import threading
import unittest

from assemblage.runtime.service import Backoff, RestartPolicy, Service
from assemblage.runtime.supervisor import Supervisor


class TestBackoff(unittest.TestCase):
    def test_no_jitter_is_exponential_capped(self):
        backoff = Backoff(initial=1.0, maximum=8.0, factor=2.0, jitter=0.0)
        self.assertEqual(
            [backoff.delay(i) for i in range(6)],
            [1.0, 2.0, 4.0, 8.0, 8.0, 8.0],
        )

    def test_seeded_jitter_is_deterministic(self):
        backoff = Backoff(initial=1.0, maximum=60.0, factor=2.0, jitter=0.25)
        random.seed(1234)
        first = [backoff.delay(i) for i in range(6)]
        random.seed(1234)
        second = [backoff.delay(i) for i in range(6)]
        self.assertEqual(first, second)

    def test_jitter_stays_within_bounds(self):
        backoff = Backoff(initial=4.0, maximum=100.0, factor=2.0, jitter=0.25)
        random.seed(0)
        for attempt in range(20):
            base = 4.0 * 2.0**attempt
            delay = backoff.delay(attempt)
            self.assertGreaterEqual(delay, 0.0)
            self.assertLessEqual(delay, base * 1.25)


class _CrashThenBlock(Service):
    """Crashes the first ``crashes`` runs, then blocks until stopped."""

    def __init__(self, crashes: int) -> None:
        self.name = "crash"
        self._crashes = crashes
        self.starts = 0
        self.ready = threading.Event()

    def run(self, stop: threading.Event) -> None:
        self.starts += 1
        if self.starts <= self._crashes:
            raise RuntimeError("boom")
        self.ready.set()
        stop.wait()


class _BlockUntilStopped(Service):
    def __init__(self) -> None:
        self.name = "block"
        self.stop_requested = threading.Event()

    def run(self, stop: threading.Event) -> None:
        stop.wait()

    def request_stop(self) -> None:
        self.stop_requested.set()


class _ShutdownCaller(Service):
    def __init__(self, supervisor: Supervisor, code: int) -> None:
        self.name = "shutdown"
        self._supervisor = supervisor
        self._code = code

    def run(self, stop: threading.Event) -> None:
        self._supervisor.request_shutdown(self._code)
        stop.wait()


_FAST = RestartPolicy(backoff=Backoff(0.001, 0.001, jitter=0.0), reset_after=1000.0)


class TestSupervisor(unittest.TestCase):
    def test_restarts_service_after_crash(self):
        service = _CrashThenBlock(crashes=2)
        supervisor = Supervisor()
        supervisor.add(service, _FAST)
        supervisor.start()
        try:
            self.assertTrue(service.ready.wait(5), "service never recovered")
            self.assertEqual(service.starts, 3)
        finally:
            supervisor.stop(timeout=5)
        for thread in supervisor._threads:
            self.assertFalse(thread.is_alive())

    def test_graceful_stop_calls_request_stop_and_joins(self):
        service = _BlockUntilStopped()
        supervisor = Supervisor()
        supervisor.add(service, RestartPolicy(restart=False))
        supervisor.start()
        supervisor.stop(timeout=5)
        self.assertTrue(service.stop_requested.is_set())
        for thread in supervisor._threads:
            self.assertFalse(thread.is_alive())

    def test_run_until_signal_returns_requested_exit_code(self):
        supervisor = Supervisor()
        caller = _ShutdownCaller(supervisor, code=7)
        supervisor.add(caller, RestartPolicy(restart=False))
        code = supervisor.run_until_signal()
        self.assertEqual(code, 7)
        for thread in supervisor._threads:
            self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()

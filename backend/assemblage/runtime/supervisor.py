"""Supervised threading: one named thread per service, crash-restart, and
graceful SIGTERM/SIGINT shutdown.

Replaces the old stop-the-world ``sys.excepthook`` + ``os._exit`` model: a
single crashed service restarts with backoff instead of killing the process,
and shutdown drains every service cooperatively.
"""

import logging
import signal
import threading
import time
from types import FrameType

from assemblage.runtime.service import RestartPolicy, Service

logger = logging.getLogger(__name__)


class Supervisor:
    """Runs a set of :class:`Service` objects, each on its own thread."""

    def __init__(self) -> None:
        self._services: list[tuple[Service, RestartPolicy]] = []
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._exit_code = 0
        self._lock = threading.Lock()

    def add(self, service: Service, policy: RestartPolicy | None = None) -> None:
        """Register a service (before :meth:`start`)."""
        with self._lock:
            self._services.append((service, policy or RestartPolicy()))

    def start(self) -> None:
        """Launch one non-daemon thread per registered service."""
        with self._lock:
            services = list(self._services)
        for service, policy in services:
            self._launch(service, policy)

    def add_and_start(self, service: Service, policy: RestartPolicy | None = None) -> None:
        """Register and immediately launch a service after :meth:`start`.

        Used by the coordinator's dispatch manager, which spins up one
        per-buildopt dispatcher as each builder registers — i.e. after the
        supervisor is already running. A no-op if shutdown has begun.
        """
        policy = policy or RestartPolicy()
        with self._lock:
            if self._stop.is_set():
                return
            self._services.append((service, policy))
        self._launch(service, policy)

    def _launch(self, service: Service, policy: RestartPolicy) -> None:
        thread = threading.Thread(
            target=self._supervise,
            args=(service, policy),
            name=service.name,
            daemon=False,
        )
        with self._lock:
            self._threads.append(thread)
        thread.start()

    def _supervise(self, service: Service, policy: RestartPolicy) -> None:
        attempt = 0
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                service.run(self._stop)
            except Exception:
                logger.exception("service %r crashed", service.name)
            else:
                logger.info("service %r returned", service.name)
            if self._stop.is_set() or not policy.restart:
                break
            if time.monotonic() - started >= policy.reset_after:
                attempt = 0
            delay = policy.backoff.delay(attempt)
            attempt += 1
            logger.warning("restarting service %r in %.2fs", service.name, delay)
            if self._stop.wait(delay):
                break
        logger.info("service %r stopped", service.name)

    def request_shutdown(self, code: int = 0) -> None:
        """Ask the supervisor to stop everything and exit with ``code``.

        Safe to call from any thread (e.g. the builder's 1000-task recycle).
        """
        with self._lock:
            self._exit_code = code
        self._stop.set()
        self._wake_services()

    def _wake_services(self) -> None:
        with self._lock:
            services = [service for service, _ in self._services]
        for service in services:
            try:
                service.request_stop()
            except Exception:
                logger.exception("request_stop failed for %r", service.name)

    def stop(self, timeout: float = 25.0) -> None:
        """Signal every service to stop and join with a shared deadline."""
        self._stop.set()
        self._wake_services()
        with self._lock:
            threads = list(self._threads)
        deadline = time.monotonic() + timeout
        for thread in threads:
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(remaining)
        for thread in threads:
            if thread.is_alive():
                logger.warning("service thread %r did not stop within %.0fs", thread.name, timeout)

    def run_until_signal(self) -> int:
        """Start services, block until SIGTERM/SIGINT or shutdown, then stop.

        Returns the process exit code. Must be called from the main thread
        (signal handlers can only be installed there).
        """
        self.start()

        def _handler(signum: int, _frame: FrameType | None) -> None:
            logger.info("received signal %s, shutting down", signum)
            self.request_shutdown(0)

        prev_term = signal.signal(signal.SIGTERM, _handler)
        prev_int = signal.signal(signal.SIGINT, _handler)
        try:
            while not self._stop.wait(0.5):
                pass
        finally:
            signal.signal(signal.SIGTERM, prev_term)
            signal.signal(signal.SIGINT, prev_int)
            self.stop()
        with self._lock:
            return self._exit_code

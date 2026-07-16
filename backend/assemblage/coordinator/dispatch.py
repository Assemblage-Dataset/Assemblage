"""Per-buildopt dispatch: one supervised thread pushes tasks to one builder queue.

Dispatch stays one thread per build option because the pacing and thresholds are
per-opt stateful; multiplexing would serialize the DB scans across opts, an
observable change. The frozen semantics live here unchanged:

- consult the build_opt queue depth first; above the threshold, idle;
- otherwise pull one un-started task and, if none, flag starvation so the
  scraper-request service asks for more repos;
- **publish first, mark PROCESSING only after the publish is confirmed** — the
  one approved behavioural delta (the old code marked PROCESSING even when the
  silent-failing publish never reached the broker).
"""

import json
import logging
import threading
import time

import pika.exceptions

from assemblage.constants import (
    BIN_DIR,
    COORDINATOR_REPO_REQUEST_THRESHOLD,
    DISPATCH_INTERVAL,
    WAIT_AFTER_REQ_INTERVAL,
)
from assemblage.db.store import CoordinatorStore
from assemblage.messages import BuildTask
from assemblage.mq.connection import ConnectionFactory, MQConnectionError
from assemblage.mq.publisher import Publisher, PublishError
from assemblage.mq.topology import build_opt_queue
from assemblage.runtime.service import Backoff, Service
from assemblage.runtime.supervisor import Supervisor

logger = logging.getLogger(__name__)


def patch_url(url: str) -> str:
    """Turn a GitHub API URL into a cloneable one (drops ``api.`` and ``repos/``)."""
    return url.replace("repos/", "").replace("api.", "")


class StarvationSignals:
    """Per-opt 'ran out of work' flags shared with the scraper-request service."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[int, threading.Event] = {}

    def mark(self, opt_id: int) -> None:
        """Signal that ``opt_id`` has no dispatchable work."""
        with self._lock:
            self._events.setdefault(opt_id, threading.Event()).set()

    def take_starving(self) -> list[int]:
        """Return the opts flagged since the last call and clear their flags."""
        with self._lock:
            starving = [opt for opt, event in self._events.items() if event.is_set()]
            for opt in starving:
                self._events[opt].clear()
            return starving


class DispatcherService(Service):
    """Dispatches tasks for a single build option ``opt_id``."""

    def __init__(
        self,
        opt_id: int,
        store: CoordinatorStore,
        starvation: StarvationSignals,
        factory: ConnectionFactory,
        reconnect: Backoff | None = None,
    ) -> None:
        self.name = f"dispatch-{opt_id}"
        self._opt_id = opt_id
        self._store = store
        self._starvation = starvation
        self._factory = factory
        self._reconnect = reconnect or Backoff()
        self._queue = build_opt_queue(opt_id)
        self._dispatched = 0

    def run(self, stop: threading.Event) -> None:
        publisher = Publisher(self.name, self._factory)
        attempt = 0
        try:
            while not stop.is_set():
                try:
                    self.dispatch_step(publisher, stop)
                    attempt = 0
                except (pika.exceptions.AMQPError, MQConnectionError, PublishError) as error:
                    if stop.is_set():
                        break
                    delay = self._reconnect.delay(attempt)
                    attempt += 1
                    logger.warning(
                        "dispatch %s error, retrying in %.2fs: %s", self.name, delay, error
                    )
                    publisher.close()
                    stop.wait(delay)
        finally:
            publisher.close()

    def dispatch_step(self, publisher: Publisher, stop: threading.Event) -> None:
        """One dispatch iteration (depth check -> select -> publish -> mark)."""
        depth = publisher.queue_depth(self._queue)
        if depth > COORDINATOR_REPO_REQUEST_THRESHOLD:
            stop.wait(DISPATCH_INTERVAL)
            return

        candidate = self._store.next_dispatchable(self._opt_id)
        if candidate is None:
            self._starvation.mark(self._opt_id)
            stop.wait(WAIT_AFTER_REQ_INTERVAL)
            return

        task = BuildTask(
            name=candidate.name,
            url=patch_url(candidate.url),
            task_id=candidate.task_id,
            opt_id=candidate.opt_id,
            repo_id=candidate.repo_id,
            updated_at=candidate.updated_at,
            build_system=candidate.build_system,
            msg_time=time.time(),
            compiler_flag=candidate.compiler_flag,
        )
        publisher.publish(self._queue, self._encode(task))
        # publish-confirmed-first: only now is the task really in flight.
        self._store.mark_clone_processing(candidate.task_id)
        self._dispatched += 1
        if self._dispatched % 100 == 0:
            logger.info("dispatched %d tasks on build_opt_%d", self._dispatched, self._opt_id)
        stop.wait(DISPATCH_INTERVAL)

    @staticmethod
    def _encode(task: BuildTask) -> str:
        """Serialize a task, re-adding the write-only keys the legacy builder needs.

        The new ``BuildTask`` drops ``output_dir`` / ``mod_timestamp``, but the
        not-yet-rewritten builder's ``BuilderTaskOut`` still requires
        ``output_dir`` positionally (it would raise ``TypeError`` on a missing
        key). We re-add both here — the builder ignores their values — so the
        pipeline keeps working across the P6/P7 boundary. Once P7's builder
        parses ``BuildTask`` (``extra="ignore"``) this shim can drop away.
        """
        payload = task.model_dump()
        payload["output_dir"] = f"{BIN_DIR}/{task.task_id}"
        payload["mod_timestamp"] = "0"
        return json.dumps(payload)


class DispatchManager:
    """Starts exactly one :class:`DispatcherService` per build option, on demand."""

    def __init__(
        self,
        factory: ConnectionFactory,
        store: CoordinatorStore,
        starvation: StarvationSignals,
        supervisor: Supervisor,
    ) -> None:
        self._factory = factory
        self._store = store
        self._starvation = starvation
        self._supervisor = supervisor
        self._lock = threading.Lock()
        self._started: set[int] = set()

    def ensure_started(self, opt_id: int) -> None:
        """Idempotently launch the dispatcher for ``opt_id``."""
        with self._lock:
            if opt_id in self._started:
                logger.info("dispatcher for build_opt_%d already running", opt_id)
                return
            self._started.add(opt_id)
            service = DispatcherService(opt_id, self._store, self._starvation, self._factory)
        logger.info("starting dispatcher for build_opt_%d", opt_id)
        self._supervisor.add_and_start(service)

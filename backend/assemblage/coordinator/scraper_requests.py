"""The scraper-request service: turn dispatch starvation into REQUEST_REPOS broadcasts.

When every dispatcher for some build option runs dry it flags starvation; this
service polls those flags once a second and, gated by a re-request timeout,
broadcasts a ``REQUEST_REPOS`` control message to every registered scraper (or a
bare ``scraper_ctrl`` fallback when none has registered). The pending gate is
cleared from ``handle_scrape`` when a bundle actually arrives.
"""

import logging
import threading
import time

from assemblage.constants import COORDINATOR_REPO_REQUEST_TIMEOUT
from assemblage.coordinator.dispatch import StarvationSignals
from assemblage.enums import ScraperMsgType
from assemblage.messages import ScraperControlReply
from assemblage.mq.connection import ConnectionFactory
from assemblage.mq.publisher import Publisher, PublishError
from assemblage.mq.topology import QueueSpec
from assemblage.runtime.service import Service

logger = logging.getLogger(__name__)

_FALLBACK_QUEUE = "scraper_ctrl"


class ScraperRegistry:
    """Reply queues of registered scrapers, targets for REQUEST_REPOS broadcasts."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: list[str] = []

    def register(self, queue_name: str) -> None:
        with self._lock:
            if queue_name not in self._queues:
                self._queues.append(queue_name)
                logger.info("registered scraper reply queue %s", queue_name)

    def targets(self) -> list[str]:
        with self._lock:
            return list(self._queues)


class PendingRepoRequest:
    """Global 'a repo request is outstanding' gate with a re-request timeout."""

    def __init__(self, timeout: int = COORDINATOR_REPO_REQUEST_TIMEOUT) -> None:
        self._lock = threading.Lock()
        self._timeout = timeout
        self._pending = False
        self._requested_at = 0.0

    def clear(self) -> None:
        """A bundle arrived — allow the next starvation to request again."""
        with self._lock:
            self._pending = False

    def claim(self) -> bool:
        """Return True if a request should be sent now (and mark one outstanding)."""
        with self._lock:
            now = time.time()
            if self._pending and self._requested_at + self._timeout >= now:
                return False
            self._pending = True
            self._requested_at = now
            return True


class ScraperRequestService(Service):
    """Polls starvation flags and broadcasts REQUEST_REPOS to registered scrapers."""

    name = "scraper-requests"

    def __init__(
        self,
        factory: ConnectionFactory,
        starvation: StarvationSignals,
        registry: ScraperRegistry,
        pending: PendingRepoRequest,
    ) -> None:
        self._factory = factory
        self._starvation = starvation
        self._registry = registry
        self._pending = pending

    def run(self, stop: threading.Event) -> None:
        publisher = Publisher(self.name, self._factory)
        try:
            while not stop.is_set():
                if self._starvation.take_starving() and self._pending.claim():
                    self._broadcast(publisher)
                stop.wait(1.0)
        finally:
            publisher.close()

    def _broadcast(self, publisher: Publisher) -> None:
        body = ScraperControlReply(
            message_type=ScraperMsgType.REQUEST_REPOS, specific_recipient=False
        ).model_dump_json()
        targets = self._registry.targets() or [_FALLBACK_QUEUE]
        for queue_name in targets:
            try:
                # declare=False: the scraper owns its (non-durable, auto-delete)
                # ctrl queue, so we must not re-declare it with default flags.
                publisher.publish(QueueSpec(queue_name), body, declare=False, persistent=False)
            except PublishError as error:
                logger.warning("could not request repos on %s: %s", queue_name, error)

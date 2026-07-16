"""Scraper composition root: control consumer + crawl service on the substrate.

``ScraperApp`` runs two supervised services against the coordinator:

- a control :class:`ConsumerLoop` on the private ``scraper_ctrl_{uuid}`` queue
  that applies SETUP/UPDATE config and flips the ON_REQUEST bundle gate on
  REQUEST_REPOS (rejecting messages addressed to another scraper by
  correlation id via REQUEUE);
- a :class:`CrawlService` that performs the SETUP handshake (publish
  :class:`ScraperControlRequest` with ``reply_to``/``correlation_id`` until the
  coordinator's config lands), then walks GitHub and bundles repos with the
  frozen policy — CONTINUOUS publishes a bundle every 25 repos; ON_REQUEST fills
  to 25 then waits for a REQUEST_REPOS broadcast, never dropping cached repos —
  emitting an UPDATE sync whenever the crawl window advances.

Replaces the ``BasicWorker``-based ``worker/scraper.py`` and its ``run_ctrl`` /
``run_job`` daemon threads with the graceful-shutdown supervisor model.
"""

import logging
import threading
import uuid

from assemblage.constants import SCRAPER_REPO_BUNDLESIZE
from assemblage.enums import ScraperMsgType, ScraperOutputPolicy
from assemblage.messages import (
    RepoRecord,
    ScrapeBundle,
    ScraperControlReply,
    ScraperControlRequest,
)
from assemblage.mq.connection import ConnectionFactory
from assemblage.mq.consumer import AckDecision, ConsumerLoop, IncomingMessage
from assemblage.mq.publisher import Publisher, PublishError
from assemblage.mq.topology import SCRAPE, SCRAPER_REG, QueueSpec, scraper_ctrl_queue
from assemblage.runtime.service import Service
from assemblage.runtime.supervisor import Supervisor
from assemblage.scraper.github import GitHubClient, GitHubRepoSearch
from assemblage.settings import ScraperSettings

logger = logging.getLogger(__name__)

_ON_REQUEST_POLL_S = 0.1
_SETUP_RETRY_S = 5.0


class CrawlControl:
    """Thread-safe control state shared by the ctrl consumer and crawl service.

    The consumer thread flips ``ready`` / ``bundle_requested`` and updates the
    policy and last-synced crawl time; the crawl thread reads them.
    """

    def __init__(self, *, policy: ScraperOutputPolicy, ready: bool) -> None:
        self.ready = threading.Event()
        if ready:
            self.ready.set()
        self.bundle_requested = threading.Event()
        self._lock = threading.Lock()
        self._policy = policy
        self._last_sent_crawltime: int | None = None

    @property
    def policy(self) -> ScraperOutputPolicy:
        with self._lock:
            return self._policy

    def set_policy(self, policy: ScraperOutputPolicy) -> None:
        with self._lock:
            self._policy = policy

    @property
    def last_sent_crawltime(self) -> int | None:
        with self._lock:
            return self._last_sent_crawltime

    def set_last_sent_crawltime(self, value: int | None) -> None:
        with self._lock:
            self._last_sent_crawltime = value


class RepoBundler:
    """Accumulates scraped repos and publishes them as a bare-array bundle.

    Holds its cache across service restarts (it is owned by the app, not the
    run loop), and only clears the cache after a *confirmed* publish — a publish
    failure raises and the repos are retained rather than dropped.
    """

    def __init__(self, queue: QueueSpec, uuid: str, bundle_size: int) -> None:
        self._queue = queue
        self._uuid = uuid
        self._size = bundle_size
        self._cache: list[RepoRecord] = []
        self._total_sent = 0

    def __len__(self) -> int:
        return len(self._cache)

    def add(self, repo: RepoRecord) -> None:
        # Never guard this behind a condition: losing a repo here loses it forever.
        self._cache.append(repo)

    def full(self) -> bool:
        return len(self._cache) >= self._size

    def flush(self, publisher: Publisher) -> int:
        """Publish the cached repos as one bundle and clear the cache.

        Returns the number sent (0 for an empty cache). Raises
        :class:`PublishError` before clearing if the publish is not confirmed.
        """
        if not self._cache:
            logger.warning("Empty bundle requested")
            return 0
        bundle = ScrapeBundle(self._cache)
        publisher.publish(self._queue, bundle.model_dump_json(), correlation_id=self._uuid)
        count = len(self._cache)
        self._total_sent += count
        logger.info(
            "Scraper %s bundled and sent %s repos to coordinator. Total repos sent: %s",
            self._uuid[:5],
            count,
            self._total_sent,
        )
        self._cache = []
        return count


class CrawlService(Service):
    """Registers via SETUP, then crawls GitHub and bundles repos per policy."""

    name = "scraper-crawl"

    def __init__(
        self,
        factory: ConnectionFactory,
        search: GitHubRepoSearch,
        control: CrawlControl,
        bundler: RepoBundler,
        settings: ScraperSettings,
        uuid: str,
        ctrl_queue_name: str,
    ) -> None:
        self._factory = factory
        self._search = search
        self._control = control
        self._bundler = bundler
        self._settings = settings
        self._uuid = uuid
        self._ctrl_queue_name = ctrl_queue_name

    def run(self, stop: threading.Event) -> None:
        publisher = Publisher(f"scraper-crawl-{self._uuid}", self._factory)
        try:
            self._await_config(publisher, stop)
            if stop.is_set():
                return
            logger.info("Scraper %s started.", self._uuid[:5])
            self._crawl(publisher, stop)
            logger.info("Crawler %s End Task", self._uuid[:5])
        finally:
            publisher.close()

    # --- SETUP handshake ------------------------------------------------------

    def _await_config(self, publisher: Publisher, stop: threading.Event) -> None:
        """Announce readiness and (if configured) wait for the coordinator's config."""
        self._publish_setup(publisher)
        while (
            self._settings.wait_for_config
            and not self._control.ready.is_set()
            and not stop.is_set()
        ):
            if self._control.ready.wait(_SETUP_RETRY_S):
                break
            logger.info("Scraper %s waiting for config...", self._uuid[:5])
            self._publish_setup(publisher)

    def _publish_setup(self, publisher: Publisher) -> None:
        msg = ScraperControlRequest(
            message_type=ScraperMsgType.SETUP,
            start_time=self._search.crawl_time_start,
            end_time=self._search.crawl_time_end,
        )
        try:
            publisher.publish(
                SCRAPER_REG,
                msg.model_dump_json(),
                correlation_id=self._uuid,
                reply_to=self._ctrl_queue_name,
            )
        except PublishError as error:
            logger.warning("could not publish SETUP: %s", error)

    # --- crawl loop -----------------------------------------------------------

    def _crawl(self, publisher: Publisher, stop: threading.Event) -> None:
        for repo in self._search:
            if stop.is_set():
                break
            # Cache first, unconditionally, so no repo is ever lost.
            self._bundler.add(repo)
            self._check_for_update(publisher)

            policy = self._control.policy
            if policy == ScraperOutputPolicy.CONTINUOUS:
                if self._bundler.full():
                    self._bundler.flush(publisher)
            elif policy == ScraperOutputPolicy.ON_REQUEST and self._bundler.full():
                logger.info(
                    "Scraper %s has collected max number of repos (%s). "
                    "Sleeping until request to send is received from coordinator.",
                    self._uuid[:5],
                    len(self._bundler),
                )
                while self._bundler.full() and not stop.is_set():
                    stop.wait(_ON_REQUEST_POLL_S)
                    if self._control.bundle_requested.is_set():
                        self._bundler.flush(publisher)
                        self._control.bundle_requested.clear()

    def _check_for_update(self, publisher: Publisher) -> None:
        """Sync the coordinator's DB when the crawl window advances."""
        current = self._search.current_crawl_time
        if self._control.last_sent_crawltime == current:
            return
        logger.debug("Updating time from %s to %s", self._control.last_sent_crawltime, current)
        msg = ScraperControlRequest(
            message_type=ScraperMsgType.UPDATE,
            start_time=current,
            end_time=self._search.crawl_time_end,
        )
        try:
            publisher.publish(SCRAPER_REG, msg.model_dump_json(), correlation_id=self._uuid)
        except PublishError as error:
            logger.warning("could not publish UPDATE sync: %s", error)
            return
        self._control.set_last_sent_crawltime(current)


class ScraperApp:
    """Builds and runs the scraper's control consumer and crawl service."""

    def __init__(self, settings: ScraperSettings, worker_id: int = 0) -> None:
        self._settings = settings
        self._uuid = str(uuid.uuid1())
        self._factory = ConnectionFactory(settings.mq)
        self._supervisor = Supervisor()

        client = GitHubClient(
            settings.git_token.get_secret_value(),
            alternate_tokens=settings.alternative_git_tokens,
            proxies=list(settings.proxies),
            worker_id=worker_id,
        )
        self._search = GitHubRepoSearch(
            client,
            set(settings.qualifiers),
            settings.default_start_time,
            settings.default_end_time,
            settings.interval,
            worker_id=worker_id,
        )
        self._control = CrawlControl(
            policy=settings.default_policy, ready=not settings.wait_for_config
        )
        self._ctrl_queue = scraper_ctrl_queue(self._uuid)
        self._bundler = RepoBundler(SCRAPE, self._uuid, SCRAPER_REPO_BUNDLESIZE)

    # --- control handler ------------------------------------------------------

    def _on_control(self, incoming: IncomingMessage) -> AckDecision:
        msg = ScraperControlReply.model_validate_json(incoming.body)
        if msg.specific_recipient and incoming.correlation_id != self._uuid:
            return AckDecision.REQUEUE

        if msg.message_type in (ScraperMsgType.SETUP, ScraperMsgType.UPDATE):
            if msg.start_time is not None:
                self._search.crawl_time_start = msg.start_time
                if msg.message_type == ScraperMsgType.SETUP:
                    # init last-sent so a spurious extra sync isn't needed on startup
                    self._control.set_last_sent_crawltime(self._search.crawl_time_start)
            if msg.end_time is not None:
                self._search.crawl_time_end = msg.end_time
            if msg.policy is not None:
                self._control.set_policy(msg.policy)
            if msg.qualifiers is not None:
                self._search.qualifiers = set(msg.qualifiers)
            self._control.ready.set()
            if msg.message_type == ScraperMsgType.SETUP:
                logger.info("Scraper %s configured by coordinator", self._uuid[:5])
        elif msg.message_type == ScraperMsgType.REQUEST_REPOS:
            self._control.bundle_requested.set()
            logger.debug("Repo bundle request accepted by scraper %s", self._uuid[:5])

        return AckDecision.ACK

    # --- startup --------------------------------------------------------------

    def _declare_ctrl(self) -> None:
        """Declare the private ctrl queue before the coordinator can reply to it."""
        connection = self._factory.open(name=f"scraper-declare-{self._uuid}")
        try:
            channel = connection.channel()
            channel.queue_declare(
                queue=self._ctrl_queue.name,
                durable=self._ctrl_queue.durable,
                auto_delete=self._ctrl_queue.auto_delete,
            )
        finally:
            connection.close()

    def run(self) -> int:
        logger.info("scraper %s starting", self._uuid)
        self._declare_ctrl()
        control = ConsumerLoop("scraper-ctrl", self._factory, self._ctrl_queue, self._on_control)
        crawl = CrawlService(
            self._factory,
            self._search,
            self._control,
            self._bundler,
            self._settings,
            self._uuid,
            self._ctrl_queue.name,
        )
        self._supervisor.add(control)
        self._supervisor.add(crawl)
        logger.info("scraper %s running", self._uuid)
        return self._supervisor.run_until_signal()


def main() -> int:
    settings = ScraperSettings()
    logging.basicConfig(
        format="%(asctime)s %(levelname)s:%(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=settings.log_level,
    )
    return ScraperApp(settings).run()

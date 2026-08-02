"""Coordinator composition root.

Wires the store, the seven inbound :class:`ConsumerLoop`s, the dispatch manager
and the scraper-request service under one :class:`Supervisor`, then blocks in
``run_until_signal`` for a graceful SIGTERM shutdown. Replaces the 827-line
``coordinator.py`` God class and its stop-the-world excepthook / ``os._exit``.
"""

import logging
import time

from assemblage.blocklist import FileBlocklist
from assemblage.coordinator.dispatch import DispatchManager, StarvationSignals
from assemblage.coordinator.ingest import (
    BuildStats,
    handle_binary,
    handle_build_status,
    handle_clone_status,
    handle_ir,
    handle_scrape,
)
from assemblage.coordinator.registration import (
    handle_builder_registration,
    handle_scraper_registration,
)
from assemblage.coordinator.scraper_requests import (
    PendingRepoRequest,
    ScraperRegistry,
    ScraperRequestService,
)
from assemblage.db.bootstrap import conditional_init_db
from assemblage.db.engine import make_engine
from assemblage.db.store import CoordinatorStore
from assemblage.messages import (
    BinaryRecordMsg,
    BuilderRegistered,
    BuilderRegistration,
    BuildStatusMsg,
    CloneStatusMsg,
    IrRecordMsg,
    ScrapeBundle,
    ScraperControlReply,
    ScraperControlRequest,
)
from assemblage.mq.connection import ConnectionFactory
from assemblage.mq.consumer import AckDecision, ConsumerLoop, IncomingMessage
from assemblage.mq.publisher import Publisher
from assemblage.mq.topology import (
    BINARY,
    BUILD,
    BUILD_OPT_EXCHANGE,
    BUILDER_REG,
    CLONE,
    IR,
    SCRAPE,
    SCRAPER_REG,
    QueueSpec,
)
from assemblage.runtime.supervisor import Supervisor
from assemblage.settings import CoordinatorSettings

logger = logging.getLogger(__name__)

_SCHEMA_RETRY_S = 10
_BUILDER_CTRL_FALLBACK = "builder_ctrl"
_SCRAPER_CTRL_FALLBACK = "scraper_ctrl"


class CoordinatorApp:
    """Builds and runs the coordinator's services."""

    def __init__(self, settings: CoordinatorSettings) -> None:
        self._settings = settings
        self._blocklist = FileBlocklist(settings.blocklist_path)
        self._store = CoordinatorStore(make_engine(settings.db.url), self._blocklist.current)
        self._factory = ConnectionFactory(settings.mq)
        self._supervisor = Supervisor()
        self._starvation = StarvationSignals()
        self._registry = ScraperRegistry()
        self._pending = PendingRepoRequest()
        self._stats = BuildStats()
        self._dispatch = DispatchManager(
            self._factory, self._store, self._starvation, self._supervisor
        )

    # --- startup --------------------------------------------------------------

    def _ensure_schema(self) -> None:
        while not self._store.tables_exist():
            try:
                conditional_init_db(self._settings.db)
            except Exception:
                logger.warning(
                    "database has no tables and auto-init failed; retrying. "
                    "If this persists, run `alembic upgrade head` in the coordinator.",
                    exc_info=True,
                )
            if self._store.tables_exist():
                break
            time.sleep(_SCHEMA_RETRY_S)

    def _ensure_exchange(self) -> None:
        publisher = Publisher("coordinator-exchange-setup", self._factory)
        try:
            publisher.ensure_exchange(BUILD_OPT_EXCHANGE, topic=True)
        finally:
            publisher.close()

    # --- reply helper ---------------------------------------------------------

    def _send_reply(self, incoming: IncomingMessage, body: str, fallback: str) -> None:
        """Publish a reply on the caller's private queue (props.reply_to).

        A fresh publisher per reply keeps every publish confined to the consumer
        thread that produced it — registration is rare, so the cost is moot.
        """
        queue = QueueSpec(incoming.reply_to or fallback)
        publisher = Publisher(f"coordinator-reply-{queue.name}", self._factory)
        try:
            publisher.publish(
                queue,
                body,
                correlation_id=incoming.correlation_id,
                reply_to=incoming.reply_to,
                declare=False,
            )
        finally:
            publisher.close()

    # --- consumer handlers ----------------------------------------------------

    def _on_scrape(self, incoming: IncomingMessage) -> AckDecision:
        bundle = ScrapeBundle.model_validate_json(incoming.body)
        return handle_scrape(self._store, bundle, self._pending.clear)

    def _on_clone(self, incoming: IncomingMessage) -> AckDecision:
        return handle_clone_status(self._store, CloneStatusMsg.model_validate_json(incoming.body))

    def _on_build(self, incoming: IncomingMessage) -> AckDecision:
        return handle_build_status(
            self._store, BuildStatusMsg.model_validate_json(incoming.body), self._stats
        )

    def _on_binary(self, incoming: IncomingMessage) -> AckDecision:
        return handle_binary(self._store, BinaryRecordMsg.model_validate_json(incoming.body))

    def _on_ir(self, incoming: IncomingMessage) -> AckDecision:
        return handle_ir(self._store, IrRecordMsg.model_validate_json(incoming.body))

    def _on_builder_reg(self, incoming: IncomingMessage) -> AckDecision:
        reg = BuilderRegistration.model_validate_json(incoming.body)

        def reply(message: BuilderRegistered) -> None:
            self._send_reply(incoming, message.model_dump_json(), _BUILDER_CTRL_FALLBACK)

        return handle_builder_registration(self._store, reg, reply, self._dispatch.ensure_started)

    def _on_scraper_reg(self, incoming: IncomingMessage) -> AckDecision:
        request = ScraperControlRequest.model_validate_json(incoming.body)
        reply_queue = incoming.reply_to or _SCRAPER_CTRL_FALLBACK

        def reply(message: ScraperControlReply) -> None:
            self._send_reply(incoming, message.model_dump_json(), _SCRAPER_CTRL_FALLBACK)

        def on_setup() -> None:
            self._registry.register(reply_queue)

        return handle_scraper_registration(
            self._store, request, reply, on_setup, incoming.correlation_id
        )

    # --- run ------------------------------------------------------------------

    def _build_consumers(self) -> list[ConsumerLoop]:
        specs = [
            ("coordinator-scrape", SCRAPE, self._on_scrape),
            ("coordinator-clone", CLONE, self._on_clone),
            ("coordinator-build", BUILD, self._on_build),
            ("coordinator-binary", BINARY, self._on_binary),
            ("coordinator-ir", IR, self._on_ir),
            ("coordinator-builder-reg", BUILDER_REG, self._on_builder_reg),
            ("coordinator-scraper-reg", SCRAPER_REG, self._on_scraper_reg),
        ]
        return [ConsumerLoop(name, self._factory, queue, handler) for name, queue, handler in specs]

    def run(self) -> int:
        """Bring up the schema, register services, and run until signalled."""
        logger.info("coordinator starting")
        self._ensure_schema()
        self._store.ready_scraper_table()
        self._ensure_exchange()

        for consumer in self._build_consumers():
            self._supervisor.add(consumer)
        self._supervisor.add(
            ScraperRequestService(self._factory, self._starvation, self._registry, self._pending)
        )

        logger.info("coordinator running")
        try:
            return self._supervisor.run_until_signal()
        finally:
            self._store.shutdown()


def main() -> int:
    settings = CoordinatorSettings()
    logging.basicConfig(
        format="%(asctime)s %(levelname)s:%(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=settings.log_level,
    )
    return CoordinatorApp(settings).run()

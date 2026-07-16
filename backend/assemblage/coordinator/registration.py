"""Builder and scraper registration handshakes (pure handlers).

The builder handshake is where the historical double-ack bug is dead *by
construction*: the ConsumerLoop performs the single ack, so this handler only
registers the build option, sends the reply on the caller's private reply queue
(``props.reply_to`` with ``props.correlation_id``), and asks the dispatch
manager to start the opt's dispatcher.
"""

import logging
from collections.abc import Callable

from assemblage.db.store import CoordinatorStore
from assemblage.enums import ScraperMsgType
from assemblage.messages import (
    BuilderRegistered,
    BuilderRegistration,
    ScraperControlReply,
    ScraperControlRequest,
)
from assemblage.mq.consumer import AckDecision

logger = logging.getLogger(__name__)


def handle_builder_registration(
    store: CoordinatorStore,
    msg: BuilderRegistration,
    reply: Callable[[BuilderRegistered], None],
    ensure_dispatcher: Callable[[int], None],
) -> AckDecision:
    """Register (or re-enable) the builder's build option, reply, start dispatch."""
    logger.info(
        "builder %s registering: %s %s on %s (%s)",
        msg.name,
        msg.compiler,
        msg.compiler_flag,
        msg.platform,
        msg.language,
    )
    opt_id = store.register_build_opt(msg)
    reply(BuilderRegistered(build_opt_id=opt_id))
    ensure_dispatcher(opt_id)
    return AckDecision.ACK


def handle_scraper_registration(
    store: CoordinatorStore,
    msg: ScraperControlRequest,
    reply: Callable[[ScraperControlReply], None],
    on_setup: Callable[[], None],
    correlation_id: str | None,
) -> AckDecision:
    """SETUP: claim/create a scrapers row and reply with its window; UPDATE: advance it."""
    uuid = correlation_id or ""
    if msg.message_type == ScraperMsgType.SETUP:
        config = store.register_scraper(uuid, msg.start_time, msg.end_time)
        reply(
            ScraperControlReply(
                message_type=ScraperMsgType.SETUP,
                start_time=config["start_time"],
                end_time=config["end_time"],
            )
        )
        on_setup()
    else:
        store.update_scraper(uuid, msg.start_time, msg.end_time)
        reply(ScraperControlReply(message_type=ScraperMsgType.UPDATE))
    return AckDecision.ACK

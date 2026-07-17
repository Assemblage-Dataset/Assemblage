"""RabbitMQ topology: the frozen queue/exchange names as typed specs.

These are the wire-level queue names the whole system agrees on (see
``tests/fixtures/messages/README.md``). Declaring a queue from a :class:`QueueSpec`
always uses the same durability/exchange flags, so producers and consumers can
never disagree about a queue's shape.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class QueueSpec:
    """A declarable queue (and its optional exchange binding)."""

    name: str
    durable: bool = True
    exclusive: bool = False
    auto_delete: bool = False
    exchange: str = ""
    routing_key: str | None = None

    @property
    def routing(self) -> str:
        """Routing key to publish/bind with (defaults to the queue name)."""
        return self.routing_key if self.routing_key is not None else self.name


# Durable work queues consumed by the coordinator (direction: worker -> coordinator).
SCRAPE = QueueSpec("scrape")
CLONE = QueueSpec("clone")
BUILD = QueueSpec("build")
BINARY = QueueSpec("binary")
BUILDER_REG = QueueSpec("builder_reg")
SCRAPER_REG = QueueSpec("scraper_reg")
# Added 2026-07-17 with Rust IR dumping. A NEW queue rather than new keys on the
# frozen `binary` message: the six names above are frozen, and this one is additive
# -- a coordinator that never declares it is simply a coordinator without IR.
IR = QueueSpec("ir")

# Topic exchange fanning build tasks out to per-buildopt queues.
BUILD_OPT_EXCHANGE = "build_opt"


def build_opt_queue(opt_id: int) -> QueueSpec:
    """The durable queue a builder for build option ``opt_id`` consumes from."""
    name = f"build_opt_{opt_id}"
    return QueueSpec(name, exchange=BUILD_OPT_EXCHANGE, routing_key=name)


def builder_ctrl_queue(uuid: str) -> QueueSpec:
    """A builder's private control/reply queue (non-durable, auto-delete)."""
    return QueueSpec(f"builder_ctrl_{uuid}", durable=False, auto_delete=True)


def scraper_ctrl_queue(uuid: str) -> QueueSpec:
    """A scraper's private control/reply queue (non-durable, auto-delete)."""
    return QueueSpec(f"scraper_ctrl_{uuid}", durable=False, auto_delete=True)

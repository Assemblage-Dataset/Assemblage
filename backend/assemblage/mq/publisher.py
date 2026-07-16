"""A thread-confined publisher with delivery confirmation that RAISES on loss.

The old ``send_msg`` swallowed every exception, so the dispatcher could mark a
task ``PROCESSING`` even when the publish never reached the broker. This
publisher turns on publisher confirms, retries connection-level failures with
backoff, and raises :class:`PublishError` when it finally cannot confirm a
delivery — the caller only proceeds after a confirmed publish.

Not thread-safe: one :class:`Publisher` per thread (pika's constraint).
"""

import logging
import threading

import pika
import pika.exceptions
from pika.adapters.blocking_connection import BlockingChannel, BlockingConnection
from pika.exchange_type import ExchangeType
from pika.spec import PERSISTENT_DELIVERY_MODE, TRANSIENT_DELIVERY_MODE

from assemblage.mq.connection import ConnectionFactory, MQConnectionError
from assemblage.mq.topology import QueueSpec
from assemblage.runtime.service import Backoff

logger = logging.getLogger(__name__)

_RETRYABLE = (
    pika.exceptions.AMQPConnectionError,
    pika.exceptions.StreamLostError,
    pika.exceptions.ChannelWrongStateError,
    pika.exceptions.ConnectionClosed,
    MQConnectionError,
)


_DEFAULT_PUBLISH_BACKOFF = Backoff(0.5, 10.0)


class PublishError(RuntimeError):
    """Raised when a message could not be confirmed delivered."""


class Publisher:
    def __init__(
        self,
        name: str,
        factory: ConnectionFactory,
        retry: Backoff = _DEFAULT_PUBLISH_BACKOFF,
        max_attempts: int = 5,
    ) -> None:
        self.name = name
        self._factory = factory
        self._retry = retry
        self._max_attempts = max_attempts
        self._connection: BlockingConnection | None = None
        self._channel: BlockingChannel | None = None

    def _channel_ready(self) -> BlockingChannel:
        if (
            self._connection is not None
            and self._connection.is_open
            and self._channel is not None
            and self._channel.is_open
        ):
            return self._channel
        self._close_quietly()
        connection = self._factory.open(name=self.name)
        channel = connection.channel()
        channel.confirm_delivery()
        self._connection = connection
        self._channel = channel
        return channel

    def ensure_exchange(self, name: str, topic: bool = True) -> None:
        channel = self._channel_ready()
        channel.exchange_declare(
            exchange=name,
            exchange_type=ExchangeType.topic if topic else ExchangeType.direct,
        )

    def publish(
        self,
        queue: QueueSpec,
        body: str | bytes,
        *,
        correlation_id: str | None = None,
        reply_to: str | None = None,
        declare: bool = True,
        persistent: bool = True,
    ) -> None:
        payload = body.encode() if isinstance(body, str) else body
        properties = pika.BasicProperties(
            delivery_mode=PERSISTENT_DELIVERY_MODE if persistent else TRANSIENT_DELIVERY_MODE,
            correlation_id=correlation_id,
            reply_to=reply_to,
        )
        attempt = 0
        last_error: Exception | None = None
        while attempt < self._max_attempts:
            try:
                channel = self._channel_ready()
                if declare:
                    self._declare(channel, queue)
                channel.basic_publish(
                    exchange=queue.exchange,
                    routing_key=queue.routing,
                    body=payload,
                    properties=properties,
                    mandatory=True,
                )
                return
            except pika.exceptions.UnroutableError as error:
                raise PublishError(
                    f"message to {queue.name!r} was unroutable (no bound queue)"
                ) from error
            except pika.exceptions.NackError as error:
                raise PublishError(f"broker nacked message to {queue.name!r}") from error
            except _RETRYABLE as error:
                last_error = error
                self._close_quietly()
                delay = self._retry.delay(attempt)
                attempt += 1
                logger.warning(
                    "publish to %r failed (attempt %d), retrying in %.2fs: %s",
                    queue.name,
                    attempt,
                    delay,
                    error,
                )
                threading.Event().wait(delay)
        raise PublishError(
            f"failed to publish to {queue.name!r} after {self._max_attempts} attempts"
        ) from last_error

    @staticmethod
    def _declare(channel: BlockingChannel, queue: QueueSpec) -> None:
        if queue.exchange:
            channel.exchange_declare(exchange=queue.exchange, exchange_type=ExchangeType.topic)
        channel.queue_declare(
            queue=queue.name,
            durable=queue.durable,
            exclusive=queue.exclusive,
            auto_delete=queue.auto_delete,
        )
        if queue.exchange:
            channel.queue_bind(queue.name, queue.exchange, queue.routing)

    def queue_depth(self, queue: QueueSpec) -> int:
        """Message count for ``queue`` via a passive declare (0 if absent)."""
        try:
            channel = self._channel_ready()
            result = channel.queue_declare(queue=queue.name, passive=True)
            return int(result.method.message_count)
        except pika.exceptions.ChannelClosedByBroker:
            self._close_quietly()
            return 0

    def _close_quietly(self) -> None:
        connection = self._connection
        self._connection = None
        self._channel = None
        if connection is None:
            return
        try:
            if connection.is_open:
                connection.close()
        except Exception:
            logger.debug("error closing publisher connection %r", self.name, exc_info=True)

    def close(self) -> None:
        self._close_quietly()

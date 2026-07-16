"""A supervised consumer loop that performs exactly one ack/nack per delivery.

``ConsumerLoop`` owns its own connection (pika is not thread-safe), declares
its queue, and dispatches each delivery to a handler that returns an
:class:`AckDecision`. The loop — never the handler — acks or nacks, so the
double-ack / ack-after-nack class of bug is structurally impossible.

``ack_early=True`` preserves the builder task queue's load-bearing
at-most-once semantics (ack before doing the work); everywhere else a handler
exception requeues the message (today's behaviour).
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

import pika.exceptions
from pika.adapters.blocking_connection import BlockingChannel, BlockingConnection
from pika.exchange_type import ExchangeType

from assemblage.mq.connection import ConnectionFactory, MQConnectionError
from assemblage.mq.topology import QueueSpec
from assemblage.runtime.service import Backoff, Service

logger = logging.getLogger(__name__)

_DEFAULT_RECONNECT_BACKOFF = Backoff()


class AckDecision(Enum):
    ACK = auto()
    REQUEUE = auto()


@dataclass
class IncomingMessage:
    body: bytes
    correlation_id: str | None
    reply_to: str | None
    redelivered: bool
    routing_key: str


Handler = Callable[[IncomingMessage], AckDecision]


class ConsumerLoop(Service):
    def __init__(
        self,
        name: str,
        factory: ConnectionFactory,
        queue: QueueSpec,
        handler: Handler,
        *,
        prefetch: int = 1,
        ack_early: bool = False,
        declare: bool = True,
        reconnect: Backoff = _DEFAULT_RECONNECT_BACKOFF,
    ) -> None:
        self.name = name
        self._factory = factory
        self._queue = queue
        self._handler = handler
        self._prefetch = prefetch
        self._ack_early = ack_early
        self._declare = declare
        self._reconnect = reconnect
        self._lock = threading.Lock()
        self._connection: BlockingConnection | None = None
        self._channel: BlockingChannel | None = None

    def run(self, stop: threading.Event) -> None:
        attempt = 0
        while not stop.is_set():
            try:
                self._consume(stop)
                attempt = 0
            except (pika.exceptions.AMQPError, MQConnectionError) as error:
                if stop.is_set():
                    break
                delay = self._reconnect.delay(attempt)
                attempt += 1
                logger.warning(
                    "consumer %r lost connection, reconnecting in %.2fs: %s",
                    self.name,
                    delay,
                    error,
                )
                stop.wait(delay)
            finally:
                self._teardown()

    def _consume(self, stop: threading.Event) -> None:
        connection = self._factory.open(name=self.name, stop=stop)
        channel = connection.channel()
        with self._lock:
            self._connection = connection
            self._channel = channel
        channel.basic_qos(prefetch_count=self._prefetch)
        if self._declare:
            if self._queue.exchange:
                channel.exchange_declare(
                    exchange=self._queue.exchange, exchange_type=ExchangeType.topic
                )
            channel.queue_declare(
                queue=self._queue.name,
                durable=self._queue.durable,
                exclusive=self._queue.exclusive,
                auto_delete=self._queue.auto_delete,
            )
            if self._queue.exchange:
                channel.queue_bind(self._queue.name, self._queue.exchange, self._queue.routing)
        channel.basic_consume(queue=self._queue.name, on_message_callback=self._on_message)
        channel.start_consuming()

    def _on_message(
        self,
        channel: BlockingChannel,
        method: Any,
        properties: Any,
        body: bytes,
    ) -> None:
        message = IncomingMessage(
            body=body,
            correlation_id=properties.correlation_id,
            reply_to=properties.reply_to,
            redelivered=method.redelivered,
            routing_key=method.routing_key,
        )
        tag = method.delivery_tag
        if self._ack_early:
            channel.basic_ack(tag)
            try:
                self._handler(message)
            except Exception:
                logger.exception("handler failed after early ack on %r", self.name)
            return
        try:
            decision = self._handler(message)
        except Exception:
            logger.exception("handler raised on %r, requeueing delivery", self.name)
            decision = AckDecision.REQUEUE
        if decision is AckDecision.ACK:
            channel.basic_ack(tag)
        else:
            channel.basic_nack(tag, requeue=True)

    def request_stop(self) -> None:
        with self._lock:
            connection = self._connection
            channel = self._channel
        if connection is None or channel is None:
            return
        try:
            connection.add_callback_threadsafe(channel.stop_consuming)
        except Exception:
            logger.debug("could not schedule stop_consuming on %r", self.name, exc_info=True)

    def _teardown(self) -> None:
        with self._lock:
            connection = self._connection
            self._connection = None
            self._channel = None
        if connection is None:
            return
        try:
            if connection.is_open:
                connection.close()
        except Exception:
            logger.debug("error closing connection on %r", self.name, exc_info=True)

"""Blocking-connection factory with real backoff-retry.

Replaces ``mq/client.py``'s broken ``connect()`` (which retried at most once
and then raised ``AttributeError`` on ``self.host``). Each thread that needs a
connection asks the factory to :meth:`open` one; the factory retries with
jittered backoff until it connects or the caller's ``stop`` event is set.
"""

import logging
import threading

import pika
import pika.exceptions
from pika.adapters.blocking_connection import BlockingConnection

from assemblage.constants import CHANNEL_HEARTBEAT, CHANNEL_TIMEOUT
from assemblage.runtime.service import Backoff
from assemblage.settings import MQSettings

logger = logging.getLogger(__name__)

_DEFAULT_CONNECT_BACKOFF = Backoff(1.0, 60.0)


class MQConnectionError(RuntimeError):
    """Raised when a connection cannot be established (or stop was requested)."""


class ConnectionFactory:
    """Opens RabbitMQ ``BlockingConnection``s from :class:`MQSettings`."""

    def __init__(self, settings: MQSettings) -> None:
        self._settings = settings

    @property
    def endpoint(self) -> str:
        return f"{self._settings.host}:{self._settings.port}"

    def _parameters(self, name: str) -> pika.ConnectionParameters:
        credentials = pika.PlainCredentials(
            self._settings.user, self._settings.password.get_secret_value()
        )
        return pika.ConnectionParameters(
            host=self._settings.host,
            port=self._settings.port,
            credentials=credentials,
            heartbeat=CHANNEL_HEARTBEAT,
            blocked_connection_timeout=CHANNEL_TIMEOUT,
            client_properties={"connection_name": name},
        )

    def open(
        self,
        *,
        name: str,
        stop: threading.Event | None = None,
        backoff: Backoff = _DEFAULT_CONNECT_BACKOFF,
    ) -> BlockingConnection:
        """Connect, retrying with backoff until connected or ``stop`` is set."""
        attempt = 0
        last_error: Exception | None = None
        while stop is None or not stop.is_set():
            try:
                connection = pika.BlockingConnection(self._parameters(name))
                logger.debug("connection %r established to %s", name, self.endpoint)
                return connection
            except pika.exceptions.AMQPConnectionError as error:
                last_error = error
                delay = backoff.delay(attempt)
                attempt += 1
                logger.warning(
                    "connection %r to %s failed (attempt %d), retrying in %.2fs: %s",
                    name,
                    self.endpoint,
                    attempt,
                    delay,
                    error,
                )
                if stop is not None and stop.wait(delay):
                    break
                if stop is None:
                    threading.Event().wait(delay)
        raise MQConnectionError(
            f"could not connect {name!r} to RabbitMQ at {self.endpoint}"
        ) from last_error

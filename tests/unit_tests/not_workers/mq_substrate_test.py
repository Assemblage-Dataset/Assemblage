"""Unit tests for the MQ substrate: topology, ConsumerLoop ack semantics,
Publisher raise-on-failure and queue_depth. Uses fake pika channel/connection
objects (no broker)."""

import unittest

import pika.exceptions
from assemblage.mq import topology
from assemblage.mq.consumer import AckDecision, ConsumerLoop, IncomingMessage
from assemblage.mq.publisher import Publisher, PublishError
from assemblage.mq.topology import QueueSpec
from assemblage.runtime.service import Backoff


class _FakeMethod:
    def __init__(self, tag=1, redelivered=False, routing_key="q"):
        self.delivery_tag = tag
        self.redelivered = redelivered
        self.routing_key = routing_key


class _FakeProps:
    def __init__(self, correlation_id=None, reply_to=None):
        self.correlation_id = correlation_id
        self.reply_to = reply_to


class _RecordingChannel:
    def __init__(self, events=None):
        self.events = events if events is not None else []
        self.acks = []
        self.nacks = []

    def basic_ack(self, delivery_tag):
        self.events.append("ack")
        self.acks.append(delivery_tag)

    def basic_nack(self, delivery_tag, requeue):
        self.events.append("nack")
        self.nacks.append((delivery_tag, requeue))


def _loop(handler, *, ack_early=False):
    return ConsumerLoop(
        "test",
        factory=None,  # never used: we drive _on_message directly
        queue=topology.CLONE,
        handler=handler,
        ack_early=ack_early,
    )


class TestConsumerLoop(unittest.TestCase):
    def test_ack_decision_acks_exactly_once(self):
        loop = _loop(lambda msg: AckDecision.ACK)
        channel = _RecordingChannel()
        loop._on_message(channel, _FakeMethod(tag=5), _FakeProps(), b"body")
        self.assertEqual(channel.acks, [5])
        self.assertEqual(channel.nacks, [])

    def test_requeue_decision_nacks_exactly_once(self):
        loop = _loop(lambda msg: AckDecision.REQUEUE)
        channel = _RecordingChannel()
        loop._on_message(channel, _FakeMethod(tag=6), _FakeProps(), b"body")
        self.assertEqual(channel.acks, [])
        self.assertEqual(channel.nacks, [(6, True)])

    def test_handler_exception_requeues(self):
        def boom(msg):
            raise ValueError("handler failed")

        loop = _loop(boom)
        channel = _RecordingChannel()
        loop._on_message(channel, _FakeMethod(tag=7), _FakeProps(), b"body")
        self.assertEqual(channel.acks, [])
        self.assertEqual(channel.nacks, [(7, True)])

    def test_ack_early_acks_before_handler(self):
        events: list[str] = []

        def handler(msg):
            events.append("handler")
            return AckDecision.ACK

        loop = _loop(handler, ack_early=True)
        channel = _RecordingChannel(events)
        loop._on_message(channel, _FakeMethod(), _FakeProps(), b"body")
        self.assertEqual(events, ["ack", "handler"])
        self.assertEqual(channel.nacks, [])

    def test_ack_early_still_single_ack_on_handler_exception(self):
        def boom(msg):
            raise RuntimeError("after ack")

        loop = _loop(boom, ack_early=True)
        channel = _RecordingChannel()
        loop._on_message(channel, _FakeMethod(tag=9), _FakeProps(), b"body")
        self.assertEqual(channel.acks, [9])
        self.assertEqual(channel.nacks, [])

    def test_incoming_message_carries_delivery_metadata(self):
        captured: list[IncomingMessage] = []
        loop = _loop(lambda msg: captured.append(msg) or AckDecision.ACK)
        method = _FakeMethod(redelivered=True, routing_key="build_opt_7")
        props = _FakeProps(correlation_id="cid", reply_to="reply-q")
        loop._on_message(_RecordingChannel(), method, props, b"payload")
        message = captured[0]
        self.assertEqual(message.body, b"payload")
        self.assertEqual(message.correlation_id, "cid")
        self.assertEqual(message.reply_to, "reply-q")
        self.assertTrue(message.redelivered)
        self.assertEqual(message.routing_key, "build_opt_7")


class _PublishChannel:
    def __init__(self, *, publish_error=None, depth=0, passive_raises=False):
        self.is_open = True
        self._publish_error = publish_error
        self._depth = depth
        self._passive_raises = passive_raises
        self.published = []

    def confirm_delivery(self):
        pass

    def exchange_declare(self, **kwargs):
        pass

    def queue_declare(self, queue, passive=False, **kwargs):
        if passive:
            if self._passive_raises:
                raise pika.exceptions.ChannelClosedByBroker(404, "not found")
            method = type("M", (), {"message_count": self._depth})()
            return type("R", (), {"method": method})()
        return None

    def queue_bind(self, *args, **kwargs):
        pass

    def basic_publish(self, **kwargs):
        if self._publish_error is not None:
            raise self._publish_error
        self.published.append(kwargs)


class _PublishConnection:
    def __init__(self, channel):
        self.is_open = True
        self._channel = channel

    def channel(self):
        return self._channel

    def close(self):
        self.is_open = False


class _FakeFactory:
    def __init__(self, channel):
        self._channel = channel
        self.opens = 0

    def open(self, *, name, stop=None, backoff=None):
        self.opens += 1
        return _PublishConnection(self._channel)


_NO_WAIT = Backoff(0.0, 0.0, jitter=0.0)


class TestPublisher(unittest.TestCase):
    def test_publish_success(self):
        channel = _PublishChannel()
        publisher = Publisher("p", _FakeFactory(channel), retry=_NO_WAIT, max_attempts=3)
        publisher.publish(topology.CLONE, b"hello")
        self.assertEqual(len(channel.published), 1)
        self.assertEqual(channel.published[0]["routing_key"], "clone")
        self.assertTrue(channel.published[0]["mandatory"])

    def test_publish_raises_after_retries(self):
        channel = _PublishChannel(publish_error=pika.exceptions.AMQPConnectionError("down"))
        factory = _FakeFactory(channel)
        publisher = Publisher("p", factory, retry=_NO_WAIT, max_attempts=3)
        with self.assertRaises(PublishError):
            publisher.publish(topology.CLONE, b"hello")
        self.assertEqual(factory.opens, 3)

    def test_publish_raises_on_unroutable(self):
        channel = _PublishChannel(publish_error=pika.exceptions.UnroutableError([]))
        publisher = Publisher("p", _FakeFactory(channel), retry=_NO_WAIT, max_attempts=3)
        with self.assertRaises(PublishError):
            publisher.publish(topology.CLONE, b"hello")

    def test_queue_depth(self):
        channel = _PublishChannel(depth=42)
        publisher = Publisher("p", _FakeFactory(channel), retry=_NO_WAIT)
        self.assertEqual(publisher.queue_depth(topology.CLONE), 42)

    def test_queue_depth_absent_queue_is_zero(self):
        channel = _PublishChannel(passive_raises=True)
        publisher = Publisher("p", _FakeFactory(channel), retry=_NO_WAIT)
        self.assertEqual(publisher.queue_depth(topology.CLONE), 0)


class TestTopology(unittest.TestCase):
    def test_frozen_queue_names(self):
        self.assertEqual(topology.SCRAPE.name, "scrape")
        self.assertEqual(topology.CLONE.name, "clone")
        self.assertEqual(topology.BUILD.name, "build")
        self.assertEqual(topology.BINARY.name, "binary")
        self.assertEqual(topology.BUILDER_REG.name, "builder_reg")
        self.assertEqual(topology.SCRAPER_REG.name, "scraper_reg")
        self.assertEqual(topology.BUILD_OPT_EXCHANGE, "build_opt")

    def test_build_opt_queue_binds_topic_exchange(self):
        spec = topology.build_opt_queue(7)
        self.assertEqual(spec.name, "build_opt_7")
        self.assertEqual(spec.exchange, "build_opt")
        self.assertEqual(spec.routing, "build_opt_7")
        self.assertTrue(spec.durable)

    def test_ctrl_queues_are_non_durable_auto_delete(self):
        for spec in (
            topology.builder_ctrl_queue("abc"),
            topology.scraper_ctrl_queue("def"),
        ):
            self.assertFalse(spec.durable)
            self.assertTrue(spec.auto_delete)

    def test_routing_defaults_to_name(self):
        self.assertEqual(QueueSpec("q").routing, "q")


if __name__ == "__main__":
    unittest.main()

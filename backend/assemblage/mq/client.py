'''
message queue client for worker
Alex Duly
'''

from dataclasses import dataclass
import logging
import time
from typing import Callable
import pika
from pika.adapters.blocking_connection import BlockingChannel, BlockingConnection
import pika.exceptions
from pika.exchange_type import ExchangeType
from pika.spec import PERSISTENT_DELIVERY_MODE
from assemblage.consts import (CHANNEL_HEARTBEAT, CHANNEL_TIMEOUT,
                               CHANNEL_CONNECTION_ATTEMPTS, CHANNEL_RETRY_DELAY, InputQueue, OutputQueue)


# this reduces a lot of errors
logger = logging.getLogger(__name__)


@dataclass
class MQQueue:

    name: InputQueue | OutputQueue
    callback: Callable | None = None
    exchange_name: str | None = None
    routing_key: str | None = None
    durable: bool = True
    exclusive: bool = False
    auto_delete: bool = False
    # def __str__(self) -> str:
    #     return self.name

    def __post_init__(self):
        # If no routing_key specified, use queue name
        if self.routing_key is None:
            self.routing_key = self.name

        # If no exchange specified, use default (direct routing)
        if self.exchange_name is None:
            self.exchange_name = ""


class Connection:
    '''
    Wrapper for individual connection channel
    Pika is not thread safe so require 1 connection/channel per thread

    Multiple queues per channel/connections

    '''

    def __init__(self, mq_host: str, mq_port: int, conn_name: str,
                 channel_name: str,
                 heartbeat: int = CHANNEL_HEARTBEAT, timeout: int = CHANNEL_TIMEOUT,
                 connection_attempts: int = CHANNEL_CONNECTION_ATTEMPTS,
                 retry_delay: int = CHANNEL_RETRY_DELAY,
                 username: str = "guest",
                 password: str = "guest",
                 ):
        self.mq_host = mq_host
        self.mq_port = mq_port
        self.heartbeat = heartbeat
        self.timeout = timeout
        self.connection_attempts = connection_attempts
        self.retry_delay = retry_delay
        self.username = username
        self.password = password
        self.conn_name = conn_name
        self.exchange_name = None 
        self.chan_name = channel_name
        self.conn: BlockingConnection | None = None
        self.chan: BlockingChannel | None = None  #  actually stores the MQ shcnanel
        # is this connection producing or consuming ( could be useful to differentiate)
        self.queues: dict[str, MQQueue] = {}

    def __str__(self):
        # channel/connection named the same typically
        return f"Connection: {self.conn_name}"

    def connect(self, auto_retry: bool = True, retry_attempts: int | None = 10):

        if self.conn and self.conn.is_open:
            logger.debug(f"{self} already connected")
            return self.conn

        credentials = pika.PlainCredentials(self.username, self.password)
        conn_params = pika.ConnectionParameters(
            host=self.mq_host, port=self.mq_port,
            connection_attempts=self.connection_attempts, retry_delay=self.retry_delay,
            heartbeat=self.heartbeat, blocked_connection_timeout=self.timeout, credentials=credentials)
        attempt = 0
        while auto_retry:
            try:
                self.conn = BlockingConnection(conn_params)
                if self.conn.is_open:
                    logger.debug(f"{self} now open ")
                    return self.conn
            except pika.exceptions.AMQPConnectionError as e:
                logger.error(
                    f"Failed to create connection {self}. RabbitMQ connection error: {e}")
            except Exception as e:
                logger.error(
                    f"Failed to create connection: {self}. Unexpected error: {e}")
            attempt += 1
            if not auto_retry:
                break
            if retry_attempts is not None and attempt >= retry_attempts:
                logger.error(
                    f"Connection {self} failed. Maximum retry attempts ({retry_attempts}) reached.")
                break
            else:
                logger.info(
                    f"Retrying to connect on {self} in {self.retry_delay}s")
                time.sleep(self.retry_delay)
                break
        raise ConnectionError(
            f"Failed to connect on {self} to RabbitMQ {self.host}")

    def create_channel(self):
        '''
        Create the channel on the given connection
        If it already exists and is open, then it returns that channel

        '''
        if self.chan and self.chan.is_open:
            return self.chan
        self.chan = self.conn.channel()
        self.chan.confirm_delivery()
        self.chan.basic_qos(prefetch_count=1)
        return self.chan

    def add_queue(self, queue: MQQueue):
        '''
        Declare queue and add it to queue map if successful
        '''
        logger.debug(f"Adding: {queue} ")
        if not queue:
            raise ValueError("Queue cannot be none")

        try:
            if not self.chan or self.chan.is_closed:
                raise Exception(
                    f"Channel is closed, cannot create queue on {self}")
            self.chan.queue_declare(queue=queue.name, durable=True)
            logger.info(f"Created queue: {queue} on {self}")
            if queue.exchange_name and queue.routing_key:
                logger.debug(
                    f"Binding routing key {queue.routing_key}  and exchagne {queue.exchange_name}")
                self.chan.queue_bind(
                    queue.name, queue.exchange_name, queue.routing_key)
            self.queues[queue.name] = queue

            return queue
        except Exception as e:
            logger.error(f"Failed to create queue {queue} on {self} - {e} ")
            import traceback
            traceback.print_exc()

    def ensure_connection(self):
        """Ensure connection and channel are alive."""
        if not self.conn or self.conn.is_closed:
            logger.warning(f"{self}: Connection closed. Reconnecting...")
            self.connect()
        if not self.chan or self.chan.is_closed:
            logger.warning(f"{self}: No channel Creating now ...")
            self.create_channel()

    def delete_queue(self, queue: MQQueue):
        try:
            self.chan.queue_delete(queue.name)
            self.queues.pop(queue.name)
        except Exception as e:
            logger.error(f"Failed to delete {queue} on {self}")

    def add_topic_exchange(self, exchange_name):
        ''' add a topic exchanger to channel '''
        self.exchange_name = exchange_name
        self.chan.exchange_declare(exchange=exchange_name,
                                   exchange_type=ExchangeType.topic)
    def ensure_exchange(self, exchange_name):
        if self.exchange_name and exchange_name != "":
            self.chan.exchange_declare(exchange=exchange_name,
                                   exchange_type=ExchangeType.topic)
        

    def ensure_queue(self, queue: MQQueue):
        '''
        Ensures a queue exists
        '''
        queue_check= self.queues.get(queue.name)
        if not queue_check:
            queue = self.add_queue(queue)
        else: 
            self.chan.queue_declare(queue=queue.name, passive=True)


    def send_msg(self, queue: MQQueue, msg, exchange='', reply_to: str | None = None, corr_id: str | None = None):
        '''
        send message into the queue, should only be used on Producer connections
        '''
        logging.debug("MQ queued length %s", len(msg))
  
      # woudl it be better to just pass in mqqueue type and deal with exception later?
        try:
            self.ensure_connection()
            self.ensure_queue(queue)
            self.ensure_exchange(exchange)
            self.chan.basic_publish(exchange=exchange,
                                    routing_key=queue.routing_key,
                                    body=msg,
                                    properties=pika.BasicProperties(delivery_mode=PERSISTENT_DELIVERY_MODE,
                                                                    reply_to=reply_to,
                                                                    correlation_id=corr_id,
                                                                    ))

        except Exception as err:
            logging.error(f"failed to send message: {err}")

    def consume(self, queue: MQQueue, auto_ack=False):
        """Consume from specified queue."""
        logger.debug(f"Consuming from {queue}")

        # woudl it be better to just pass in mqqueue type and deal with exception later?
        
        try:
            self.ensure_connection()
            self.ensure_queue(queue)
            self.consume_tag = self.chan.basic_consume(
                queue=queue.name,
                on_message_callback=queue.callback,
                auto_ack=auto_ack
            )
            self.chan.start_consuming()
        except (pika.exceptions.AMQPConnectionError,
                pika.exceptions.StreamLostError,
                ConnectionError) as e:
            logger.error(f"{self}: Connection lost during consume: {e}")
            raise  # client decides retry policy
        except Exception as e:
            logger.critical(
                f"{self}: Unexpected consume failure ({type(e).__name__}): {e}", exc_info=True)
            raise e

    def close(self):
        try:
            if self.chan and self.chan.is_open:
                self.chan.close()
            if self.conn and self.conn.is_open:
                self.conn.close()
        finally:
            self.conn = None
            self.chan = None


class MessageClient:
    ''' a rabbit mq wrapper for all different worker 
    Essentially a wrapper for a connection for each application
    '''

    def __init__(self, mq_host: str, mq_port: int,
                 username: str = "guest",
                 password: str = "guest",
                 ):
        """
                Initialize MessageClient (does not connect yet)
        Args:
            mq_host: RabbitMQ host
            mq_port: RabbitMQ port

            username: username for mq service for this application
            password: password for mq service for this application
        """

        self.mq_host = mq_host
        self.mq_port = mq_port
        self.username = username
        self.password = password
        # self.routing_key = input_routing_key
        # default exchange name
        # self.consume_tag = ''
        self.connections: dict[str, Connection] = {}

    def create_connection(self, conn_name: str, channel_name: str, heartbeat: int = CHANNEL_HEARTBEAT, timeout: int = CHANNEL_TIMEOUT,
                          connection_attempts: int = CHANNEL_CONNECTION_ATTEMPTS,
                          retry_delay: int = CHANNEL_RETRY_DELAY, auto_connect: bool = True) -> Connection:
        '''
        Create a new connection, 
        Defaults to auto connect

        if auto connect, then the connection is automatically opened 
        '''
        connection: Connection | None
        connection = self.connections.get(conn_name)

        connection = Connection(
            self.mq_host,
            mq_port=self.mq_port,
            conn_name=conn_name,
            channel_name=channel_name,
            heartbeat=heartbeat,
            timeout=timeout,
            connection_attempts=connection_attempts,
            retry_delay=retry_delay,
            username=self.username,
            password=self.password
        )
        if auto_connect:
            logger.debug(
                f"Auto connect enabled, tryng to connect {connection} now")
            connection.connect()

        self.connections[conn_name] = connection
        return connection

    def delete_connection(self, conn_name):
        ''' remove a connection, return True if closed/deleted or it doesnt exist'''
        connection: Connection | None = self.connections.get(conn_name)
        if not connection:
            return True
        try:
            connection.close()
            self.connections.pop(conn_name)
            return True
        except Exception as e:
            logger.error(f"Failed to delete {connection}, exec={e}")
            return False

    def get_connection(self, conn_name):
        '''Fetch a connection from the client, will return None if not in the connection dict  '''
        return self.connections.get(conn_name)

    def start_consumer(self, conn: Connection, queue: MQQueue, auto_ack=False, retry_delay=10):
        """Run a consumer loop with reconnection + retry."""
        conn = self.get_connection(conn.conn_name)
        if not conn:
            raise ValueError(
                f"Connection {conn.conn_name} not found in client")

        while True:
            try:
                conn.consume(queue, auto_ack=auto_ack)
            except Exception as e:
                logger.error(f"Consumer on {queue} failed: {e}", exc_info=True)
                logger.info(f"Retrying in {retry_delay}s...")
                time.sleep(retry_delay)

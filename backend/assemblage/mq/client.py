'''
message queue client for worker
Yihao Sun
'''

from dataclasses import dataclass
import logging
import time
from typing import Callable
import pika
from pika.adapters.blocking_connection import BlockingChannel, BlockingConnection
from pika.exchange_type import ExchangeType
from pika.spec import PERSISTENT_DELIVERY_MODE
from assemblage.consts import (CHANNEL_HEARTBEAT, CHANNEL_TIMEOUT, CHANNEL_CONNECTION_ATTEMPTS, CHANNEL_RETRY_DELAY)




# this reduces a lot of errors
logger = logging.getLogger(__name__)

@dataclass
class MQQueue:
    
    name: str
    callback: Callable | None = None
    exchange_name: str | None = None
    routing_key: str | None = None
    durable: bool = True
    exclusive: bool = False
    auto_delete: bool = False
    def __repr__(self) -> str:
        return self.name

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
        self.chan_name = channel_name
        self.conn: BlockingConnection | None = None
        self.chan: BlockingChannel | None = None  #  actually stores the MQ shcnanel
        # is this connection producing or consuming ( could be useful to differentiate)
        self.queues: dict[str,MQQueue] = {}
        
    def __str__(self):
        return f"Connection: {self.conn_name}" # channel/connection named the same typically

    def connect(self, auto_retry: bool = True, retry_attempts: int | None = 10):

        if self.conn and self.conn.is_open:
            return self.conn

        credentials = pika.PlainCredentials(self.username, self.password)
        conn_params = pika.ConnectionParameters(
            host=self.mq_host, port=self.mq_port,
            connection_attempts=self.connection_attempts, retry_delay=self.retry_delay,
            heartbeat=self.heartbeat, blocked_connection_timeout=self.timeout, credentials=credentials)        
        attempt = 0
        while auto_retry: 
            try:
                self.conn = pika.BlockingConnection(conn_params)
                if self.conn.is_open:
                    return self.conn
            except pika.exceptions.AMQPConnectionError as e:
                logger.error(f"Failed to create connection {self}. RabbitMQ connection error: {e}")
            except Exception as e:
                logger.error(f"Failed to create connection: {self}. Unexpected error: {e}")
            attempt += 1
            if not auto_retry:
                break
            if retry_attempts is not None and attempt >= retry_attempts:
                logger.error(f"Connection {self} failed. Maximum retry attempts ({retry_attempts}) reached.")
                break
            else: 
                logger.info(f"Retrying to connect on {self} in {self.retry_delay}s")
                time.sleep(self.retry_delay)
                break
        raise ConnectionError(f"Failed to connect on {self} to RabbitMQ {self.host}")


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
        try:
            if not self.chan or self.chan.is_closed:
                raise Exception(f"Channel is closed, cannot create queue on {self}")
            self.chan.queue_declare(queue=queue.name, durable=True)
            logger.info(f"Created queue: {queue} on {self}")
            if queue.exchange_name and queue.routing_key:
                logger.debug(f"Binding routing key {queue.routing_key }  and exchagne {queue.exchange_name}")
                self.chan.queue_bind(queue.name, queue.exchange_name, queue.routing_key)
            self.queues[queue.name] = queue

            return queue
        except Exception as e:
            logger.error(f"Failed to create queue {queue} on {self} - {e} ")

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

    def send_msg(self, queue_name, msg, exchange='', reply_to: str | None = None, corr_id: str | None = None):
        '''
        send message into the queue, should only be used on Producer connections
        '''
        logging.debug("MQ queued length %s", len(msg))

        queue = self.queues.get(queue_name) # woudl it be better to just pass in mqqueue type and deal with exception later?
        if not queue:
            raise ValueError(
                f"Queue is not in this connection's queue map: {self}. Please create queue before sending message")
        # rabbit mq doesnt like it when you go to sleep
        if not self.chan:
            # not sure on which errors to raise here. try later
            raise ValueError("Channel does not exist ")
        if self.chan.is_closed:
            # not sure we want to always automatically reopen? Or maybe we do
            raise ConnectionError("Channel is closed")
        # self.ensure_connection()
        try:
            self.chan.basic_publish(exchange=exchange,
                                    routing_key=queue.routing_key,
                                    body=msg,
                                    properties=pika.BasicProperties(delivery_mode=PERSISTENT_DELIVERY_MODE,
                                                                    reply_to=reply_to,
                                                                    correlation_id=corr_id,
                                                                    ))
            
        except Exception as err:
            logging.error(f"failed to send message: {err}")

    def consume(self, queue: MQQueue, auto_ack = False):
        '''
        Consume, on speicifed queue
        '''
        if queue.name not in self.queues:
            raise ValueError(
                f"Queue is not in this connection's queue map: {self}. Please create queue before consuming message")
        try:


            self.consume_tag = self.chan.basic_consume(queue=queue.name,
                                                        on_message_callback=queue.callback, auto_ack=auto_ack)
            self.chan.start_consuming()
        except Exception as e:
            logger.critical(
                f"__consume_from_queue from queue {queue} connection {self} failed!")
            logger.critical(e)

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
                          retry_delay: int = CHANNEL_RETRY_DELAY, auto_connect: bool = True)-> Connection:
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
            connection.connect()
        self.connections[conn_name] = connection
        return connection

    def delete_connection(self, conn_name):
        connection: Connection | None = self.connections.get(conn_name)
        if not connection: 
            raise ValueError(f"Connection does not exist in this client, cannot delete")
        try: 
             connection.close()
             self.connections.pop(conn_name)
        except Exception as e: 
            logger.error(f"Failed to delete {connection}, exec={e}")
            
    def get_connection(self, conn_name):
        return self.connections.get(conn_name)
        

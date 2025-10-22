'''
message queue client for worker
Yihao Sun
'''

from dataclasses import dataclass
from enum import Enum
import logging
from shutil import ExecError
from typing import Callable
import pika
from pika.adapters.blocking_connection import BlockingChannel, BlockingConnection
from pika.exchange_type import ExchangeType
from pika.frame import Method


logger = logging.getLogger(__name__)


class ConnectionType(str, Enum):
    PRODUCER = 'producer'
    CONSUMER = 'consumer'


@dataclass
class MQQueue:
    name: str
    callback: Callable | None= None
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

    def __init__(self, mq_host: str, mq_port: int,conn_type: ConnectionType, connection_name: str,
                 channel_name: str, 
                 exchange_name: str | None = None,
                 heartbeat: int = 300, timeout: int = 300,
                 connection_attempts: int = 35,
                 retry_delay: int = 3,
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
        self.exchange_name = exchange_name
        self.password = password
        self.conn_name = connection_name
        self.chan_name = channel_name
        self.conn: BlockingConnection | None = None
        self.chan: BlockingChannel | None = None  #  actually stores the MQ shcnanel
        self.conn_type = conn_type # is this connection producing or consuming ( could be useful to differentiate)
        self.queues: dict[str: MQQueue] = {}

    def __str__(self):
        return f"{self.conn_name}:{self.chan_name}"

    def connect(self):

        if self.conn and self.conn.is_open:
            return self.conn

        credentials = pika.PlainCredentials(self.username, self.password)
        conn_params = pika.ConnectionParameters(
            host=self.mq_host, port=self.mq_port,
            connection_attempts=self.connection_attempts, retry_delay=self.retry_delay,
            heartbeat=self.heartbeat, blocked_connection_timeout=self.timeout, credentials=credentials)
        self.conn = pika.BlockingConnection(conn_params)
        return self.conn

    def create_channel(self):
        '''
        Create the channel on the given channel
        If it already exists, then it closes the existing channel and reopens it

        '''
        if self.chan and self.chan.is_open:
            self.chan.close()
        self.chan = self.conn.channel()
        self.chan.confirm_delivery()
        self.chan.basic_qos(prefetch_count=1)
        return self.chan

    def add_queue(self, queue: MQQueue):
        '''
        Declare queue and add it to queue map if successful
        '''
        
        try:
            self.chan.queue_declare(queue=queue.name, durable=True)

            self.queues[queue.name] = queue               
        except Exception as e:
            logger.error(f"Failed to create queue {queue} on {self} ")
    
    def delete_queue(self, queue: MQQueue):
        try: 
            self.chan.queue_delete(queue.name)
            self.queues.pop(queue.name)
        except Exception as e:
            logger.error(f"Failed to delete {queue} on {self}")
        
    def add_topic_exchange(self, exchange_name):
        ''' add a topic exchanger to channel '''
        self.exchange_name = exchange_name
        self.channel.exchange_declare(exchange=exchange_name,
                                      exchange_type=ExchangeType.topic)

    def send_msg(self, queue_name, msg, exchange=''):
        '''
        send message into the queue, should only be used on Producer connections
        '''
        logging.debug("MQ queued length %s", len(msg))
        
        if self.conn_type is ConnectionType.CONSUMER:
            raise Exception(f"This connection :{self} is a consumer, should not be sending messages")
        
        queue = self.queues.get(queue_name)
        
        if not queue: 
            raise ValueError(f"Queue is not in this connection's queue map: {self}. Please create queue before sending message")
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
                                    routing_key=queue.name,
                                    body=msg,
                                    properties=pika.BasicProperties(delivery_mode=pika.DeliveryMode.Persistent))
        except Exception as err:
            logging.error(err)
    
    def consume(self, queue_name):
        '''
        Consume, only valid if connection type is consumer
        '''
        if self.conn_type is ConnectionType.PRODUCER:
            raise Exception(f"This connection :{self} is a producer, should not be consuming")
        queue = self.queues.get(queue_name)
        
        if not queue: 
            raise ValueError(f"Queue is not in this connection's queue map: {self}. Please create queue before consuming message")
        while True:
                try: 
                    if self.hang_flag:
                        continue

                    self.consume_tag = self.chan.basic_consume(queue=queue,
                                                                on_message_callback=queue.callback)
                    self.chan.start_consuming()
                except Exception as e: 
                    logger.critical(
                        f"__consume_from_queue from queue {queue} connection {self} failed!")
                    logger.critical(e)
  

class MessageClient:
    ''' a rabbit mq wrapper for all different worker 
    Essentially a wrapper for a connection for each application
    '''

    def __init__(self, mq_host: str, mq_port: int, heartbeat: int = 300, timeout: int = 300,
                 connection_attempts: int = 35,
                 retry_delay: int = 3,
                 username: str = "guest",
                 password: str = "guest",
                 ):
        """
                Initialize MessageClient (does not connect yet)
        Args:
            mq_host: RabbitMQ host
            mq_port: RabbitMQ port
            exchange_name: Optional default exchange name
            heartbeat: Heartbeat interval in seconds
            timeout: Blocked connection timeout in seconds
            connection_attempts: Number of connection retry attempts
            retry_delay: Delay between retries in seconds
            username: username for mq service for this application
            password: password for mq service for this application
        """

        self.mq_host = mq_host
        self.mq_port = mq_port
        self.heartbeat = heartbeat
        self.timeout = timeout
        self.connection_attempts = connection_attempts
        self.retry_delay = retry_delay
        self.username = username
        self.password = password
        # self.routing_key = input_routing_key
        # default exchange name
        # self.consume_tag = ''
        self.hang_flag = False
        self.connections: dict[str | Connection] = {}

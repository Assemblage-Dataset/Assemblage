'''
message queue client for worker
Yihao Sun
'''

import logging
from multiprocessing import connection

import pika
from pika.adapters.blocking_connection import BlockingChannel, BlockingConnection
from pika.exchange_type import ExchangeType
from pika.frame import Method


logger = logging.getLogger(__name__)

class InputQueueSetup:
    name: str
    callback: Method | None # i think this type is right
    exchange_name: str | None
    routing_key: str  | None
    
    

class Connection: 
    '''
    Wrapper individual exisitng connection created by the Message Client
    '''
    def __init__(self, connection: BlockingConnection, queue , heartbeat, timeout ):
        self.conn = connection
        self.chan : BlockingChannel = connection.channel()
        self.queue: str  = queue
        self.heartbeat: int = heartbeat
        self.timeout: int = timeout
    
    def ensure_channel(self):
        if self.chan.is_closed:
            self.chan = self.conn.channel()
            
class MessageClient:
    ''' a rabbit mq wrapper for all different worker '''

    def __init__(self, rabbitmq_host, rabbitmq_port, input_routing_key):
        self.rabbitmq_host = rabbitmq_host
        self.rabbitmq_port = rabbitmq_port
        self.routing_key = input_routing_key  
        self.input_callbacks = []      
        # default exchange name
        self.exchange_name = None
        self.consume_tag = ''
        self.hang_flag = False
        self.connections: dict[str | Connection]  # all the connections the MQ client is handling

    def create_connection(self, name, heartbeat, timeout, queue=""):
        """ create a rabbitmq channel, with empty to queue by default 
        Should this be in Connection class too in init?. feel the connection should be
        created/destroyed from above not within
        
        """
        conn_params = pika.ConnectionParameters(
            host=self.rabbitmq_host, port=self.rabbitmq_port,
            connection_attempts=35, retry_delay=3,
            heartbeat=heartbeat, blocked_connection_timeout=timeout)
        conn = pika.BlockingConnection(conn_params)
        self.connections[name] = Connection(conn, conn.channel(), queue, heartbeat, timeout)

    def ensure_connection(self, conn_name):
        '''
        Ensure a channel/connection is alive and recreates if it has failed
        Could this be moved to the Connection info class...? 
        '''
        conn: Connection = self.connections[conn_name]
        if conn.is_closed:    
            self.create_connection(conn_name, conn.heartbeat, conn.timeout, conn.queue)
        
    def add_topic_exchange(self, exchange_name):
        ''' add a topic exchanger to channel '''
        self.exchange_name = exchange_name
        self.channel.exchange_declare(exchange=exchange_name,
                                      exchange_type=ExchangeType.topic)

    def add_output_queues(self, qs: list[str]):
        '''
        adding queues to mq channel
        a worker can have multiple output queues
        qs is a list of dict [{'name': ..., 'params': {...}} ...]
        '''
        for q in qs:
            self.channel.queue_declare(q, durable=True)

# name, params, input_callback
    def add_input_queues(self, qs: list[InputQueueSetup]):
        '''
        Workers have multiple input queues 
        The job input queue 
        And the control input queue 
        
        '''
        for q in qs: 
            self.channel.queue_declare(q.name, durable=True)
            self.input_callbacks.append(q.callback)

    def send_kind_msg(self, kind, msg, exchange=''):
        '''
        send message into the queue with name `kind`
        '''
        logging.info("MQ queued length %s", len(msg))
        # rabbit mq doesnt like it when you go to sleep 
     
        self.ensure_connection()
        try:
            self.channel.basic_publish(exchange=exchange,
                                   routing_key=kind,
                                   body=msg,
                                   properties=pika.BasicProperties(delivery_mode=2))
        except Exception as err:
            logging.error(err)

    def __consume_from_queue(self, queue: InputQueueSetup):
        '''
        Consume from a queue
        '''
        while True:
            try:
                logger.info("Consume thread on queue '%s' started", queue)
                # Create a channel and listen on the relevant queue
                thread_channel = self.create_channel(self.rabbitmq_host, self.rabbitmq_port)
                if queue.exchange_name:
                    thread_channel.queue_bind(queue.name,
                                        queue.exchange_name,
                                        routing_key=self.routing_key)
                
                thread_channel.basic_consume(queue=queue.name, on_message_callback=queue.callback)
                thread_channel.start_consuming()
                logger.critical("Consume thread '%s' exited", queue)
            except Exception as err:
                logger.critical("__consume_from_queue from queue '%s' failed!", queue)
                logger.critical(err)
    
        

    def consume_input(self):
        ''' start to listen and handle data from input channels '''
        while True:
            if self.hang_flag:
                continue
            self.channel.confirm_delivery()
            self.channel.basic_qos(prefetch_count=1)
            if self.exchange_name:
                self.channel.queue_bind(self.input_queue_name,
                                        self.exchange_name,
                                        routing_key=self.routing_key)
            logging.info("MQ input_queue_name %s", self.input_queue_name)
            self.consume_tag = self.channel.basic_consume(queue=self.input_queue_name,
                                                          on_message_callback=self.input_callback)
            self.channel.start_consuming()

    def change_input_queue(self, name, arg, input_callback):
        """ change the input queue, cancel original consuming """
        self.hang_flag = True
        self.channel.basic_cancel(self.consume_tag)
        self.add_input_queues(name, arg, input_callback)
        self.hang_flag = False

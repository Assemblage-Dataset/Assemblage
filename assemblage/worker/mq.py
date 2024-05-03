'''
message queue client for worker
Yihao Sun
'''

import logging

import pika
from pika.exchange_type import ExchangeType

class MessageClient:
    ''' a rabbit mq wrapper for all different worker '''

    def __init__(self, rabbitmq_host, rabbitmq_port, input_routing_key):
        self.rabbitmq_host = rabbitmq_host
        self.rabbitmq_port = rabbitmq_port
        self.routing_key = input_routing_key
        self.input_queue_name = None
        self.input_callback = None
        # default exchange name
        self.exchange_name = None
        self.consume_tag = ''
        self.hang_flag = False
        self.conn, self.channel = self.create_channel(300, 300)

    def create_channel(self, heartbeat, timeout):
        """ create a rabbitmq channel """
        conn_params = pika.ConnectionParameters(
            host=self.rabbitmq_host, port=self.rabbitmq_port,
            connection_attempts=35, retry_delay=3,
            heartbeat=heartbeat, blocked_connection_timeout=timeout)
        conn = pika.BlockingConnection(conn_params)
        return (conn, conn.channel())

    def add_topic_exchange(self, exchange_name):
        ''' add a topic exchanger to channel '''
        self.exchange_name = exchange_name
        self.channel.exchange_declare(exchange=exchange_name,
                                      exchange_type=ExchangeType.topic)

    def add_output_queues(self, qs):
        '''
        adding queues to mq channel
        a worker can have multiple output queues
        qs is a list of dict [{'name': ..., 'params': {...}} ...]
        '''
        for q in qs:
            self.channel.queue_declare(q['name'], **q['params'])

    def add_input_queue(self, name, params, input_callback):
        '''
        a woker can only have on input queue.
        if a new queue added old queue will lost
        '''
        res = self.channel.queue_declare(name, **params)
        self.input_callback = input_callback
        self.input_queue_name = res.method.queue

    def send_kind_msg(self, kind, msg, exchange=''):
        '''
        send message into the queue with name `kind`
        '''
        # logging.info("Sending %s", msg)
        self.channel.basic_publish(exchange=exchange,
                                   routing_key=kind,
                                   body=msg,
                                   properties=pika.BasicProperties(delivery_mode=2))

    def consume(self):
        ''' start to listen and handle data from input channel '''
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
        self.add_input_queue(name, arg, input_callback)
        self.hang_flag = False

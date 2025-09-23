"""
A worker running ddisasm to get output database

please at least impl following thing

`setup_job_queue_info`
`control_message_handler`
`job_handler`

"""

import logging
from pydoc_data.topics import topics
import threading
import time
import uuid
from assemblage.consts import PING_INTERVAL

from assemblage.protobufs.assemblage_pb2 import PingRequest, RegisterRequest
from assemblage.worker.mq import MessageClient


class BasicWorker:
    """
    Worker base class
    """

    def __init__(self, rabbitmq_host, rabbitmq_port, rpc_stub, worker_type, opt_id):
        self.rpc_stub = rpc_stub
        self.worker_type = worker_type
        self.opt_id = opt_id
        self.rabbitmq_host = rabbitmq_host
        self.rabbitmq_port = rabbitmq_port
        self.uuid = str(uuid.uuid1())

        self.input_queue_name = None
        self.input_queue_args = None
        self.output_message_queue = []
        self.route_key = ""
        self.topic_exchange = None
        self.mq_client = None
        self.t_daemon = None
        self.t_job = None
        self.platform = ""
        self.on_init()

    def on_init(self):
        """ worker initialization hook """

    def setup_job_queue_info(self):
        """
        setup following mq connection infomation to get job from coordinator

        input message queue name (plz check python pika doc)
        input message queue arguments
        output message queue (a list of output queue)
        route_key
        topic exchanger

        if no need for pull job from coodinator leave input_message_queue None
        """

    def job_handler(self, ch, method, _props, body):
        """ a handler to connect all hook and real job function """
        logging.info("empty job handler....")

    def control_message_handler(self, msg):
        """ control message from coordinator """

    def setup_mq_client(self):
        """ setup mq connection based on the infomation provided in `setup_job_queue_info` """
        self.mq_client = MessageClient(self.rabbitmq_host, self.rabbitmq_port,
                                       self.route_key)
        if self.topic_exchange:
            self.mq_client.add_topic_exchange(self.topic_exchange)
        if self.output_message_queue != []:
            self.mq_client.add_output_queues(self.output_message_queue)
        if self.input_queue_name:
            logging.info("add input queue")
            self.mq_client.add_input_queue(self.input_queue_name, self.input_queue_args,
                                           self.job_handler)

    def change_input(self, input_queue_name, input_queue_args):
        """ reset the input queue """
        if input_queue_name:
            self.mq_client.change_input_queue(input_queue_name, input_queue_args,
                                              self.job_handler)

    def control_thread(self):
        ''' try to restart whole program when builder is keep idle '''
        while True:
            req = PingRequest()
            req.ping = 1
            req.uuid = self.uuid
            req.task = self.opt_id
            req.msg = "ping"
            response = self.rpc_stub.ping(req)
            if response.task != self.opt_id:
                self.control_message_handler(response.task)
            time.sleep(PING_INTERVAL)

    def job_thread(self):
        """ the job thread """
        if self.input_queue_name:
            logging.info("start consuming")
            self.mq_client.consume()
            logging.info("finish consuming")
        else:
            self.job_handler(None, None, None, None)

    def register(self):
        """ register worker to server """
        register_req = RegisterRequest()
        register_req.uuid = self.uuid
        register_req.opt = self.opt_id
        register_req.type = self.worker_type
        self.rpc_stub.registWorker(register_req)

    def run(self):
        """ run the worker """
        logging.info("starting worker ....")
        self.setup_job_queue_info()
        self.setup_mq_client()
        logging.info("MQ started ....")
        logging.info("Job queue started ....")
        self.register()
        logging.info("Worker registered ....")
        self.t_daemon = threading.Thread(target=self.control_thread)
        self.t_daemon.start()
        self.t_job = threading.Thread(target=self.job_thread)
        self.t_job.start()
        logging.info("Worker %s inited", self.uuid)
        self.t_daemon.join()
        self.t_job.join()

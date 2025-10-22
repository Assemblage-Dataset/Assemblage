"""
A worker running ddisasm to get output database

please at least impl following thing

`setup_job_queue_info`
`control_message_handler`
`job_handler`

"""

from abc import ABC
import logging
import threading
import uuid
from assemblage.mq.client import MQQueue, MessageClient


logger = logging.getLogger(__name__)
class BasicWorker(ABC):
    """
    Worker base class
    """
    mq_client: MessageClient 
    uuid: str
    def __init__(self, name, rabbitmq_host, rabbitmq_port):
        self.name = name
        self.rabbitmq_host = rabbitmq_host
        self.rabbitmq_port = rabbitmq_port
        self.uuid = str(uuid.uuid1())
        
        self.sleep_job_event = threading.Event()

        # self.route_key = ""
        # self.topic_exchange = None 
        self.mq_client = MessageClient(self.rabbitmq_host, self.rabbitmq_port,
                                       username='guest', password='guest') # config later to do dynamically / better auth
        self.t_ctrl: threading.Thread | None = None
        self.t_job: threading.Thread | None = None
        
        self.control_queue_in = MQQueue(f'{self.name}-ctrl', callback=self.control_message_handler) # also include a correlationID
        # of the uuid in case two wokrers of same name exist

    
    def control_message_handler(self, ch, method, _props, body):
        """
        Handler for the control queue to callback to
        """
        logger.info("Empty control message handler called")

    def job_handler(self, ch, method, _props, body): 
        """ handler to pass to the queues to listen on """
        logging.info("empty job handler called ")
        
    def run_job(self):
        pass
    
    def run_ctrl(self):
        pass
        
        
    def run(self):
        """ run the worker """
        logging.info(f"Starting worker {self.name}...")

        # setup consumer functions     
        self.t_ctrl = threading.Thread(target=self.run_ctrl)
        self.t_ctrl.start()
        self.t_job = threading.Thread(target=self.run_job)
        self.t_job.start() # to start with. just one control thread, and one job thread per worker. can expand later

        logging.info(f"Worker {self.name}:{self.uuid} running") # add healthcheck function here 

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
from assemblage.consts import OutputQueue, WorkerType


logger = logging.getLogger(__name__)
class BasicWorker(ABC):
    """
    Worker base class
    """
    mq_client: MessageClient 
    uuid: str
    def __init__(self, name, rabbitmq_host, rabbitmq_port, worker_type: WorkerType):
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
        self.type = worker_type
        if self.type == WorkerType.Builder:
            control_queue_in_name = OutputQueue.BUILDER_CTRL
        elif self.type == WorkerType.Scraper:
            control_queue_in_name = OutputQueue.SCRAPER_CTRL
        else: 
            control_queue_in_name = "Unknown-type" # probably should sys exit/ return
        
        self.control_queue_in = MQQueue(control_queue_in_name, callback=self.control_message_handler) 
        # of the uuid in case two wokrers of same name exist

    def __str__(self):
        return f'{self.type.value}-{self.uuid}'
    
    def control_message_handler(self, ch, method, _props, body):
        """
        Handler for the control queue to callback to
        """
        logger.info("Empty control message handler called")

    def job_handler(self, ch, method, _props, body): 
        """ handler to pass to the queues to listen on """
        logging.info("empty job handler called ")
    
    def run_ctrl(self):
        '''
        Run the control function of the worker. 
        '''
        pass
         
    def run_job(self):
        '''
        Run the main task of the worker. ie builder or scraper
        '''
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

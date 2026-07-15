"""
A worker running ddisasm to get output database

please at least impl following thing

`setup_job_queue_info`
`control_message_handler`
`job_handler`

"""

import logging
import threading
import uuid
from abc import ABC

from assemblage.consts import OutputQueue, WorkerType
from assemblage.mq.client import MessageClient, MQQueue

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
        self.mq_client = MessageClient(
            self.rabbitmq_host, self.rabbitmq_port, username="guest", password="guest"
        )  # config later to do dynamically / better auth
        self.t_ctrl: threading.Thread | None = None
        self.t_job: threading.Thread | None = None
        self.type = worker_type
        # Give each worker its own control queue so stale or unrelated
        # messages can't block startup (e.g. old correlation_ids sitting on a
        # shared queue). The coordinator will reply to the queue name supplied
        # in `reply_to`.
        if self.type == WorkerType.Builder:
            control_queue_in_name = f"{OutputQueue.BUILDER_CTRL}_{self.uuid}"
        elif self.type == WorkerType.Scraper:
            control_queue_in_name = f"{OutputQueue.SCRAPER_CTRL}_{self.uuid}"
        else:
            control_queue_in_name = "Unknown-type"  # probably should sys exit/ return

        self.control_queue_in = MQQueue(
            control_queue_in_name,
            callback=self.control_message_handler,
            durable=False,
            auto_delete=True,
        )

    def __str__(self):
        return f"{self.type.value}-{self.uuid}"

    def control_message_handler(self, ch, method, _props, body):
        """
        Handler for the control queue to callback to
        """
        logger.info("Empty control message handler called")

    def job_handler(self, ch, method, _props, body):
        """handler to pass to the queues to listen on"""
        logging.info("empty job handler called ")

    def run_ctrl(self):
        """
        Run the control function of the worker.
        """

    def run_job(self):
        """
        Run the main task of the worker. ie builder or scraper
        """

    def run(self):
        """run the worker"""
        logging.info(f"Starting worker {self.name}...")

        # setup consumer functions
        self.t_ctrl = threading.Thread(target=self.run_ctrl)
        self.t_ctrl.start()
        self.t_job = threading.Thread(target=self.run_job)
        self.t_job.start()  # to start with. just one control thread, and one job thread per worker. can expand later
        logging.info(f"Worker {self.name}:{self.uuid} running")  # add healthcheck function here

        self.t_job.join()
        self.t_ctrl.join()
        logger.info(f"Worker {self.name}:{self.uuid} exiting")

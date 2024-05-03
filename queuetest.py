"""
Assemblage Coordinator/Server

Yihao Sun
"""

import sys
from multiprocessing import Process
import threading
import time
import logging
import json
from concurrent.futures import ThreadPoolExecutor
import os
import collections
import grpc
import pika
import random
from pika.exceptions import NackError

from assemblage.protobufs.assemblage_pb2_grpc import add_AssemblageServiceServicer_to_server

from assemblage.coordinator.rpc import InfoService
from assemblage.data.db import DBManager

from assemblage.consts import BIN_DIR, TASK_TIMEOUT_THRESHOLD, WORKER_TIMEOUT_THRESHOLD, BuildStatus

logging.basicConfig(level=logging.DEBUG)
def create_channel(host, port, heartbeat, timeout):
    """
    create a rabbit mq channel,
    this is blocking channel, since we are using single process worker
    don't do anything blocking before ack
    """
    conn_params = pika.ConnectionParameters(host=host, port=port,
                                            connection_attempts=35, retry_delay=3,
                                            heartbeat=heartbeat, blocked_connection_timeout=timeout)
    conn = pika.BlockingConnection(conn_params)
    return conn.channel()

def on_callback(x):
    return

channel = create_channel("localhost", 5672, 500, 350)
res = channel.queue_declare(
        queue="scrape",
        passive=True
)

print(res)


"""
Assemblage Coordinator/Server

Yihao Sun
"""

import sys
import threading
import time
import logging
import json
from concurrent.futures import ThreadPoolExecutor
import grpc
import pika
import boto3
import datetime
import re
from botocore.exceptions import ClientError
import sqlalchemy

from assemblage.protobufs.assemblage_pb2_grpc import add_AssemblageServiceServicer_to_server

from assemblage.coordinator.rpc import InfoService
from assemblage.data.db import DBManager

from assemblage.consts import AWS_AUTO_REBOOT_PREFIX, BIN_DIR, TASK_TIMEOUT_THRESHOLD, WORKER_TIMEOUT_THRESHOLD, BuildStatus, REPO_SIZE_THRESHOLD

formatter = logging.Formatter("%(asctime)s %(levelname)s:%(message)s",
                              "%Y-%m-%d %H:%M:%S")
SLEEP_INTERVAL = 3600
HEARTBEAT=1200
BLOK_TMOUT=900
logging.basicConfig(format=formatter, level=logging.DEBUG)


def stop_the_world_excepthook(args):
    """
    this is a thread execption handler if an thread trigger this, no matter normal
    exit or not will shutdown the how coordinator. In coordinator all thread should
    run forever!
    """
    sys.excepthook(args.exc_type, args.exc_value, args.exc_traceback)
    exit(1)


threading.excepthook = stop_the_world_excepthook


def patch_url(_url):
    """ make a url cloneable """
    return _url.replace('repos/', '').replace('api.', '')


def unpatch_url(_url: str) -> str:
    """ make url searchable in db """
    api_index = _url.find("/")

    rest_url = _url[api_index + 2:]
    with_api = _url[:api_index + 2] + "api." + rest_url

    com_index = with_api.find(".com")

    rest_repos = with_api[com_index + 4:]
    unpatched = with_api[:com_index + 4] + "/repos" + rest_repos

    return unpatched


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


class Coordinator:
    """
    coordinator node, dispatch work to woker node and also collect data
    """

    def __init__(self, rabbitmq_host, rabbitmq_port, grpc_addr, db_addr, cluster_name, aws_mode=0, reproduce_mode=0):
        logging.info("Coordinator Init")
        self.rabbitmq_host = rabbitmq_host
        self.rabbitmq_port = rabbitmq_port
        self.channel = create_channel(
            self.rabbitmq_host, self.rabbitmq_port, HEARTBEAT, BLOK_TMOUT)
        # Do not use round-robin scheduling.
        self.channel.basic_qos(prefetch_count=1)

        # To receive results about cloning
        self.channel.queue_declare(queue='clone', durable=True)
        # To receive results about building
        self.channel.queue_declare(queue='build', durable=True)
        # To receive results about scraping
        self.channel.queue_declare(queue='scrape', durable=True)
        # To receive results about binaries
        self.channel.queue_declare(queue='binary', durable=True)
        self.grpc_addr = grpc_addr
        self.db_addr = db_addr
        self.cluster_name = cluster_name
        self.reproduce_mode = reproduce_mode
        if aws_mode == 1:
            self.aws_flag = True
        else:
            self.aws_flag = False
        # setup rpc service
        self.rpc_service = InfoService(self.db_addr)

    def __del__(self):
        self.channel.close()

    def __rpc(self):
        """ start rpc server, this blocking """
        rpc_server = grpc.server(ThreadPoolExecutor(max_workers=50))
        add_AssemblageServiceServicer_to_server(self.rpc_service, rpc_server)
        rpc_server.add_insecure_port(self.grpc_addr)
        logging.info("Starting server on %s", self.grpc_addr)
        rpc_server.start()
        rpc_server.wait_for_termination()

    def __dispatch_task(self, build_opt_id, sleep=True):
        """ send a number of task into worker, and the keep repo name if queue """
        try:
            logging.info("__dispatch_task thread started on %s", build_opt_id)
            thread_channel = create_channel(
                self.rabbitmq_host, self.rabbitmq_port, HEARTBEAT, BLOK_TMOUT)
            # we use topics to control which worker gets which jobs.
            thread_channel.exchange_declare(
                exchange='build_opt', exchange_type='topic')
            thread_channel.confirm_delivery()
            db_man = DBManager(self.db_addr)
        except:
            logging.info("__dispatch_task start fail")
        task_count = 0
        sleep_interval = SLEEP_INTERVAL
        task_pausetime = int(time.time()) - \
            int(time.time()) % SLEEP_INTERVAL + sleep_interval
        while True:
            time_before_query = time.time()
            tasks = db_man.find_status_by_status_code(
                build_opt_id=build_opt_id,
                clone_status=BuildStatus.INIT,
                build_status=BuildStatus.INIT,
                priority=BuildStatus.SUCCESS,
                limit=1)
            if len(tasks) == 0:
                tasks = db_man.find_status_by_status_code(
                    build_opt_id=build_opt_id,
                    clone_status=BuildStatus.INIT,
                    build_status=BuildStatus.INIT,
                    limit=1)
            if len(tasks) == 0:
                continue
            task = tasks[0]
            uncloned_repo = db_man.find_repo_by_id(task.repo_id)
            # if uncloned_repo.size < REPO_SIZE_THRESHOLD:
            #     logging.info("Discard task %s size %s", task.repo_id, uncloned_repo.size)
            #     continue
            build_opt = db_man.find_build_opt_by_id(task.build_opt_id)
            try:
                db_man.update_repo_status(status_id=task.id, clone_status=BuildStatus.PROCESSING)
            except sqlalchemy.exc.OperationalError:
                continue
            time_after_query = time.time()
            repo_url = patch_url(uncloned_repo.url)
            out_dir = f'{BIN_DIR}/{task.id}'
            # os.makedirs(out_dir, exist_ok=True)
            repo_url = patch_url(uncloned_repo.url)
            out_dir = f'{BIN_DIR}/{task.id}'
            clone_req = {'name': uncloned_repo.name, 'url': repo_url,
                            'task_id': task.id, 'opt_id': build_opt.id,
                            #  'commit_hexsha': task.commit_hexsha,
                            'output_dir': out_dir,
                            'repo_id': uncloned_repo._id,
                            'updated_at': uncloned_repo.updated_at.strftime("%m/%d/%Y, %H:%M:%S"),
                            'build_system': uncloned_repo.build_system,
                            'default_branch': uncloned_repo.default_branch,
                            #  also add timestamp when this messsage sent
                            'msg_time': time.time()}
            thread_channel.basic_publish(
                exchange='build_opt', routing_key=f'worker.{build_opt.id}',
                body=json.dumps(clone_req),
                properties=pika.BasicProperties(delivery_mode=2))
            logging.info('Placed %sth task on build option %d, took %ss', task_count,
                                task.build_opt_id, str(time_after_query - time_before_query)[:5])
            task_count += 1

    def __recycle_clone(self):
        try:
            logging.info("Recycle thread starting")
            db_man = DBManager(self.db_addr)
        except:
            logging.info("Recycle start fail")
        # while True:
        #     logging.info("Recycle thread running...")
        #     # db_man.reset_failures()
        #     logging.info("Recycle thread sleeping")
        #     time.sleep(120)

    def __clean_worker(self):
        time.sleep(60)
        while True:
            worker = self.rpc_service.workers.copy()
            while len(self.rpc_service.workers) > 0:
                self.rpc_service.workers.pop()
            for worker_info in worker:
                if abs(time.time() - worker_info['timestamp']) < WORKER_TIMEOUT_THRESHOLD:
                    self.rpc_service.workers.append(worker_info)
            time.sleep(1)

    def __consume_binary(self):
        while True:
            try:
                logging.info(
                    "Coordinator binary consume thread started")
                thread_channel = create_channel(
                    self.rabbitmq_host, self.rabbitmq_port, HEARTBEAT, BLOK_TMOUT)
                thread_channel.basic_consume(queue='binary',
                                             on_message_callback=self.recv_binary)
                thread_channel.start_consuming()
                logging.critical("Consuming binary exited!")
            except Exception as err:
                logging.critical(err)

    def __consume_clone(self):
        while True:
            try:
                logging.info(
                    "Coordinator clone consume thread started")
                thread_channel = create_channel(
                    self.rabbitmq_host, self.rabbitmq_port, HEARTBEAT, BLOK_TMOUT)
                thread_channel.basic_consume(queue='clone',
                                             on_message_callback=self.recv_clone_info)
                thread_channel.start_consuming()
                logging.critical("Consuming clone exited")
            except Exception as err:
                logging.critical(err)

    def __consume_build(self):
        while True:
            try:
                logging.info(
                    "Coordinator build consume thread started")
                thread_channel = create_channel(
                    self.rabbitmq_host, self.rabbitmq_port, HEARTBEAT, BLOK_TMOUT)
                thread_channel.basic_consume(queue='build',
                                             on_message_callback=self.recv_build_info)
                thread_channel.start_consuming()
                logging.critical("Consuming build exited")
            except Exception as err:
                logging.critical(err)

    def __consume_scraped_data(self):
        while True:
            try:
                logging.info(
                    "Coordinator crawl consume thread started")
                thread_channel = create_channel(
                    self.rabbitmq_host, self.rabbitmq_port, HEARTBEAT, BLOK_TMOUT)
                thread_channel.basic_consume(queue='scrape',
                                             on_message_callback=self.recv_scrape_info)
                thread_channel.start_consuming()
                logging.critical("Consuming scrape exited")
            except Exception as err:
                logging.critical(err)

    def __clean_overtime(self):
        ''' restore all overtime repo every 2 build circle '''
        db_man = DBManager(self.db_addr)
        while True:
            time.sleep(TASK_TIMEOUT_THRESHOLD)
            db_man.reset_timeout_status(TASK_TIMEOUT_THRESHOLD)
            logging.info(">>>>>>>>>>>>>>>>>>>>>> cleanning overtime"
                         " tasks ......")

    def __reboot_worker(self):
        ''' reboot worker every hr, only in aws mode '''
        if not self.aws_flag:
            return
        sesh = boto3.Session(profile_name='default')
        ec2_resource = sesh.resource('ec2')
        ec2_client = sesh.client('ec2')
        sleep_time = SLEEP_INTERVAL
        while 1:
            reboot_instance_ids = []
            for instance in ec2_resource.instances.all():
                if instance.tags:
                    for tag in instance.tags:
                        print(tag['Key'], tag['Value'])
                        if tag['Key'] == 'Name' and (AWS_AUTO_REBOOT_PREFIX in tag['Value']):
                            reboot_instance_ids.append(instance.id)
            logging.info("Found instances %s", reboot_instance_ids)
            if reboot_instance_ids != []:
                for instance_id in reboot_instance_ids:
                    try:
                        response = ec2_client.reboot_instances(
                            InstanceIds=[instance_id])
                        logging.info("Reboot instances out %s", response)
                    except Exception as err:
                        logging.info("Reboot instances err %s", err)
            for i in range(int(sleep_time/60)):
                logging.info("%s min to next reboot", sleep_time/60-i)
                time.sleep(60)

    def recv_scrape_info(self, ch, method, _props, body):
        ''' store scraped messga to database page by page '''
        logging.info("Crawled msg received")
        ch.basic_ack(delivery_tag=method.delivery_tag)
        prev_time = time.time()
        recv_msg = json.loads(body.decode())
        db_man = DBManager(self.db_addr)
        successes = 0
        result = 0
        for onerepo in recv_msg:
            result = db_man.insert_repos(onerepo)
            successes += result
        if result == 0:
            logging.debug("%s inserted err", recv_msg[-1]['url'])
        db_man.shutdown()
        after_time = time.time()
        logging.info("Saved %s/%s in %ss", successes,
                     len(recv_msg), int(after_time-prev_time))

    def recv_binary(self, ch, method, _props, body):
        """ collect binary metadata from worker"""
        db_man = DBManager(self.db_addr)
        recv_msg = json.loads(body.decode())
        if "exe" in recv_msg['file_name'] or "dll" in recv_msg['file_name']:
            logging.info("Received binary: %s on %s",
                     recv_msg['file_name'], recv_msg['task_id'])
            db_man.insert_binary(
                file_name=recv_msg['file_name'],
                description='',
                status_id=recv_msg['task_id']
            )
        ch.basic_ack(delivery_tag=method.delivery_tag)

    def recv_build_info(self, ch, method, _props, body):
        """ collect and update build status of a task """
        db_man = DBManager(self.db_addr)
        recv_msg = json.loads(body.decode())
        # task = db_man.find_status_by_id(recv_msg['task_id'])
        if recv_msg['status'] == BuildStatus.OUTDATED_MSG:
            logging.info("discarding an timeout build msg %s", body.decode())
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return
        task = db_man.find_status_by_id(recv_msg['task_id'])
        db_man.update_repo_status(
            status_id=recv_msg['task_id'],
            build_time=recv_msg['build_time'],
            build_status=recv_msg['status'],
            build_msg=recv_msg['msg'][-500:])

        status_msg = ["INIT", "PEND","FAILED", "SUCC"][int(recv_msg['status'])]
        if status_msg != "PEND":
            logging.info("BUILD task on buildopt %s id %s updated to %s msg: %s",
                    recv_msg['opt_id'], recv_msg["task_id"], status_msg, " ".join(recv_msg['msg'].split())[-500:])
        ch.basic_ack(delivery_tag=method.delivery_tag)

    def recv_clone_info(self, ch, method, _props, body):
        """ collect and update clone stsatus of a task """
        db_man = DBManager(self.db_addr)
        recv_msg = json.loads(body.decode())
        # if the status code is timeout discard it
        if recv_msg['status'] == BuildStatus.OUTDATED_MSG:
            logging.info("discarding an timeout clone msg %s", body.decode())
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return
        db_man.update_repo_status(
            status_id=recv_msg['task_id'],
            clone_status=recv_msg['status'],
            clone_msg=recv_msg['msg'][-200:])
        task = db_man.find_status_by_id(recv_msg['task_id'])
        if task.clone_status != BuildStatus.SUCCESS:
            logging.info("CLONE task on buildopt %s updated to %s",
                         recv_msg['opt_id'], task.clone_status)
        ch.basic_ack(delivery_tag=method.delivery_tag)

    def __daemon(self):
        while True:
            time.sleep(1)

    def run(self):
        """
        Run various threads for interacting with queues and RPC.
        """
        db_man = DBManager(self.db_addr)
        t_dispatch_list = []
        logging.info("%s dispathing thread starts", len(
            [x for x in db_man.all_enabled_build_options()]))
        for build_opt in db_man.all_enabled_build_options():
            logging.info("boot dispatching thread for %d ...", build_opt.id)
            if build_opt.platform == 'linux':
                t_dispatch_list.append(threading.Thread(
                    target=self.__dispatch_task, args=(build_opt.id, False)))
            else:
                t_dispatch_list.append(threading.Thread(
                    target=self.__dispatch_task, args=(build_opt.id, True)))
        t_rpc = threading.Thread(target=self.__rpc)
        # t_ddisasm = threading.Thread(target=self.__disasm_task)
        t_consume_clone = threading.Thread(target=self.__consume_clone)
        t_consume_build = threading.Thread(target=self.__consume_build)
        t_consume_binary = threading.Thread(target=self.__consume_binary)
        t_scrape = threading.Thread(target=self.__consume_scraped_data)
        t_clean_task = threading.Thread(target=self.__clean_overtime)
        t_clean_worker = threading.Thread(target=self.__clean_worker)
        t_recycle_worker = threading.Thread(target=self.__recycle_clone)
        t_reboot_worker = threading.Thread(target=self.__reboot_worker)
        t_daemon = threading.Thread(target=self.__daemon)
        logging.info("Processes ready")
        t_clean_worker.start()
        t_clean_task.start()
        for t_dispatch in t_dispatch_list:
            t_dispatch.start()
        t_rpc.start()
        t_recycle_worker.start()
        t_consume_clone.start()
        t_consume_build.start()
        t_consume_binary.start()
        t_scrape.start()
        t_reboot_worker.start()
        logging.info("Threads joining")
        t_clean_task.join()
        t_rpc.join()
        for t_dispatch in t_dispatch_list:
            t_dispatch.join()
        t_scrape.join()
        t_clean_worker.join()
        t_consume_binary.join()
        t_consume_clone.join()
        t_consume_build.join()
        t_recycle_worker.join()
        t_reboot_worker.join()
        t_daemon.join()

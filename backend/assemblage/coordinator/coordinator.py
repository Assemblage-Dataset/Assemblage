"""
Assemblage Coordinator/Server

Yihao Sun
"""

import os
from sqlite3 import connect
import sys
import threading
import time
import logging
import json
# from concurrent.futures import ThreadPoolExecutor
import pika
import boto3  # Only used in AWS mode
from pika.exchange_type import ExchangeType

from assemblage.data.db import DBManager
from collections import Counter
from assemblage.consts import (AWS_AUTO_REBOOT_PREFIX,
                               BIN_DIR, CLEAN_OVERTIME_INTERVAL, WORKER_TIMEOUT_THRESHOLD, BuildStatus,
                               REPO_SIZE_THRESHOLD, CloneStatus, InputQueue, OutputQueue,
                               CHANNEL_HEARTBEAT, CHANNEL_TIMEOUT, CHANNEL_CONNECTION_ATTEMPTS, CHANNEL_RETRY_DELAY,
                               DISPATCH_INTERVAL, IDLE_DISPATCH_INTERVAL, AWS_REBOOT_SLEEP_INTERVAL
                               )

from assemblage.config import CoordinatorSettings
from assemblage.mq.messages import BuilderRegIn, BuilderRegOut


logger = logging.getLogger(__name__)

def stop_the_world_excepthook(args):
    """ 
    this is a thread execption handler if an thread trigger this, no matter normal 
    exit or not will shutdown the how coordinator. In coordinator all thread should
    run forever!
    """
    sys.excepthook(args.exc_type, args.exc_value, args.exc_traceback)
    exit(1)


threading.excepthook = stop_the_world_excepthook
# TODO: this looks the same as the default excepthook, is this necessary?


def patch_url(_url):
    """ make a url cloneable """
    return _url.replace('repos/', '').replace('api.', '')


# Unused.
def unpatch_url(_url: str) -> str:
    """ make url searchable in db """
    api_index = _url.find("/")

    rest_url = _url[api_index + 2:]
    with_api = _url[:api_index + 2] + "api." + rest_url

    com_index = with_api.find(".com")

    rest_repos = with_api[com_index + 4:]
    unpatched = with_api[:com_index + 4] + "/repos" + rest_repos

    return unpatched

# TODO This is duplicated in rabbitmq and only slightly changed.


def create_channel(host, port, heartbeat=CHANNEL_HEARTBEAT, timeout=CHANNEL_TIMEOUT,
                   connection_attempts=CHANNEL_CONNECTION_ATTEMPTS, retry_delay=CHANNEL_RETRY_DELAY):
    """
    create a rabbit mq channel,
    this is blocking channel, since we are using single process worker
    don't do anything blocking before ack
    """
    # A blocking connection halts execution of the caller thread when an action on the channel
    # (e.g. connected, channel_open, exchange_declared, queue_declared) is called until it returns returns.
    conn_params = pika.ConnectionParameters(host=host, port=port,
                                            connection_attempts=connection_attempts, retry_delay=retry_delay,
                                            heartbeat=heartbeat, blocked_connection_timeout=timeout)
    conn = pika.BlockingConnection(conn_params)
    return conn.channel()


class Coordinator:
    """
    coordinator node, dispatch work to worker node and also collect data
    TODO: also configure creating separate RabbitMQ users for each service
    """

    # def __init__(self, rabbitmq_host, rabbitmq_port, db_addr, cluster_name, aws_mode=0, reproduce_mode=0):
    def __init__(self, settings: CoordinatorSettings):
        logger.info("Coordinator Init")
        self.rabbitmq_host = settings.mq_host
        self.rabbitmq_port = settings.mq_port
        self.channel = create_channel(self.rabbitmq_host, self.rabbitmq_port) # default channel. 
        # Do not use round-robin scheduling.
        self.channel.basic_qos(prefetch_count=1)

        # to recieve results about builder registration
        self.channel.queue_declare(queue=InputQueue.BUILD_REG, durable=True)
        # To receive results about cloning
        self.channel.queue_declare(queue=InputQueue.CLONE, durable=True)
        # To receive results about building
        self.channel.queue_declare(queue=InputQueue.BUILD, durable=True)
        # To receive results about scraping
        self.channel.queue_declare(queue=InputQueue.SCRAPE, durable=True)
        # To receive results about binaries
        self.channel.queue_declare(queue=InputQueue.BINARY, durable=True)
        
        # declare the exchange - is accessible by all 
        self.channel.exchange_declare(
                exchange='build_opt', exchange_type=ExchangeType.topic)
        self.db_addr = settings.databaseURL
        # to do create better session management
        self.db_man = DBManager(self.db_addr)
        # Appears to be used only in AWS mode for reboots
        self.cluster_name = settings.cluster_name
        self.reproduce_mode = settings.reproduce_mode
        self.aws_flag = settings.aws_mode

        self.t_dispatch_map_lock = threading.Lock()

        # list of dispatched job threads
        self.t_dispatch_map: dict[int, threading.Thread] = {}
        # setup rpc service

    def __del__(self):
        self.channel.close()  # ensure that channels are gracefully closed on deletion of object

    # This is the task that sends work to the builder.
    # 10/20/2025 minor change in functionality, dispatch now pauses for a short time between sends
    # rather than a long sleep every 1200 seconds
    def __dispatch_task(self, build_opt_id, sleep=True):
        """Sends unbuilt repositories to the worker by enqueueing them with RabbitMQ"""
        try:
            logger.info("__dispatch_task thread started on %s", build_opt_id)
            thread_channel = create_channel(
                self.rabbitmq_host, self.rabbitmq_port)
            # we use topics to control which worker gets which jobs.

            thread_channel.confirm_delivery()
            tasks = self.db_man.find_status_by_status_code(
                build_opt_id=build_opt_id,
                clone_status=CloneStatus.NOT_STARTED,
                build_status=BuildStatus.INIT,
                limit=99999)
            logger.info(
                "__dispatch_task started successfully, %s tasks are ready to be queued for dispatch on build_opt_%d", len(tasks), build_opt_id)
        except:
            logger.info("__dispatch_task start fail")
            exit(1)
        task_count = 0
        while True:
            try:
                # find an unstarted task
                time_before_query = time.time()
                tasks = self.db_man.find_status_by_status_code(
                    build_opt_id=build_opt_id,
                    clone_status=CloneStatus.NOT_STARTED,
                    build_status=BuildStatus.INIT,
                    limit=1)
                if len(tasks) == 0:
                    logger.info(
                        "Dispatch thread on build option %s idle", build_opt_id)
                    time.sleep(IDLE_DISPATCH_INTERVAL)
                    continue
                # extract task
                task = tasks[0]
                # get the rest of the necessary information from the other tables in database
                uncloned_repo = self.db_man.find_repo_by_id(task.repo_id)
                # if uncloned_repo.size < REPO_SIZƒE_THRESHOLD:
                #     logger.info("Discard task %s size %s", task.repo_id, uncloned_repo.size)
                #     continue
                build_opt = self.db_man.find_build_opt_by_id(task.build_opt_id)
                self.db_man.update_repo_status(
                    status_id=task.id, clone_status=CloneStatus.PROCESSING)
                time_after_query = time.time()
                repo_url = patch_url(uncloned_repo.url)
                out_dir = f'{BIN_DIR}/{task.id}' # dont think this is needed anymore
                # correction. later. would be good to replace this with the projectid from scrapes
                # only once the build and clone is fully fixed and reliable 
                
                # format a request to be sent to the builder/cloner
                clone_req = {'name': uncloned_repo.name, 'url': repo_url,
                             'task_id': task.id, 'opt_id': build_opt.id,
                             #  'commit_hexsha': task.commit_hexsha,
                             'output_dir': out_dir,
                             'repo_id': uncloned_repo.id,
                             'updated_at': uncloned_repo.updated_at.strftime("%m/%d/%Y, %H:%M:%S"),
                             'build_system': uncloned_repo.build_system,
                             #  also add timestamp when this messsage sent
                             'msg_time': time.time()}
                if self.reproduce_mode:
                    clone_req["mod_timestamp"] = task.mod_timestamp

                # Publish this task, to be picked up by a worker with the appropriate build option settings
                thread_channel.basic_publish(
                    exchange='build_opt', routing_key=f'builder.opt.{build_opt.id}',
                    body=json.dumps(clone_req),
                    properties=pika.BasicProperties(delivery_mode=2))

                # log progress
                if task_count % 10 == 0:
                    logger.info('Placed %sth task on build option %d in %ss', task_count,
                                task.build_opt_id, str(time_after_query - time_before_query)[:5])
                task_count += 1

                # sleep
                if sleep:
                    time.sleep(DISPATCH_INTERVAL)
            except Exception as e:
                logger.info("Dispatch Err:", err=str(e))
                thread_channel = create_channel(
                    self.rabbitmq_host, self.rabbitmq_port)
                thread_channel.exchange_declare(
                    exchange='build_opt', exchange_type=ExchangeType.topic)
                thread_channel.confirm_delivery()
                # db_man = DBManager(self.db_addr)

    # TODO: Possibly this runs occasionally at very long time scales, but I think this is a candidate for cutting
    # Appears to be a helper method for the old DB system
    def __recycle_clone(self):
        '''Runs a background thread which attempts to set certain failed clone attempts as ready to retry.'''
        # My understanding is this attempts to retry previously-failed repo clones,
        # setting their clone status to "not started".
        # TODO: is the count+= 1 code unreachable? (checks for clone status is both success then failure?)
        try:
            logger.info("Recycle thread starting")
        except:
            logger.info("Recycle start fail")
        while True:
            count = 0
            try:
                for repo in self.db_man.find_repo_by_status(build_status=BuildStatus.SUCCESS,
                                                            clone_status=CloneStatus.SUCCESS,
                                                            build_opt_id=None):
                    for b_status in self.db_man.find_status_by_repoid(repo.id):
                        if b_status.clone_status == CloneStatus.FAILED and b_status.build_status == BuildStatus.INIT:
                            self.db_man.update_repo_status(
                                status_id=b_status.id,
                                clone_status=CloneStatus.NOT_STARTED)
                            count += 1
                    if count % 100 == 0 and count != 0:
                        logger.info("Recycled %s tasks", count)
                time.sleep(1)
            except Exception as err:
                logger.info("Recycle thread err %s", err)
            time.sleep(1)

    # def __consume_binary(self):
    #     '''Consumes binaries sent on 'binary' by calling recv_binary() on each message.'''
    #     while True: # retries if process fails
    #         try:
    #             logger.info(
    #                 "Coordinator binary consume thread started")
    #             thread_channel = create_channel(
    #                 self.rabbitmq_host, self.rabbitmq_port)
    #             thread_channel.basic_consume(queue=QueueName.BINARY,
    #                                          on_message_callback=self.recv_binary)
    #             thread_channel.start_consuming()
    #             logger.critical("Consuming binary exited!")
    #         except Exception as err:
    #             logger.critical("Saving binary failed!")
    #             logger.critical(err)

    # def __consume_clone(self):
    #     while True:
    #         try:
    #             logger.info(
    #                 "Coordinator clone consume thread started")
    #             thread_channel = create_channel(
    #                 self.rabbitmq_host, self.rabbitmq_port)
    #             thread_channel.basic_consume(queue=QueueName.CLONE,
    #                                          on_message_callback=self.recv_clone_info)
    #             thread_channel.start_consuming()
    #             logger.critical("Consuming clone exited")
    #         except Exception as err:
    #             logger.critical("Saving clone failed!")
    #             logger.critical(err)

    # def __consume_build(self):
    #     while True:
    #         try:
    #             logger.info(
    #                 "Coordinator build consume thread started")
    #             thread_channel = create_channel(
    #                 self.rabbitmq_host, self.rabbitmq_port)
    #             thread_channel.basic_consume(queue=QueueName.BUILD,
    #                                          on_message_callback=self.recv_build_info)
    #             thread_channel.start_consuming()
    #             logger.critical("Consuming build exited")
    #         except Exception as err:
    #             logger.critical("Saving build failed!")
    #             logger.critical(err)

    # def __consume_scraped_data(self):
    #     while True:
    #         try:
    #             logger.info(
    #                 "Coordinator crawl consume thread started")
    #             thread_channel = create_channel(
    #                 self.rabbitmq_host, self.rabbitmq_port)
    #             thread_channel.basic_consume(queue=QueueName.SCRAPE,
    #                                          on_message_callback=self.recv_scrape_info)
    #             thread_channel.start_consuming()
    #             logger.critical("Consuming scrape exited")
    #         except Exception as err:
    #             logger.critical("Saving scraped repo failed!")
    #             logger.critical(err)

    def __consume_from_queue(self, queue):
        logger.info(f"consuming from {queue}")
        match queue:
            case InputQueue.SCRAPE:
                callback = self.recv_scrape_info
            case InputQueue.CLONE:
                callback = self.recv_clone_info
            case InputQueue.BUILD:
                callback = self.recv_build_info
            case InputQueue.BINARY:
                callback = self.recv_binary
            case InputQueue.BUILD_REG:
                callback = self.recv_builder_registration
            case _:
                logger.error(
                    f"Error: queue type '%s' is not defined in __consume_from_queue", queue)
                callback = None
                return

        while True:
            try:
                logger.info(
                    "Consume thread on queue '%s' started in coordinator", queue)
                # Create a channel and listen on the relevant queue
                thread_channel = create_channel(
                    self.rabbitmq_host, self.rabbitmq_port)
                thread_channel.basic_consume(
                    queue=queue, on_message_callback=callback)
                thread_channel.start_consuming()
                logger.critical("Consume thread '%s' exited", queue)
            except Exception as err:
                logger.critical(
                    "Coordinator __consume_from_queue from queue '%s' failed!", queue)
                logger.critical(err)

    # TODO: another candidate for cutting?

    def __clean_overtime(self):
        ''' restore all overtime repo every 2 build circle '''
        self.db_man = DBManager(self.db_addr)
        while True:
            time.sleep(CLEAN_OVERTIME_INTERVAL)
            self.db_man.reset_timeout_status(CLEAN_OVERTIME_INTERVAL)
            logger.info(">>>>>>>>>>>>>>>>>>>>>> cleanning overtime"
                        " tasks ......")

    def __reboot_worker(self):
        ''' reboot worker every hr, only in aws mode '''
        if not self.aws_flag:
            return
        sesh = boto3.Session(profile_name='assemblage')
        ec2_resource = sesh.resource('ec2')
        ec2_client = sesh.client('ec2')
        sleep_time = AWS_REBOOT_SLEEP_INTERVAL
        while 1:
            reboot_instance_ids = []
            for instance in ec2_resource.instances.all():
                if instance.tags:
                    for tag in instance.tags:
                        cluster_auto_prefix = f"{self.cluster_name}-{AWS_AUTO_REBOOT_PREFIX}"
                        if tag['Key'] == 'Name' and (cluster_auto_prefix in tag['Value']):
                            reboot_instance_ids.append(instance.id)
            if reboot_instance_ids != []:
                response = ec2_client.reboot_instances(
                    InstanceIds=reboot_instance_ids, DryRun=False)
                logger.info("Rebooting %s vms msg %s",
                            len(reboot_instance_ids), response)
            for _ in reboot_instance_ids:
                for i in range(int(sleep_time/60)):
                    logger.info("%s min to next reboot", sleep_time/60-i)
                    time.sleep(60)

    # The callback, according to Pika's requirements, takes four arguments: the channel that the message was received on,
    # delivery metadata, properties, and the message body.

    def recv_scrape_info(self, ch: pika.channel.Channel, method: pika.spec.Basic.Deliver, _props: pika.BasicProperties, body):
        ''' store scraped message to database page by page '''
        logger.info("Crawled msg received")
        ch.basic_ack(delivery_tag=method.delivery_tag)
        prev_time = time.time()
        recv_msg = json.loads(body.decode())
        successes = 0
        result = 0
        for onerepo in recv_msg:
            result = self.db_man.insert_repos(onerepo)
            successes += result
        if result == 0:
            logger.debug("%s inserted err", recv_msg[-1]['url'])
        after_time = time.time()
        logger.info("Build system counter %s", Counter(
            x['build_system'] for x in recv_msg))
        logger.info("Saved %s/%s repos in %ss", successes,
                    len(recv_msg), int(after_time-prev_time))

    def recv_binary(self, ch, method, _props, body):
        """ collect binary metadata from worker"""
        recv_msg = json.loads(body.decode())

        self.db_man.insert_binary(
            file_name=recv_msg['file_name'],
            description='',
            status_id=recv_msg['task_id']
        )
        ch.basic_ack(delivery_tag=method.delivery_tag)

    def recv_build_info(self, ch, method, _props, body):
        """ collect and update build status of a task """
        recv_msg = json.loads(body.decode())
        # task = db_man.find_status_by_id(recv_msg['task_id'])
        if BuildStatus(recv_msg['status']) == BuildStatus.OUTDATED_MSG:
            logger.info("discarding an timeout build msg %s", body.decode())
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return
        task = self.db_man.find_status_by_id(recv_msg['task_id'])
        if task.clone_status != BuildStatus.SUCCESS:
            print(">>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>> Clone failed but still built!")
        self.db_man.update_repo_status(
            status_id=recv_msg['task_id'],
            build_time=recv_msg['build_time'],
            build_status=BuildStatus(recv_msg['status']),
            build_msg=recv_msg['msg'][-500:],
            commit_hexsha=recv_msg['commit_hexsha'])
        logger.info("BUILD task on buildopt %s updated to %s: %s",
                    recv_msg['opt_id'], recv_msg['status'], " ".join(recv_msg['msg'].split())[-500:])
        ch.basic_ack(delivery_tag=method.delivery_tag)

    def recv_clone_info(self, ch, method, _props, body):
        """ collect and update clone stsatus of a task """
        self.db_man = DBManager(self.db_addr)
        recv_msg = json.loads(body.decode())
        # if the status code is timeout discard it
        if recv_msg['status'] == BuildStatus.OUTDATED_MSG:
            logger.info("discarding an timeout clone msg %s", body.decode())
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return
        self.db_man.update_repo_status(
            status_id=recv_msg['task_id'],
            clone_status=BuildStatus(recv_msg['status']),
            clone_msg=recv_msg['msg'][-200:])
        task = self.db_man.find_status_by_id(recv_msg['task_id'])
        if task.clone_status != BuildStatus.SUCCESS:
            logger.info("CLONE task on buildopt %s updated to %s: %s",
                        recv_msg['opt_id'], task.clone_status, recv_msg['msg'])
        ch.basic_ack(delivery_tag=method.delivery_tag)

    def recv_builder_registration(self, ch, method, props, body):
        '''
        This function receives a registration from the builder. 
        On first connection to the coordinator, the builder sends a message containing what buildoptions it is using
        If it doesnt exist in the database already, the build option is inserted into the table. 
        Then a queue is spun up, and the coordinator will message the builder telling it the name of the queue to listen on for 
        build instructions
        '''

        reg_info: BuilderRegIn = BuilderRegIn.from_json(body)
        logger.info(f"Recieved registration request from builder: {reg_info.name}, intending to compile {reg_info.language} on {reg_info.platform}:{reg_info.library}")

        # search for build opt

        build_opt_id = self.db_man.register_build_opt(reg_info)

        ch.basic_publish(
            exchange='',
            routing_key=props.reply_to,
            properties=pika.BasicProperties(
                correlation_id=props.correlation_id  # echo back
            ),
            body=BuilderRegOut(build_opt_id).to_json()
        )

        ch.basic_ack(delivery_tag=method.delivery_tag)
        with self.t_dispatch_map_lock:
            alive_count = sum(t.is_alive()
                              for t in self.t_dispatch_map.values())
            existing = self.t_dispatch_map.get(build_opt_id)
            if existing and existing.is_alive():
                logger.info(
                    f"New builder registered, build opt thread {build_opt_id} already running. Currently running {alive_count} build opt threads")
                return

            logger.info("boot dispatching thread for %d ...", build_opt_id)
            new_build_opt_t = threading.Thread(
                target=self.__dispatch_task, args=(build_opt_id, True))
            new_build_opt_t.start()
            # add to list for management. maybe ( do we need some mutex on this...)
            self.t_dispatch_map[build_opt_id] = new_build_opt_t
            logger.info(f"Now running {alive_count+1} build opt threads")

    def __daemon(self):
        while True:
            time.sleep(1)

    def run(self):
        """
        Run various threads for interacting with queues and RPC.
        """
        try:
            os.remove("/tmp/setup_complete.txt")
        except OSError:
            pass

        while True:
            try:
                if self.db_man.tables_exist():
                    break
                else:
                    logger.warning('''No tables in database.
                                        Please use docker exec -it assemblage-coordinator-1;
                                        alembic upgrade head.
                                        To create the database to the latest revision. 
                                        Please note you may have to run docker compose up -d again to start the other containers''')
                    time.sleep(10)
            except:
                logger.error("error checking if tables exist")

        # we only want to create threads when a builder is actually registered. so the builder has to register,
        # and the thread will be created when it registers
        # logger.info("%s dispatching thread starts", len(
        #     [x for x in self.db_man.all_enabled_build_options()]))

        # # Create a dispatch thread for each build option configuration
        # for build_opt in self.db_man.all_enabled_build_options():
        #     logger.info("boot dispatching thread for %d ...", build_opt.id)
        #     self.t_dispatch_map.append(threading.Thread(
        #         target=self.__dispatch_task, args=(build_opt.id, True)))

        # t_ddisasm = threading.Thread(target=self.__disasm_task)
        # t_consume_clone = threading.Thread(target=self.__consume_clone)
        # t_consume_build = threading.Thread(target=self.__consume_build)
        # t_consume_binary = threading.Thread(target=self.__consume_binary)
        # t_scrape = threading.Thread(target=self.__consume_scraped_data)

        # t_consume_config = threading.Thread(self.__consume_from_queue, args=(QueueName.CONFIG,))
        t_consume_clone = threading.Thread(
            target=self.__consume_from_queue, args=(InputQueue.CLONE,))
        t_consume_build = threading.Thread(
            target=self.__consume_from_queue, args=(InputQueue.BUILD,))
        t_consume_binary = threading.Thread(
            target=self.__consume_from_queue, args=(InputQueue.BINARY,))
        t_scrape = threading.Thread(
            target=self.__consume_from_queue, args=(InputQueue.SCRAPE,))
        t_consume_build_reg = threading.Thread(
            target=self.__consume_from_queue, args=(InputQueue.BUILD_REG,))

        t_clean_task = threading.Thread(target=self.__clean_overtime)
        t_recycle_worker = threading.Thread(target=self.__recycle_clone)
        t_reboot_worker = threading.Thread(target=self.__reboot_worker)
        t_daemon = threading.Thread(target=self.__daemon)
        logger.info("Processes ready")
        with open("/tmp/setup_complete.txt", "w") as f:
            f.write("done")
        t_clean_task.start()
        for t_dispatch in self.t_dispatch_map:
            t_dispatch.start()
        t_recycle_worker.start()
        t_consume_clone.start()
        t_consume_build.start()
        t_consume_binary.start()
        t_scrape.start()
        t_consume_build_reg.start()
        t_reboot_worker.start()
        logger.info("Threads joining")
        t_clean_task.join()
        for t_dispatch in t_dispatch_map:
            t_dispatch.join()
        t_scrape.join()
        t_consume_binary.join()
        t_consume_clone.join()
        t_consume_build.join()
        t_recycle_worker.join()
        t_reboot_worker.join()
        t_daemon.join()

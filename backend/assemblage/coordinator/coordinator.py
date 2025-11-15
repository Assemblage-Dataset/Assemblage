"""
Assemblage Coordinator/Server

Yihao Sun
"""

import os
import sys
import threading
import time
import logging
import json
# from concurrent.futures import ThreadPoolExecutor
import pika
# from pika.exchange_type import ExchangeType

from assemblage.data.db import DBManager
from collections import Counter
from assemblage.consts import (AWS_AUTO_REBOOT_PREFIX, COORDINATOR_DATABASE_SYNC_TIMEOUT,
                               BIN_DIR, CLEAN_OVERTIME_INTERVAL, WORKER_TIMEOUT_THRESHOLD, BuildStatus,
                               REPO_SIZE_THRESHOLD, CloneStatus, InputQueue, OutputQueue,
                               DISPATCH_INTERVAL, IDLE_DISPATCH_INTERVAL, AWS_REBOOT_SLEEP_INTERVAL
                               )

from assemblage.config import CoordinatorSettings
from assemblage.mq.messages import BuilderRegIn, BuilderRegOut, ScraperDataOutBundle, ScraperDataOutSingle
from assemblage.mq.client import MQQueue, MessageClient, Connection
from assemblage.s3.client import S3Client, S3Bucket


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

# NOTE: if we want to get rid of this function, the api does provide this url (as 'html_url') so we can
# pass that along from the scraper


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


class Coordinator:
    """
    coordinator node, dispatch work to worker node and also collect data
    TODO: also configure creating separate RabbitMQ users for each service
    """

    # def __init__(self, rabbitmq_host, rabbitmq_port, db_addr, cluster_name, aws_mode=0, reproduce_mode=0):
    def __init__(self, settings: CoordinatorSettings):
        logger.info("Coordinator Init")
        logger.debug(f"Settings: {settings}")
        self.mq_client = MessageClient(settings.mq_host, settings.mq_port,
                                       username='guest', password='guest')

        self.db_addr = settings.databaseURL
        # to do create better session management
        self.db_man = DBManager(self.db_addr)

        # Appears to be used only in AWS mode for reboots
        self.cluster_name = settings.cluster_name
        self._create_buildopt_exchange()

        self.reproduce_mode = settings.reproduce_mode
        self.aws_flag = settings.aws_mode

        self.t_dispatch_map_lock = threading.Lock()

        # list of dispatched job threads
        self.t_dispatch_map: dict[int, threading.Thread] = {}


        if settings.s3_enabled:
            # settings.validate_s3()
            self.s3_client = S3Client(host=settings.S3_HOST,port=settings.S3_PORT, access_key=settings.S3_ACCESS_KEY,
                                    secret_access_key=settings.S3_SECRET_ACCESS_KEY, https=settings.S3_HTTPS, region_name=settings.S3_REGION)
            # coordindator creates but then only needs read only ( unless used to delete ) - leave for now.  
            # stores cloned projects
            self.ProjectBucket = S3Bucket(self.s3_client, "project-archive")
            # store build artifacts
            self.ArtifactBucket = S3Bucket(self.s3_client, "artifacts")
        else: 
            self.s3_client = None
            self.ProjectBucket = None
            self.ArtifactBucket = None

    def __str__(self):
        return f'Coordinator-{self.cluster_name}'


    def _create_buildopt_exchange(self):

        # This channel is created exclusively to add the topic exchange
        conn: Connection = self.mq_client.create_connection(
            conn_name=f'{self}-build-opt', channel_name=f'{self}-build-opt')
        conn.create_channel()
        conn.add_topic_exchange('build_opt')
        conn.close()

    # def __del__(self):
    #     self.channel.close()  # ensure that channels are gracefully closed on deletion of object

    # This is the task that sends work to the builder.
    # 10/20/2025 minor change in functionality, dispatch now pauses for a short time between sends
    # rather than a long sleep every 1200 seconds
    def __dispatch_task(self, build_opt_id, sleep=True):
        """Sends unbuilt repositories to the worker by enqueueing them with RabbitMQ"""
        try:
            logger.info(
                "__dispatch_task thread on buildopt %s initializing...", build_opt_id)

            conn: Connection = self.mq_client.create_connection(
                conn_name=f'{self}', channel_name=f'{self}')
            # workaround for issue mentioned in todo below
            _thread_channel = conn.create_channel()
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
                # correction. later. would be good to replace this with the projectid from scrapes
                # only once the build and clone is fully fixed and reliable

                # format a request to be sent to the builder/cloner
                clone_req = {'name': uncloned_repo.name, 'url': repo_url,
                             'task_id': task.id, 'opt_id': build_opt.id,
                             #  'commit_hexsha': task.commit_hexsha,
                             'repo_id': uncloned_repo.id,
                             'updated_at': uncloned_repo.updated_at.strftime("%m/%d/%Y, %H:%M:%S"),
                             'build_system': uncloned_repo.build_system,
                             #  also add timestamp when this messsage sent
                             'msg_time': time.time()}
                if self.reproduce_mode:
                    clone_req["mod_timestamp"] = task.mod_timestamp

                # Publish this task, to be picked up by a worker with the appropriate build option settings
                _thread_channel.basic_publish(
                    exchange='build_opt', routing_key=f'builder.opt.{build_opt.id}',
                    body=json.dumps(clone_req),
                    properties=pika.BasicProperties(delivery_mode=2))
                # TODO: need messageclient function that can publish to exchange w/out queue
                # conn.send_msg_on_exchange(
                #     routing_key=f'builder.{build_opt.id}',
                #     msg=json.dumps(clone_req),
                #     exchange='build_opt'
                # )

                # log progress
                if task_count % 10 == 0:
                    logger.info('Placed %sth task on build option %d in %ss', task_count,
                                task.build_opt_id, str(time_after_query - time_before_query)[:5])
                task_count += 1

                # sleep
                if sleep:
                    time.sleep(DISPATCH_INTERVAL)
            except Exception as e:
                logger.info(f"Dispatch Err:  {e}")
                # _thread_channel = conn.create_channel() # should work but not tested yet
                # _thread_channel = create_channel(
                #     self.rabbitmq_host, self.rabbitmq_port)
                # _thread_channel.exchange_declare(
                #     exchange='build_opt', exchange_type=ExchangeType.topic)
                # _thread_channel.confirm_delivery()
                break  # this should fail, and then be reopened once the builder re registers

        logger.info(f"__dispatch_task Build Opt {build_opt_id} exiting...")
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

    def __consume_from_queue(self, queue):
        logger.info(f"__consume_from_queue on {queue} init...")
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
                conn: Connection = self.mq_client.create_connection(
                    conn_name=f'{self}-{queue}', channel_name=f'{self}-{queue}')

                if conn.conn.is_closed:
                    logger.warning("The connection was never opened!")
                conn.create_channel()
                queue_object = MQQueue(name=queue, callback=callback)
                self.mq_client.start_consumer(
                    conn=conn, queue=queue_object, retry_delay=10)
                logger.critical("Consume thread '%s' exited", queue)
            except Exception as err:
                logger.critical(
                    "Coordinator __consume_from_queue from queue '%s' failed!", queue)
                logger.critical(err)

    # TODO: another candidate for cutting?

    def __clean_overtime(self):
        ''' restore all overtime repo every 2 build circle '''
        while True:
            time.sleep(CLEAN_OVERTIME_INTERVAL)
            self.db_man.reset_timeout_status(CLEAN_OVERTIME_INTERVAL)
            logger.info(">>>>>>>>>>>>>>>>>>>>>> cleanning overtime"
                        " tasks ......")

    # def __reboot_worker(self):
    #     ''' reboot worker every hr, only in aws mode '''
    #     if not self.aws_flag:
    #         return
    #     sesh = boto3.Session(profile_name='assemblage')
    #     ec2_resource = sesh.resource('ec2')
    #     ec2_client = sesh.client('ec2')
    #     sleep_time = AWS_REBOOT_SLEEP_INTERVAL
    #     while 1:
    #         reboot_instance_ids = []
    #         for instance in ec2_resource.instances.all():
    #             if instance.tags:
    #                 for tag in instance.tags:
    #                     cluster_auto_prefix = f"{self.cluster_name}-{AWS_AUTO_REBOOT_PREFIX}"
    #                     if tag['Key'] == 'Name' and (cluster_auto_prefix in tag['Value']):
    #                         reboot_instance_ids.append(instance.id)
    #         if reboot_instance_ids != []:
    #             response = ec2_client.reboot_instances(
    #                 InstanceIds=reboot_instance_ids, DryRun=False)
    #             logger.info("Rebooting %s vms msg %s",
    #                         len(reboot_instance_ids), response)
    #         for _ in reboot_instance_ids:
    #             for i in range(int(sleep_time/60)):
    #                 logger.info("%s min to next reboot", sleep_time/60-i)
    #                 time.sleep(60)

    # The callback, according to Pika's requirements, takes four arguments: the channel that the message was received on,
    # delivery metadata, properties, and the message body.

    def recv_scrape_info(self, ch: pika.channel.Channel, method: pika.spec.Basic.Deliver, _props: pika.BasicProperties, body):
        ''' store scraped message to database page by page '''
        logger.info("Crawled msg received")
        ch.basic_ack(delivery_tag=method.delivery_tag)
        start_time = time.time()
        successes = 0
        result = 0
        bundle = ScraperDataOutBundle.from_json(body.decode())
        for repo in bundle:
            # must convert repo from ScrapedDataOutSingle to dict
            result = self.db_man.insert_repos(repo.to_dict())
            successes += result
        if result == 0:
            logger.info(f"{bundle.repos[0].url} inserted err")
        logger.info(
            f"Build system counter {Counter(x.build_system for x in bundle)}", )
        logger.info(
            f"Saved {successes}/{len(bundle)} repos in {round(time.time()-start_time, 2)}s")

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
            logger.info("discarding a timeout build msg %s", body.decode())
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return
        task = self.db_man.find_status_by_id(recv_msg['task_id'])
        if task.clone_status != CloneStatus.SUCCESS:
            # If building is extremely quick, there's a small chance that build info will be sent
            # before the clone status is even updated in the database, so wait for sync if the status is unexpected.
            # Removing this code won't break anything as of writing, but could introduce bugs in the future.
            if task.clone_status in [CloneStatus.NOT_STARTED, CloneStatus.PROCESSING]:
                timeout = COORDINATOR_DATABASE_SYNC_TIMEOUT
                logger.info("Waiting for database sync...")
                while (timeout > 0 and task.clone_status in [CloneStatus.NOT_STARTED, CloneStatus.PROCESSING]):
                    # relatively long wait time to reduce required db accesses
                    time.sleep(1)
                    timeout -= 1
                    task = self.db_man.find_status_by_id(recv_msg['task_id'])
            if task.clone_status != CloneStatus.SUCCESS:  # sync attempt timed out or clone was a failure
                logger.warning(
                    f"Clone failed but still built: repo id {task.repo_id}")
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
        """ collect and update clone status of a task """
        recv_msg = json.loads(body.decode())
        # if the status code is timeout discard it
        if recv_msg['status'] == BuildStatus.OUTDATED_MSG:
            logger.info("discarding a timeout clone msg %s", body.decode())
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
        logger.info(
            f"Recieved registration request from builder: {reg_info.name}, intending to compile {reg_info.language} on {reg_info.platform}:{reg_info.library}")
        # search for build opt
        logger.debug(
            f"Will be replying to {props.reply_to} with corr_id : {props.correlation_id}")

        build_opt_id = self.db_man.register_build_opt(reg_info)

        conn: Connection = self.mq_client.create_connection(
            conn_name=f'{self}-builder-ctrl', channel_name=f'{self}-builder-ctrl')
        queue = MQQueue(OutputQueue.BUILDER_CTRL)

        conn.send_msg(queue=queue, msg=BuilderRegOut(build_opt_id).to_json(),
                      exchange="",
                      reply_to=props.reply_to,
                      corr_id=props.correlation_id
                      )

        # conn.send_msg(
        #     exchange='',
        #     routing_key=props.reply_to,
        #     properties=pika.BasicProperties(
        #         correlation_id=props.correlation_id  # echo back
        #     ),
        #     body=BuilderRegOut(build_opt_id).to_json()
        # )
        # ch.basic_publish(
        #     exchange='',
        #     routing_key=props.reply_to,
        #     properties=pika.BasicProperties(
        #         correlation_id=props.correlation_id  # echo back
        #     ),
        #     body=BuilderRegOut(build_opt_id).to_json()
        # )

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
            # note: the comma is important to parse args as tuple
            target=self.__consume_from_queue, args=(InputQueue.CLONE,))
        t_consume_build = threading.Thread(
            target=self.__consume_from_queue, args=(InputQueue.BUILD,))
        t_consume_binary = threading.Thread(
            target=self.__consume_from_queue, args=(InputQueue.BINARY,))
        t_consume_scrape = threading.Thread(
            target=self.__consume_from_queue, args=(InputQueue.SCRAPE,))
        t_consume_build_reg = threading.Thread(
            target=self.__consume_from_queue, args=(InputQueue.BUILD_REG,))

        t_clean_task = threading.Thread(target=self.__clean_overtime)
        t_recycle_worker = threading.Thread(target=self.__recycle_clone)
        # t_reboot_worker = threading.Thread(target=self.__reboot_worker)
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
        t_consume_scrape.start()
        t_consume_build_reg.start()
        # t_reboot_worker.start()
        t_daemon.start()
        logger.info("Threads joining")
        # TODO: No code beyond this point should be run
        t_clean_task.join()
        for t_dispatch in self.t_dispatch_map:
            t_dispatch.join()
        t_consume_scrape.join()
        t_consume_binary.join()
        t_consume_clone.join()
        t_consume_build.join()
        t_recycle_worker.join()
        t_reboot_worker.join()
        t_daemon.join()

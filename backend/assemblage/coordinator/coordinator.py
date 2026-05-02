"""
Assemblage Coordinator/Server

Yihao Sun
"""

import os
import sys
import threading
import time
from datetime import datetime , timezone
import logging
import json
# from concurrent.futures import ThreadPoolExecutor
import pika
# from pika.exchange_type import ExchangeType

from assemblage.data.db import DBManager
from assemblage.data.initialize_database import conditional_init_db
from collections import Counter
from assemblage.consts import (AWS_AUTO_REBOOT_PREFIX, COORDINATOR_DATABASE_SYNC_TIMEOUT,
                               BIN_DIR, CLEAN_OVERTIME_INTERVAL,  BuildStatus, #WORKER_TIMEOUT_THRESHOLD,
                               CloneStatus, InputQueue, OutputQueue, ScraperMsgType, ScraperOutputPolicy,  #REPO_SIZE_THRESHOLD,
                               DISPATCH_INTERVAL, IDLE_DISPATCH_INTERVAL, AWS_REBOOT_SLEEP_INTERVAL,
                               COORDINATOR_REPO_REQUEST_THRESHOLD, SCRAPER_REPO_BUNDLESIZE, WAIT_AFTER_REQ_INTERVAL,
                               )

from assemblage.config import CoordinatorSettings
from assemblage.mq.messages import (
    BuilderRegIn, BuilderRegOut, ScraperDataOutBundle, BuilderTaskOut, CloneStatusMsgIn, BuildStatusMsgIn, BinaryTaskMsgIn,
    ScraperControlTaskOut, ScraperControlTaskIn
    )
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
        self.db_name = settings.db_name
        # to do create better session management
        self.db_man = DBManager(self.db_addr)

        # Used for status updates
        self.info_successes = 0
        self.info_failures = 0

        # Appears to be used only in AWS mode for reboots
        self.cluster_name = settings.cluster_name
        self._create_buildopt_exchange()
        self._dispatch_queue_map : dict[int, MQQueue]  = {}  # To be set by dispatch thread

        self.reproduce_mode = settings.reproduce_mode
        self.aws_flag = settings.aws_mode

        self.t_dispatch_map_lock = threading.Lock()

        # list of dispatched job threads
        self.t_dispatch_map: dict[int, threading.Thread] = {}
        
        self.t_empty_built_opt_lock = threading.Lock()
        self.t_empty_built_opt: dict[int, threading.Event] = {}
        
        # records if any build option has requested repos
        # True while waiting for repos but before they've been received
        self._pending_repos : bool = False
        self._pending_repos_time : int = 0

        # per-scraper reply queues (populated during registration)
        self._scraper_queues_lock = threading.Lock()
        self._scraper_queues: list[str] = []
        

        if settings.s3_enabled:
            # settings.validate_s3()
            self.s3_client = S3Client(host=settings.S3_HOST, port=settings.S3_PORT, access_key=settings.S3_ACCESS_KEY,
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
            conn_name=f'{self}-build_opt', channel_name=f'{self}-build_opt')
        conn.create_channel()
        conn.add_topic_exchange(f"{OutputQueue.BUILD_OPT}")
        conn.close()

    # def __del__(self):
    #     self.channel.close()  # ensure that channels are gracefully closed on deletion of object

    # This is the task that sends work to the builder.
    # 10/20/2025 minor change in functionality, dispatch now pauses for a short time between sends
    # rather than a long sleep every 1200 seconds
    def __dispatch_task(self, build_opt_id, sleep=True, only_run_once=False): # last arg is for tests
        """Sends unbuilt repositories to the worker by enqueueing them with RabbitMQ"""
        try:
            logger.debug("__dispatch_task thread on buildopt %s initializing...", build_opt_id)
            
            self._dispatch_queue_map[build_opt_id] = MQQueue( name= f'{OutputQueue.BUILD_OPT}_{build_opt_id}', exchange_name=f'{OutputQueue.BUILD_OPT}', routing_key=f'{OutputQueue.BUILD_OPT}_{build_opt_id}')
            conn: Connection = self.mq_client.create_connection(conn_name=f'{self}-build-opt-{build_opt_id}', channel_name=f'{self}-build-opt-{build_opt_id}')
            # control_conn: Connection = self.mq_client.create_connection(conn_name=f'{self}-scraper-ctrl', channel_name=f'{self}-scraper-ctrl')
            with self.t_empty_built_opt_lock:
                if build_opt_id not in self.t_empty_built_opt:
                    self.t_empty_built_opt[build_opt_id] = threading.Event()
            num_tasks = self.db_man.get_tasks_to_dispatch_on_opt(build_opt_id)
            
            logger.info(
                "__dispatch_task started successfully, %s tasks are ready to be queued for dispatch on build_opt_%d", num_tasks, build_opt_id)
        except:
            logger.info("__dispatch_task start fail")
            exit(1)
        task_count = 0
        while True:
            
            try:
                conn.ensure_connection()
                task_count += self._dispatch_to_builder(
                    build_opt_id, conn, sleep, task_count
                )
            except Exception as e:
                logger.error(f"Build opt id : {build_opt_id} Dispatch Err:  {e}")
                
                # try to restart thread in case this was a fluke
                # break
            if only_run_once:
                break
        logger.warning(f"__dispatch_task Build Opt {build_opt_id} exiting...")

    def _dispatch_to_builder( self, build_opt_id, 
            conn : Connection, 
            sleep : bool, task_count : int ):
        '''
            Look for and, if present, dispatch unstarted tasks from database to this 
            thread's build option channel. If no tasks are present to be dispatched,
            check if more repositories need to be requested. 
            build_opt_id: the build option of the worker(s) this thread sends to
            conn: the connection used for publishes
            control_conn: the connection used for requesting bundles from the scraper
            sleep: whether this process should sleep a bit between dispatches
            task_count: for keeping track of this thread's total dispatches
        '''

        # TODO the queue name here should be linked more permanently to the builder's input queue
        # routing key is also slightly reduntant here unless we change to have a single build_opt queue with routing keys wiithin?
        # or do we want to change the routing key to be the optimization for example if we break out that way? 
        builder_receive_queue = MQQueue(f"{OutputQueue.BUILD_OPT}_{build_opt_id}", routing_key=f'{OutputQueue.BUILD_OPT}_{build_opt_id}')
        messages_on_buildopt = conn.get_queue(builder_receive_queue).method.message_count

        # find an unstarted task
        build_message = self.db_man.get_dispatch_task(build_opt_id, self.reproduce_mode)

        # rabbitmq performance allegedly is much better with shorter queues. 
        # so only enqueue messages when necessary
        if messages_on_buildopt > COORDINATOR_REPO_REQUEST_THRESHOLD:
            time.sleep(DISPATCH_INTERVAL)
            return 0
        
        if build_message is None:  # no more scraped repos to dispatch. determine whether to idle or request repos

            # if there are not many messages waiting to be consumed, request more repos 
            if messages_on_buildopt <= COORDINATOR_REPO_REQUEST_THRESHOLD:
                #logger.info(f"Dispatch thread on build option {build_opt_id} requesting {SCRAPER_REPO_BUNDLESIZE} more repos from any scraper...")
                
                logger.debug( f"Dispatch thread on build option {build_opt_id} waiting for dispatch request to be sent" )
                with self.t_empty_built_opt_lock:
                    self.t_empty_built_opt[build_opt_id].set()
                
                time.sleep(WAIT_AFTER_REQ_INTERVAL)
            else:
                logger.info( f"Dispatch thread on build option {build_opt_id} idling ({messages_on_buildopt} tasks waiting to be built)" )
                time.sleep(IDLE_DISPATCH_INTERVAL)
            return 0
        
        else:
            
            # # Publish this task, to be picked up by a worker with the appropriate build option settings
            conn.send_msg(queue=self._dispatch_queue_map[build_opt_id], 
                          msg=build_message.to_json().encode(),
                          exchange=f"{OutputQueue.BUILD_OPT}")
            
            self.db_man.update_repo_status( status_id=build_message.task_id, clone_status=CloneStatus.PROCESSING )

            # log progress
            if task_count % 100 == 0:
                logger.info(f'Placed {task_count}th task on build option {build_opt_id}')

            # sleep
            if sleep:
                time.sleep(DISPATCH_INTERVAL)
            
            return 1
        

    def _request_repos(self, build_opt_id: int, conn: Connection):
            '''
                Signals to any available scraper that the coordinator has run out of repositories to dispatch.
                If all scrapers use the on_request policy, this function must be called in order to receive repos.
                Otherwise it's not necessary to ensure that it's called.
            '''

            if self._pending_repos:
                if self._pending_repos_time + 60 < int(time.time()):
                    # temporary bugfix: sometimes the repo request seems to get lost. 
                    # Have only seen it once: seems related to multiple buildopts consuming at different rates
                    # this at least prevents permanent timeouts until I can replicate it + fixx
                    logger.info("Previous repo request timed out. Sending another.")
                    pass
                else:
                    # another scraper beat this to the punch, or a valid request is still waiting: don't request yet
                    logger.debug("Waiting for repos...")
                    return
            try:
                build_opt_lang = self.db_man.get_build_opt_language(build_opt_id)
                # to do use the above to request
                msg = ScraperControlTaskOut(
                    message_type=ScraperMsgType.REQUEST_REPOS,
                    specific_recipient=False,
                    )

                self._pending_repos = True
                self._pending_repos_time = int(time.time())

                with self._scraper_queues_lock:
                    targets = list(self._scraper_queues) if self._scraper_queues else [OutputQueue.SCRAPER_CTRL]

                for queue_name in targets:
                    queue = MQQueue(queue_name)
                    conn.send_msg( queue=queue, msg=msg.to_json(), exchange="" )
                logger.info(f"Coordinator requesting repos on {build_opt_id}.")
            except Exception as e:
                logger.error(f"Failed to request repos: {e}") 
                

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

    def __scraper_recv_setup(self):
        '''
        Does necessary setup before listening for scraper registration requests
        '''

        self.db_man.ready_scraper_table()



    def __consume_from_queue(self, queue, only_run_once=False):  # only_run_once is for testing only
        logger.debug(f"__consume_from_queue on {queue} init...")
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
            case InputQueue.SCRAPER_REG:
                callback = self.recv_scraper_reg
                self.__scraper_recv_setup()
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
            
            if only_run_once:
                break

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

    def recv_scrape_info(self, ch: pika.channel.Channel, method: pika.spec.Basic.Deliver, props: pika.BasicProperties, body):
        ''' store scraped message to database page by page '''
        #logger.info("Crawled msg received")
        
        try: 
            self._pending_repos = False
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

            if ch and ch.is_open:
                ch.basic_ack(delivery_tag=method.delivery_tag)
                logger.info(f"Received {len(bundle)} / saved {successes} repos in {round(time.time()-start_time, 2)}s")

            else:
                logger.warning("Channel recv_scrape_info closed before ack, message will be redelivered")
                
            # maybe here clear the set flag on each build opt? would also require 
            #  scraperdataoutbunder from specifying the language and then clearing that way?
            # 
            
        except Exception as e:
            logger.error(f"Error processing recv_scrape_info: {e}")
            if ch and ch.is_open:
                try:
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                except Exception as nack_err:
                    logger.error(f"Failed to nack recv_scrape_info: {nack_err}")
        
        #logger.info(f"Build system counter {Counter(x.build_system for x in bundle)}", )
        
     
    def __run_builder_ctrl(self):
        '''
        Docstring for t_run_ctrl
        Running the control threads for builder and scraper , 
        Currently just create and ensure the ctrl connection is alive
        :param self: Description
        '''
        try: 
            conn: Connection = self.mq_client.create_connection(conn_name=f'{self}-{OutputQueue.BUILDER_CTRL}',
                                                                        channel_name=f'{self}-{OutputQueue.BUILDER_CTRL}',
                                                                        )
            while True: 
                conn.ensure_connection()
                time.sleep(30) 
        except Exception as e:
            logger.error(f"Builder Control thread failed on coordinator, exec={e}")   
            
            
    def __run_scraper_ctrl(self):
        '''
        Docstring for t_run_scraper_ctrl
        Running the control thread for the scraper. Monitor for flags from build options to request scraper to scrape
        
        :param self: Description
        '''
        try: 
            conn: Connection = self.mq_client.create_connection(conn_name=f'{self}-{OutputQueue.SCRAPER_CTRL}',
                                                                        channel_name=f'{self}-{OutputQueue.SCRAPER_CTRL}',
                                                                        )
            while True: 
                conn.ensure_connection()
                
                with self.t_empty_built_opt_lock:
                    for opt_id, event in self.t_empty_built_opt.items():
                        if event.is_set():
                            # request repo
                            self._request_repos(opt_id, conn)
                            event.clear() # maybe figure out a better way, ie only clear once repos are actually recieved?

                time.sleep(1) # check this every 30 seconds?

        except Exception as e:
            logger.error(f"Scraper Control thread failed on coordinator, exec={e}")
        
        

    def recv_binary(self, ch, method, _props, body):
        """ collect binary metadata from worker"""   
        
        try:      
            recv_msg = BinaryTaskMsgIn.from_json( body.decode() )

            self.db_man.insert_binary(
                file_name=recv_msg.file_name,
                description='',
                status_id=recv_msg.task_id,
            )
            if ch and ch.is_open:
                ch.basic_ack(delivery_tag=method.delivery_tag)
            else:
                logger.warning("Channel recv_binary closed before ack, message will be redelivered")
            
        except Exception as e:
            logger.error(f"Error processing recv_binary info: {e}")
            if ch and ch.is_open:
                try:
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                except Exception as nack_err:
                    logger.error(f"Failed to nack recv_binary info: {nack_err}")

    def recv_build_info(self, ch, method, _props, body):
        """ collect and update build status of a task """
        
        try: 
            recv_msg = BuildStatusMsgIn.from_json( body.decode() )
            # task = db_man.get_status_row_by_id(recv_msg['task_id'])
            if BuildStatus(recv_msg.status) == BuildStatus.OUTDATED_MSG:
                logger.debug("discarding a timeout build msg %s", body.decode())
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return
            task = self.db_man.get_status_row_by_id(recv_msg.task_id)
            if task.clone_status != CloneStatus.SUCCESS:
                # If building is extremely quick, there's a small chance that build info will be sent
                # before the clone status is even updated in the database, so wait for sync if the status is unexpected.
                # Removing this code won't break anything as of writing, but could introduce bugs in the future.
                if task.clone_status in [CloneStatus.NOT_STARTED, CloneStatus.PROCESSING]:
                    timeout = COORDINATOR_DATABASE_SYNC_TIMEOUT
                    logger.debug("Waiting for database sync...")
                    while (timeout > 0 and task.clone_status in [CloneStatus.NOT_STARTED, CloneStatus.PROCESSING]):
                        # relatively long wait time to reduce required db accesses
                        time.sleep(1)
                        timeout -= 1
                        task = self.db_man.get_status_row_by_id(recv_msg.task_id)
                if task.clone_status != CloneStatus.SUCCESS:  # sync attempt timed out or clone was a failure
                    logger.warning(
                        f"Clone failed but still built: repo id {task.repo_id}")
            self.db_man.update_repo_status(
                status_id=recv_msg.task_id,
                build_time=recv_msg.build_time,
                build_status=BuildStatus(recv_msg.status),
                build_msg=recv_msg.msg[-500:],
                commit_hexsha=recv_msg.commit_hexsha)
            logger.debug(f"BUILD task {task} on buildopt %s updated to %s: %s",
                        recv_msg.opt_id, recv_msg.status, " ".join(recv_msg.msg.split())[-500:])
            if (recv_msg.status in [BuildStatus.SUCCESS, BuildStatus.FAILED]):
                if recv_msg.status == BuildStatus.SUCCESS:
                    self.info_successes += 1
                elif recv_msg.status == BuildStatus.FAILED:
                    self.info_failures += 1
                    # Fail all other INIT tasks for the same repo
                    skipped = self.db_man.fail_sibling_statuses(
                        task.repo_id, recv_msg.task_id,
                        msg="Sibling build failed")
                    if skipped > 0:
                        self.info_failures += skipped
                        logger.info(f"Skipped {skipped} sibling tasks for repo {task.repo_id}")
                if (self.info_successes + self.info_failures) % 100 == 0:
                    logger.info(f"Builds completed: {self.info_successes + self.info_failures} ({self.info_successes} successes, {self.info_failures} failures)")

            if ch and ch.is_open:
                ch.basic_ack(delivery_tag=method.delivery_tag)
            else:
                logger.warning("Channel closed before ack, message will be redelivered")
            
        except Exception as e:
            logger.error(f"Error processing build info: {e}")
            if ch and ch.is_open:
                try:
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                except Exception as nack_err:
                    logger.error(f"Failed to nack recv build info: {nack_err}")

    def recv_clone_info(self, ch, method, _props, body):
        """ collect and update clone status of a task """
        try: 
            recv_msg = CloneStatusMsgIn.from_json( body.decode() )
            # if the status code is timeout discard it
            if recv_msg.status == BuildStatus.OUTDATED_MSG:
                logger.info("discarding a timeout clone msg %s", body.decode())
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return
            self.db_man.update_repo_status(
                status_id=recv_msg.task_id,
                clone_status=BuildStatus(recv_msg.status),
                clone_msg=recv_msg.msg[-200:])
            task = self.db_man.get_status_row_by_id(recv_msg.task_id)
            if task.clone_status != BuildStatus.SUCCESS:
                logger.info(f"CLONE task {task} on buildopt %s updated to %s: %s",
                            recv_msg.opt_id, task.clone_status, recv_msg.msg)
            if ch and ch.is_open:
                ch.basic_ack(delivery_tag=method.delivery_tag)
            else:
                logger.warning("Channel closed before ack, message will be redelivered")
            
        except Exception as e:
            logger.error(f"Error processing clone info: {e}")
            if ch and ch.is_open:
                try:
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                except Exception as nack_err:
                    logger.error(f"Failed to nack recv clone info: {nack_err}")
                    
    def recv_builder_registration(self, ch, method, props, body):
        '''
        This function receives a registration from the builder. 
        On first connection to the coordinator, the builder sends a message containing what buildoptions it is using
        If it doesnt exist in the database already, the build option is inserted into the table. 
        Then a queue is spun up, and the coordinator will message the builder telling it the name of the queue to listen on for 
        build instructions
        '''
        try: 
            reg_info: BuilderRegIn = BuilderRegIn.from_json(body)
            logger.info(
                f"Recieved registration request from builder: {reg_info.name}, intending to compile {reg_info.language} on {reg_info.platform}:{reg_info.library}")
            # search for build opt
            logger.debug(
                f"Will be replying to {props.reply_to} with corr_id : {props.correlation_id}")

            build_opt_id = self.db_man.register_build_opt(reg_info)
            if build_opt_id is None:
                logger.error("Failed to register build option for builder %s — skipping", reg_info.name)
                if ch and ch.is_open:
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                return

            conn: Connection = self.mq_client.get_connection(conn_name=f'{self}-{InputQueue.BUILD_REG}')
            if conn: 
                conn.ensure_connection()
            else:
                conn: Connection = self.mq_client.create_connection(conn_name=f'{self}-{InputQueue.BUILD_REG}', channel_name=f'{self}-{InputQueue.BUILD_REG}')
            
            # Respond on the caller-provided reply queue (unique per builder)
            reply_queue_name = props.reply_to if props and props.reply_to else OutputQueue.BUILDER_CTRL
            queue = MQQueue(reply_queue_name)

            conn.send_msg(queue=queue, msg=BuilderRegOut(build_opt_id).to_json(),
                        exchange="",
                        reply_to=props.reply_to,
                        corr_id=props.correlation_id,
                        skip_declare=True,
                        )
            if ch and ch.is_open:

                ch.basic_ack(delivery_tag=method.delivery_tag)
                with self.t_dispatch_map_lock:
                    alive_count = sum(t.is_alive()
                                    for t in self.t_dispatch_map.values())
                    existing = self.t_dispatch_map.get(build_opt_id)
                    if existing and existing.is_alive():
                        logger.info(
                            f"New builder registered, build opt thread {build_opt_id} already running. Currently running {alive_count} build opt threads")
                        return

                    logger.debug("boot dispatching thread for %d ...", build_opt_id)
                    new_build_opt_t = threading.Thread(
                        target=self.__dispatch_task, args=(build_opt_id, True))
                    new_build_opt_t.start()
                    # add to list for management. maybe ( do we need some mutex on this...)
                    self.t_dispatch_map[build_opt_id] = new_build_opt_t
                    logger.info(f"Now running {alive_count+1} build opt threads")
            else:
                logger.warning("Channel for builder registration closed before ack, message will be redelivered")
            
        except Exception as e:
            logger.error(f"Error processing builder registration info: {e}")
            if ch and ch.is_open:
                try:
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                except Exception as nack_err:
                    logger.error(f"Failed to nack builder registration info: {nack_err}")

    def recv_scraper_reg(self, ch, method, props, body):
        '''
            When a scraper requests config (ie asks for start time) send it a start and end time from DB
        '''
        try: 
            request_msg: ScraperControlTaskIn = ScraperControlTaskIn.from_json(body)

            if (request_msg.message_type == ScraperMsgType.SETUP):
                logger.debug(f"Received scraper request for setup info, correlation id {props.correlation_id}")

                # Checks if an unclaimed config is available: if so, claims it and returns its data,
                # if not uses the defaults passed in to create a new config row in DB
                get_config = self.db_man.register_scraper(
                    props.correlation_id,
                    request_msg.start_time,
                    request_msg.end_time
                )
            
                msg = ScraperControlTaskOut(
                    message_type=ScraperMsgType.SETUP,
                    start_time=get_config["start_time"],
                    end_time=get_config["end_time"]
                )

            elif (request_msg.message_type == ScraperMsgType.UPDATE):
                # tries to update the existing row in DB
                get_config = self.db_man.update_scraper(
                    props.correlation_id,
                    request_msg.start_time,
                    request_msg.end_time
                )
            
                msg = ScraperControlTaskOut(
                    message_type=ScraperMsgType.UPDATE,
                    start_time=None,
                    end_time=None
                )



            conn: Connection = self.mq_client.get_connection(conn_name=f'{self}-{InputQueue.SCRAPER_REG}')
            if conn:
                conn.ensure_connection()
            else:
                conn = self.mq_client.create_connection(conn_name=f'{self}-{InputQueue.SCRAPER_REG}', channel_name=f'{self}-{InputQueue.SCRAPER_REG}')

            reply_queue_name = props.reply_to if props and props.reply_to else OutputQueue.SCRAPER_CTRL
            queue = MQQueue(reply_queue_name)

            conn.send_msg(queue=queue, msg=msg.to_json(),
                exchange="",
                reply_to=props.reply_to,
                corr_id=props.correlation_id,
                skip_declare=True,
            )

            # Track scraper queue for REQUEST_REPOS broadcasts
            if request_msg.message_type == ScraperMsgType.SETUP:
                with self._scraper_queues_lock:
                    if reply_queue_name not in self._scraper_queues:
                        self._scraper_queues.append(reply_queue_name)
                        logger.info(f"Registered scraper queue: {reply_queue_name}")

            if ch and ch.is_open:
                ch.basic_ack(delivery_tag=method.delivery_tag)
            else:
                logger.warning("Channel closed before ack, message will be redelivered")
            
        except Exception as e:
            logger.error(f"Error processing recv_scraper_reg info: {e}")
            if ch and ch.is_open:
                try:
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                except Exception as nack_err:
                    logger.error(f"Failed to nack recv_scraper_reg info: {nack_err}")
            ch.basic_ack(delivery_tag=method.delivery_tag)


            
                

    def __daemon(self):
        while True:
            time.sleep(1)

    def run(self):
        """
        Run various threads for interacting with queues and RPC.
        """
        while True:
                if self.db_man.tables_exist():
                    break
                else:
                    try:
                        conditional_init_db(self.db_name, self.db_addr)
                    except:
                        logger.warning('''No tables in database.
                                        Please use docker exec -it assemblage-coordinator-1;
                                        alembic upgrade head.
                                        To create the database to the latest revision. 
                                        Please note you may have to run docker compose up -d again to start the other containers''')
                    time.sleep(10)

        self.db_man.ready_scraper_table()

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
        t_consume_scraper_reg = threading.Thread(
            target=self.__consume_from_queue, args=(InputQueue.SCRAPER_REG,))        
        t_run_builder_ctrl = threading.Thread(target=self.__run_builder_ctrl)
        t_run_scraper_ctrl = threading.Thread(target=self.__run_scraper_ctrl)
        # t_reboot_worker = threading.Thread(target=self.__reboot_worker)
        t_daemon = threading.Thread(target=self.__daemon)
        logger.info("Processes ready")
        for t_dispatch in self.t_dispatch_map:
            t_dispatch.start()
        t_consume_clone.start()
        t_consume_build.start()
        t_consume_binary.start()
        t_consume_scrape.start()
        t_consume_build_reg.start()
        t_consume_scraper_reg.start()
        t_run_builder_ctrl.start()
        t_run_scraper_ctrl.start()
        t_daemon.start()
        logger.info(f"Processes started. {self} now running successfully.")
      
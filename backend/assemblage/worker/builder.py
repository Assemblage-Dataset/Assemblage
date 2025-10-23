"""
Assemblage Worker Node
1. clone repo
2. build repo
3. collect binary file
Yihao Sun

2025
Alex Duly
"""

import datetime
import logging
import os
import queue
import shutil
import json
import sys
import time
import stat

import glob
import ntpath

from assemblage.consts import BINPATH, PDBPATH, TASK_TIMEOUT_THRESHOLD, BuildStatus, MAX_MQ_SIZE, CloneStatus, InputQueue, WorkerType
from assemblage.worker.base_worker import BasicWorker
from assemblage.worker import build_method
from assemblage.worker.find_bin import find_elf_bin
from assemblage.worker.profile import AWSProfile
from assemblage.worker.build_method import LinuxBuildStrategy, WindowsDefaultStrategy
from assemblage.config import BuilderSettings
from assemblage.mq.messages import BuilderRegIn, BuilderRegOut
from assemblage.mq.client import Connection, MQQueue

logger = logging.getLogger(__name__)


NON_EXE_MODE = stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH


class Builder(BasicWorker):
    """
    A Worker that clones and builds repositories.
    It places built binaries in a target directory given by the task.
    """

    def __init__(self,
                 settings: BuilderSettings,  # generic builder settings class,
                 # keep for now i thik this sets the build opt from the table?  - change to be included in message from coordinator...
                 build_mode="Debug",  # change to enum / string literal
                 library="",
                 compiler_flag="",
                 tmp_dir="/tmp/",
                 compiler="",
                 rand_build=False,
                 random_pick=0,
                 blacklist=None,
                 proxy_clone_servers=None,
                 proxy_token="",
                 #  send_binary_method="s3"
                 aws_profile=None
                 ):
        super().__init__(settings.name, settings.mq_host, settings.mq_port, worker_type=WorkerType.Builder)
        self.platform = settings.build_os

        self.library = settings.library  # x64 vs x86. architecture might be better name

        self.build_mode = build_mode
        self.build_opt_queue = None
        self.opt_id = None
        self.build_opt_queue_args = {
                        'arguments': {
                            'x-max-length': MAX_MQ_SIZE,
                            'x-overflow': 'reject-publish',
                            'x-message-ttl': TASK_TIMEOUT_THRESHOLD
                        }
                    }
        
        if blacklist:  # what is this
            self.blacklist = blacklist
        else:
            self.blacklist = []
        # "S3" method will utilize the credentials found in
        # `~/.aws/credentials`. Use "FTP" if you want to connect
        # to a local FTP server instead.
        self.aws_profile = aws_profile  # probably strip and rewrite for minio?
        self.rand_build = rand_build  # what?
        self.library = settings.library
        self.random_pick = random_pick  # what is this?
        #  a repo keep track of the (URL, opt_id) built before
        self.built_b_status_list = []  # is this used?
        self.tmp_dir = os.path.realpath(tmp_dir)  # is this needed now?
        if not os.path.exists(self.tmp_dir):
            os.mkdir(self.tmp_dir)
        self.clone_proxy_servers = proxy_clone_servers  # what?
        self.clone_proxy_token = proxy_token  # what?
        # self.build_callback = build_method.default_build_command_generator
        
        # these are set in the build_opt tables. not sure what these should be so settign empty for now
        self.compiler_flag = None  
        self.build_system = "all" # i think this is the default?
        self.build_command = None # 


        if self.platform == "linux":
            # maybe filter by language here too
            self.build_strategy = LinuxBuildStrategy(
                # rename to linux build strat? and add compilier flags but eh for now
                compiler=settings.compiler, language = settings.language, save_assembly=settings.save_assembly)
        elif self.platform == "windows":
            self.build_strategy = WindowsDefaultStrategy(
                compiler=settings.compiler, language = settings.language, save_assembly=settings.save_assembly)
        else:
            logger.error(
                f"Running on invalid platform: {self.platform}. Options are Linux or Windows")
            sys.exit(1)
            
        self.output_message_queue = [ MQQueue("build"), 
                                     MQQueue(name="clone"),
                                     MQQueue(name="binary")
        ]
            
        
     
        

    def run_ctrl(self):
        '''
        At the moment, all this does is send a registering message to the coordinator.
        Then it waits for a response and then sets the build option queue to listen on.
        '''
        try:
            conn: Connection = self.mq_client.create_connection(conn_name=f'{self}-ctrl',
                                                                channel_name=f'{self}-ctrl',
                                                        )
            conn.create_channel()
            coordinator_queue = MQQueue(
                InputQueue.BUILD_REG)

            conn.add_queue(coordinator_queue)
            
            conn.add_queue(self.control_queue_in)

            self.send_msg(kind=InputQueue.BUILD_REG, repo=None)
            
            
            conn.consume(self.control_queue_in)


        except Exception as e:
            logger.error(f"Failed to create builder control thread, exec={e}")
            

    def run_job(self):
        ''' 
        Run the build job. 

        '''
        logger.info(f"setting up Build option channel for channel for {self}")        
   
        # create input connection and channel
        # create input queue 
        # start consuming
        
        # if not self.build_opt_queue:
        #     logger.info("Waiting for build_opt_thread to be set")
        self.sleep_job_event.wait()
            
        logger.info(f"Build option queue set to {self.build_opt_queue} initialising job")
        conn: Connection = self.mq_client.create_connection(conn_name=f'{self}',
                                                                channel_name=f'{self}')
        conn.create_channel()
        
        conn.add_queue(self.build_opt_queue)
        
        for queue in self.output_message_queue:
            conn.add_queue(queue)
        
        conn.consume(self.build_opt_queue)

        

    def control_message_handler(self, ch, method, props, body):
        """ recieive a control message to specify the build option queue. 
            Figure out later how to change the build options queue, and interrupt the job handler
            for this 
            Also todo: figure out other commands/how to differentiate if necessary
        """

        msg = BuilderRegOut.from_json(body) # modifiy to include routing key + exhange name?
        self.opt_id = msg.build_opt_id 
        self.build_opt_queue = MQQueue(msg.build_opt_queue, callback=self.job_handler, exchange_name='build_opt', routing_key=f'builder.{self.opt_id}')
        ch.basic_ack(delivery_tag=method.delivery_tag)

        logger.info(f"Build {self.name} registered, waking job thread")
        self.sleep_job_event.set()
        
        
    def job_handler(self, ch, method, _props, body):
        """
        Callback for when we get a task request from a coordinator to build a project.
        """
        self.sleep_job_event.wait() # way to get the control thread to block 
        
        task = json.loads(body)  # TODO: create type for this
        url = task['url']
        ch.basic_ack(method.delivery_tag)
        # check if this is an duplicate task
        if time.time() - task['msg_time'] >= TASK_TIMEOUT_THRESHOLD:
            logger.info("Found duplicate build (%s, %d)",
                        task['url'], self.opt_id)
            self.send_msg(repo=task,
                          kind='clone',
                          url=task['url'],
                          status=BuildStatus.OUTDATED_MSG,
                          msg="duplicate")
            return

        logger.info("Received a task to build %s at %s buildsys: %s",
                    url,
                    datetime.datetime.now().strftime("%H:%M:%S"), task['build_system'])
        clone_msg, clone_status, clone_dir = self.build_strategy.clone_data(
            task)
        original_files = []
        for filename in glob.iglob(clone_dir + '**/**', recursive=True):
            original_files.append(filename)
        # respond to events before we pause to build - not sure we need this so removed. better to process with ctrl and pause
        # ch.connection.process_data_events() 
        self.send_msg(repo=task,
                      kind='clone',
                      url=task['url'],
                      status=clone_status,
                      msg=self.uuid[:5]+clone_msg.decode())
        if clone_status == CloneStatus.SUCCESS:
            logger.info("Clone SUCCESS, Attempting to build `%s`", url)
            compiler_flag = self.compiler_flag
            build_mode = self.build_mode
            compiler_version = self.build_strategy.compiler_version
            platform = self.library
            if 'commit_hexsha' in task:
                commit_hexsha = task['commit_hexsha']
            else:
                commit_hexsha = ""
            self.send_msg(repo=task,
                          kind='build',
                          url=url,
                          status=BuildStatus.PROCESSING,
                          msg="Received and building",
                          commit_hexsha=commit_hexsha,
                          build_time=0)
            before_build_time = int(time.time())

            build_msg, build_status = self.build_strategy.run_build(
                repo=task,
                target_dir=clone_dir,
                compiler_version=compiler_version,
                library=self.library,
                build_mode=build_mode,
                optimization=compiler_flag,
                platform=self.platform,
                slnfile=None,
            )

            after_build_time = int(time.time())
            # logger.info("Build exit %s", build_msg.replace("\n", " "))
            self.build_strategy.post_build_hook(clone_dir,
                                                build_mode, platform,
                                                task, compiler_version,
                                                compiler_flag, commit_hexsha)
            logger.info(f"Post build hook done, build_status: {build_status}")

            if build_status == BuildStatus.SUCCESS:
                dest_binfolder = self.save_binaries(
                    clone_dir, task, original_files=original_files)
                logger.info(f"Binaries saved to {dest_binfolder}")
            self.send_msg(repo=task,
                          kind='build',
                          url=url,  # can we send id + commit
                          status=build_status,
                          msg="Build Process Finished",
                          commit_hexsha=commit_hexsha,
                          build_time=(after_build_time - before_build_time))
        else:
            logger.info("Clone FAILURE %s: %s", url, clone_msg)
        # build_method.clean(folders)
        logger.debug("Worker %s finished %s at %s", self.uuid[:5], url,
                     datetime.datetime.now().strftime("%H:%M:%S"))

    def save_binaries(self, clone_dir, repo, original_files):
        """ Store the binaries in the specified output directory. 
            and send message to cooridinator to update database
        """

        self.build_strategy.own_dir(os.path.dirname(
            clone_dir))  # possibly overkill here

        if self.platform == 'linux':
            bin_found = {
                f for f in find_elf_bin(clone_dir, self.build_strategy.save_assembly)
                if (os.path.exists(f))
            }
            if not bin_found:
                logger.warning(
                    "no binaries found, build may have not been a success")
                return None
            else:
                logger.info(f"{len(bin_found)} binaries found")
            dest = f"{BINPATH}/successes/{"/".join(clone_dir.rstrip("/").split("/")[-2:])}"
            try:
                os.mkdir(dest)
            except FileNotFoundError:
                os.makedirs(dest)
            for fpath in bin_found:
                base = os.path.basename(fpath)
                # put some time stamp to avoid duplicate
                shutil.move(fpath, f"{dest}/{base}",
                            copy_function=shutil.copy2)
                os.chmod(f"{dest}/{base}", NON_EXE_MODE)

                self.send_msg(kind='binary',
                              task_id=repo['task_id'],
                              repo=repo,
                              file_name=f"{dest}/{base}")
            # own successes too...
            self.build_strategy.own_dir(os.path.dirname(dest))
            return dest
        elif self.platform == 'windows':
            dest = os.path.join(self.bin_dir, os.urandom(16).hex())
            os.makedirs(dest)
            for filename in glob.glob(clone_dir + '**/**', recursive=True):
                if os.path.isfile(filename) and self.build_strategy.is_valid_binary(filename):
                    prefix = []
                    if "debug" in filename:
                        prefix.append("debug")
                    else:
                        prefix.append("release")
                    if "x86" in filename:
                        prefix.append("x86")
                    if "x64" in filename:
                        prefix.append("x64")
                    prefix_s = "_".join(prefix)
                    dest_file = os.path.join(
                        dest, prefix_s + "_" + ntpath.basename(filename))
                    logger.info("Move file %s -> %s", os.path.join(clone_dir, filename),
                                dest_file)
                    try:
                        shutil.move(filename,
                                    dest_file)
                    except FileNotFoundError:
                        logger.info("Files not found")
                    except shutil.Error:
                        logger.info("File name is invalid")
            try:
                bins_saved = os.listdir(dest)
                logger.info("Binary Saved %s", ",".join(bins_saved))
            except FileNotFoundError:
                logger.info("Binary Not Found")
                bins_saved = []
            for bin_saved in bins_saved:
                self.send_msg(kind='binary',
                              repo=repo,
                              task_id=repo['task_id'],
                              file_name=os.path.join(dest, bin_saved))
            return dest

    def send_msg(self, kind: InputQueue, repo, **kwarg):
        '''
        send message to the coordinator input queue
        Remember input is from the perspective of the coordinator so input == output in builder and output == input
        '''
        ret = {}

        match kind:
            case InputQueue.BUILD_REG:
                ret = BuilderRegIn(
                    name=self.name,
                    uuid=self.uuid,
                    compiler=self.build_strategy.compiler,
                    library = self.library,
                    compiler_version=self.build_strategy.compiler_version,
                    language=self.build_strategy.language,
                    save_assembly=self.build_strategy.save_assembly,
                    platform=self.platform,
                    compiler_flag=self.compiler_flag,
                    build_command=self.build_command,
                    build_system=self.build_system,
                    
                ).to_json()                
                ctrl_conn = self.mq_client.get_connection(f'{self}-ctrl')
                if ctrl_conn:
                    ctrl_conn.send_msg(queue_name=kind, msg=ret,
                                                                #    exchange='builder.register',
                                                                reply_to=f"{self.control_queue_in.name}", 
                                                                corr_id=self.uuid)
                    return
                else:
                    # do we want to create if does not exist then send message?
                    raise Exception(f"Connection {self}-ctrl does not exist")
                
            case InputQueue.CLONE:
                ret = {
                    'url': kwarg['url'],
                    'opt_id': self.opt_id,
                    'status': kwarg['status'],
                    'msg': kwarg['msg'][-1000:],
                    'task_id': repo['task_id']
                }
            case InputQueue.BUILD:
                ret = {
                    'url': kwarg['url'],
                    'opt_id': self.opt_id,
                    'status': kwarg['status'],
                    'msg': kwarg['msg'][-1000:],
                    'task_id': repo['task_id'],
                    'build_time': kwarg['build_time'],
                    'commit_hexsha': kwarg['commit_hexsha']
                }
            case InputQueue.BINARY:
                ret = {
                    'task_id': kwarg['task_id'],
                    'file_name': kwarg['file_name']
                }
            case InputQueue.POST_ANALYSIS:
                ret = {
                    'file_name': kwarg['file_name'],
                    'platform': self.platform
                }
                kind = f"post_analysis.{self.opt_id}"
                # self.mq_client.send_kind_msg(f"post_analysis.{self.opt_id}", json.dumps(ret))
                logger.info("Send to post analysis channel %s \n data: \n %s",
                            f"post_analysis.{self.opt_id}", json.dumps(ret))
            case _:
                logger.warning(
                    "Unknown type of message %s, not sending... ", kind)
                return
        job_conn = self.mq_client.get_connection(f'{self}')
        if job_conn: 
            job_conn.send_msg(kind, json.dumps(ret))
        else: 
            raise Exception("No connection for job handler exists")



# class StandaloneBuilder:

#     def __init__(self, project, build_mode, optimization, cpuarch, compiler_version, build_strategy=DefaultBuildStrategy):
#         assert "url" in project
#         assert build_mode.lower() in ['debug', 'release']
#         assert optimization.lower() in ['o1', 'o2', 'o3', 'od', 'os', 'ox']
#         self.project = project
#         self.build_strategy = DefaultBuildStrategy()
#         self.cpuarch = cpuarch
#         self.build_mode = build_mode
#         self.optimization = optimization
#         self.compiler_version = compiler_version

#     def boot(self):
#         clone_dir = self.build_strategy.get_clone_dir(self.project)
#         self.build_strategy.clone_data(self.project)
#         self.build_strategy.pre_build(self.cpuarch,
#                                       self.build_mode,
#                                       clone_dir,
#                                       self.optimization,
#                                       os.urandom(4).hex(),
#                                       self.compiler_version)
#         self.build_strategy.run_build(self.project,
#                                       clone_dir,
#                                       self.build_mode,
#                                       self.library,
#                                       self.optimization,
#                                       slnfile=None,
#                                       platform='windows',
#                                       compiler_version='v142')

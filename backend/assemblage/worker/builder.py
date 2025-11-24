"""
Assemblage Worker Node
1. clone repo
2. build repo
3. collect binary file
Yihao Sun

2025
Alex Duly
"""

import logging
import os
import shutil
import json
import sys
import time
import stat
import glob
import ntpath
import tempfile
from pathlib import Path

from assemblage.consts import BINPATH, TASK_TIMEOUT_THRESHOLD, BuildStatus, MAX_MQ_SIZE, CloneStatus, InputQueue, WorkerType
from assemblage.worker.base_worker import BasicWorker
from assemblage.worker.build_method import LinuxBuildStrategy, WindowsDefaultStrategy
from assemblage.config import BuilderSettings
from assemblage.mq.messages import BuilderRegIn, BuilderRegOut
from assemblage.mq.client import Connection, MQQueue
from assemblage.s3.client import S3Client, S3Bucket


logger = logging.getLogger(__name__)


NON_EXE_MODE = stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH

TEMP_DIR = Path(tempfile.gettempdir())





class Builder(BasicWorker):
    """
    A Worker that clones and builds repositories.
    It places built binaries in a target directory given by the task.
    """

    def __init__(self,
                 settings: BuilderSettings,  # generic builder settings class,
                 # keep for now i thik this sets the build opt from the table?  - change to be included in message from coordinator...
                 tmp_dir="/tmp/",
                 rand_build=False,
                 random_pick=0,
                 blacklist=None,
                 proxy_clone_servers=None,
                 proxy_token="",
                 #  send_binary_method="s3"
                 aws_profile=None
                 ):
        super().__init__(settings.name, settings.mq_host,
                         settings.mq_port, worker_type=WorkerType.Builder)
        self.platform = settings.build_os

        self.library = settings.library  # x64 vs x86. architecture might be better name
        self.build_mode = settings.build_mode
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
        # self.aws_profile = aws_profile  # probably strip and rewrite for minio?
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
        self.build_command = None
        
        
      # s3 configuration
        if settings.s3_enabled:
            # settings.validate_s3()
            self.s3_client = S3Client(host=settings.S3_HOST, port=settings.S3_PORT, access_key=settings.S3_ACCESS_KEY,
                                    secret_access_key=settings.S3_SECRET_ACCESS_KEY, https=settings.S3_HTTPS, region_name=settings.S3_REGION)
            # coordindator creates but then only needs read only ( unless used to delete ) - leave for now.
            # stores cloned projects
            self.ProjectBucket = S3Bucket(self.s3_client, "project-archive")
            # store build artifacts
            self.ArtifactBucket = S3Bucket(self.s3_client, "artifacts")
            base_path = TEMP_DIR
        else:
            self.s3_client = None
            self.ProjectBucket = None
            self.ArtifactBucket = None
            base_path = BINPATH

        if self.platform == "linux":
            self.build_system = "all"  # i think this is the default?
            #  maybe filter by language here too
            self.build_strategy = LinuxBuildStrategy(
                # rename to linux build strat? and add compilier flags but eh for now
                compiler=settings.compiler, language=settings.language, save_assembly=settings.save_assembly, library=self.library, base_path=base_path)
        elif self.platform == "windows":
            self.build_system = "sln"  # i think this is the default?
            self.compiler_flag = "o4"
            self.build_strategy = WindowsDefaultStrategy(
                compiler=settings.compiler, language=settings.language, save_assembly=settings.save_assembly, library=self.library, base_path=base_path)
        else:
            logger.error(
                f"Running on invalid platform: {self.platform}. Options are Linux or Windows")
            sys.exit(1)


    def _clean_folder(self, path):
        '''
        Try to clean up the target folder, if it fails, will walk and try adn deelte as much as possible. 
        
        This is required due to the windows file lock on the produced executable. Only called on s3 when the projects are saved separately
        This does not delete the top level folder of the git username, but not worth the time and effort currenly
        '''
        logger.debug(f"Deleting {path}")
        if not os.path.exists(path):
            return
        
        try:
            shutil.rmtree(path)
            return  # success
        except Exception as e:
            logger.warning(f"rmtree failed for {path}: {e}.")

        for root, dirs, files in os.walk(path, topdown=False):
            for f in files:
                file_path = os.path.join(root, f)
                try:
                    os.chmod(file_path, stat.S_IWRITE)  # ensure writable
                    os.remove(file_path)
                except Exception as e:
                    logger.warning(f"Could not delete file {file_path}: {e}")

            for d in dirs:
                dir_path = os.path.join(root, d)
                try:
                    os.rmdir(dir_path)
                except Exception as e:
                    logger.warning(f"Could not delete directory {dir_path}: {e}")
        try:
            os.rmdir(path)
        except Exception as e:
            logger.warning(f"Could not delete folder {path}: {e}")

    def _process_window_file_names(self, filename: str):
        '''
        Processes the window file names to icnlude release and architecture in filename, so not in subdir
        turns /x64/Debug/filename -> x64_debug_filename
        Only the basename is used; if no known build type or architecture is found, returns the original filename.

        '''
        
        prefix = []
        if "debug" in filename.lower():
            prefix.append("debug")
        else:
            prefix.append("release")
        if "x86" in filename.lower():
            prefix.append("x86")
        if "x64" in filename.lower():
            prefix.append("x64")
            
        if prefix:
            prefix_str = "_".join(prefix)
            dest_file = f"{prefix_str}_{ntpath.basename(filename)}"
            return dest_file
        else: 
            return filename # nothing to change
        
    def run_ctrl(self):
        '''
        At the moment, all this does is send a registering message to the coordinator.
        Then it waits for a response and then sets the build option queue to listen on.
        '''
        try:

            while True:

                if not self.build_opt_queue:  # handle when errors happen in creating hte connectino/ consume without the builder having a queue - could expand to just do some of htis on start up
                    conn: Connection = self.mq_client.create_connection(conn_name=f'{self}-ctrl',
                                                                        channel_name=f'{self}-ctrl',
                                                                        )
                    conn.create_channel()
                    self.send_msg(kind=InputQueue.BUILD_REG, repo=None)
                    logger.info(
                        "Registration Message sent. Starting consumption on control queue now")
                    self.mq_client.start_consumer(
                        conn=conn, queue=self.control_queue_in, retry_delay=10)
                    logger.warning(f"Consume control on {self} has finished.")
                else:
                    logger.debug(
                        f"Builder registered and listening on: {self.build_opt_queue}")
                time.sleep(15)

        except Exception as e:
            logger.error(f"Failed to create builder control thread, exec={e}")

    def run_job(self):
        '''
        Run the build job.

        '''

        # create input connection and channel
        # create input queue
        # start consuming

        logger.info(f"{self} Waiting for build_opt_thread to be set")
        # if not self.build_opt_queue:
        #     logger.info("Waiting for build_opt_thread to be set")

        MAX_WAIT = 15 * 60  # 15 minutes in seconds
        CHECK_INTERVAL = 5  # check every 5 seconds

        start_time = time.time()

        while not self.sleep_job_event.wait(timeout=CHECK_INTERVAL):
            elapsed = time.time() - start_time
            if elapsed > MAX_WAIT:
                logger.warning(
                    f"{self}: waited {elapsed:.0f}s — exiting after 15 minutes timeout")
                return

            logger.info(
                f"{self}: still waiting for configuration ({elapsed:.0f}s elapsed). Will exit after {MAX_WAIT/60} minuites without configuration")
        logger.info(
            f"Build option queue set to {self.build_opt_queue} initialising job")
        conn: Connection = self.mq_client.create_connection(conn_name=f'{self}',
                                                            channel_name=f'{self}')
        conn.create_channel()
        logger.info(f"{self} Starting consumption on {self.build_opt_queue}")
        self.mq_client.start_consumer(
            conn=conn, queue=self.build_opt_queue, retry_delay=10)
        logger.warning(
            f"Consume build opt {self.build_opt_queue} on {self} has finished.")

    def control_message_handler(self, ch, method, props, body):
        """ recieive a control message to specify the build option queue.
            Figure out later how to change the build options queue, and interrupt the job handler
            for this
            Also todo: figure out other commands/how to differentiate if necessary
        """
        if props.correlation_id != self.uuid:  # correlation ID doesnt match, send back onto queue
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            return
        logger.debug("Recieiving builder information")
        # modifiy to include routing key + exhange name?
        msg = BuilderRegOut.from_json(body)
        logger.debug(f"Recieived Builder Reg Info {msg}")

        self.opt_id = msg.build_opt_id
        self.build_opt_queue = MQQueue(msg.build_opt_queue, callback=self.job_handler,
                                       exchange_name='build_opt', routing_key=f'builder.opt.{self.opt_id}')
        ch.basic_ack(delivery_tag=method.delivery_tag)
        logger.info(f"Build {self.name} registered, waking job thread")
        self.sleep_job_event.set()
        # maybe add some heartbeat/ check that the job queue is running. if it isnt then restart?
        logger.info(
            f"Build opt setting, Stopping consumption on control message")
        ch.stop_consuming()

    def job_handler(self, ch, method, _props, body):
        """
        Callback for when we get a task request from a coordinator to build and clone/pull a project.
        """
        self.sleep_job_event.wait()  # way to get the control thread to block
        task = json.loads(body)  # TODO: create type for this

        url = task['url']
        ch.basic_ack(method.delivery_tag)
        # check if this is an duplicate task
        # if time.time() - task['msg_time'] >= TASK_TIMEOUT_THRESHOLD: # not sure on this. is this because of a lag between starting the builder and coordinator??
        #     logger.info("Found duplicate build (%s, %d)",
        #                 task['url'], self.opt_id)
        #     self.send_msg(repo=task,
        #                   kind='clone',
        #                   url=task['url'],
        #                   status=BuildStatus.OUTDATED_MSG,
        #                   msg="duplicate")
        #     return

        logger.info("Received a task to build %s. buildsys: %s",
                    url,
                    task['build_system'])
        clone_msg, clone_status, clone_dir = self.build_strategy.clone_data(
            task)

        original_files = []
        for filename in glob.iglob(clone_dir + '**/**', recursive=True):
            original_files.append(filename)
        # respond to events before we pause to build - not sure we need this so removed. better to process with ctrl and pause
        # ch.connection.process_data_events()

        if clone_status == CloneStatus.SUCCESS:
            logger.info("Clone SUCCESS, Attempting to build `%s`", url)
            compiler_flag = self.compiler_flag
            compiler_version = self.build_strategy.compiler_version
            if 'commit_hexsha' in task:
                commit_hexsha = task['commit_hexsha']
            else:
                commit_hexsha = self.build_strategy.get_project_commit(
                    clone_dir)
            
            
            save_path = f'{self}:{clone_dir}' # default save location - will be the host + save dir ( either s3 path or the builder it is on + the path in teh builder)


            if self.s3_client:
                # save to s3 client and return location
                logger.info(f"Uploading {clone_dir} to s3 bucket { self.ProjectBucket}")
                username, project = clone_dir.rstrip("/").split("/")[-2:]
                saved = self.save_project_to_s3(clone_dir, username, project, commit_hexsha)
                if saved:
                    save_path = f"{self.ProjectBucket}/{username}/{project}/{commit_hexsha}.tar.gz"
                    logger.debug(f"Project saved to {save_path} ")
                    


            self.send_msg(repo=task,
                      kind=InputQueue.CLONE,
                      url=task['url'],
                      status=clone_status,
                      msg=self.uuid[:5]+clone_msg.decode(),
                      commit_hexsha=commit_hexsha,
                      save_path = save_path
                      )

            self.send_msg(repo=task,
                          kind=InputQueue.BUILD,
                          url=url,
                          status=BuildStatus.PROCESSING,
                          msg="Received and building",
                          commit_hexsha=commit_hexsha,
                          build_time=0)

            # this is currently only needed for windows, but linux just reutrns none too, so it wont break
            # seems cleaner to do it like this , instead of doing if statements here
            logger.debug("Starting pre build")
            sln_file = self.build_strategy.pre_build(
                build_mode=self.build_mode,
                clone_dir=clone_dir,
                optimization=self.compiler_flag
            )
            logger.debug(f"Pre Build success, now running build for {url}")
            before_build_time = int(time.time())
            build_msg, build_status = self.build_strategy.run_build(
                repo=task,
                clone_dir=clone_dir,
                build_mode=self.build_mode,
                slnfile=sln_file
            )

            after_build_time = int(time.time())
            # logger.info("Build exit %s", build_msg.replace("\n", " "))
            self.build_strategy.post_build_hook(clone_dir,
                                                self.build_mode,
                                                task, compiler_version,
                                                compiler_flag, commit_hexsha)
            logger.info(f"Post build hook done, build_status: {build_status}")
            logger.debug(f"Build message: {build_msg}")

            if build_status == BuildStatus.SUCCESS:
                # do something with dest bin_folder
                dest_binfolder = self.save_binaries(
                    clone_dir, task, original_files=original_files, commit_hexsha=commit_hexsha)
                logger.info(f"Binaries saved to {dest_binfolder}")
            self.send_msg(repo=task,
                          kind=InputQueue.BUILD,
                          url=url,  # can we send id + commit
                          status=build_status,
                          msg="Build Process Finished",
                          commit_hexsha=commit_hexsha,
                          build_time=(after_build_time - before_build_time))
            if self.s3_client:
                self._clean_folder(os.path.dirname(clone_dir)) # dirname needed to also remove parent folder with username
            
            
        else:
            self.send_msg(repo=task,
                      kind=InputQueue.CLONE,
                      url=task['url'],
                      status=clone_status,
                      msg=self.uuid[:5]+clone_msg.decode(),
                      commit_hexsha="", 
                      save_path=None)

            logger.info("Clone FAILURE %s: %s", url, clone_msg)
        # build_method.clean(folders)
        logger.debug("Worker %s finished %s", self.uuid[:5], url,
                     )

    def save_project_to_s3(self, clone_dir: str, username:str, project_name: str, commit_hexsha: str):
        '''
        ZIP and save projects to s3 Project-Archive/<github_username>/<github_project>/commit.zip
        '''

        try:

            archive = shutil.make_archive(f"{TEMP_DIR}/{commit_hexsha}",
                                "gztar", clone_dir)  # zip up
            s3_key = f"{username}/{project_name}/{commit_hexsha}.tar.gz"         
               
            self.ProjectBucket.upload_file(archive, s3_key )
            try: 
                os.remove(archive)
            except Exception as e:
                # dont want to fail if successfully failed, just hasnt tidied up
                logger.warning(f"Failed to delete archive: {s3_key} after uploading to s3")
            
            return True
        except Exception as e:
            logger.warning(f"failed to save {clone_dir} as zip archive to {self.ProjectBucket}/{username}/{project_name}/{commit_hexsha}.tar.gz : {e}")
            return False
        
        
    def save_binaries(self, target_dir, repo, original_files, commit_hexsha, optimization="None"):
        """ Store binaries locally or on S3, and notify coordinator. """
        logger.debug(f"Saving binaries of Repo: {repo}")

        self.build_strategy.own_dir(os.path.dirname(target_dir))

        bin_found = {
            f for f in self.build_strategy.find_binaries(target_dir)
            if os.path.exists(f) and f not in original_files
        }
        if not bin_found:
            logger.warning("No binaries found, build may have failed")
            return None

        logger.info(f"{len(bin_found)} binaries found")
        username, project = target_dir.rstrip("/").split("/")[-2:]
        dest_base = f"{username}/{project}/{commit_hexsha}"

        if self.s3_client:
            dest_base_full = f"{self.ProjectBucket}/{dest_base}"
        else:
            dest_base_full = os.path.join(BINPATH, "successes", username, project, commit_hexsha)
            os.makedirs(dest_base_full, exist_ok=True)
  
        for fpath in bin_found:
            base_name = os.path.basename(fpath)
            if self.platform == "windows":
                base_name = self._process_window_file_names(fpath)

            if self.s3_client:
                s3_key = f"{dest_base}/{self.build_strategy.compiler}/{optimization}/{base_name}"
                if self.ArtifactBucket.upload_file(fpath, s3_key):
                    logger.debug(f"Uploaded {fpath} -> {s3_key}")
            else:
                dest_file = os.path.join(dest_base_full, base_name)
                shutil.copy2(fpath, dest_file)
                try:
                    os.remove(fpath)
                except Exception:
                    logger.warning(f"Could not delete {fpath}")
                if self.platform == 'linux':
                    try:
                        os.chmod(dest_file, NON_EXE_MODE)
                    except Exception:
                        logger.warning(f"Failed to change permissions on {dest_file}")

            self.send_msg(kind=InputQueue.BINARY,
                        repo=repo,
                        task_id=repo['task_id'],
                        file_name=fpath if self.s3_client else dest_file)


        self.build_strategy.own_dir(dest_base_full)
        return dest_base_full

    def send_msg(self, kind: InputQueue, repo, **kwarg):
        '''
        send message to the coordinator input queue
        Remember input is from the perspective of the coordinator so input == output in builder and output == input
        '''
        ret = {}
        queue = MQQueue(kind)
        match kind:
            case InputQueue.BUILD_REG:
                ret = BuilderRegIn(
                    name=self.name,
                    uuid=self.uuid,
                    compiler=self.build_strategy.compiler,
                    library=self.library,
                    compiler_version=self.build_strategy.compiler_version,
                    language=self.build_strategy.language,
                    save_assembly=self.build_strategy.save_assembly,
                    platform=self.platform,
                    compiler_flag=self.compiler_flag,
                    build_command=self.build_command,
                    build_system=self.build_system,
                ).to_json()
                ctrl_conn = self.mq_client.get_connection(f'{self}-ctrl')
                logger.debug(
                    f"Reply to {self.control_queue_in.name}. corr_id {self.uuid}")
                if ctrl_conn:
                    logger.info(f"Registering builder with {ret}")
                    ctrl_conn.send_msg(queue=queue, msg=ret,
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
                    'task_id': repo['task_id'],
                    'commit_hexsha': kwarg['commit_hexsha'],
                    'save_path': kwarg['save_path']

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
            job_conn.send_msg(queue, json.dumps(ret))
        else:
            raise Exception("No connection for job handler exists")

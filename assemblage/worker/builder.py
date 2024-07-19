"""
Assemblage Worker Node
1. clone repo
2. build repo
3. collect binary file
Yihao Sun
"""

import datetime
import logging
import os
import shutil
import json
import time
import random
import string

import glob
import grpc
import requests
import ntpath

from assemblage.consts import BINPATH, PDBPATH, TASK_TIMEOUT_THRESHOLD, BuildStatus, MAX_MQ_SIZE
from assemblage.worker.base_worker import BasicWorker
from assemblage.worker import build_method
from assemblage.worker.find_bin import find_elf_bin
from assemblage.worker.profile import AWSProfile
from assemblage.protobufs.assemblage_pb2 import getBuildOptRequest
from assemblage.worker.build_method import DefaultBuildStrategy


class Builder(BasicWorker):
    """
    A Worker that clones and builds repositories.
    It places built binaries in a target directory given by the task.
    """

    def __init__(self,
                 rabbitmq_host,
                 rabbitmq_port,
                 rpc_stub,
                 worker_type,
                 opt_id,
                 platform="linux",
                 build_mode="Debug",
                 library="",
                 compiler_flag="",
                 tmp_dir="",
                 compiler="",
                 rand_build=False,
                 random_pick=0,
                 blacklist=None,
                 proxy_clone_servers=None,
                 proxy_token="",
                #  send_binary_method="s3"
                 aws_profile= None
                 ):
        super().__init__(rabbitmq_host, rabbitmq_port, rpc_stub, worker_type,
                         opt_id)
        logging.info("Worker inited")
        self.compiler_version = compiler
        self.compiler_flag = compiler_flag
        self.library = library
        self.opt_id = opt_id
        self.build_mode = build_mode
        if blacklist:
            self.blacklist = blacklist
        else:
            self.blacklist = []
        # "S3" method will utilize the credentials found in
        # `~/.aws/credentials`. Use "FTP" if you want to connect
        # to a local FTP server instead.
        self.aws_profile = aws_profile
        self.platform = platform
        self.rand_build = rand_build
        self.server_addr = rabbitmq_host
        self.route_key = f"worker.{self.opt_id}"
        self.mq_client = None
        if self.library == "x86" and self.platform == "windows":
            self.library = "x86"
        self.random_pick = random_pick
        #  a repo keep track of the (URL, opt_id) built before
        self.built_b_status_list = []
        self.tmp_dir = os.path.realpath(tmp_dir)
        if not os.path.exists(self.tmp_dir):
            os.mkdir(self.tmp_dir)
        self.clone_proxy_servers = proxy_clone_servers
        self.clone_proxy_token = proxy_token
        # self.build_callback = build_method.default_build_command_generator
        self.build_strategy = DefaultBuildStrategy()
        self.on_init()

    def setup_job_queue_info(self):
        logging.info("setting up mq channel for %d", self.opt_id)
        self.topic_exchange = 'build_opt'
        self.route_key = f'worker.{self.opt_id}'
        self.output_message_queue = [{
            'name': 'build',
            'params': {
                'durable': True
            }
        }, {
            'name': 'clone',
            'params': {
                'durable': True
            }
        }, {
            'name': 'binary',
            'params': {
                'durable': True
            }
        }, {
            'name': f'post_analysis.{self.opt_id}',
            # 'name': f'post_analysis',
            'params': {
                'durable': True
            }
        }]
        self.input_queue_name = f"queue_{self.opt_id}"
        # self.input_queue_args = {
        #     'x-max-length': MAX_MQ_SIZE,
        #     'x-overflow': 'reject-publish'
        # }
        # name will be generated when declare
        self.input_queue_args = {
            'arguments': {
                'x-max-length': MAX_MQ_SIZE,
                'x-overflow': 'reject-publish',
                'x-message-ttl': TASK_TIMEOUT_THRESHOLD
            }
        }

    def on_init(self):
        """ prepare dir here """
        self.bin_dir = os.path.join(os.path.abspath(os.getcwd()), BINPATH)
        self.pdb_dir = os.path.join(os.path.abspath(os.getcwd()), PDBPATH)
        if not os.path.exists(self.bin_dir):
            os.mkdir(self.bin_dir)
            logging.info('self.init Created Binary folder')
        if not os.path.exists(self.pdb_dir):
            os.mkdir(self.pdb_dir)
            logging.info('self.init Created Pdb folder')

    def control_message_handler(self, msg):
        """ reset opt id of this worker and recreate rmq connection """
        request = getBuildOptRequest(request="get")
        try:
            build_options = []
            for build_option in self.rpc_stub.getBuildOpt(request):
                build_options.append(build_option)
        except grpc.RpcError as rpc_error:
            if rpc_error.code() == grpc.StatusCode.UNAVAILABLE:
                logging.info(
                    'CLI Failed To connect to any addresses; Coordinator may be inactive'
                )
            else:
                logging.info("RPC Error: %s", rpc_error)
            return
        for build_opt_record in build_options:
            if build_opt_record.id == msg:
                self.opt_id = msg
                self.compiler_version = build_opt_record.compiler_name
                self.library = build_opt_record.library
                self.compiler_flag = build_opt_record.compiler_flag.replace(
                    "-", "")
                self.input_queue_name = f"queue_{self.opt_id}"
                self.change_input(self.input_queue_name, self.input_queue_args)
                logging.info("Build opt id switched to %d", msg)

    def scan_binaries(self, clone_dir, repo, original_files):
        """ Store the binaries in the specified output directory. """
        if self.platform == 'linux':
            bin_found = {
                f for f in find_elf_bin(clone_dir)
                if (os.path.exists(f) and self.build_strategy.is_valid_binary(f))
            }
            logging.info(bin_found)
            dest = f"{BINPATH}/{time.time()}"
            try:
                os.mkdir(dest)
            except FileNotFoundError:
                os.makedirs(dest)
            for fpath in bin_found:
                base = os.path.basename(fpath)
                # put some time stamp to avoid duplicate
                shutil.move(fpath, f"{dest}/{base}")
                self.send_msg(kind='binary',
                              task_id=repo['task_id'],
                              repo=repo,
                              file_name=f"{dest}/{base}")
                logging.info('Moving ELF file `%s` %s', fpath, f"{dest}/{base}")
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
                    dest_file = os.path.join(dest, prefix_s + "_" + ntpath.basename(filename))
                    logging.info("Move file %s -> %s", os.path.join(clone_dir, filename),
                                 dest_file)
                    try:
                        shutil.move(filename,
                                    dest_file)
                    except FileNotFoundError:
                        logging.info("Files not found")
                    except shutil.Error:
                        logging.info("File name is invalid")
            try:
                bins_saved = os.listdir(dest)
                logging.info("Binary Saved %s", ",".join(bins_saved))
            except FileNotFoundError:
                logging.info("Binary Not Found")
                bins_saved = []
            for bin_saved in bins_saved:
                self.send_msg(kind='binary',
                              repo=repo,
                              task_id=repo['task_id'],
                              file_name=os.path.join(dest, bin_saved))
            return dest

    def send_msg(self, kind, repo, **kwarg):
        '''
        send message into the queue with name `kind`
        '''
        if kind == 'clone':
            ret = {
                'url': kwarg['url'],
                'opt_id': self.opt_id,
                'status': kwarg['status'],
                'msg': kwarg['msg'][-1000:],
                'task_id': repo['task_id']
            }
        elif kind == 'build':
            ret = {
                'url': kwarg['url'],
                'opt_id': self.opt_id,
                'status': kwarg['status'],
                'msg': kwarg['msg'][-1000:],
                'task_id': repo['task_id'],
                'build_time': kwarg['build_time'],
                'commit_hexsha': kwarg['commit_hexsha']
            }
        elif kind == 'binary':
            ret = {
                'task_id': kwarg['task_id'],
                'file_name': kwarg['file_name']
            }
        elif kind == 'post_analysis':
            ret = {
                'file_name': kwarg['file_name'],
                'platform': self.platform
            }
            kind = f"post_analysis.{self.opt_id}"
            # self.mq_client.send_kind_msg(f"post_analysis.{self.opt_id}", json.dumps(ret))
            logging.info("Send to post analysis channel %s \n data: \n %s",
                         f"post_analysis.{self.opt_id}", json.dumps(ret))
        self.mq_client.send_kind_msg(kind, json.dumps(ret))


    def job_handler(self, ch, method, _props, body):
        """
        Callback for when we get a task request from a coordinator.
        """
        task = json.loads(body)
        url = task['url']
        ch.basic_ack(method.delivery_tag)
        # check if this is an duplicate task
        if time.time() - task['msg_time'] >= TASK_TIMEOUT_THRESHOLD:
            logging.info("Found duplicate build (%s, %d)",
                         task['url'], self.opt_id)
            self.send_msg(repo=task,
                          kind='clone',
                          url=task['url'],
                          status=BuildStatus.OUTDATED_MSG,
                          msg="duplicate")
            return
        
        logging.info("Received a task to build %s at %s buildsys: %s",
                     url,
                     datetime.datetime.now().strftime("%H:%M:%S"), task['build_system'])
        clone_msg, clone_status, clone_dir = self.build_strategy.clone_data(task)
        folders = []
        original_files = []
        for filename in glob.iglob(clone_dir + '**/**', recursive=True):
            original_files.append(filename)
        # respond to events before we pause to build
        self.mq_client.conn.process_data_events()
        self.send_msg(repo=task,
                      kind='clone',
                      url=task['url'],
                      status=clone_status,
                      msg=self.uuid[:5]+clone_msg.decode())
        if clone_status == BuildStatus.SUCCESS:
            logging.info("Clone SUCCESS, Attempting to build `%s`", url)
            folders.append(clone_dir)
            compiler_flag = self.compiler_flag
            build_mode = self.build_mode
            compiler_version = self.compiler_version
            platform = self.library
            if 'commit_hexsha' in task:
                commit_hexsha = task['commit_hexsha']
            self.send_msg(repo=task,
                            kind='build',
                            url=url,
                            status="3",
                            msg="Received and building",
                            commit_hexsha=commit_hexsha,
                            build_time=1)    
            build_msg, build_status = self.build_strategy.run_build(
                repo=task,
                target_dir=clone_dir,
                compiler_version=compiler_version,
                library=self.library,
                build_mode=build_mode,
                optimization=compiler_flag,
                platform=self.platform
            )
            after_build_time = int(time.time())
            logging.info("Build exit %s", build_msg.replace("\n", " "))
            if build_status == BuildStatus.SUCCESS:
                dest_binfolder = clone_dir
                self.build_strategy.post_build_hook(dest_binfolder,
                                            build_mode, platform,
                                            task, compiler_version,
                                            compiler_flag, commit_hexsha)
                folders.append(os.path.join(self.bin_dir, dest_binfolder))
        else:
            logging.info("Clone FAILURE %s: %s", url, clone_msg)
        build_method.clean(folders, platform=self.platform)
        logging.debug("Worker %s finished %s at %s", self.uuid[:5], url,
                      datetime.datetime.now().strftime("%H:%M:%S"))


class StandaloneBuilder:

    def __init__(self, project, build_mode, optimization, cpuarch, compiler_version, build_strategy=DefaultBuildStrategy):
        assert "url" in project
        assert build_mode.lower() in ['debug', 'release']
        assert optimization.lower() in ['o1', 'o2', 'o3', 'od', 'os', 'ox']
        self.project = project
        self.build_strategy = DefaultBuildStrategy()
        self.cpuarch = cpuarch
        self.build_mode = build_mode
        self.optimization = optimization
        self.compiler_version = compiler_version

    def boot(self):
        clone_dir = self.build_strategy.get_clone_dir(self.project)
        self.build_strategy.clone_data(self.project)
        self.build_strategy.pre_build(self.cpuarch,
                    self.build_mode,
                    clone_dir,
                    self.optimization,
                    os.urandom(4).hex(),
                    self.compiler_version)
        self.build_strategy.run_build(self.project,
                clone_dir,
                self.build_mode,
                self.library,
                self.optimization,
                slnfile=None,
                platform='windows',
                compiler_version='v142')

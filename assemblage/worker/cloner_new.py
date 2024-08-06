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
import sys
import shutil
import json
from threading import TIMEOUT_MAX
import time
import hashlib
import random
import string

import glob
from urllib import response
from urllib.request import urlretrieve
import zipfile
import grpc
import requests

from assemblage.consts import BINPATH, PDBPATH, TASK_TIMEOUT_THRESHOLD, BuildStatus, MAX_MQ_SIZE
from assemblage.worker.base_worker import BasicWorker
from assemblage.worker import build_method
from assemblage.worker.find_bin import find_elf_bin
from assemblage.protobufs.assemblage_pb2 import getBuildOptRequest


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
                 proxy_token=""):
        super().__init__(rabbitmq_host, rabbitmq_port, rpc_stub, worker_type,
                         opt_id)
        logging.info(
            ">>>>>>>>>>>>>>>>>>>>>> Init worker <<<<<<<<<<<<<<<<<<<<<<<<<<<")
        self.compiler_version = compiler
        self.compiler_flag = compiler_flag
        self.library = library
        self.opt_id = opt_id
        self.build_mode = build_mode
        if blacklist:
            self.blacklist = blacklist
        else:
            self.blacklist = []
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
        self.clone_proxy_servers = proxy_clone_servers
        self.clone_proxy_token = proxy_token
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
            'name': 'post_analysis',
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
        if self.platform == 'windows':
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

    def get_clone_dir(self, repo):
        """
        Form a target directory with repo information
        """
        hashedurl = str(hash(repo['url'])).replace("-", "")
        hashedurl = hashedurl + \
            ''.join(random.choice(string.ascii_lowercase) for _ in range(5))
        return f"{self.tmp_dir}/{hashedurl}"

    def scan_binaries(self, clone_dir, repo, original_files):
        """ Store the binaries in the specified output directory. """
        if self.platform == 'linux':
            bin_found = {
                f
                for f in find_elf_bin(clone_dir) if os.path.exists(f)
            }
            for fpath in bin_found:
                logging.info('Moving ELF file `%s`', fpath)
                base = os.path.basename(fpath)
                # put some time stamp to avoid duplicate
                dest = f"{repo['output_dir']}/{base}_{time.time()}"
                try:
                    os.mkdir(dest)
                except FileNotFoundError:
                    os.makedirs(dest)
                shutil.move(fpath, f"{dest}/{base}")
                self.send_msg(kind='binary',
                              task_id=repo['task_id'],
                              repo=repo,
                              file_name=f"{dest}/{base}")
        elif self.platform == 'windows':
            dest = os.path.join(self.bin_dir,
                                repo['output_dir'].replace("/binaries/", "") +
                                ''.join(random.choice(string.ascii_lowercase) for i in range(10)))
            os.makedirs(dest)
            for filename in glob.iglob(clone_dir + '**/**', recursive=True):
                if filename not in original_files and not os.path.isdir(filename) and \
                   (filename.endswith("exe") or filename.endswith("dll") or filename.endswith("pdb")):
                    dest_file = os.path.join(dest, filename.split("\\")[-1])
                    clean_filename = filename.split("\\")[-1]
                    logging.info("Move file %s -> %s", os.path.join(clone_dir, filename),
                                 dest_file)
                    try:
                        shutil.move(os.path.join(clone_dir, filename),
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
                'build_time': kwarg['build_time']
            }
        elif kind == 'binary':
            ret = {
                'task_id': kwarg['task_id'],
                'file_name': kwarg['file_name']
            }
        elif kind.startswith('post_analysis'):
            ret = {
                'file_name': kwarg['file_name']
            }
        self.mq_client.send_kind_msg(kind, json.dumps(ret))

    def clone(self, repo, clone_dir):
        """ Clone repo """
        logging.info("Cloning %s", repo["url"])
        out, err, exit_code = build_method.cmd_with_output([
            'gh', 'repo', 'clone', repo['url'], clone_dir, "--", "--depth",
            "1", "--recursive"
        ], 60, self.platform)
        if exit_code == 0:
            return b'CLONE SUCCESS', BuildStatus.SUCCESS
        if exit_code == 10:
            return err, BuildStatus.TIMEOUT
        return out + err, BuildStatus.FAILED

    def clone_from_proxy(self, repo, clone_dir):
        """ clone from proxy server """
        logging.info("Cloning %s with proxy", repo["url"])
        proxy_chosen = random.choice(self.clone_proxy_servers)
        zip_url = f"http://{proxy_chosen}/" + \
            hashlib.md5(repo["url"].encode()).hexdigest()+".zip"
        logging.info("Cloning %s to %s", zip_url, clone_dir)
        try:
            response = requests.get(f"http://{proxy_chosen}/proxy/clone", {
                                    "repo_url": repo["url"], "auth": self.clone_proxy_token}, timeout=60)
        except Exception as err:
            logging.info(err)
            return (str(err)).encode(), BuildStatus.FAILED
        os.makedirs(clone_dir)
        tmp_file_path = os.path.join(self.tmp_dir, hashlib.md5(
            repo["url"].encode()).hexdigest()+".zip")
        logging.info("Proxy response: %s", response.text)
        if int(response.text) == 0:
            response = requests.get(zip_url)
            open(tmp_file_path, "wb").write(response.content)
            shutil.unpack_archive(tmp_file_path, clone_dir)
            os.remove(tmp_file_path)
            logging.info("Sending delete request")
            response = requests.get(f"http://{proxy_chosen}/proxy/delete", {
                                    "zip_url": hashlib.md5(repo["url"].encode()).hexdigest()+".zip"}, timeout=10)
            return b'CLONE SUCCESS', BuildStatus.SUCCESS
        else:
            return bytes(response.text, "utf-8"), BuildStatus.FAILED

    def job_handler(self, ch, method, _props, body):
        """
        Callback for when we get a task request from a coordinator.
        """
        repo = json.loads(body)
        url = repo['url']
        ch.basic_ack(method.delivery_tag)
        # check if this is an duplicate task
        if ((repo['url'], self.opt_id) in self.built_b_status_list) or (time.time() - repo['msg_time'] >= TASK_TIMEOUT_THRESHOLD):
            logging.info("Found duplicate build (%s, %d)",
                         repo['url'], self.opt_id)
            self.send_msg(repo=repo,
                          kind='clone',
                          url=repo['url'],
                          status=BuildStatus.OUTDATED_MSG,
                          msg="duplicate")
            return
        logging.info("Worker %s received a task to build %s at %s buildsys: %s",
                     self.uuid[:5], url,
                     datetime.datetime.now().strftime("%H:%M:%S"), repo['build_system'])
        if repo['build_system'] != "sln" and self.platform == 'windows':
            self.send_msg(kind='clone',
                          url=repo['url'],
                          repo=repo,
                          status=BuildStatus.BLACKLIST,
                          msg="non-sln sent to Windows")
            logging.info("non-sln sent to Windows")
            return
        for item in self.blacklist:
            if repo['name'] in item or item in repo['name']:
                self.send_msg(kind='clone',
                              url=repo['url'],
                              repo=repo,
                              status=BuildStatus.BLACKLIST,
                              msg="blacklist")
                return
        # check if a task is overtime, discard it

        clone_dir = self.get_clone_dir(repo)
        if self.clone_proxy_servers:
            clone_msg, clone_status = self.clone_from_proxy(repo, clone_dir)
        if clone_status != BuildStatus.SUCCESS:
            clone_msg, clone_status = self.clone(repo, clone_dir)
        folders = []
        original_files = []
        for filename in glob.iglob(clone_dir + '**/**', recursive=True):
            original_files.append(filename)
        # respond to events before we pause to build
        self.mq_client.conn.process_data_events()
        self.send_msg(repo=repo,
                      kind='clone',
                      url=repo['url'],
                      status=clone_status,
                      msg=self.uuid[:5]+clone_msg.decode())
        if clone_status == BuildStatus.SUCCESS:
            logging.info("Clone SUCCESS, Attempting to build `%s`", url)
            folders.append(clone_dir)
            build_task_configs = []
            random_compiler_flags = ["O1", "O2", "Ox", "Od"]
            random_modes = ["Debug", "Release"]
            random_plat = ["x86", "x64"]
            rand_compiler_versions = ["v142", "v141", "v140"]
            build_task_configs.append(
                (self.library, self.build_mode,
                 self.compiler_version, self.compiler_flag)
            )

            if self.random_pick > 0:
                build_task_configs = []
                if self.rand_build:
                    for _ in range(10):
                        url_md5 = hashlib.md5(url.encode('utf-8')).hexdigest()
                        hashed_picks = [int(i) for i in url_md5 if i.isdigit()]
                        if len(hashed_picks) < 4:
                            hashed_picks = [1, 1, 1, 1]
                        aconfig = (
                            random_plat[hashed_picks[0] % (len(random_plat))],
                            random_modes[hashed_picks[1] % len(random_modes)],
                            rand_compiler_versions[hashed_picks[2] % len(
                                rand_compiler_versions)],
                            random_compiler_flags[hashed_picks[3] % len(
                                random_compiler_flags)]
                        )
                        if aconfig not in build_task_configs:
                            build_task_configs.append(aconfig)
                        if len(build_task_configs) > self.random_pick:
                            break
            if len(build_task_configs) > self.random_pick+1:
                build_task_configs = build_task_configs[:self.random_pick+1]
            build_count = 0
            for library, mode, version, opti in build_task_configs:
                compiler_flag = opti
                build_mode = mode
                compiler_version = version
                platform = library
                before_build_time = int(time.time())
                build_msg, build_status, _ = build_method.build(
                    target_dir=clone_dir,
                    build_tool="only needed by linux",
                    compiler_version=compiler_version,
                    library=library,
                    build_mode=build_mode,
                    optimization=compiler_flag,
                    platform=self.platform)
                after_build_time = int(time.time())
                if build_status == BuildStatus.SUCCESS:
                    dest_binfolder = self.scan_binaries(
                        clone_dir, repo, original_files=original_files)
                self.send_msg(repo=repo,
                              kind='build',
                              url=url,
                              status=build_status,
                              msg=build_msg,
                              build_time=(after_build_time - before_build_time))
                self.built_b_status_list.append((url, self.opt_id))
                logging.info("Build exit %s", build_msg.replace("\n", " "))
                if self.platform == "windows" and build_status == BuildStatus.SUCCESS:
                    build_method.post_processing_pdb(dest_binfolder,
                                                     build_mode, platform,
                                                     repo, compiler_version,
                                                     compiler_flag)
                    zip_file = build_method.post_processing_compress(
                        dest_binfolder, repo, self.opt_id, build_count)
                    repo_fname = dest_binfolder.split("\\")[-1]
                    build_method.post_processing_ftp(
                        self.server_addr, f"{BINPATH}/{repo_fname}/"+zip_file, repo, zip_file)
                    self.send_msg("post_analysis", repo,
                                  file_name=f"/binaries/ftp/{zip_file}")
                    folders.append(dest_binfolder)
                build_count += 1
        else:
            logging.info("Clone FAILURE %s: %s", url, clone_msg)
            if not self.clone_proxy_servers:
                time.sleep(60)
            else:
                time.sleep(1)
        build_method.clean(folders)
        logging.debug("Worker %s finished %s at %s", self.uuid[:5], url,
                      datetime.datetime.now().strftime("%H:%M:%S"))

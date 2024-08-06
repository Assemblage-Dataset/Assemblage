"""
a bootstrap program to let user define and boot a cluster
chain call style

Yihao Sun
"""
import __main__

import argparse
import json
import logging
import os
import threading
import time
from typing import List

import grpc
import tqdm
import yaml

from assemblage.analyze.analyze import get_build_system
from assemblage.coordinator.coordinator import Coordinator
from assemblage.data.object import init_clean_database
from assemblage.data.db import DBManager
from assemblage.protobufs.assemblage_pb2_grpc import AssemblageServiceStub
from assemblage.worker.scraper import DataSource, Scraper
from assemblage.worker.builder import Builder
from assemblage.worker.profile import AWSProfile
from assemblage.worker.postprocess import PostAnalysis, PostProcessor
from assemblage.worker.build_method import cmd_with_output
from sqlalchemy_utils import database_exists

class AssmeblageCluster:

    def __init__(self, name, coordinator_addr="coordinator", aws_mode=False) -> None:
        self.init_gh_flag = False
        self.name = name
        self.docker_network_name = "assemblage-net"
        self.init_docker_network_flag = False
        self.db_addr = "mysql:3306"
        self.db_name = "assemblage"
        self.db_username = "assemblage"
        self.db_password = "assemblage"
        self.db_init_flag = True
        self.db_boot_flag = False
        self.init_json_path = ""
        self.scraper_configs = []
        self.builder_configs = []
        self.postprocessor_configs = []
        self.coordinator_config = {
            "cluster_name": "assemblage",
            "rabbitmq_host": "rabbitmq",
            "rabbitmq_port": 5672,
            "grpc_addr": "[::]:50052",
            "db_path": "mysql+pymysql://assemblage:assemblage@assemblage.com:?/assemblage?charset=utf8mb4",
            "aws_mode": 1
        }
        self.aws_mode = aws_mode
        self.coordinator_addr = coordinator_addr
        self.build_options = []
        self.mq_addr = "rabbitmq"
        self.mq_port = 5672
        self.mq_image = "rabbitmq:3-management"
        self.mq_manage_port = 56723
        self.build_docker_flag = False
        self.aws_profile = None
        self.buidl_sys_callback = get_build_system

    @property
    def grpc_addr(self):
        return f"{self.coordinator_addr}:50052"
    
    def build_system_analyzer(self, build_sys_callback=get_build_system):
        """
        declare how to determine the build system of code fold
        `build_sys_callback`: build_sys_callback(files: path) -> build_system_name
        """
        self.buidl_sys_callback = build_sys_callback
        return self

    def pull_baseimage(self):
        """ use prebuilt `gh` docker base image  """
        self.init_gh_flag = True
        self.build_docker_flag = True
        return self

    def aws(self, aws_profile: AWSProfile):
        """ declare aws setting """
        self.aws_profile = aws_profile
        return self

    def docker_network(self, net_name, init_flag=False):
        """ declare  docker network for assemblage local cluster, if init flag setted a new one will be created """
        self.docker_network_name = net_name
        self.init_docker_network_flag = init_flag
        return self

    def use_new_mysql_local(self):
        """ boot mysql docker container when create cluster """
        self.db_boot_flag = True
        return self

    def mysql(self, db_addr="mysql:3306", db_name="assemblage",
              username="assemblage", password="assemblage",
              init_flag=True):
        """ 
        declare the database connection infomation,
        if need boot mysql after configure please `init_flag` = True
        """
        self.db_addr = db_addr
        self.db_name = db_name
        self.db_username = username
        self.db_password = password
        self.db_init_flag = init_flag
        mysql_conn_str = f'mysql+pymysql://{self.db_username}:{self.db_password}@{self.db_addr}/{self.db_name}?charset=utf8mb4'
        self.coordinator_config["db_path"] = mysql_conn_str
        return self

    def message_broker(self, mq_addr="rabbitmq", mq_port=5672, mq_image="rabbitmq:3-management",
                       mq_manage_port=56723):
        """ declare message broker address """
        self.mq_addr = mq_addr
        self.mq_port = mq_port
        self.mq_image = mq_image
        self.mq_manage_port = mq_manage_port
        return self

    def builder(self, platform="linux", compiler="gcc", build_opt=0,
                docker_image="assemblage-gcc:default",
                blacklist=[
                    "linux", "llvm-mirror", "llvm",
                    "llvm-project", "git", "php-src",
                    "linux-rpi", "tuxonice-kernel",
                    "kernel_samsung_tuna", "freebsd",
                    "freebsd-src", "mangos", "mono",
                    "freebsd-ports", "gcc"
                ],
                custom_build_method=None,
                aws_profile=None,
                worker_thread=1):
        """ declare a builder in cluster"""
        self.builder_configs.append({
            "platform": platform,
            "compiler": compiler, "build_opt": build_opt,
            "blacklist": blacklist,
            "custom_build_method": custom_build_method,
            "docker_image": docker_image,
            "aws_profile": aws_profile,
            "worker_thread": worker_thread
        })
        return self

    def scraper(self, data_resources: List[DataSource], node_memory="20480M"):
        """ declare a scraper with given data resource """
        self.scraper_configs.append({
            "data_sources": data_resources,
            "node_memory": node_memory
        })
        return self

    def post_processor(self, name, analysis: PostAnalysis, opt_id, docker_image,
                       number=1):
        """ declare a postprocesser, a postprocesser can only work on one type of
          build option  """
        self.postprocessor_configs = [{
            "name": name,
            "analysis": analysis,
            "opt_id": opt_id,
            "docker_image": docker_image,
            "number": number
        }]
        return self

    def build_option(self, opt_id, platform="linux", language="c",
                     compiler_name="gcc", compiler_flag="",
                     library="x64",
                     build_command="Debug",
                     build_system="make/cmake"):
        """ declare a build option """
        self.build_options.append(
            {
                "_id": opt_id,
                "platform": platform,
                "language": language,
                "compiler_name": compiler_name,
                "compiler_flag": compiler_flag,
                "build_system": build_system,
                "library": library,
                "build_command": build_command,
            }
        )
        return self

    def init_with_json(self, json_path):
        """ init mysql database with given JSON file contain all repos """
        self.init_json_path = json_path
        return self

    def _prepare_gh(self):
        os.system("docker pull stargazermiao/assemblage-gh")
        os.system("docker tag stargazermiao/assemblage-gh assemblage-gh:base")
        print("Assemblage gh image not found")
        os.system('sh pre_build.sh')
        input("About to stop rabbitmq!")
        os.system("docker stop rabbitmq && docker rm rabbitmq")

    def _build_coordinator_image(self):
        os.system('sh build.sh')

    def _boot_mysql(self):
        cmds = []
        cmds.append("docker pull mysql/mysql-server")
        cmds.append("docker container stop mysql&&docker container rm mysql")
        cmds.append(f"docker run --name=mysql -p 3306:3306 --network={self.docker_network_name} -e MYSQL_ROOT_PASSWORD=assemblage -d mysql/mysql-server")
        for cmd in cmds:
            os.system(cmd)
        
        out, err, exitcode=cmd_with_output("docker exec mysql mysql -u root -passemblage")
        while "2002" in out.decode() or "2002" in err.decode():
            print("MySQL initing, waiting for 5 seconds")
            time.sleep(5)
            out, err, exitcode=cmd_with_output("docker exec mysql mysql -u root -passemblage")
        os.system(f"docker exec -i mysql mysql -u root -passemblage < {os.getcwd()}/assemblage/data/init.sql")
        mysql_conn_str = f'mysql+pymysql://{self.db_username}:{self.db_password}@{self.db_addr}/{self.db_name}?charset=utf8mb4'
        mysql_conn_str_local = f'mysql+pymysql://{self.db_username}:{self.db_password}@localhost:3306/{self.db_name}?charset=utf8mb4'
        assert database_exists(mysql_conn_str_local)
        if self.db_init_flag:
            init_clean_database(mysql_conn_str_local)
        print("DB inited")
        # wirte mysql configure
        self.coordinator_config["db_path"] = mysql_conn_str
        self.coordinator_config["cluster_name"] = self.name

    def _build_image(self):
        os.system("sh build.sh")

    def is_valid_repo_row(self, repo: dict):
        """
        check if repos is in following format
        {'name': name, 
        'url': url,
        'language': language,
        'owner_id': owner_id,
        'description': description[:100],
        'created_at': created_at,
        'updated_at': updated_at,
        'build_system': build_tool}
        """
        standard_pack = {
            "url": "https://api.github.com/repos/rana-raafat/Page-Replacement-Simulator",
            "name": "Page-Replacement-Simulator",
            "description": "",
            "language": "C++",
            "created_at": "2021-12-16 11:40:39",
            "deleted": 0,
            "updated_at": "2021-12-26 16:24:29",
            "forked_commit_id": 0,
            "priority": 0,
            "build_system": "sln"
        }
        if "id" in repo.keys():
            del repo["id"]
        repokeys = list(repo.keys())
        repokeys.sort()
        standard_keys = list(standard_pack.keys())
        standard_keys.sort()
        return standard_keys == repokeys

    def _init_docker_network(self):
        cmd_with_output(f"docker network create {self.docker_network_name}")
        return

    def _init_db(self):
        mysql_conn_str = f'mysql+pymysql://{self.db_username}:{self.db_password}@localhost/{self.db_name}?charset=utf8mb4'
        print(mysql_conn_str)
        db_man = DBManager(mysql_conn_str)
        for opt in self.build_options:
            db_man.add_build_option(**opt)
        if not os.path.exists(self.init_json_path):
            return False

        if self.init_json_path == "":
            return
        with open(self.init_json_path, "r") as repo_json_f:
            parsed_json = json.loads(repo_json_f.read())
            repo_list = parsed_json['repo_list']
            opt_list = parsed_json['opt_list']
            for opt in opt_list:
                opt['enable'] = True
                del opt["id"]
                db_man.add_build_option(**opt)
                print("Build opt sent")
            # check if input file is valid
            count = 0
            for repo in repo_list:
                if not self.is_valid_repo_row(repo):
                    count = count + 1
            print(f"{len(repo_list)} found, {count} invalid, not sending them")
            if input("Build docker images? [y/n]").strip().lower() == 'y':
                for repo in tqdm(repo_list):
                    try:
                        db_man.insert_repos(repo)
                    except AttributeError as err:
                        pass
            db_man.shutdown()

    def generate_config_file(self):
        """ this is for deploy """


    def generate_docker_file(self, name, base_image):
        """ generate dockerfile for a builder """
        base_dir = os.getcwd()
        docker_dir = f"{base_dir}/docker/{name}"
        if not os.path.exists(docker_dir):
            os.mkdir(docker_dir)
        with open(f"{base_dir}/docker/template") as tf:
            t = tf.read().replace("{}", base_image)
            with open(f"{docker_dir}/Dockerfile", "w+") as df:
                df.write(t)

    def build_builder_image(self):
        for bd in self.builder_configs:
            os.system(f"docker build -t {bd['compiler']} -f "
                    f" {os.getcwd()}/docker/{bd['compiler']}/Dockerfile {os.getcwd()}")
        for pc in self.postprocessor_configs:
            os.system(f"docker build -t {pc['name']} -f "
                    f" {os.getcwd()}/docker/{pc['name']}/Dockerfile {os.getcwd()}")

    def generate_cluster_compose_file(self):
        script_name = os.path.basename(__main__.__file__)
        services_dict = {}
        services_dict["rabbitmq"] = {
            "image": self.mq_image,
            "environment": {
                "RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS": "-rabbit consumer_timeout 900000"
            },
            "ports": [
                str(self.mq_manage_port),
                f"{self.mq_port}:{self.mq_port}"
            ]
        }
        services_dict["coordinator"] = {
            "image": "assemblage-gcc:default",
            "command": " ".join([
                    "python3", f" /assemblage/{script_name}"
                    " --type", " coordinator"]),
            "ports": ["50052:50052"],
            "depends_on": ["rabbitmq"],
            "volumes": ["shared-data:/binaries"],
            "deploy": {
                "restart_policy": {"condition": "on-failure"}
            }
        }
        for i in range(len(self.scraper_configs)):
            sc = self.scraper_configs[i]
            services_dict[f"scraper_{i}"] = {
                "image": "assemblage-gcc:default",
                "command": " ".join([
                    "python3", f" /assemblage/{script_name}"
                    " --type", " scraper", " --id", str(i)]),
                "depends_on": ["rabbitmq", "coordinator"],
                "deploy": {
                    "resources": {
                        "limits": {"memory": sc["node_memory"]}
                    },
                    "restart_policy": {"condition": "on-failure"}
                },
                "volumes": ["shared-data:/binaries"]
            }
        
        for i in range(len(self.builder_configs)):
            bd = self.builder_configs[i]
            if (bd["docker_image"] ==  ""):
                continue
            services_dict[f"builder_{i}"] = {
                "image": bd['compiler'],
                "command": f"python3 /assemblage/{script_name} --type builder --id {i}",
                "volumes": ["shared-data:/binaries"],
                "depends_on": ["rabbitmq", "coordinator"],
                "deploy": {
                    # "resources": {
                    #     "limits": {
                    #         "cpu": 4,
                    #         "memory": "8192M"
                    #     }
                    # },
                    "restart_policy": {"condition": "on-failure"}
                }
            }
            self.generate_docker_file(bd["compiler"], bd["docker_image"])

        for i in range(len(self.postprocessor_configs)):
            pc = self.postprocessor_configs[i]
            # for n in range(pc["number"]):
            services_dict[f"postprocessor_{i}"] = {
                "image": pc["name"],
                "command": f"python3 /assemblage/{script_name} --type postprocessor --id {i}",
                "volumes": ["shared-data:/binaries"],
                "depends_on": ["rabbitmq", "coordinator"],
                "deploy": {
                    "restart_policy": {"condition": "on-failure"}
                }
            }
            if pc["number"] > 1:
                services_dict[f"postprocessor_{i}"]["replicas"] = pc["number"]
            self.generate_docker_file(pc["name"], pc["docker_image"])

        py_config_dict = {
            "version": '3',
            "services": services_dict,
            "networks": {
                "default": {"external": {"name": self.docker_network_name}}
            },
            "volumes": {
                "shared-data": None
            }
        }
        with open("docker-compose.yml", "w+") as yf:
            yaml.dump(py_config_dict, yf)

    def _run_scraper(self, scraper_config):
        workers = [object()
                   for i in range(len(scraper_config['data_sources']))]
        threads = [object()
                   for i in range(len(scraper_config['data_sources']))]
        time_now = int(time.time())
        start = time_now - time_now % 86400
        querylap = 14400
        for i in range(len(workers)):
            workers[i] = Scraper(
                rabbitmq_host=self.mq_addr,
                rabbitmq_port=self.mq_port,
                workerid=i,
                data_source=scraper_config['data_sources'][i])
            start -= querylap
            threads[i] = threading.Thread(target=workers[i].run)
        for t in threads:
            t.start()

    def _run_builder(self, builder_config):
        with grpc.insecure_channel(self.grpc_addr) as channel:
            opt = None
            for o in self.build_options:
                if builder_config['build_opt'] == o['_id']:
                    opt = o
            if opt is None:
                logging.error("Buildopt %d not exists", builder_config['build_opt'])
                exit()
            if os.name == 'nt':
                from assemblage.consts import BUILDPATH
                while 1:
                    worker = Builder(
                            self.mq_addr,
                            self.mq_port,
                            AssemblageServiceStub(channel),
                            "builder;windows",
                            platform="windows",
                            build_mode=opt["build_command"],
                            opt_id=builder_config['build_opt'],
                            tmp_dir=BUILDPATH,
                            library=opt['library'],
                            compiler=opt['compiler_name'],
                            compiler_flag=opt['compiler_flag'],
                            aws_profile=self.aws_profile)
                    worker.build_strategy = builder_config["custom_build_method"]
                    t = threading.Thread(target=worker.run)
                    t.start()
                    time.sleep(60)
            else:
                af = self.aws_profile
                while True:
                    worker = Builder(
                        self.mq_addr,
                        self.mq_port,
                        rpc_stub=AssemblageServiceStub(channel),
                        worker_type="builder;linux",
                        platform="linux",
                        opt_id=builder_config['build_opt'],
                        blacklist=builder_config['blacklist'],
                        compiler=builder_config['compiler'],
                        aws_profile=af
                    )
                    worker.build_strategy = builder_config["custom_build_method"]
                    worker.run()
                    time.sleep(600)
        

    def _run_coordinator(self):
        cor = Coordinator(self.mq_addr,
                          self.mq_port,
                          "[::]:50052",
                          self.coordinator_config["db_path"],
                          self.name,
                          self.aws_mode)
        cor.run()

    def _run_postprocesser(self, post_conf):
        time.sleep(10)
        with grpc.insecure_channel(self.grpc_addr) as channel:
            post = PostProcessor(
                self.mq_addr,
                self.mq_port,
                AssemblageServiceStub(channel),
                "postprocesser;linux",
                post_conf["opt_id"],
                self.aws_profile,
                post_conf["analysis"]
            )
            post.run()

    def init(self):
        """ init cluster, generate configure """

    def boot(self):
        """
        Cluster/All nodes boot entrance function
        """
        parser = argparse.ArgumentParser(
            description='Assemblage bootstrap script')
        parser.add_argument('--type', metavar='type', type=str, default='bootstrap',
                            help='type of node, current can be scraper or builder.')
        parser.add_argument('--id', metavar='type', type=int, default=0,
                            help='type of node, current can be scraper or builder.')
        args = parser.parse_args()
        node_type = args.type
        node_id = args.id
        if node_type.strip() == "bootstrap":
            self.generate_cluster_compose_file()
            # os.system("sh start.sh")
            if self.init_docker_network_flag:
                self._init_docker_network()
            if self.db_boot_flag:
                self._boot_mysql()
                self._init_db()
            if self.init_gh_flag:
                self._prepare_gh()
            self._build_coordinator_image()
            self.build_builder_image()
            return
        elif node_type.strip() == "coordinator":
            self._run_coordinator()
        elif node_type.strip() == "scraper":
            sc = self.scraper_configs[node_id]
            self._run_scraper(sc)
        elif node_type.strip() == "builder":
            sc = self.builder_configs[node_id]
            self._run_builder(sc)
        elif node_type.strip() == "postprocessor":
            sc = self.postprocessor_configs[node_id]
            self._run_postprocesser(sc)


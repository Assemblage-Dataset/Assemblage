"""
a terminal interface

Yihao Sun
Daniel Lugo
"""

import argparse
import datetime
import json
import os
import logging
import shutil
import subprocess
from time import sleep, time
import traceback
import time
import requests
import grpc
from prompt_toolkit import PromptSession, prompt
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.validation import ValidationError, Validator
from prompt_toolkit.shortcuts import clear
import pymysql
from tqdm import tqdm

from pyfiglet import Figlet
import plotext as plt
from pypager.source import GeneratorSource
from pypager.pager import Pager
from assemblage.consts import BuildStatus
from assemblage.data.db import DBManager
from assemblage.protobufs.assemblage_pb2 import DumpRequest, RepoRequest, WorkerRequest, BuildRequest, \
    ProgressRequest, Repo, BuildOpt, enableBuildOptRequest, getBuildOptRequest, SetOptRequest
from assemblage.protobufs.assemblage_pb2_grpc import AssemblageServiceStub
from assemblage.data.object import init_clean_database
from assemblage.worker.build_method import cmd_with_output


logging.basicConfig(level=logging.INFO)

# Use Figlet for easier CLI banner rendering, easier to edit
BANNER2 = Figlet(font='slant')

PROMPT = '\U0001F3D7   '

COMMANDS = {
    'help': 'get help info',
    'clear': 'clear screen output',
    'repoinfo': '"repoinfo [cloned or built]": query total cloned or total built repos',
    'workerinfo': 'return  how many work is working and port status',
    'searchrepo': 'search repo by name or name and url, press enter to skip query criteria',
    'clonefails': 'list all cloned repo',
    'build': 'build [repo_nam] : force build some repo immediately by hand',
    'cmd': 'cmd "[a build shell command]" : add a possible build command into system',
    'buildfails': 'buildfails [2 or 10] show all repo build fail',
    'buildopt': 'add a build option (i.e gcc, clang)',
    'progress': 'print status of cloning and building in this hour/day/month',
    'enableBuildOpt': 'enables build option in db',
    'displayBuildOpt': 'Display stored build options',
    'setWorkerOpt': 'setWorkerOpt [uuid] [opt_id] : Set the option type of a worker',
    'export': 'export [start_time] [end_time] : export repos into a json file,'
                       ' time format in "dd/mm/yyyy--hh:mm:s" e.g. export 09/02/2022--00:00:0 12/02/2022--00:00:0'
                       'You can also specify what columns you want to export, e.g. export 09/02/2022--00:00:0 12/02/2022--00:00:0 url,language',
    'dumpconfig': 'Generate config JSON for workers',
    'loadrepo': 'Load repo from json',
    'exit': 'Exit with code 0'
}


def print_repo(repo):
    """ pretty print a repo info """
    _text = f"""
        ID           : {repo._id}
        Name         : {repo.name}
        url          : {repo.url}
        description  : {repo.description}
        created_at   : {repo.created_at}
        updated_at   : {repo.updated_at}
        ---------------------------------
        """
    return _text


def print_build_opt(buildopt):
    """ pretty print build option info """
    _text = f"""
        platform:       {buildopt.platform},
        language:       {buildopt.language},
        compiler_name:  {buildopt.compiler_name},
        compiler_flag:  {buildopt.compiler_flag},
        build_system:   {buildopt.build_system},
        build_command:  {buildopt.build_command},
        library:        {buildopt.library},
        enable:         {str(buildopt.enable)}
    """
    return _text


def print_worker(worker) -> None:
    """ pretty print a worker info """
    _text = f'''
    uuid         : {worker.uuid}
    platform     : {worker.platform}
    job_type     : {worker.job_type}
    opt_id       : {worker.opt_id}
    '''
    print(_text)


def plot_repo_info(total_repos: list, total_query: list, cmd: str) -> None:
    """
    plot repo info
    """
    # pylint: disable=unexpected-keyword-arg, no-member
    plt.clp()
    plt.title(f"Total Repos Vs Total {cmd.capitalize()}")

    plt.plot(total_repos, label=f"Total Repos: {total_repos[1]}")
    plt.plot(total_query, label=f"Total {cmd.capitalize()}: {total_query[1]}")

    plt.scatter(total_repos)
    plt.scatter(total_query)

    plt.figsize(50, 25)
    plt.show()


def print_repo_info(repo_total: int, query_total: int, cmd: str) -> None:
    """ print the meta data of a repo into console """
    _text = f'''
    --------------------
    Total Repos: {repo_total}
    Total {cmd.capitalize()} : {query_total}
    '''
    print(_text)


def paginate_results(content, category):
    """ paginate the result from grpc server """
    contents = content
    p = Pager()

    def send_repos(data, cag):  # Generator needed by pypager
        counter = 0
        for item in iter(data):
            counter += 1
            if cag == "repo":
                yield [("", 'line {}: {}\n'.format(counter, print_repo(item)))]
            elif cag == "buildopt":
                yield [("", 'line {}: {}\n'.format(counter, print_build_opt(item)))]
    p.add_source(GeneratorSource(send_repos(contents, category)))
    p.run()


def parse_cmd(raw_cmd) -> tuple:
    """
    parse a command return command and arg list
    """
    command = raw_cmd.split(' ')[0].strip()
    use = raw_cmd.split(' ')
    # print(use)
    if len(raw_cmd.split(' ')) == 1:
        return command, []
    elif command in ['repoinfo', 'buildfails']:
        # only one arg for these cmd s
        arguments = [raw_cmd.split(' ')[1].strip()]
    elif command in ['build']:
        arguments = [use[1].strip(), use[2].strip()]
    else:
        # string arg
        arguments = raw_cmd.split(' ')[1:]
    return command, arguments


def get_public_ip():
    """ get public ip """
    try:
        return requests.get('https://checkip.amazonaws.com').text.strip()
    except:
        out, err, exit_code = cmd_with_output(
            f"dig +short myip.opendns.com @resolver1.opendns.com", platform='linux')
        return out.decode().strip()


class CommandValidator(Validator):
    """
    validator for prompt_tool
    """

    def validate(self, document):
        text = document.text
        try:
            command, args_list = parse_cmd(text)
        except:
            command = ''
            args_list = []
        if command not in COMMANDS.keys():
            raise ValidationError(message=f'{command} is not a valid command.')
        # check lens of arg
        if command in ['cmd', 'repoinfo', 'buildfails']:
            if len(args_list) != 1:
                raise ValidationError(message='arg number mismatch expect 1.')
        elif command in ['build']:
            if len(args_list) != 2:
                raise ValidationError(message='arg number mismatch expect 2')


class CommandExecutor:
    """
    class context manager for command execution
    """

    def __init__(self, stub: AssemblageServiceStub, server_addr):
        self.server_base = server_addr
        self.stub = stub

    def exec(self, raw_cmd):
        """
        run
        """
        command, cargs = parse_cmd(raw_cmd)
        if command == 'help':
            self.__print_help()
        elif command == 'searchrepo':
            self.__search_repo()
        elif command == 'buildfails':
            self.__query_failed(cargs[0])
        elif command == 'workerinfo':
            self.__worker_info()
        elif command == 'clonefails':
            self.__cloned_failed_repo()
        elif command == 'clear':
            self.__clear()
        elif command == 'repoinfo':
            self.__repo_info(cargs[0])
        elif command == 'build':
            self.__build_repo(cargs[0], cargs[1])
        elif command == 'buildopt':
            self.__add_build_opt()
        elif command == 'progress':
            self.__print_progress_status()
        elif command == 'enableBuildOpt':
            self.__enable_build_opt()
        elif command == 'displayBuildOpt':
            self._display_buildopt()
        elif command == 'dumpconfig':
            self.__generateconfig()
        elif command == 'loadrepo':
            self.__loadrepos()
        elif command == 'exit':
            exit(0)
        elif command == 'setWorkerOpt':
            if len(cargs) != 2:
                print("arguments number wrong see helper!")
            else:
                self.__set_worker_opt(cargs[0], int(cargs[1]))
        elif command == 'export':
            if len(cargs) != 2:
                self.__dump_success()
            elif len(cargs) == 2:
                self.__dump_success(cargs[0], cargs[1])
            else:
                self.__dump_success(cargs[0], cargs[1], cargs[2])

        else:
            print('not impl')

    def __print_help(self) -> None:
        """
        print help info
        """
        for k, v in COMMANDS.items():
            with patch_stdout():
                print('{:<10} : {}'.format(k, v))

    def __query_failed(self, command) -> None:
        """
        print all repo build fail
        """
        if command == '2' or command == '10':
            request = RepoRequest(name=command)
            repos = []
            try:
                for repo in self.stub.failedRepo(request):
                    repos.append(repo)
                paginate_results(repos, "repo")
            except EOFError as e:
                print(e)
                return
        else:
            print("Invalid command! buildfails 2 or buildfails 10")

    def __search_repo(self):
        """
        search a repo by name
        """
        repo_name = prompt("Enter Repo Name: ")
        repo_url = prompt("Enter Repo url: ")
        repo_build_opt = prompt("Enter Repo Build Opt: ")

        if repo_name == '' and repo_url == '' and repo_build_opt == '':
            print("Query not filled. Enter at least repo name")
            return

        if repo_build_opt == '':
            repo_build_opt = 0

        query = f"{repo_name};{repo_url}"

        request = RepoRequest(name=query, opt_id=int(repo_build_opt))
        repos = []
        try:
            for repo in self.stub.queryRepo(request):
                repos.append(repo)
            paginate_results(repos, "repo")
        except EOFError:
            return

    def __dump_success(self, start_time="01/01/2020--00:00:0", end_time="01/12/2030--00:00:0", cols=""):
        """ dump succesful repo into json """
        time_f = "%d/%m/%Y--%H:%M:%S"
        start_timestamp = int(datetime.datetime.strptime(
            start_time, time_f).timestamp())
        end_timestamp = int(datetime.datetime.strptime(
            end_time, time_f).timestamp())
        repo_request = DumpRequest(
            status=BuildStatus.SUCCESS,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp)
        opt_request = getBuildOptRequest(request="get")

        build_opts = [{
            "id": b.id,
            "platform": b.platform,
            "language": b.language,
            "compiler_name": b.compiler_name,
            "compiler_flag": b.compiler_flag,
            "build_system": b.build_system,
            "library": b.library,
            "build_command": b.build_command,
            "enable": b.enable
        } for b in self.stub.getBuildOpt(opt_request) if b.enable]
        print(f"{len(build_opts)} build opt found")

        repos = [{
            "id": repo_info.id,
            "url": repo_info.url,
            "name": repo_info.name,
            "description": repo_info.description,
            "language": repo_info.language,
            "created_at": repo_info.created_at,
            "deleted": repo_info.deleted,
            "updated_at": repo_info.updated_at,
            "forked_commit_id": repo_info.forked_commit_id,
            "priority": repo_info.priority,
            "build_system": repo_info.build_system,
        } for repo_info in self.stub.dumpSuccessRepo(repo_request)]
        print(f"{len(repos)} repos found")

        b_statuses = []
        for b_status in self.stub.dumpSuccessStatus(repo_request):
            b_statuses.append({
                "id": b_status.id,
                "clone_status": b_status.clone_status,
                "build_status": b_status.build_status,
                "build_opt_id": b_status.build_opt_id,
                "repo_id": b_status.repo_id,
                "mod_timestamp": b_status.mod_timestamp,
                "build_time": b_status.build_time})

        print(f"{len(b_statuses)} buildtasks found")
        fname = f'{os.urandom(4).hex()}__{start_time}__{end_time}.json'.replace("/", "_")
        print("Saving")
        with open(fname, 'w') as dump_f:
            out_obj = json.dumps(
                {'buildopt': build_opts,
                 'b_status': b_statuses,
                 "projects": repos
                 },
                indent=4)
            dump_f.write(out_obj)

    def __worker_info(self) -> None:
        """
        get all worker status
        """
        request = WorkerRequest(req='req')
        for _w in self.stub.workerStatus(request):
            print_worker(_w)

    def __cloned_failed_repo(self) -> None:
        """
        get all cloned failed repo save to file (maybe also save to file and
        open in emacs)
        """
        request = RepoRequest()
        repos = []
        try:
            for repo in self.stub.clonedFailedRepo(request):
                repos.append(repo)
            paginate_results(repos)
        except EOFError:
            return

    @classmethod
    def __clear(cls) -> None:
        """
        clear output screen
        """
        clear()

    def __repo_info(self, cmd: str) -> None:
        """
        print total cloned repos
        """
        # TODO: Fix histograms
        request = RepoRequest(name=cmd)
        try:
            response = self.stub.queryRepoInfo(request)
            total_rep = [0, response.total]
            if cmd.lower() == 'cloned':
                total_cloned = [0, response.cloned]
                plot_repo_info(total_rep, total_cloned, 'Cloned')
                print_repo_info(response.total, response.cloned, 'Cloned')
            elif cmd.lower() == 'built':
                total_built = [0, response.built]
                plot_repo_info(total_rep, total_built, 'Built')
                print_repo_info(response.total, response.built, 'Built')
            else:
                print("Invalid Command!")
        except EOFError:
            print("Something went wrong...")
            return

    def __build_repo(self, name: str, url: str) -> None:  # ADDED TESTING
        """
        Build repo on command
        """
        _r = Repo()

        _r._id = 1
        _r.name = name
        _r.url = url.strip()
        _r.description = 'repo'
        _r.language = 'c++'
        _r.created_at = bytes(str(datetime.datetime.now()), encoding='utf-8')
        _r.forked_from = 1
        _r.deleted = 1
        _r.updated_at = bytes(str(datetime.datetime.now()), encoding='utf-8')
        _r.forked_commit_id = 1

        request = BuildRequest(requested_repo=_r, platform='linux')
        response = self.stub.buildRepo(request)
        print(response)

    def __generateconfig(self) -> None:  # ADDED TESTING
        """
        Build repo on command
        """
        worker = prompt(
            "Choose what config you want to generate: \n1:Crawler\n2:Windows worker\n3:Linux worker on other machine\nInput from [1, 2, 3]: ")
        while worker.lower() not in ["1", "2", "3"]:
            worker = prompt(
                "Choose what config you want to generate: \n1:Crawler\n2:Windows worker\n3:Linux worker on other machine\nInput from [1, 2, 3]: ")
        if worker == "1":
            tokens = []
            while True:
                token = prompt(
                    "Input a token then press Enter to input another one\nPress Enter to finish input\n")
                if token == "":
                    break
                else:
                    tokens.append(token)
            with open("assemblage/configure/scraper_config_sample.json") as f:
                configs = json.load(f)
            configs["git_token"] = tokens
            with open("assemblage/configure/scraper_config.json", "w") as f_new:
                f_new.write(json.dumps(configs))
            print("Crawler token saved")
        if worker == "2":
            server_addr = prompt(
                "Do you want to use this machine's public ip? [y/n]: ")
            if server_addr == "y":
                server_addr = get_public_ip()
            else:
                server_addr = prompt("Input server address: ")
            buildoptions = self._get_buildopt()
            for buildoption in buildoptions:
                with open("assemblage/configure/worker_config_sample.json") as f:
                    configs = json.load(f)
                configs["rabbitmq_host"] = server_addr
                configs["grpc_addr"] = f"{server_addr}:50052"
                configs["default_build_opt"] = buildoption.id
                configs["compiler"] = buildoption.compiler_name
                configs["library"] = buildoption.library
                configs["platform"] = buildoption.platform
                configs["build_mode"] = buildoption.build_command
                configs["optimization"] = buildoption.compiler_flag.replace(
                    "-", "")
                configs["random_pick"] = 0
                configs["clone_proxy"] = []
                configs["clone_proxy_token"] = ""
                try:
                    del configs["blacklist"]
                    del configs["build_opt"]
                except KeyError:
                    pass
                with open(f"assemblage/configure/windows_config{buildoption.id}.json", "w") as f_new:
                    json.dump(configs, f_new, indent=4)
                print(f"Windows worker config {buildoption.id} saved")
        if worker == "3":
            with open("assemblage/configure/worker_config_sample.json") as f:
                configs = json.load(f)
            server_addr = prompt("Input coordinator's ip address")
            configs["rabbitmq_host"] = server_addr
            configs["grpc_addr"] = f"{server_addr}:50052"
            buildoptions = self._display_buildopt()
            build_opt_id = prompt("Please choose a build option id to build")
            configs["default_build_opt"] = build_opt_id
            with open("assemblage/configure/worker_config.json", "w") as f_new:
                f_new.write(json.dumps(configs))
            print("Linux worker config saved")

    def __add_build_opt(self) -> None:
        """
        Add a build option into the build option table (i.e gcc, clang)
        :param new_option: new build option to add, taken from the parsed args
        :return: None
        """
        platform = prompt("Enter Platform (linux, windows): ")
        while platform.lower() not in ["linux", "windows"]:
            platform = prompt("Enter Platform (linux, windows): ")
        language = prompt("Enter language (c++, java): ")
        compiler_name = prompt("Enter compiler name (v142, v141, ...): ")
        compiler_flag = prompt("Enter compiler flag (-Od, -O1,... ): ")
        build_system = prompt("Enter build system: (sln, make, ...): ")
        build_command = prompt("Enter build command: (Debug, Release)")
        library = prompt("Enter library (x86, x64,...): ")
        proceed = prompt("\nCommit this entry [y/n]: ")
        if proceed.lower() == 'y':
            confirm = prompt("Confirm to proceed [y/n]: ")
            if confirm.lower() == 'y':
                try:
                    request = BuildOpt(
                        platform=platform,
                        language=language,
                        compiler_name=compiler_name,
                        compiler_flag=compiler_flag,
                        build_system=build_system,
                        build_command=build_command,
                        library=library
                    )
                    response = self.stub.addBuildOpt(request)
                    print(f"\n{response}")
                except EOFError:
                    return
                except Exception as e:
                    print("Something Went Wrong")
                    print(e)
            else:
                print("Entry cancelled")
        else:
            print("Entry cancelled")

    def __print_progress_status(self) -> None:
        # pylint: disable=line-too-long
        request = ProgressRequest(request='req')
        print("Fetching progress status...")
        try:
            response = self.stub.checkProgress(request)
            print(
                "+-------------------------------------------------------------------------------------+")
            print(
                f"Cloned Success: {response.hour_clone}/this hour {response.day_clone}/this 24h {response.month_clone}/this month")
            print(
                f"Clone Fails: {response.hour_fail_clone}/this hour {response.day_fail_clone}/this 24h {response.month_fail_clone}/this month ")
            print(
                f"Build Success: {response.hour_build}/this hour {response.day_build}/this 24h {response.month_build}/this month")
            print(
                f"Build Fails: {response.hour_fail_build}/this hour {response.day_fail_build}/this 24h {response.month_fail_build}/this month")
            print(
                f"Binaries Saved: {response.hour_binary}/this hour {response.day_binary}/this 24h {response.month_binary}/this month")
            print(
                f"Windows binaries Saved: {response.hour_Windows_binary}/this hour {response.day_Windows_binary}/this 24h {response.month_Windows_binary}/this month")
            print(
                "+-------------------------------------------------------------------------------------+")
        except grpc.RpcError as rpc_error:
            if rpc_error.code() == grpc.StatusCode.UNAVAILABLE:
                logging.info(
                    'CLI Failed To connect to any addresses; Coordinator may be inactive')

    def __set_worker_opt(self, uuid, opt_id):
        request = SetOptRequest()
        request.uuid = uuid
        request.opt = opt_id
        request.msg = ""
        self.stub.setWorkerOpt(request)

    def __loadrepos(self):
        mysql_conn_str = prompt("Input db string:")
        if not mysql_conn_str:
            with open("assemblage/configure/coordinator_config.json") as f:
                mysql_conn_str = json.load(f)['db_path']
        db_man = DBManager(mysql_conn_str)
        repo_json_path = prompt("Please input the JSON file path: ")
        if not os.path.exists(repo_json_path):
            print("File not found")
            return False
        with open(repo_json_path, "r") as repo_json_f:
            parsed_json = json.loads(repo_json_f.read())
            print(parsed_json.keys())
            repo_list = parsed_json['projects'] if 'projects' in parsed_json else []
            opt_list = parsed_json['buildopt']
            bstatus_list = parsed_json['b_status']
            print(f"{len(opt_list)} opt found, {len(repo_list)} repos found, {len(bstatus_list)} b_status found")
            if input("Reconstruct databse will delete ALL data in databse! Are you sure? [y/n]").strip().lower() != 'y':
                return
            if input("Reconstruct database? [y/n]").strip().lower() == 'y':
                init_clean_database(mysql_conn_str)
                print("Restore buildopt")
                for opt in opt_list:
                    opt['enable'] = 1
                    opt["_id"] = opt["id"]
                    del opt["id"]
                db_man.bulk_insert_buildopt(opt_list)
                print("Restore repos")
                for repo in repo_list:
                    repo["_id"] = repo["id"]
                    del repo["id"]
                for bstatus in bstatus_list:
                    bstatus["_id"] = bstatus["id"]
                    del bstatus["id"]
                db_man.bulk_insert_repos(repo_list)
                db_man.bulk_insert_b_status(bstatus_list)
                print("Restore done")
        db_man.reset_bstatus()
        db_man.shutdown()

    def __enable_build_opt(self):
        build_option_id = prompt("Enter Build Option ID: ")
        enable = prompt("Enable [true/false]: ")

        if build_option_id == "" or enable == "":
            print("Cannot leave fields empty!")
            return

        if enable.lower() == "true":
            enable = True
        elif enable.lower() == "false":
            enable = False
        else:
            print("Invalid option for enable, [true/false]")
            return

        proceed = prompt("\nCommit this entry [y/n]: ")
        if proceed.lower() == 'y':
            try:
                request = enableBuildOptRequest(
                    _id=int(build_option_id),
                    enable=enable
                )
                response = self.stub.enableBuildOpt(request)
                print(f"Enable Build Option: {response.success}")
            except EOFError:
                return
            except grpc.RpcError as rpc_error:
                if rpc_error.code() == grpc.StatusCode.UNAVAILABLE:
                    logging.info(
                        'CLI Failed To connect to any addresses; Coordinator may be inactive')
                print(f"Another error occurred: {rpc_error}")
                return
        else:
            print("Entry cancelled")

    def _display_buildopt(self):
        request = getBuildOptRequest(request="get")
        try:
            build_options = []
            for build_option in self.stub.getBuildOpt(request):
                build_options.append(build_option)
            paginate_results(build_options, category="buildopt")
            return build_options
        except grpc.RpcError as rpc_error:
            if rpc_error.code() == grpc.StatusCode.UNAVAILABLE:
                logging.info(
                    'CLI Failed To connect to any addresses; Coordinator may be inactive')
            else:
                logging.info(f"RPC Error: {rpc_error}")
            return

    def _get_buildopt(self):
        request = getBuildOptRequest(request="get")
        try:
            build_options = []
            for build_option in self.stub.getBuildOpt(request):
                build_options.append(build_option)
            return build_options
        except grpc.RpcError as rpc_error:
            return []


def init_guide():
    """
    a guide to guide user init data base and boot coordinator
    """
    start_flag = input("coordinator connection not provided, start a coordinator"
                       " initialization guide? [y/n]")
    if start_flag.strip() != 'y':
        return False
    cluster_name = prompt('Please input cluster name:\n')
    images_built = False
    with subprocess.Popen("docker images",
                          stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,
                          shell=True) as process:
        try:
            out, err = process.communicate(timeout=5)
            exit_code = process.wait()
            process.kill()
        except subprocess.TimeoutExpired:
            pass
    out_dec = out.decode()
    print(out_dec)
    test_gh_flag = input(
        "Use testing assemblage-gh image?  (this will use some existing testing github login credential) [y/n]?")
    if test_gh_flag.strip() == "y":
        os.system("docker pull stargazermiao/assemblage-gh")
        os.system("docker tag stargazermiao/assemblage-gh assemblage-gh:base")
    shutdown_flag = input("This will shutdown all rabbitmq and assemblage docker container"
                          ", continue? [y/n]")
    if shutdown_flag.strip() == "y":
        images_built = "assemblage-gh" in out_dec
        if not images_built:
            print("Assemblage gh image not found")
            os.system('sh pre_build.sh')
            input("About to stop rabbitmq!")
            os.system("docker stop rabbitmq && docker rm rabbitmq")
    test_mysql_flag = prompt("Boot an mysql testing docker container? [y/n]?")
    if test_mysql_flag.strip() == 'y':
        os.system("docker pull mysql/mysql-server")
        os.system("docker container stop mysql&&docker container rm mysql")
        os.system("docker run --name=mysql -p 3306:3306 --network=assemblage-net -e MYSQL_ROOT_PASSWORD=assemblage -d mysql/mysql-server")
        print("Booting mysql...")
        sleep(20)
        os.system(
            "docker exec -i mysql mysql -u root -passemblage < ./assemblage/data/default_user.sql")
        # all default
        db_addr = "mysql"
        db_addr = db_addr + ':3306'
        db_name = "assemblage"
        user_name = "assemblage"
        user_pass = "assemblage"
        mysql_conn_str = f'mysql+pymysql://{user_name}:{user_pass}@{db_addr}/{db_name}?charset=utf8mb4'
        mysql_conn_str_local = f'mysql+pymysql://{user_name}:{user_pass}@localhost:3306/{db_name}?charset=utf8mb4'
    else:
        db_addr = prompt(
            'Please input MySQL connection address e.g. 172.18.0.5:3306\n')
        db_name = prompt('Please input MySQL database used for Assemblage\n')
        user_name = prompt('Please input MySQL username e.g. assemblage\n')
        user_pass = prompt('Please input MySQL password\n')
        if not db_addr:
            db_addr = "mysql:3306"
        if not db_name:
            db_name = "assemblage"
        if not user_name:
            user_name = "root"
        if not user_pass:
            user_pass = "assemblage"
        mysql_conn_str = f'mysql+pymysql://{user_name}:{user_pass}@{db_addr}/{db_name}?charset=utf8mb4'
        mysql_conn_str_local = mysql_conn_str
    wipe_flag = input("clean and initialize db(all data will be wiped)? [y/n]")
    if wipe_flag.strip().lower() == 'y':
        init_clean_database(mysql_conn_str_local)
    print("write configure to disk...")
    # clean and stage old configure file
    coord_config_path_old = "assemblage/configure/coordinator_config.json.old"
    coord_config_path = "assemblage/configure/coordinator_config.json"
    if os.path.exists(coord_config_path_old):
        os.remove(coord_config_path_old)
    shutil.copy(coord_config_path, coord_config_path_old)
    with open(coord_config_path_old, 'r') as f_old:
        config_json = json.loads(f_old.read())
        config_json['db_path'] = mysql_conn_str
        config_json['cluster_name'] = cluster_name
        with open(coord_config_path, "w") as f_new:
            f_new.write(json.dumps(config_json))
    if input("Build docker images? [y/n]").strip().lower() == 'y':
        print("Building docker image...")
        os.system("sh build.sh")
    # test_worker_flag = input("Boot an testing aws worker? [y/n]")
    # if test_worker_flag.strip().lower() == 'y':

    input_file_flag = input(
        "Initialize database with JSON(check README for data format)? [y/n]")
    if input_file_flag.strip() == 'y':
        db_man = DBManager(mysql_conn_str)
        repo_json_path = input("Please input the JSON file path: ")
        if not os.path.exists(repo_json_path):
            return False
        with open(repo_json_path, "r") as repo_json_f:
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
                if not is_valid_repo_row(repo):
                    count = count + 1
            print(f"{len(repo_list)} found, {count} invalid, not sending them")
            if input("Build docker images? [y/n]").strip().lower() == 'y':
                for repo in tqdm(repo_list):
                    try:
                        db_man.insert_repos(repo)
                    except AttributeError as err:
                        pass
            db_man.shutdown()
    print("Waiting...")
    for i in range(3):
        time.sleep(1)
    print("Rebooting system, shouldn't take long...")
    os.system("sh start.sh")
    print("Configure finish")
    exit()


def is_valid_repo_row(repo: dict):
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


def main(server_addr):
    """
    entrance for CLI
    """
    if (server_addr is None) or server_addr == "":
        if not init_guide():
            return
    print(BANNER2.renderText("Assemblage"))  # Rendering banner
    print("A Tool can automatically scrape and build github c/c++ repo~, Ctrl+D to exit")
    cmd_completer = WordCompleter(list(COMMANDS.keys()), True)
    cmd_validator = CommandValidator()
    session = PromptSession(completer=cmd_completer)

    with grpc.insecure_channel(server_addr) as channel:
        stub = AssemblageServiceStub(channel)
        executor = CommandExecutor(stub, server_addr)
        # try:
        #     executor.exec('progress')
        # except grpc.RpcError as rpc_error:  # catch coordinator not being active
        #     traceback.print_exc()
        #     if rpc_error.code() == grpc.StatusCode.UNAVAILABLE:
        #         logging.info(
        #             'CLI Failed To connect to any addresses; Coordinator may be inactive')
        while True:
            try:
                cmd = session.prompt(PROMPT, validator=cmd_validator)
                executor.exec(cmd)
            except KeyboardInterrupt:
                continue
            except EOFError:
                break
            except grpc.RpcError as rpc_error:
                if rpc_error.code() == grpc.StatusCode.UNAVAILABLE:
                    logging.info('CLI Failed To connect to any addresses; '
                                 'Coordinator may be inactive')
                    continue

    print('bye (TvT)/ ~')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='CLI for query build status & system health')
    parser.add_argument('--server',
                        metavar='server_addr',
                        type=str,
                        # required=False,
                        help='the address of status server')

    args = parser.parse_args()
    main(args.server)

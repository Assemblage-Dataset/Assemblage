"""
RPC service of coordinator

Yihao Sun
Daniel Lugo
"""

import time
import hashlib
import logging

from assemblage.protobufs.assemblage_pb2_grpc import AssemblageService
from assemblage.protobufs.assemblage_pb2 import RegisterResponse, RepoInfoResponse, \
    BuildResponse, BinaryResponse, CmdResponse, ProgressResponse, enableBuildOptResponse, \
    PongResponse, SetOptResponse

from assemblage.consts import BIN_DIR
from assemblage.data.db import DBManager
from assemblage.coordinator.convert import pack_bstatus_msg, unpack_repo_msg, pack_repo_msg, pack_worker_msg, \
     pack_buildOpt_msg

logging.basicConfig(level=logging.INFO)


class InfoService(AssemblageService):
    """ rpc server stub, has a bg thread check if all worker is alive """

    def __init__(self, db_addr):
        self.db_man = DBManager(db_addr)
        self.workers = []

    def queryRepo(self, request, _context):
        # pylint: disable=arguments-differ
        """ search repo info by repo name """
        repo_name, repo_url = request.name.split(";")[0], request.name.split(";")[1]
        opt_id = request.opt_id
        logging.info('query repo name contains %s %s', repo_name, repo_url)
        for repo in self.db_man.search_repo(
            repo_name=repo_name,
            repo_url=repo_url,
            build_opt=opt_id):
            repo = pack_repo_msg(repo)
            yield repo

    def failedRepo(self, request, _context):
        # pylint: disable=arguments-differ

        """ print all build failed repo """
        logging.info(">>>>>>>>>>>>>>>>>>>Logging Failed builds")
        for repo in self.db_man.find_repo_by_status(clone_status=3, build_status=request.name):
            print(f">>>>>>>>>>>>>>>>>>>>>>>>>>>>> {repo}")
            repo = pack_repo_msg(repo)
            yield repo

    def ping(self, request, _context):
        """ keep alive check and job type control """
        worker_ping = None
        rep = PongResponse()
        rep.uuid = request.uuid
        rep.task = request.task
        for _w in self.workers:
            if _w['uuid'] == request.uuid:
                worker_ping = _w
        if not worker_ping:
            rep.ping = 0
            rep.msg = "worker id not registed or cleaned"
        else:
            worker_ping['timestamp'] = time.time()
            rep.ping = 1
            rep.task = worker_ping['opt_id']
            rep.msg = "success"
        return rep


    def registWorker(self, request, _context):
        # pylint: disable=arguments-differ

        """ register a work into coordinator """
        compiler_ = request.type.split(";")[0]
        platform_ = request.type.split(";")[1]
        opt_ = request.opt
        uuid = request.uuid
        self.workers.append({
            'job_type': compiler_,
            'pid': request.pid,
            'platform': platform_,
            'opt_id': opt_,
            'timestamp': time.time(),
            'uuid': uuid
        })
        logging.info('work %s is registered', request.uuid)
        response = RegisterResponse(code=1, msg='success')
        return response

    def workerStatus(self, _request, _context):
        # pylint: disable=arguments-differ
        """ return all worker in current system """
        for _w in self.workers:
            print(_w)
            _w = pack_worker_msg(_w)
            yield _w

    def clonedFailedRepo(self, _request, _context):
        # pylint: disable=arguments-differ
        """ return all repo in current system fail to clone """
        for repo in self.db_man.find_repo_by_status(clone_status=2, build_status=0):
            repo = pack_repo_msg(repo)
            yield repo

    def queryRepoInfo(self, request, _context):
        # pylint: disable=arguments-differ
        """ return total cloned or built repos """
        total_repos, total_query = self.db_man.query_repo_info(
            command=request.name)
        info_response = RepoInfoResponse(
            total=total_repos, cloned=total_query, built=total_query)
        return info_response

    def sendBinary(self, request, _context):
        # pylint: disable=arguments-differ
        """ harvest binary """
        buffer = []
        for req_chunk in request:
            # bname = req_chunk.name
            buffer.append(req_chunk)
        buffer.sort(key=lambda c: c.seq)
        bdata = b''
        for c in buffer:
            bdata += c.content
        h = hashlib.sha256()
        h.update(bdata)
        hsh = h.hexdigest()
        bin_dir = BIN_DIR + '/' + hsh
        with open(bin_dir, 'wb+') as f:
            f.write(bdata)
        response = BinaryResponse(code=0, msg='')
        return response

    def buildRepo(self, request, _context):
        # pylint: disable=arguments-differ
        """
        Build repo on command. Using repo name, url, and priority
        BuildResponse:
        bool is_successful = 1;
        string return_message = 2;
        string platform = 3;
        """
        print("COORDINATOR CALLING DB")
        self.db_man.insert_repos(unpack_repo_msg(request.requested_repo))
        response = BuildResponse(is_successful=3, return_message='Successful', platform='linux')

        return response

    def addBuildOpt(self, request, _context):
        # pylint: disable=arguments-differ
        """
        add build option to BuildOpt table
        """
        print("COORDINATOR CALLING DB - ADD BUILD OPT")
        self.db_man.add_build_option(
            platform=request.platform, language=request.language,
            compiler_name=request.compiler_name, compiler_flag=request.compiler_flag,
            build_system=request.build_system, build_command=request.build_command,
            library=request.library)
        response = CmdResponse(status="Successful")
        return response

    def checkProgress(self, request, _context):
        # pylint: disable=arguments-differ
        repo_results = self.db_man.query_progress()
        response = ProgressResponse(
            hour_clone=repo_results['hour_clone'],
            day_clone=repo_results['day_clone'],
            month_clone=repo_results['month_clone'],
            hour_build=repo_results['hour_build'],
            day_build=repo_results['day_build'],
            month_build=repo_results['month_build'],
            hour_fail_clone=repo_results['hour_fail_clone'],
            day_fail_clone=repo_results['day_fail_clone'],
            month_fail_clone=repo_results['month_fail_clone'],
            hour_fail_build=repo_results['hour_fail_build'],
            day_fail_build=repo_results['day_fail_build'],
            month_fail_build=repo_results['month_fail_build'],
            hour_binary=repo_results['hour_binary'],
            day_binary=repo_results['day_binary'],
            month_binary=repo_results['month_binary'],
            month_Windows_binary=repo_results['month_Windows_binary'],
            hour_Windows_binary=repo_results['hour_Windows_binary'],
            day_Windows_binary=repo_results['day_Windows_binary']
        )
        return response

    def enableBuildOpt(self, request, _context):
        """
        Enable build opt in DB
        """
        # pylint: disable=arguments-differ
        enable_attempt = self.db_man.enable_build_option(_id=request._id,
                                                         is_enabled=request.enable)
        response = enableBuildOptResponse(success=enable_attempt)
        return response

    def getBuildOpt(self, request, _context):
        """
        Display Stored Build Options
        """
        # pylint: disable=arguments-differ
        for option in self.db_man.display_build_options():
            build_option = pack_buildOpt_msg(option)
            yield build_option

    def setWorkerOpt(self, request, _context):
        """
        set job option for a worker
        """
        status_flag = False
        for worker in self.workers:
            if worker['uuid'] == request.uuid:
                status_flag = True
                worker['opt_id'] = request.opt
        rep = SetOptResponse()
        if status_flag:
            rep.status = 1
            rep.msg = "success"
        else:
            rep.status = 0
            rep.msg = "fail"
        rep.msg = ""
        return rep

    def dumpSuccessRepo(self, request, _context):
        """
        dump the successful built repo in a given time
        """
        logging.info("RPC dumpSuccessRepo")

        repos = self.db_man.dump_repos(
            request.status,
            request.start_timestamp,
            request.end_timestamp)
        logging.info("%s, %s, %s", request.status,
            request.start_timestamp,
            request.end_timestamp)
        logging.info("%d found", len(repos))
        for r in repos:
            repo = pack_repo_msg(r)
            yield repo
    
    def dumpSuccessStatus(self, request, _context):
        """
        dump all success build status
        """
        logging.info("RPC dumpSuccessStatus")
        b_status_list = self.db_man.dump_b_status(
            request.status,
            request.start_timestamp,
            request.end_timestamp)
        for _b in b_status_list:
            b_status = pack_bstatus_msg(_b)
            yield b_status
        
    # def restoreBstatus(self, request, _context):
    #     """
    #     Insert the b_status back to databse
    #     """
    #     logging.info("RPC restoreBstatus")

        
    #     logging.info("%s, %s, %s", request.status,
    #         request.start_timestamp,
    #         request.end_timestamp)
    #     logging.info("%d found", len(repos))
    #     for r in repos:
    #         repo = pack_repo_msg(r)
    #         yield repo

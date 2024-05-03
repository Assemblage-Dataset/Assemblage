'''
convert a grpc message into a python database object

Yihao Sun
'''

from assemblage.data.object import BuildDO, RepoDO
from assemblage.protobufs.assemblage_pb2 import BStatus, Repo, Worker, BuildOpt


def pack_repo_msg(repodo):
    """ convert a RepoDO object into grpc message """
    size = 0 if "size" not in repodo else repodo["size"]
    return Repo(
        id=repodo._id,
        url=repodo.url,
        name=repodo.name,
        description=repodo.description,
        language=repodo.language,
        created_at=str(repodo.created_at),
        forked_from=repodo.fork_from,
        deleted=repodo.deleted,
        updated_at=str(repodo.updated_at),
        forked_commit_id=repodo.forked_commit_id,
        priority=repodo.priority,
        build_system=repodo.build_system,
        size=size
    )


def unpack_repo_msg(repo_info):
    """ convert a grpc message object into RepoDO object """
    return RepoDO(
        url=repo_info.url,
        name=repo_info.name,
        description=repo_info.description,
        language=repo_info.language,
        created_at=repo_info.created_at,
        # fork_from=repo_info.fork_from,
        deleted=repo_info.deleted,
        updated_at=repo_info.updated_at,
        forked_commit_id=repo_info.forked_commit_id,
        priority=repo_info.priority,
        build_system=repo_info.build_system
    )


def pack_worker_msg(worker_info):
    """ convert a grpc message object into Worker object """
    return Worker(
        pid=int(worker_info['pid']),
        platform=worker_info['platform'],
        job_type=worker_info['job_type'],
        opt_id=int(worker_info['opt_id']),
        uuid=worker_info['uuid']
    )


def unpack_bianry_msg(bin_info):
    """ convert a grpc message object into BuildDO object """
    return BuildDO(
        file_name=bin_info['file_name'],
        status_id=bin_info['status_id']
    )


def pack_buildOpt_msg(option):
    """ convert BuildOpt object into RPC message """
    return BuildOpt(
        id=option._id,
        platform=option.platform,
        language=option.language,
        compiler_name=option.compiler_name,
        compiler_flag=option.compiler_flag,
        build_system=option.build_system,
        build_command=option.build_command,
        library=option.library,
        enable=option.enable
    )


def pack_bstatus_msg(b_status):
    """ convert Status object to RPC message """

    build_time = int(b_status.build_time) if int(
        b_status.build_time) > 0 else 0
    id = int(b_status._id) if int(b_status._id) > 0 else 0
    mod_timestamp = int(b_status.mod_timestamp) if int(
        b_status.mod_timestamp) > 0 else 0

    return BStatus(
        id=id,
        priority=int(b_status.priority),
        clone_status=int(b_status.clone_status),
        clone_msg=str(b_status.clone_msg),
        build_status=int(b_status.build_status),
        build_msg=str(b_status.build_msg),
        build_opt_id=int(b_status.build_opt_id),
        repo_id=int(b_status.repo_id),
        mod_timestamp=mod_timestamp,
        build_time=build_time
    )

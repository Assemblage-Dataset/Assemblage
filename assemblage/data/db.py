"""
Database access management

Yihao Sun
"""

import datetime
import random
import time
import logging

import sqlalchemy.exc
from sqlalchemy import select, update, create_engine, func, or_
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import desc, true
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql import Insert

from assemblage.data.object import BuildDO, BuildOpt, RepoDO, Status
from assemblage.consts import BuildStatus, SUPPORTED_LANGUAGE


@compiles(Insert, "mysql")
def mysql_upsert(insert, compiler, **kw):
    '''
    a monkey patch to make all mysql insert upsert so we don't fail on
    duplicated repo insertion, this is too broad, actually affect every
    insert query, but in our current, all insertion need to become upsert
    so maybe it is okay for now.
    '''
    return compiler.visit_insert(insert.prefix_with("IGNORE"), **kw)


class DBManager:
    """ manager for db query and connection """

    def __init__(self, db_addr):
        self.engine = create_engine(db_addr, echo=False,
                                    pool_pre_ping=True,
                                    connect_args={'connect_timeout': 100})

    def shutdown(self):
        """ Close DB connection """
        self.engine.dispose()

    def find_repo_by_id(self, repo_id):
        """ fetch a repo object from database by it's id """
        with Session(self.engine) as session:
            query = select(RepoDO).where(RepoDO._id == repo_id)
            result = session.execute(query).first()
            return result[0]

    def find_status_by_id(self, status_id):
        with Session(self.engine) as session:
            query = select(Status).where(Status._id == status_id)
            result = session.execute(query).first()
            return result[0]

    def find_one_undisasmed_bin(self):
        """ pop first binary haven't run ddisasm """
        with Session(self.engine) as session:
            query = select(BuildDO).where(BuildDO.disasmed == False).limit(1)
            result = session.execute(query)
            return result[0][0]

    def update_undisasmed_bin(self, bin_id):
        """  set a binary disasmed """
        with Session(self.engine) as session:
            query = update(BuildDO).values(
                disasmed=True).where(BuildDO._id == bin_id)
            session.execute(query)

    def find_build_opt_by_id(self, opt_id):
        """ fetch a build object from database by it's id """
        with Session(self.engine) as session:
            query = select(BuildOpt).where(BuildOpt._id == opt_id)
            result = session.execute(query).first()
            return result[0]

    def find_repo_by_status(self, clone_status, build_status, build_opt_id=None, limit=-1):
        """ find possible build target repo by given build/clone info """
        with Session(self.engine) as session:
            query = select(RepoDO).join_from(RepoDO, Status)
            if build_opt_id is not None:
                query = query.where(
                    Status.clone_status == clone_status,
                    Status.build_status == build_status,
                    Status.build_opt_id == build_opt_id
                )
            else:
                query = query.where(
                    Status.clone_status == clone_status,
                    Status.build_status == build_status)
            query = query.order_by(desc(Status.priority))
            if limit > 0:
                result = session.execute(query.limit(limit))
            else:
                result = session.execute(query)
            repos = []
            for _s in result:
                repos.append(_s[0])
            return repos

    def find_status_by_repoid(self, repod_id):
        with Session(self.engine) as session:
            query = select(Status).where(Status.repo_id == repod_id)
            statuses = []
            result = session.execute(query)
            for _s in result:
                statuses.append(_s[0])
            return statuses

    # TODO: refactor to add repodo size limit
    def find_status_by_status_code(self, clone_status, build_opt_id, build_status=None, limit=-1):
        """ find lines of record in status table by specific build/clone status code """
        with Session(self.engine) as session:
            # make sure every type of worker has same chance to work, and also make query faster
            enabled_opt_query = select(BuildOpt).where(
                BuildOpt._id == build_opt_id)
            boptid = session.execute(enabled_opt_query).all()
            build_sys = boptid[0][0].build_system
            if build_status:
                query = select(Status).join_from(Status, RepoDO).where(
                    Status.clone_status == clone_status,
                    Status.build_status == build_status,
                    Status.build_opt_id == build_opt_id,
                )
            else:
                query = select(Status).join_from(Status, RepoDO).where(
                    Status.clone_status == clone_status,
                    Status.build_opt_id == build_opt_id,
                    RepoDO.build_system.contains(build_sys),
                )
            if limit > 0:
                query = query.limit(limit)
            result = session.execute(query)
            statuses = []
            for _s in result:
                statuses.append(_s[0])
            return statuses

    def reset_timeout_status(self, timeout):
        """ reset all timeout status record back to uncloned """
        with Session(self.engine) as session:
            query = update(Status).values(
                clone_status=BuildStatus.INIT,
                build_status=BuildStatus.INIT).where(
                Status.clone_status == BuildStatus.PROCESSING,
            )
            session.execute(query)
            session.commit()

    def search_repo(self, repo_name: str, repo_url: str, build_opt: int):
        """
        fetch a repo info from DB by it's name, return all meta info
        of a repo in py dict, attribute plz see db scheme
        original sql : SELECT * FROM  projects WHERE name LIKE ?
        """
        if repo_name != '' and repo_url != '' and build_opt != 0:
            print(
                f"Searching NAME and URL and BUILDOPT: {repo_name}; {repo_url}; {build_opt}")
            with Session(self.engine) as session:
                query = select(Status).join_from(RepoDO, BuildOpt).where(
                    RepoDO.name.like(f"%{repo_name}%"),
                    RepoDO.url.like(f"%{repo_url}%"),
                    BuildOpt._id.like(f"%{build_opt}%")
                )
                result = session.execute(query)
                for _r in result:
                    yield _r
        elif repo_name != '' and repo_url != '':
            print(f"Searching NAME and URL: {repo_name}; {repo_url}")
            with Session(self.engine) as session:
                result = session.query(RepoDO).where(RepoDO.name.like(f"%{repo_name}%"),
                                                     RepoDO.url.like(f"%{repo_url}%"))
                for _r in result:
                    yield _r
        elif repo_name != '' and build_opt != 0:
            # BuildOpt
            print(f"Searching NAME and BuildOpt: {repo_name}; {build_opt}")
            with Session(self.engine) as session:
                result = session.query(RepoDO).where(RepoDO.name.like(f"%{repo_name}%"),
                                                     BuildOpt._id.like(f"%{build_opt}%"))
                for _r in result:
                    yield _r
        else:
            print(f"SEARCHING FOR NAME ONLY: {repo_name}")
            with Session(self.engine) as session:
                result = session.query(RepoDO).filter(
                    RepoDO.name.like(f'%{repo_name}%'))
                for _r in result:
                    yield _r

    def update_repo_status(self, url=None, opt_id=None, status_id=None, build_time=-1,
                           build_status=None, build_msg='', clone_status=None,
                           clone_msg='', commit_hexsha=''):
        """ update the build/clone status of a repo for one build option """
        status_val = {'mod_timestamp': time.time(), 'build_time': build_time}
        if build_status is None and clone_status is None:
            return
        if build_status is not None:
            status_val['build_status'] = build_status
            status_val['build_msg'] = build_msg
        if clone_status is not None:
            status_val['clone_status'] = clone_status
            status_val['clone_msg'] = clone_msg
        status_val['commit_hexsha'] = commit_hexsha
        with Session(self.engine) as session:
            # fetch qualified repo
            # if status_id is not None:
            update_stmt = update(Status).values(
                **status_val).where(Status._id == status_id)
            session.execute(update_stmt)
            session.commit()

    def query_repo_info(self, command: str) -> tuple:
        """
        find total cloned repos or total build
        """
        with Session(self.engine) as session:
            total_repos = session.query(func.count(Status._id)).all()[0][0]

        print(f">>>>>>>>>>>>TOTAL REPOS: {total_repos}")
        if command == 'cloned':
            with Session(self.engine) as session:
                total_cloned = session.query(func.count(Status._id)).where(
                    Status.clone_status == BuildStatus.SUCCESS).all()[0][0]
                print(f">>>>>>>>>>>>TOTAL CLONED: {total_cloned}")
            return total_repos, total_cloned
        elif command == 'built':
            with Session(self.engine) as session:
                total_built = session.query(func.count(Status._id)).where(
                    Status.build_status == BuildStatus.SUCCESS).all()[0][0]
            return total_repos, total_built
        else:
            return total_repos, -1

    def bulk_insert_repos(self, repo_msg_list):
        """ used to import lot of repos at a time """
        # clean id if exists in message
        for repo_msg in repo_msg_list:
            if "id" in repo_msg.keys():
                del repo_msg["id"]
            if "_id" in repo_msg.keys():
                del repo_msg["_id"]
        with Session(self.engine) as session:
            repo_list = [RepoDO(**repo_msg) for repo_msg in repo_msg_list]
            session.bulk_save_objects(repo_list)
            session.commit()

    def bulk_insert_buildopt(self, opt_msg_list):
        for opt_msg in opt_msg_list:
            if "id" in opt_msg.keys():
                del opt_msg["id"]
            if "_id" in opt_msg.keys():
                del opt_msg["_id"]
        with Session(self.engine) as session:
            opt_list = [BuildOpt(**opt_msg) for opt_msg in opt_msg_list]
            session.bulk_save_objects(opt_list)
            session.commit()

    def bulk_insert_b_status(self, b_status_msg_list):
        """ this assume all repo and build option used in b_status is already in database """
        with Session(self.engine) as session:
            bstatus_list = [Status(**status_msg)
                            for status_msg in b_status_msg_list]
            session.bulk_save_objects(bstatus_list)
            session.commit()

    def insert_b_status(self, b_status_msg):
        with Session(self.engine) as session:
            _s = Status()
            _s.clone_status = BuildStatus.INIT
            _s.clone_msg = ''
            _s.build_status = BuildStatus.INIT
            _s.build_msg = ''
            _s.build_opt_id = b_status_msg["build_opt_id"]
            _s.mod_timestamp = int(b_status_msg["mod_timestamp"])
            _s.repo_id = b_status_msg["repo_id"]
            _s.build_time = 0
            session.add(_s)
            session.flush()
            session.commit()
        return 1

    def insert_repos(self, repos_msg, cascade=True, repoonly=False):
        """
        Query repo to build on command
        if cascade is `True`, it will also add possible b_status for it
        since current database is very dirty, many duplicate URL,
        so have to use some strange code here
        https://api.github.com/lua/lua
        TODO: maybe change ORM to core API, so we can avoid upsert problem,
        but this may make query complicated
        """
        with Session(self.engine) as session:
            # looking for if a repo exists
            repo = RepoDO(**repos_msg)
            # t_prev = time.time()
            session.add(repo)
            session.flush()
            if "_id" in repos_msg and repo._id != repos_msg["_id"]:
                logging.info("Insert repo error %s", repos_msg['url'])
                return 0
            all_opt = session.execute(select(BuildOpt)).all()
            # logging.info("%s", all_opt)
            if not repoonly:
                for opt in all_opt:
                    if repos_msg['build_system'] in opt[0].build_system:
                        _s = Status()
                        _s.clone_status = BuildStatus.INIT
                        _s.clone_msg = ''
                        _s.build_status = BuildStatus.INIT
                        _s.build_msg = ''
                        _s.build_opt_id = opt[0]._id
                        _s.mod_timestamp = int(time.time())
                        _s.repo_id = repo._id
                        session.add(_s)
            session.commit()
        return 1

    def insert_binary(self, file_name, description, status_id):
        """
        add a binary record into database, 1 buildopt may have multiple binaries.
        and binaries may already deleted on disk
        """
        new_bin = BuildDO(
            file_name=file_name, description=description,
            status_id=status_id)
        with Session(self.engine) as session:
            session.add(new_bin)
            session.commit()

    def insert_build_option(self, optmsg):
        with Session(self.engine) as session:
            opt = BuildOpt(**optmsg)
            session.add(opt)
            session.flush()
            session.commit()

    def add_build_option(self, _id, platform, language, compiler_name, compiler_flag,
                         build_system, build_command, library, enable=True):
        """
        insert build option into BuildOpt table for repo contain certain build system&language
        """
        with Session(self.engine) as session:
            opt = BuildOpt()
            opt._id = _id
            opt.platform = platform
            opt.language = language
            opt.compiler_name = compiler_name
            opt.compiler_flag = compiler_flag
            opt.build_system = build_system
            opt.build_command = build_command
            opt.library = library
            opt.enable = enable
            session.add(opt)
            query_repo = select(RepoDO)
            repos = session.execute(query_repo)
            status_ = []
            for repo in repos:
                # logging.info("Adding buildopt %s, repo is %s", build_system, repo[0].build_system)
                if build_system in repo[0].build_system:
                    new_status = Status(
                        repo_id=repo[0]._id,
                        build_opt_id=opt._id
                    )
                    status_.append(new_status)
            session.bulk_save_objects(status_)
            session.commit()

    def add_build_option_without_repo(self, platform, language, compiler_name, compiler_flag,
                                      build_system, build_command, library, enable=True, _id=-1):
        """
        insert build option into BuildOpt table for repo contain certain build system&language
        """
        with Session(self.engine) as session:
            opt = BuildOpt()
            opt._id = _id
            opt.platform = platform
            opt.language = language
            opt.compiler_name = compiler_name
            opt.compiler_flag = compiler_flag
            opt.build_system = build_system
            opt.build_command = build_command
            opt.library = library
            opt.enable = enable
            session.add(opt)
            session.commit()

    def query_progress(self):
        """
        query the repo's cloned, and built in the past hour/day/month
        :return: counts
        """
        interval_map = {
            'hour': 60 * 60,
            'day': 24 * 60 * 60,
            'month': 30 * 24 * 60 * 60
        }
        data_result = {}
        with Session(self.engine) as session:
            for time_str, interval in interval_map.items():
                data_result[f'{time_str}_clone'] = int(session.query(func.count(Status._id)).
                                                       where(
                    Status.clone_status == BuildStatus.SUCCESS,
                    Status.mod_timestamp > int(time.time()) - interval
                ).all()[0][0])
                data_result[f'{time_str}_fail_clone'] = int(session.query(func.count(Status._id)).
                                                            where(
                    Status.clone_status == BuildStatus.FAILED,
                    Status.mod_timestamp > int(time.time()) - interval
                ).all()[0][0])
                data_result[f'{time_str}_build'] = int(session.query(func.count(Status._id)).
                                                       where(
                    Status.build_status == BuildStatus.SUCCESS,
                    Status.mod_timestamp > int(time.time()) - interval
                ).all()[0][0])
                data_result[f'{time_str}_fail_build'] = int(session.query(func.count(Status._id)).
                                                            where(
                    or_(
                        Status.build_status == BuildStatus.FAILED,
                        Status.build_status == BuildStatus.COMMAND_FAILED
                    ),
                    Status.mod_timestamp > int(time.time()) - interval
                ).all()[0][0])
                cur_dtime = datetime.datetime.fromtimestamp(
                    int(time.time()) - interval)
                data_result[f'{time_str}_binary'] = int(session.query(func.count(BuildDO._id)).
                                                        where(
                    BuildDO.build_date > cur_dtime
                ).all()[0][0])
                data_result[f'{time_str}_Windows_binary'] = int(session.query(func.count(BuildDO._id)).
                                                                where(
                    BuildDO.build_date > cur_dtime,
                    or_(BuildDO.file_name.endswith(".exe"),
                        BuildDO.file_name.endswith(".dll"))
                ).all()[0][0])
        # logging.info(data_result)
        return data_result

    def dump_repos(self, status, start_timestamp: int, end_timestamp: int):
        """ dump the successful binary in an given time period (time in int) """
        with Session(self.engine) as session:
            succesful_repos = session.query(RepoDO).join(Status).where(
                Status.mod_timestamp > start_timestamp,
                Status.mod_timestamp < end_timestamp,
                Status.build_status == status
            ).all()
            return succesful_repos

    def dump_b_status(self, status, start_timestamp: int, end_timestamp: int):
        """ dump all build status has <status> """
        with Session(self.engine) as session:
            successful_bstatus = session.query(Status).where(
                Status.mod_timestamp > start_timestamp,
                Status.mod_timestamp < end_timestamp,
                Status.build_status == status
            ).all()
            return successful_bstatus

    def enable_build_option(self, _id, is_enabled: bool) -> str:
        """
        Enable a buildOpt in the database
        Selection made by platform and language
        """
        with Session(self.engine) as session:
            try:
                build_option = session.query(BuildOpt).filter(
                    BuildOpt._id == _id).first()
                build_option.enable = is_enabled
                session.commit()
                return "Success"
            except sqlalchemy.exc.NoResultFound:
                return "Failure"

    def display_build_options(self):
        """
        Stream Build options stored in DB
        """
        with Session(self.engine) as session:
            build_options = session.query(BuildOpt).all()
            for option in build_options:
                yield option

    def all_repos(self):
        """ query all repo in database """
        with Session(self.engine) as session:
            repos = session.query(RepoDO).all()
            for option in repos:
                yield option

    def all_enabled_build_options(self):
        """
        return all enabled build option
        """
        with Session(self.engine) as session:
            build_options = session.query(BuildOpt).where(
                BuildOpt.enable == true()).all()
            yield from build_options

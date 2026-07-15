'''
    [Stub] Tests the DBManager with a test db
'''

import pytest
import unittest
from unittest.mock import patch, MagicMock, ANY
import logging
import time 
import datetime

import assemblage.data.db as db
import tests.integration_tests.integration_helper as helper
import assemblage.database.models as model
import assemblage.mq.messages as msg
import assemblage.consts as const 
import sqlalchemy as sqla



pytestmark = pytest.mark.integration  # needs the live test database
logging.basicConfig(format="%(asctime)s [TEST] %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S", level=const.TEST_MESSAGE_LEVEL)
logger = logging.getLogger(__name__)



class TestDBManager(unittest.TestCase):

    
    def setup_helper(self):
        '''
            This creates an infinite loop (useful for looking inside the test DB)
        '''
        dbm = db.DBManager(const.TEST_DB_ADDR)  # creates DB if it doesn't exist
        while True:
            time.sleep(10)
            logger.info("Running...")

    
    def forever_test_seed(self):
        helper.seed_database_projects()
        helper.seed_database_buildopts()
        helper.seed_database_statuses_unstarted()
        while True:
            time.sleep(10)
            logger.info("Running...")
    

    def test_get_projects_row_by_id(self):
        '''
            Tests get_projects_row_by_id by attempting to retrieve PROJECT_1 by its
            id (1). 
        '''
        
        helper.truncate_all()
        helper.seed_database_projects()
        INPUT_ID = 1
        expected_data = {
            '_sa_instance_state': ANY,
            'owner_id': 11, 'description': 'DESCRIPTION', 'created_at': datetime.datetime(2025, 11, 15, 12, 28, 25), 
            'deleted': False, 'forked_commit_id': 0, 'priority': const.PriorityStatus.LOW, 'build_system': 'BUILD_SYS', 
            'url': 'URL_1', 'id': INPUT_ID, 'name': 'PROJECT_1', 'language': 'LANG', 'fork_from': 0, 
            'updated_at': datetime.datetime(2025, 11, 15, 12, 41, 59), 'branch': 'BRANCH', 'size': 5
        }
        # expected_row = model.RepoDO(**expected_data)   # comparing dicts directly gives more useful error message
        
        dbm = db.DBManager(const.TEST_DB_ADDR)
        out = dbm.get_projects_row_by_id(INPUT_ID)

        
        self.assertEqual(
            type(out),
            model.RepoDO,
            "Type of return data was not as expected (was expecting RepoDO object)"
        )
        self.assertEqual(
            out.id,
            INPUT_ID,
            'Row with matching ID was not retrieved'
        )
        self.assertEqual(
            out.__dict__,
            expected_data,
            'Data was not expected: has database schema changed, or was the data seeded properly?'
        )
        

    def test_get_status_row_by_id(self):
        '''
            Tests get_status_row_by_id by trying to get the first status entry.
        '''
        
        helper.truncate_all()
        helper.seed_database_projects()
        helper.seed_database_buildopts()
        helper.seed_database_statuses_unstarted()
        INPUT_ID = 1
        expected_data = { '_sa_instance_state': ANY, 'build_opt_id': 1, 'repo_id': 1 }
        expected_keys = {'repo_id', 'id', 'clone_status', 'build_status', 'build_opt_id', 'build_time', 'build_msg', 'mod_timestamp', 'priority', 'clone_msg', '_sa_instance_state', 'commit_hexsha'}
        
        dbm = db.DBManager(const.TEST_DB_ADDR)
        out : model.Status = dbm.get_status_row_by_id(INPUT_ID)

        
        self.assertEqual(
            type(out),
            model.Status,
            "Type of return data was not as expected (was expecting Status object)"
        )
        self.assertEqual(
            out.id,
            INPUT_ID,
            'Row with matching ID was not retrieved'
        )
        self.assertEqual( out.build_opt_id, expected_data['build_opt_id'] )
        self.assertEqual( out.repo_id, expected_data['repo_id'] )
        self.assertEqual(
            expected_keys,
            set(out.__dict__.keys()),
            'Mismatch between expected and actual columns (has schema changed?)'
        )


    def test_find_build_opt_by_id(self):
        '''
            Tests find_build_opt_by_id.
        '''
        
        helper.truncate_all()
        helper.seed_database_projects()
        helper.seed_database_buildopts()
        helper.seed_database_statuses_unstarted()
        INPUT_ID = 1
        expected_data = { '_sa_instance_state': ANY,
            'compiler_name': 'clang', 'id': 1, 'language': 'c++', 
            'build_system': 'all', 'library': 'x64', 'save_assembly': True, 'platform': 'linux', 'compiler_flag': ANY, 
            'compiler_version': '10.0.0', 'build_command': ANY, 'enable': True}
        
        dbm = db.DBManager(const.TEST_DB_ADDR)
        out : model.Status = dbm.find_build_opt_by_id(INPUT_ID)

        
        self.assertEqual(
            type(out),
            model.BuildOpt,
            "Type of return data was not as expected (was expecting BuildOpt object)"
        )
        self.assertEqual(
            out.id,
            INPUT_ID,
            'Row with matching ID was not retrieved'
        )
        self.assertEqual(
            expected_data,
            out.__dict__,
            'Mismatch between expected and actual data (has schema changed?)'
        )
        


    @patch('assemblage.mq.client.BlockingChannel')
    @patch('assemblage.mq.client.BlockingConnection')
    def test_get_dispatch_msg(self, MockConn, MockChan):
        '''
            Tests that get_dispatch_task builds a good message
        '''
        
        self.maxDiff = None

        # setup database
        helper.truncate_all()
        helper.seed_database_projects()
        helper.seed_database_buildopts()
        # only seed 1 project so we guarantee which one we're getting
        project1_b1 = { 'clone_status': const.CloneStatus.NOT_STARTED, 'build_status': const.BuildStatus.INIT, 'build_opt_id': 1, 'repo_id': 1 }
        
        message_expected = msg.BuilderTaskOut(
            name='PROJECT_1', url='URL_1', task_id=1, opt_id=1, 
            output_dir= f'{const.BIN_DIR}/1', repo_id=1, updated_at='11/15/2025, 12:41:59', 
            build_system='BUILD_SYS', msg_time=ANY, commit_hexsha='', mod_timestamp=''
        )
        dbm = db.DBManager(const.TEST_DB_ADDR)
        message_actual = None
        with dbm.get_session() as session:
            # set up the task to be dispatched
            session.add(model.Status(**project1_b1))


        message_actual = dbm.get_dispatch_task(1, False)
        
        self.assertEqual( type(message_actual), msg.BuilderTaskOut )
        self.assertEqual( message_actual, message_expected )

    def test_insert_repo(self):
        '''
            Tests that insert_repo works as expected (inserts only 1 repo, and the repo is named the same as the input)
        '''

        helper.truncate_all()

        single_msg1_json : str = '{"name": "DOOM", "url": "https://api.github.com/repos/id-Software/DOOM", "language": "C++", "owner_id": 1395534, "description": "DOOM Open Source Release", "created_at": "2012-01-31 21:28:06", "updated_at": "2024-05-24 13:18:59", "size": 149, "build_system": "others", "branch": "master"}'
        repomsg = msg.ScraperDataOutSingle.from_json(single_msg1_json)
        
        dbm = db.DBManager(const.TEST_DB_ADDR)

        dbm.insert_repos(repomsg.to_dict())

        result = None
        with dbm.get_session() as session:
            query = sqla.select(model.RepoDO)
            # Allows us to iterate over ORM objects outside of session, so we can have uncaught assertions
            result = session.execute(query, execution_options={"prebuffer_rows": True})
            session.expunge_all() 

        n = 0
        for r in result.scalars():
            self.assertEqual( r.name, repomsg.name )
            self.assertEqual( r.id, 1 )
            n += 1
        self.assertEqual(n, 1)  # only 1 repo

        

    def test_update_repo_status_various_configs(self):
        helper.truncate_all()
        helper.seed_database_projects()
        helper.seed_database_buildopts()
        helper.seed_database_statuses_unstarted()

        dbm = db.DBManager(const.TEST_DB_ADDR)
        # status id and clone status (most common)
        dbm.update_repo_status( status_id=1, clone_status=const.CloneStatus.FAILED )

        # build info callback
        dbm.update_repo_status(
            status_id=2, 
            build_time=3, 
            build_status=const.BuildStatus.FAILED,
            build_msg='test', 
            commit_hexsha='000000'
            )
        
        # clone info callback
        dbm.update_repo_status(
            status_id=3,
            clone_status=const.CloneStatus.FAILED,
            clone_msg='test2'
            )
        
        # check all results as expected
        result = None
        with dbm.get_session() as session:
            query = sqla.select(model.Status)

            result = session.execute(query, execution_options={"prebuffer_rows": True})
            session.expunge_all() 

        n = 0
        for r in result.scalars():
            if r.id == 1:
                self.assertTrue( r.clone_status == const.CloneStatus.FAILED )
                self.assertTrue( r.build_status == const.BuildStatus.INIT )
            elif r.id == 2:
                self.assertTrue( r.clone_status == const.CloneStatus.NOT_STARTED )
                self.assertTrue( r.build_status == const.BuildStatus.FAILED )
                self.assertTrue( r.build_time == 3 )
                self.assertTrue( r.build_msg == 'test' )
                self.assertTrue( r.commit_hexsha == '000000' )
            elif r.id == 3:
                self.assertTrue( r.clone_status == const.CloneStatus.FAILED )
                self.assertTrue( r.build_status == const.BuildStatus.INIT )
                self.assertTrue( r.clone_msg == 'test2' )
            
            n += 1
        self.assertTrue(n == 6)


    def test_register_build_opt(self):

        helper.truncate_all()

        msg1 = msg.BuilderRegIn(
            "NAME1", "0001", "Clang", "0.0.1", "lib", "c++", True, "x65", "comp_flag", "", "x65"
        )
        msg2 = msg.BuilderRegIn(
            "NAME2", "0004", "GCC", "0.0.1", "lib", "c++", True, "x65", "comp_flag", "", "x65"
        )

        
        dbm = db.DBManager(const.TEST_DB_ADDR)

        ret = dbm.register_build_opt(msg1)
        self.assertTrue(ret == 1)
        ret = dbm.register_build_opt(msg2)
        self.assertTrue(ret == 2)
        ret = dbm.register_build_opt(msg2)
        self.assertTrue(ret == 2)

        result = None
        with dbm.get_session() as session:
            query = sqla.select(model.BuildOpt)

            result = session.execute(query, execution_options={"prebuffer_rows": True})
            session.expunge_all() 

        n = 0
        for r in result.scalars():
            if r.id == 1:
                self.assertEqual( r.compiler_name, msg1.compiler )
            if r.id == 2:
                self.assertEqual( r.compiler_name, msg2.compiler )
            n += 1
        self.assertEqual(n, 2)  # only 2 buildopts

    def test_insert_binary(self):

        
        helper.truncate_all()
        helper.seed_database_projects()
        helper.seed_database_buildopts()
        helper.seed_database_statuses_unstarted()

        
        dbm = db.DBManager(const.TEST_DB_ADDR)

        dbm.insert_binary("FILENAME", "DESCRIPTION", 3)
        dbm.insert_binary("FILENAME2", "DESCRIPTION", 3)

        
        # check all results as expected
        result = None
        with dbm.get_session() as session:
            query = sqla.select(model.BuildDO)

            result = session.execute(query, execution_options={"prebuffer_rows": True})
            session.expunge_all() 

        for r in result.scalars():
            if r.id == 1:
                self.assertTrue( r.file_name == "FILENAME" )
                self.assertTrue( r.status_id == 3 )
            elif r.id == 2:
                self.assertTrue( r.file_name == "FILENAME2" )
                self.assertTrue( r.status_id == 3 )
            else:
                # throw err should only be 2 file
                
                logger.info(r)
                self.assertTrue( False )

if __name__ == '__main__':
    logger.info("Starting tests in db_manager_regression_test.py")
    unittest.main()

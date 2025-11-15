'''
    [Stub] Some basic regression + unit tests for coordinator functionality. 
    todo
    * Test recv_ functions
    * Test dispatch thread
    * Test consume functions

    It would be nice to replace assertions around the DBManager functionality to
    assertions on the underlying database, so that we could change
    the exact mechanism by which data is committed without breaking
    the tests. But that could be easier said than done, and these
    tests function fine as regression tests at least.

    Note: most of these tests are closer to regression tests than unit tests,
    because the person writing this code isn't the one who wrote the original
    functions. Especially for the non-scraper modules, test inputs and outputs
    were obtained via testing and examining the actual code.
'''

import unittest
import json
from unittest.mock import patch, MagicMock, ANY
import logging
import assemblage.coordinator.coordinator as coordinator
from assemblage.config import CoordinatorSettings
from pika.exchange_type import ExchangeType
from pika import BasicProperties
import assemblage.mq.messages as msg
from assemblage.consts import BuildStatus, CloneStatus, COORDINATOR_DATABASE_SYNC_TIMEOUT, OutputQueue

logging.basicConfig(format="%(asctime)s [TEST] %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S", level='DEBUG')

logger = logging.getLogger(__name__)
DefaultSettings = CoordinatorSettings()

class TestCoordinator(unittest.TestCase):


    ### CLASS FUNCS

    def test_patch_url(self):
        input = "https://api.github.com/repos/Assemblage-Dataset/Assemblage"
        expected_output = "https://github.com/Assemblage-Dataset/Assemblage"

        output = coordinator.patch_url(input)

        self.assertEqual(expected_output, output)

    def test_unpatch_url(self):
        input = "https://github.com/id-Software/DOOM"
        expected_output = "https://api.github.com/repos/id-Software/DOOM"

        output = coordinator.unpatch_url(input)

        self.assertEqual(expected_output, output)

    ### SETUP FUNCTIONALITY

    @patch('assemblage.mq.client.BlockingChannel')
    @patch('assemblage.mq.client.BlockingConnection')
    def test_topic_exchange_setup_on_init(self, MockConnection, MockChannel):
        '''
            Ensure that the topic exchange is initialized when the coordinator is initialized
        '''

        mock_connection, mock_channel = self._mock_functioning_rabbitmq(MockConnection, MockChannel)
        
        c = coordinator.Coordinator(DefaultSettings)
        
        mock_channel.exchange_declare.assert_called_with(exchange="build_opt", exchange_type=ExchangeType.topic)


    ### RECV FUNCTIONS
    # Scrape



    @patch('assemblage.mq.client.BlockingChannel')
    @patch('assemblage.mq.client.BlockingConnection')
    @patch('assemblage.coordinator.coordinator.DBManager')
    def test_recv_scrape_info_multiple(self, MockDBManager, MockConnection, MockChannel):
        '''
            Checks that recv_scrape_info successfully inserts the given data into the database
            (assumes database works as expected)
        '''
        mock_connection, mock_channel = self._mock_functioning_rabbitmq(MockConnection, MockChannel)
        mock_db = self._mock_functioning_dbmanager(MockDBManager)

        # these are mocks so i don't have to worry about creating valid acknowledgement dummy data
        input_method = MagicMock()
        input_props = MagicMock()
        # Bundle of 1
        single_msg1_json = '{"name": "DOOM", "url": "https://api.github.com/repos/id-Software/DOOM", "language": "C++", "owner_id": 1395534, "description": "DOOM Open Source Release", "created_at": "2012-01-31 21:28:06", "updated_at": "2024-05-24 13:18:59", "size": 149, "build_system": "others", "branch": "master"}'
        single_msg2_json = '{"name": "DEFINITELY_NOT_DOOM", "url": "urlhere", "language": "C++", "owner_id": 1, "description": "", "created_at": "2012-01-31 21:28:06", "updated_at": "2024-05-24 13:18:59", "size": 149, "build_system": "others", "branch": "master"}'
        single_msg1 = msg.ScraperDataOutSingle.from_json(single_msg1_json)
        single_msg2 = msg.ScraperDataOutSingle.from_json(single_msg2_json)
        input_body = msg.ScraperDataOutBundle([single_msg1, single_msg2]).to_json().encode()  # must be given as bytes, hence encode()
        
        c = coordinator.Coordinator(DefaultSettings)
        c.recv_scrape_info(mock_channel, input_method, input_props, input_body)

        # check that expected DB accesses happened
        mock_db.insert_repos.assert_any_call(single_msg1.to_dict())
        mock_db.insert_repos.assert_any_call(single_msg2.to_dict())
        self.assertEqual(mock_db.insert_repos.call_count, 2)

        # check that no rabbitmq calls happened (this function shouldn't send messages)
        self.assertFalse( mock_channel.basic_publish.called )

        # check that an acknowledgement was sent
        mock_channel.basic_ack.assert_called_with(delivery_tag=input_method.delivery_tag)


    # Binary


    @patch('assemblage.mq.client.BlockingChannel')
    @patch('assemblage.mq.client.BlockingConnection')
    @patch('assemblage.coordinator.coordinator.DBManager')
    def test_recv_binary(self, MockDBManager, MockConnection, MockChannel):
        '''
            Checks that recv_binary inserts received binary. 
            This is basically a regression test as I'm not very familiar with the mechanisms here
            (note difference between scraper and binary: binary is one msg = one binary file,
            whereas scraper is one msg = many scraped repositories)
            note:
            recv_binary assumes that the DB will handle invalid data, but provides no real functionality
            to check if data provided is valid nor any way of handling/indicating an error to user
            if the DB is given bad data. Given data flow I don't think it's possible for bad data to be
            passed via a message right now, but this could change
            TODO: tests that cover expected behavior when bad data is passed to the recv methods?
        '''
        
        mock_connection, mock_channel = self._mock_functioning_rabbitmq(MockConnection, MockChannel)
        mock_db = self._mock_functioning_dbmanager(MockDBManager)

        # see above for reasoning as to why using mocks
        input_method = MagicMock()
        input_props = MagicMock()
        mock_body = '{"file_name": "FILENAME", "task_id": "TASK_ID_IN_DB_FROM_SCRAPER"}'.encode()

        c = coordinator.Coordinator(DefaultSettings)
        c.recv_binary(mock_channel, input_method, input_props, mock_body)

        # check that acknowledgement and one db insertion happened        
        mock_channel.basic_ack.assert_called_once_with(delivery_tag=input_method.delivery_tag)
        mock_db.insert_binary.assert_called_with(
            file_name="FILENAME", description=ANY, status_id="TASK_ID_IN_DB_FROM_SCRAPER"
        )
        mock_db.insert_binary.assert_called_once()

        # check that no rabbitmq calls happened (this function shouldn't send messages)
        self.assertFalse( mock_channel.basic_publish.called )


    # Build info


    @patch('assemblage.mq.client.BlockingChannel')
    @patch('assemblage.mq.client.BlockingConnection')
    @patch('assemblage.coordinator.coordinator.DBManager')
    def test_recv_build_info_timeout(self, MockDBManager, MockConnection, MockChannel):
        '''
            Tests that messages with the OUTDATED_MSG status are discarded
            (practically speaking, OUTDATED_MSG seems to be used for duplicate tasks fyi)
        '''

        mock_connection, mock_channel = self._mock_functioning_rabbitmq(MockConnection, MockChannel)
        mock_db = self._mock_functioning_dbmanager(MockDBManager)
        # see above for reasoning as to why using mocks
        input_method = MagicMock()
        input_props = MagicMock()
        mock_body = '{"url": "url", "opt_id": 1, "status":"'+ BuildStatus.OUTDATED_MSG +'"}'
        mock_body = mock_body.encode()

        c = coordinator.Coordinator(DefaultSettings)
        c.recv_build_info(mock_channel, input_method, input_props, mock_body)

        # check that acknowledgement happened        
        mock_channel.basic_ack.assert_called_once_with(delivery_tag=input_method.delivery_tag)
        # check that no rabbitmq publishes or db calls happened
        mock_channel.basic_publish.assert_not_called()
        mock_db.update_repo_status.assert_not_called()
        mock_db.find_status_by_id.assert_not_called()
        #TODO: assert that no db calls happen whatsoever, not just these ones? or no writes at least


    @patch('assemblage.mq.client.BlockingChannel')
    @patch('assemblage.mq.client.BlockingConnection')
    @patch('assemblage.coordinator.coordinator.DBManager')
    def test_recv_build_info_good(self, MockDBManager, MockConnection, MockChannel):
        '''
            Tests that valid build messages are saved
        '''

        mock_connection, mock_channel = self._mock_functioning_rabbitmq(MockConnection, MockChannel)
        mock_db = self._mock_functioning_dbmanager(MockDBManager)
        input_method = MagicMock()
        input_props = MagicMock()

        # Also need to mock a return value for self.db_man.find_status_by_id(recv_msg['task_id']),
        # which should be a mock with the attribute clone_status equal to SUCCESS
        mock_task = MagicMock()
        mock_task.clone_status = CloneStatus.SUCCESS
        mock_db.find_status_by_id.return_value = mock_task

        input_msg = {  # note: even a failed build is a "good" build message 
            "url": "5.com", "opt_id":1, "status":BuildStatus.FAILED, "msg":"finished",
            "task_id":10001, "build_time": 5, "commit_hexsha":"xxxxxx"
                     }
        mock_body = json.dumps( input_msg )
        mock_body = mock_body.encode()

        c = coordinator.Coordinator(DefaultSettings)
        c.recv_build_info(mock_channel, input_method, input_props, mock_body)

        # check that acknowledgement happened        
        mock_channel.basic_ack.assert_called_once_with(delivery_tag=input_method.delivery_tag)

        mock_channel.basic_publish.assert_not_called()

        # check that the correct db calls happened
        mock_db.update_repo_status.assert_called_with(
            status_id=10001,
            build_time=5,
            build_status=BuildStatus.FAILED,
            build_msg="finished",
            commit_hexsha="xxxxxx")
        
        # if this is called multiple times, then this test is entering the database sync code
        # which is incorrect
        mock_db.find_status_by_id.assert_called_once()

        
    @patch('assemblage.mq.client.BlockingChannel')
    @patch('assemblage.mq.client.BlockingConnection')
    @patch('assemblage.coordinator.coordinator.DBManager')
    @patch('assemblage.coordinator.coordinator.time') # patched purely to skip the waiting
    def test_recv_build_info_clone_wait_stall(self, MockTime, MockDBManager, MockConnection, MockChannel):
        '''
            Tests that if a task w a clone status of PROCESSING is passed and clone status is never updated,
            then the task will behave as expected
            Regression test
        '''

        mock_connection, mock_channel = self._mock_functioning_rabbitmq(MockConnection, MockChannel)
        mock_db = self._mock_functioning_dbmanager(MockDBManager)
        input_method = MagicMock()
        input_props = MagicMock()

        # Also need to mock a return value for self.db_man.find_status_by_id(recv_msg['task_id']),
        # which should be a mock with the attribute clone_status equal to SUCCESS
        mock_task = MagicMock()
        mock_task.clone_status = CloneStatus.PROCESSING
        mock_task.repo_id = "FOR_TEST_OUTPUT_ONLY"
        mock_db.find_status_by_id.return_value = mock_task

        input_msg = {
            "url": "5.com", "opt_id":1, "status":BuildStatus.SUCCESS, "msg":"finished",
            "task_id":10001, "build_time": 5, "commit_hexsha":"xxxxxx"
                     }
        mock_body = json.dumps( input_msg )
        mock_body = mock_body.encode()

        c = coordinator.Coordinator(DefaultSettings)
        c.recv_build_info(mock_channel, input_method, input_props, mock_body)

        # acknowledgement still necessary bc acknowledgement will remove this task from queue
        mock_channel.basic_ack.assert_called_once_with(delivery_tag=input_method.delivery_tag)

        mock_channel.basic_publish.assert_not_called()
        #mock_db.update_repo_status.assert_called()  
        # currently the repo status is updated anyway for this particular scenario.
        # whether this behavior is expected or not is unknown
        
        # should be trapped in the database sync code for the full 10 seconds
        self.assertEqual(
            mock_db.find_status_by_id.call_count,
            COORDINATOR_DATABASE_SYNC_TIMEOUT + 1
        )
         

    @patch('assemblage.mq.client.BlockingChannel')
    @patch('assemblage.mq.client.BlockingConnection')
    @patch('assemblage.coordinator.coordinator.DBManager')
    @patch('assemblage.coordinator.coordinator.time') # patched purely to skip the waiting
    def test_recv_build_info_clone_wait_eventual_success(self, MockTime, MockDBManager, MockConnection, MockChannel):
        '''
            Tests that if a task w a clone status of PROCESSING is passed, then the status is eventually updated to SUCCESS,
            the task will continue as expected
        '''

        mock_connection, mock_channel = self._mock_functioning_rabbitmq(MockConnection, MockChannel)
        mock_db = self._mock_functioning_dbmanager(MockDBManager)
        input_method = MagicMock()
        input_props = MagicMock()

        # Also need to mock a return value for self.db_man.find_status_by_id(recv_msg['task_id']),
        # which is one of two mocks: one which represents the DB returning a NOT_STARTED clone status,
        # and one which represents a SUCCESS clone status.
        # Then, we use a side effect to mimic the database updating after a few seconds and returning
        # that the clone has been completed.
        # Note: we use two mocks rather than one that we just give multiple side effects of clone_status
        # because we don't care how many times clone_status is called. Using one and putting side effects
        # on that mock will introduce unpredictable bugs, where for example tests can pass or fail depending
        # on logger statements.
        mock_task_unstarted = MagicMock()
        mock_task_unstarted.clone_status = CloneStatus.NOT_STARTED
        mock_task_unstarted.repo_id = "FOR_TEST_OUTPUT_ONLY"

        mock_task_success = MagicMock()
        mock_task_success.clone_status = CloneStatus.SUCCESS
        mock_task_success.repo_id = "FOR_TEST_OUTPUT_ONLY"

        mock_db.find_status_by_id.side_effect = [mock_task_unstarted, mock_task_unstarted, mock_task_success]

        input_msg = {
            "url": "5.com", "opt_id":1, "status":BuildStatus.SUCCESS, "msg":"finished",
            "task_id":10001, "build_time": 5, "commit_hexsha":"xxxxxx"
                     }
        mock_body = json.dumps( input_msg )
        mock_body = mock_body.encode()


        c = coordinator.Coordinator(DefaultSettings)
        c.recv_build_info(mock_channel, input_method, input_props, mock_body)

        # acknowledgement still necessary bc acknowledgement will remove this task from queue
        mock_channel.basic_ack.assert_called_once_with(delivery_tag=input_method.delivery_tag)

        mock_channel.basic_publish.assert_not_called()
        mock_db.update_repo_status.assert_called_once()  
        
        # should be trapped in the database sync code for the full 10 seconds
        self.assertEqual(
            mock_db.find_status_by_id.call_count,
            3
        )
         

    @patch('assemblage.mq.client.BlockingChannel')
    @patch('assemblage.mq.client.BlockingConnection')
    @patch('assemblage.coordinator.coordinator.DBManager')
    def test_recv_clone_info_success(self, MockDBManager, MockConnection, MockChannel):
        '''
            Tests that recv_clone_info, on receiving a non-timed-out message, adds it to the DB
            (the message has a cloned success, we mock getting this as well)
            This one is definitely a regression test, NOT a unit test. I'm not sure why the original
            function does what it does, but this checks that it keeps doing that.
            TODO: would like to mock out the logger to check what code path is explored
        '''

        mock_connection, mock_channel = self._mock_functioning_rabbitmq(MockConnection, MockChannel)
        mock_db = self._mock_functioning_dbmanager(MockDBManager)
        
        input_method = MagicMock()
        input_props = MagicMock()
        mock_body = '{"url": "url", "task_id": 1001, "msg": "Message", "opt_id": 1, "status":"'+ BuildStatus.SUCCESS +'"}'
        mock_body = mock_body.encode()

        # Mock getting the success
        mock_task_success = MagicMock()
        mock_task_success.clone_status = CloneStatus.SUCCESS
        mock_task_success.repo_id = "FOR_TEST_OUTPUT_ONLY"

        mock_db.find_status_by_id.return_value = mock_task_success


        c = coordinator.Coordinator(DefaultSettings)
        c.recv_clone_info(mock_channel, input_method, input_props, mock_body)


        # check that acknowledgement happened        
        mock_channel.basic_ack.assert_called_once_with(delivery_tag=input_method.delivery_tag)

        mock_db.update_repo_status.assert_called_once_with(
            status_id=1001,
            clone_status=BuildStatus.SUCCESS,
            clone_msg='Message'
            )
        

    @patch('assemblage.mq.client.BlockingChannel')
    @patch('assemblage.mq.client.BlockingConnection')
    @patch('assemblage.coordinator.coordinator.DBManager')
    def test_recv_clone_info_badclone(self, MockDBManager, MockConnection, MockChannel):
        '''
            Tests that recv_clone_info, on receiving a message with a non-success, behaves as expected.
            Currently this behavior is the same as a success.
            NOTE: task.clone_status is used as a PROXY for the logging task, as patching out and listening
            to the logger is more complicated than expected. The assumption is that if clone_status is accessed
            more than once, it's because it's being printed to the console, but this is a fragile assumption.

            Also, the logic in the code as-is is very questionable to me (why do we print to console that we're
            updating based on the clone status, but actually update on the build status?) so this is purely a regression test.
        '''

        mock_connection, mock_channel = self._mock_functioning_rabbitmq(MockConnection, MockChannel)
        mock_db = self._mock_functioning_dbmanager(MockDBManager)
        
        input_method = MagicMock()
        input_props = MagicMock()
        mock_body = '{"url": "url", "task_id": 1001, "msg": "Message", "opt_id": 1, "status":"'+ BuildStatus.SUCCESS +'"}'
        mock_body = mock_body.encode()

        # Mock getting the success
        mock_task_success = MagicMock()
        mock_task_success.clone_status = CloneStatus.FAILED
        mock_task_success.repo_id = "FOR_TEST_OUTPUT_ONLY"

        mock_db.find_status_by_id.return_value = mock_task_success


        c = coordinator.Coordinator(DefaultSettings)
        c.recv_clone_info(mock_channel, input_method, input_props, mock_body)


        # check that acknowledgement happened        
        mock_channel.basic_ack.assert_called_once_with(delivery_tag=input_method.delivery_tag)

        mock_db.update_repo_status.assert_called_once_with(
            status_id=1001,
            clone_status=BuildStatus.SUCCESS,  
            # I have a theory that this should instead be CloneStatus.FAILED if the system was working as expected, 
            # but this is what the code currently does.
            clone_msg='Message' 
            )

    @patch('assemblage.mq.client.BlockingChannel')
    @patch('assemblage.mq.client.BlockingConnection')
    @patch('assemblage.coordinator.coordinator.DBManager')
    @patch('assemblage.coordinator.coordinator.threading') # patched to prevent orphan threads
    def test_recv_builder_registration_db(self, MockThreading, MockDBManager, MockConnection, MockChannel):
        '''
            Tests that on builder registration, the db is appropriately accessed and an acknowledgement is sent.
            Creation of dispatch threads is tested in the next test.
        '''
        
        mock_connection, mock_channel = self._mock_functioning_rabbitmq(MockConnection, MockChannel)
        mock_db = self._mock_functioning_dbmanager(MockDBManager)

        # Set up required return from db
        mock_db.register_build_opt.return_value = 3

        input_method = MagicMock()
        input_props = MagicMock()
        mock_body = msg.BuilderRegIn(
            name= "clang-builder",
            uuid="6a70...", 
            compiler="clang", 
            compiler_version="1.0", 
            library="x64", 
            language="c++", 
            save_assembly=True, 
            platform="linux", 
            compiler_flag=None,
            build_command= None, 
            build_system="all"
        )
        mock_body = mock_body.to_json()



        c = coordinator.Coordinator(DefaultSettings)
        c.recv_builder_registration(mock_channel, input_method, input_props, mock_body.encode())



        # Check that registration happened
        mock_db.register_build_opt.assert_called_once()
        # Check that the contents of the message that was registered equals that of the test
        actual_sent_msg = mock_db.register_build_opt.call_args[0][0]
        self.assertEqual(
            mock_body,
            actual_sent_msg.to_json(),
            "Wrong message sent to register build opt in db"
        )

        mock_channel.basic_ack.assert_called_once_with(delivery_tag=input_method.delivery_tag)
        mock_channel.basic_publish.assert_called_once_with(
            exchange='',
            routing_key=OutputQueue.BUILDER_CTRL,
            properties=BasicProperties( 
                correlation_id=input_props.correlation_id,
                delivery_mode=2,
                reply_to=input_props.reply_to
                ),
            body=msg.BuilderRegOut(3).to_json()
        )


    @patch('assemblage.mq.client.BlockingChannel')
    @patch('assemblage.mq.client.BlockingConnection')
    @patch('assemblage.coordinator.coordinator.DBManager')
    @patch('assemblage.coordinator.coordinator.threading')
    def test_recv_builder_registration_createthread(self, MockThreading, MockDBManager, MockConnection, MockChannel):
        '''
            Tests that when a new builder registers, one thread is spun up and started.
            (Does not check db/rabbitmq accesses -- see above)
        '''
        
        mock_connection, mock_channel = self._mock_functioning_rabbitmq(MockConnection, MockChannel)
        mock_db = self._mock_functioning_dbmanager(MockDBManager)
        
        mock_thread = MagicMock()
        MockThreading.Thread.return_value = mock_thread

        # Set up required return from db
        mock_db.register_build_opt.return_value = 3

        input_method = MagicMock()
        input_props = MagicMock()
        mock_body = msg.BuilderRegIn(
            name= "clang-builder",
            uuid="6a70...", 
            compiler="clang", 
            compiler_version="1.0", 
            library="x64", 
            language="c++", 
            save_assembly=True, 
            platform="linux", 
            compiler_flag=None,
            build_command= None, 
            build_system="all"
        )
        mock_body = mock_body.to_json()


        c = coordinator.Coordinator(DefaultSettings)
        c.recv_builder_registration(mock_channel, input_method, input_props, mock_body.encode())

        MockThreading.Thread.assert_called_once_with(
            target = c._Coordinator__dispatch_task, args=(3, True)
            # wondering why I used _Coordinator__dispatch_task here? It's due to Python name mangling
        )
        mock_thread.start.assert_called_once()
        

    @patch('assemblage.mq.client.BlockingChannel')
    @patch('assemblage.mq.client.BlockingConnection')
    @patch('assemblage.coordinator.coordinator.DBManager')
    @patch('assemblage.coordinator.coordinator.threading')
    def test_recv_builder_registration_foundthread(self, MockThreading, MockDBManager, MockConnection, MockChannel):
        '''
            Tests that when a new builder registers on a buildopt that already has a thread, 
            it uses that thread instead
            We test this by emulating two sent messages, presumably from two builders that are nearly identical
            except for the UUID. Only one thread should be spun up.
        '''
        
        mock_connection, mock_channel = self._mock_functioning_rabbitmq(MockConnection, MockChannel)
        mock_db = self._mock_functioning_dbmanager(MockDBManager)
        
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        MockThreading.Thread.return_value = mock_thread

        # Set up required return from db
        mock_db.register_build_opt.return_value = 3

        input_method = MagicMock()
        input_props = MagicMock()
        mock_body = msg.BuilderRegIn(
            name= "clang-builder",
            uuid="6a70...", 
            compiler="clang", 
            compiler_version="1.0", 
            library="x64", 
            language="c++", 
            save_assembly=True, 
            platform="linux", 
            compiler_flag=None,
            build_command= None, 
            build_system="all"
        )
        mock_body1 = mock_body.to_json()
        mock_body.uuid="92e8..."
        mock_body2 = mock_body.to_json()


        c = coordinator.Coordinator(DefaultSettings)
        c.recv_builder_registration(mock_channel, input_method, input_props, mock_body1.encode())
        c.recv_builder_registration(mock_channel, input_method, input_props, mock_body2.encode())

        MockThreading.Thread.assert_called_once_with(
            target = c._Coordinator__dispatch_task, args=(3, True)
        )
        mock_thread.start.assert_called_once()
        
        
    @patch('assemblage.mq.client.BlockingChannel')
    @patch('assemblage.mq.client.BlockingConnection')
    @patch('assemblage.coordinator.coordinator.DBManager')
    @patch('assemblage.coordinator.coordinator.threading')
    def test_recv_builder_registration_two_diffopts(self, MockThreading, MockDBManager, MockConnection, MockChannel):
        '''
            Tests that when two separate builders register after each other with different buildopts,
            two threads are spun up.
            
        '''
        
        mock_connection, mock_channel = self._mock_functioning_rabbitmq(MockConnection, MockChannel)
        mock_db = self._mock_functioning_dbmanager(MockDBManager)
        
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        MockThreading.Thread.return_value = mock_thread

        # Set up required return from db
        mock_db.register_build_opt.side_effect = [3, 4]

        input_method = MagicMock()
        input_props = MagicMock()
        mock_body = msg.BuilderRegIn(
            name= "clang-builder",
            uuid="6a70...", 
            compiler="clang", 
            compiler_version="1.0", 
            library="x64", 
            language="c++", 
            save_assembly=True, 
            platform="linux", 
            compiler_flag=None,
            build_command= None, 
            build_system="all"
        )
        mock_body1 = mock_body.to_json()
        mock_body.uuid="92e8..."
        mock_body.compiler="clangn't"
        mock_body2 = mock_body.to_json()


        c = coordinator.Coordinator(DefaultSettings)
        c.recv_builder_registration(mock_channel, input_method, input_props, mock_body1.encode())
        c.recv_builder_registration(mock_channel, input_method, input_props, mock_body2.encode())

        self.assertEqual(
            mock_thread.start.call_count, 2
        )
        self.assertEqual(
            MockThreading.Thread.call_count, 2
        )

        MockThreading.Thread.assert_any_call(
            target = c._Coordinator__dispatch_task, args=(3, True)
        )
        MockThreading.Thread.assert_any_call(
            target = c._Coordinator__dispatch_task, args=(4, True)
        )
        



    def _mock_functioning_rabbitmq(self, MockConnection, MockChannel):
        '''
            Creates a mock for RabbitMQ connections and channels, covering just enough
            functionality to run tests (instantiation + core health checks)
        '''

        mock_connection = MagicMock()
        mock_channel = MagicMock()

        mock_connection.is_open = True
        mock_connection.channel = MagicMock(return_value = mock_channel)
        mock_channel.is_closed = False

        MockConnection.return_value = mock_connection
        MockChannel.return_value = mock_channel

        return mock_connection, mock_channel

    def _mock_functioning_dbmanager(self, MockManager):
        mock_db = MagicMock()
        MockManager.return_value = mock_db
        mock_db.insert_repos.return_value = 1
        return mock_db

    
if __name__ == '__main__':
    logger.info("Starting tests in coordinator_test.py")
    unittest.main()

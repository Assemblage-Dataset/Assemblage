"""
[Stub] Some basic regression + unit tests for coordinator functionality.
todo
* Implement the skipped test ( test_dispatch_thread_reconnect ) properly
* Figure out the underlying assumptions tested in test_recv_clone_info_badclone

It would be nice to replace assertions around the DBManager functionality to
assertions on the underlying database, so that we could change
the exact mechanism by which data is committed without breaking
the tests. But that could be easier said than done, and these
tests function fine as regression tests at least.

Note: most of these tests are closer to regression tests than unit tests,
because the person writing this code isn't the one who wrote the original
functions. Especially for the non-scraper modules, test inputs and outputs
were obtained via testing and examining the actual code.
"""

import json
import logging
import unittest
from unittest.mock import ANY, MagicMock, patch

import assemblage.config as settings
import assemblage.coordinator.coordinator as coordinator
import assemblage.mq.messages as msg
from assemblage.consts import (
    BIN_DIR,
    COORDINATOR_DATABASE_SYNC_TIMEOUT,
    TEST_MESSAGE_LEVEL,
    BuildStatus,
    CloneStatus,
    InputQueue,
)
from pika import BasicProperties
from pika.exchange_type import ExchangeType

import tests.unit_tests.helper_func as helper

logging.basicConfig(
    format="%(asctime)s [TEST] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=TEST_MESSAGE_LEVEL,
)

logger = logging.getLogger(__name__)
DefaultSettings = settings.CoordinatorSettings()


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

    @patch("assemblage.mq.client.BlockingChannel")
    @patch("assemblage.mq.client.BlockingConnection")
    def test_topic_exchange_setup_on_init(self, MockConnection, MockChannel):
        """
        Ensure that the topic exchange is initialized when the coordinator is initialized
        """

        mock_connection, mock_channel = helper.mock_functioning_rabbitmq(
            MockConnection, MockChannel
        )

        c = coordinator.Coordinator(DefaultSettings)

        mock_channel.exchange_declare.assert_called_with(
            exchange="build_opt", exchange_type=ExchangeType.topic
        )

    ### RECV FUNCTIONS
    # Scrape

    @patch("assemblage.mq.client.BlockingChannel")
    @patch("assemblage.mq.client.BlockingConnection")
    @patch("assemblage.coordinator.coordinator.DBManager")
    def test_recv_scrape_info_multiple(self, MockDBManager, MockConnection, MockChannel):
        """
        Checks that recv_scrape_info successfully inserts the given data into the database
        (assumes database works as expected)
        """
        mock_connection, mock_channel = helper.mock_functioning_rabbitmq(
            MockConnection, MockChannel
        )
        mock_db = helper.mock_functioning_dbmanager(MockDBManager)

        # these are mocks so i don't have to worry about creating valid acknowledgement dummy data
        input_method = MagicMock()
        input_props = MagicMock()
        # Bundle of 2
        single_msg1_json: str = '{"name": "DOOM", "url": "https://api.github.com/repos/id-Software/DOOM", "language": "C++", "owner_id": 1395534, "description": "DOOM Open Source Release", "created_at": "2012-01-31 21:28:06", "updated_at": "2024-05-24 13:18:59", "size": 149, "build_system": "others", "branch": "master", "commit_hexsha": "a77d"}'
        single_msg2_json: str = '{"name": "DEFINITELY_NOT_DOOM", "url": "urlhere", "language": "C++", "owner_id": 1, "description": "", "created_at": "2012-01-31 21:28:06", "updated_at": "2024-05-24 13:18:59", "size": 149, "build_system": "others", "branch": "master", "commit_hexsha": "a77d"}'
        single_msg1 = msg.ScraperDataOutSingle.from_json(single_msg1_json)
        single_msg2 = msg.ScraperDataOutSingle.from_json(single_msg2_json)
        bundle = msg.ScraperDataOutBundle([single_msg1, single_msg2])
        input_body: bytes = bundle.to_json().encode()

        c = coordinator.Coordinator(DefaultSettings)
        c.recv_scrape_info(mock_channel, input_method, input_props, input_body)

        # check that expected DB accesses happened
        mock_db.insert_repos.assert_any_call(single_msg1.to_dict())
        mock_db.insert_repos.assert_any_call(single_msg2.to_dict())
        self.assertEqual(mock_db.insert_repos.call_count, 2)

        # check that no rabbitmq calls happened (this function shouldn't send messages)
        self.assertFalse(mock_channel.basic_publish.called)

        # check that an acknowledgement was sent
        mock_channel.basic_ack.assert_called_with(delivery_tag=input_method.delivery_tag)

    # Binary

    @patch("assemblage.mq.client.BlockingChannel")
    @patch("assemblage.mq.client.BlockingConnection")
    @patch("assemblage.coordinator.coordinator.DBManager")
    def test_recv_binary(self, MockDBManager, MockConnection, MockChannel):
        """
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
        """

        mock_connection, mock_channel = helper.mock_functioning_rabbitmq(
            MockConnection, MockChannel
        )
        mock_db = helper.mock_functioning_dbmanager(MockDBManager)

        # see above for reasoning as to why using mocks
        input_method = MagicMock()
        input_props = MagicMock()
        mock_body: bytes = b'{"file_name": "FILENAME", "task_id": "TASK_ID_IN_DB_FROM_SCRAPER"}'

        c = coordinator.Coordinator(DefaultSettings)
        c.recv_binary(mock_channel, input_method, input_props, mock_body)

        # check that acknowledgement and one db insertion happened
        mock_channel.basic_ack.assert_called_once_with(delivery_tag=input_method.delivery_tag)
        mock_db.insert_binary.assert_called_once_with(
            file_name="FILENAME", description=ANY, status_id="TASK_ID_IN_DB_FROM_SCRAPER"
        )

        # check that no rabbitmq calls happened (this function shouldn't send messages)
        self.assertFalse(mock_channel.basic_publish.called)

    # Build info

    @patch("assemblage.mq.client.BlockingChannel")
    @patch("assemblage.mq.client.BlockingConnection")
    @patch("assemblage.coordinator.coordinator.DBManager")
    def test_recv_build_info_timeout(self, MockDBManager, MockConnection, MockChannel):
        """
        Tests that messages with the OUTDATED_MSG status are discarded
        (practically speaking, OUTDATED_MSG seems to be used for duplicate tasks fyi)
        """

        mock_connection, mock_channel = helper.mock_functioning_rabbitmq(
            MockConnection, MockChannel
        )
        mock_db = helper.mock_functioning_dbmanager(MockDBManager)

        input_method = MagicMock()
        input_props = MagicMock()
        mock_body: str = (
            '{"url": "url", "opt_id": 1, "status":"'
            + BuildStatus.OUTDATED_MSG
            + '", "msg":"", "task_id":1, "build_time":5, "commit_hexsha":""}'
        )
        mock_body: bytes = mock_body.encode()

        c = coordinator.Coordinator(DefaultSettings)
        c.recv_build_info(mock_channel, input_method, input_props, mock_body)

        # check that acknowledgement happened
        mock_channel.basic_ack.assert_called_once_with(delivery_tag=input_method.delivery_tag)
        # check that no rabbitmq publishes or db calls happened
        mock_channel.basic_publish.assert_not_called()
        mock_db.update_repo_status.assert_not_called()
        mock_db.get_status_row_by_id.assert_not_called()
        # TODO: assert that no db calls happen whatsoever, not just these ones? or no writes at least

    @patch("assemblage.mq.client.BlockingChannel")
    @patch("assemblage.mq.client.BlockingConnection")
    @patch("assemblage.coordinator.coordinator.DBManager")
    def test_recv_build_info_good(self, MockDBManager, MockConnection, MockChannel):
        """
        Tests that valid build messages are saved
        """

        mock_connection, mock_channel = helper.mock_functioning_rabbitmq(
            MockConnection, MockChannel
        )
        mock_db = helper.mock_functioning_dbmanager(MockDBManager)
        input_method = MagicMock()
        input_props = MagicMock()

        # A valid build message must have a clone_status of SUCCESS saved in the db
        mock_task = MagicMock()
        mock_task.clone_status = CloneStatus.SUCCESS
        mock_db.get_status_row_by_id.return_value = mock_task
        # FAILED builds fail their sibling statuses; the count is compared to 0
        mock_db.fail_sibling_statuses.return_value = 0

        input_msg = {  # note: even a failed build is a "good" build message
            "url": "5.com",
            "opt_id": 1,
            "status": BuildStatus.FAILED,
            "msg": "finished",
            "task_id": 10001,
            "build_time": 5,
            "commit_hexsha": "xxxxxx",
        }
        mock_body: str = json.dumps(input_msg)
        mock_body: bytes = mock_body.encode()

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
            commit_hexsha="xxxxxx",
        )

        # if this is called multiple times, then this test is entering the database sync code
        # which is incorrect for this input (since clone status is SUCCESS)
        mock_db.get_status_row_by_id.assert_called_once_with(10001)

    @patch("assemblage.mq.client.BlockingChannel")
    @patch("assemblage.mq.client.BlockingConnection")
    @patch("assemblage.coordinator.coordinator.DBManager")
    @patch("assemblage.coordinator.coordinator.time")  # patched purely to skip the waiting
    def test_recv_build_info_clone_wait_stall(
        self, MockTime, MockDBManager, MockConnection, MockChannel
    ):
        """
        Tests that if a task w a clone status of PROCESSING is built but clone status is never updated,
        then the recv function will behave as expected
        Regression test
        """

        mock_connection, mock_channel = helper.mock_functioning_rabbitmq(
            MockConnection, MockChannel
        )
        mock_db = helper.mock_functioning_dbmanager(MockDBManager)
        input_method = MagicMock()
        input_props = MagicMock()

        # For this test, we need a non-success CloneStatus (PROCESSING is typical for this bug)
        # repo_id is needed only in the logging message.
        mock_task = MagicMock()
        mock_task.clone_status = CloneStatus.PROCESSING
        mock_task.repo_id = "FOR_TEST_OUTPUT_ONLY"
        mock_db.get_status_row_by_id.return_value = mock_task

        input_msg = {
            "url": "5.com",
            "opt_id": 1,
            "status": BuildStatus.SUCCESS,
            "msg": "finished",
            "task_id": 10001,
            "build_time": 5,
            "commit_hexsha": "xxxxxx",
        }
        mock_body: str = json.dumps(input_msg)
        mock_body: bytes = mock_body.encode()

        c = coordinator.Coordinator(DefaultSettings)
        c.recv_build_info(mock_channel, input_method, input_props, mock_body)

        # acknowledgement still necessary bc acknowledgement will remove this task from queue
        mock_channel.basic_ack.assert_called_once_with(delivery_tag=input_method.delivery_tag)

        mock_channel.basic_publish.assert_not_called()
        # mock_db.update_repo_status.assert_called()
        # currently the repo status is updated anyway for this particular scenario.
        # whether this behavior is expected or not is unknown
        # if we want to test that this behavior stays, uncomment above line

        # should be trapped in the database sync code for the full 10 seconds
        self.assertEqual(
            mock_db.get_status_row_by_id.call_count, COORDINATOR_DATABASE_SYNC_TIMEOUT + 1
        )
        mock_db.get_status_row_by_id.assert_called_with(
            10001
        )  # checks only the last entry, but all should be 10001

    @patch("assemblage.mq.client.BlockingChannel")
    @patch("assemblage.mq.client.BlockingConnection")
    @patch("assemblage.coordinator.coordinator.DBManager")
    @patch("assemblage.coordinator.coordinator.time")  # patched purely to skip the waiting
    def test_recv_build_info_clone_wait_eventual_success(
        self, MockTime, MockDBManager, MockConnection, MockChannel
    ):
        """
        Tests that if a task w a clone status of PROCESSING is passed, then the status is updated to SUCCESS
        two attempts later, the task will continue to be processed as expected
        """

        mock_connection, mock_channel = helper.mock_functioning_rabbitmq(
            MockConnection, MockChannel
        )
        mock_db = helper.mock_functioning_dbmanager(MockDBManager)
        input_method = MagicMock()
        input_props = MagicMock()

        # We create two mock tasks, fill in appropriate data, then configure get_status_row_by_id to return the
        # success on the third time it's called, to mimic the database updating after a few seconds
        # This is more stable than using one mock with a changing clone_status,
        # as that way is affected by total calls to clone_status, which can be affected e.g. by log messages.
        mock_task_unstarted = MagicMock()
        mock_task_unstarted.clone_status = CloneStatus.NOT_STARTED
        mock_task_unstarted.repo_id = "FOR_TEST_OUTPUT_ONLY_UNSTARTED"

        mock_task_success = MagicMock()
        mock_task_success.clone_status = CloneStatus.SUCCESS
        mock_task_success.repo_id = "FOR_TEST_OUTPUT_ONLY_SUCCESS"

        mock_db.get_status_row_by_id.side_effect = [
            mock_task_unstarted,
            mock_task_unstarted,
            mock_task_success,
        ]

        input_msg = {
            "url": "5.com",
            "opt_id": 1,
            "status": BuildStatus.SUCCESS,
            "msg": "finished",
            "task_id": 10001,
            "build_time": 5,
            "commit_hexsha": "xxxxxx",
        }
        mock_body: str = json.dumps(input_msg)
        mock_body: bytes = mock_body.encode()

        c = coordinator.Coordinator(DefaultSettings)
        c.recv_build_info(mock_channel, input_method, input_props, mock_body)

        # acknowledgement still necessary bc acknowledgement will remove this task from queue
        mock_channel.basic_ack.assert_called_once_with(delivery_tag=input_method.delivery_tag)

        mock_channel.basic_publish.assert_not_called()
        mock_db.update_repo_status.assert_called_once_with(
            status_id=input_msg["task_id"],
            build_time=ANY,
            build_status=input_msg["status"],
            build_msg=ANY,
            commit_hexsha=ANY,
        )

        self.assertEqual(mock_db.get_status_row_by_id.call_count, 3)

    @patch("assemblage.mq.client.BlockingChannel")
    @patch("assemblage.mq.client.BlockingConnection")
    @patch("assemblage.coordinator.coordinator.DBManager")
    def test_recv_clone_info_success(self, MockDBManager, MockConnection, MockChannel):
        """
        Tests that recv_clone_info, on receiving a non-timed-out message, adds it to the DB
        (the message has a cloned success, we mock getting this as well)
        This one is definitely a regression test, NOT a unit test. I'm not sure why the original
        function does what it does, but this checks that it keeps doing that.
        TODO: would like to mock out the logger to check what code path is explored
        """

        mock_connection, mock_channel = helper.mock_functioning_rabbitmq(
            MockConnection, MockChannel
        )
        mock_db = helper.mock_functioning_dbmanager(MockDBManager)

        input_method = MagicMock()
        input_props = MagicMock()
        mock_body: str = (
            '{"url": "url", "task_id": 1001, "msg": "Message", "opt_id": 1, "status":"'
            + BuildStatus.SUCCESS
            + '"}'
        )
        mock_body: bytes = mock_body.encode()

        # Mock a successful query from get_status_row_by_id
        mock_task_success = MagicMock()
        mock_task_success.clone_status = CloneStatus.SUCCESS
        mock_task_success.repo_id = "FOR_TEST_OUTPUT_ONLY"

        mock_db.get_status_row_by_id.return_value = mock_task_success

        c = coordinator.Coordinator(DefaultSettings)
        c.recv_clone_info(mock_channel, input_method, input_props, mock_body)

        # check that acknowledgement happened
        mock_channel.basic_ack.assert_called_once_with(delivery_tag=input_method.delivery_tag)

        mock_db.update_repo_status.assert_called_once_with(
            status_id=1001, clone_status=BuildStatus.SUCCESS, clone_msg="Message"
        )

    @patch("assemblage.mq.client.BlockingChannel")
    @patch("assemblage.mq.client.BlockingConnection")
    @patch("assemblage.coordinator.coordinator.DBManager")
    def test_recv_clone_info_badclone(self, MockDBManager, MockConnection, MockChannel):
        """
        Tests that recv_clone_info, on receiving a message with a non-success, behaves as expected.
        Currently this behavior is the same as a success.
        NOTE: task.clone_status is used as a PROXY for the logging task, as patching out and listening
        to the logger is more complicated than expected. The assumption is that if clone_status is accessed
        more than once, it's because it's being printed to the console, but this is a fragile assumption.

        Also, the logic in the code as-is is very questionable to me (why do we print to console that we're
        updating based on the clone status, but actually update on the build status?) so this is purely a regression test.
        """

        mock_connection, mock_channel = helper.mock_functioning_rabbitmq(
            MockConnection, MockChannel
        )
        mock_db = helper.mock_functioning_dbmanager(MockDBManager)

        input_method = MagicMock()
        input_props = MagicMock()
        mock_body = (
            '{"url": "url", "task_id": 1001, "msg": "Message", "opt_id": 1, "status":"'
            + BuildStatus.SUCCESS
            + '"}'
        )
        mock_body: bytes = mock_body.encode()

        # Mock getting the success
        mock_task_success = MagicMock()
        mock_task_success.clone_status = CloneStatus.FAILED
        mock_task_success.repo_id = "FOR_TEST_OUTPUT_ONLY"

        mock_db.get_status_row_by_id.return_value = mock_task_success

        c = coordinator.Coordinator(DefaultSettings)
        c.recv_clone_info(mock_channel, input_method, input_props, mock_body)

        # check that acknowledgement happened
        mock_channel.basic_ack.assert_called_once_with(delivery_tag=input_method.delivery_tag)

        mock_db.update_repo_status.assert_called_once_with(
            status_id=1001,
            clone_status=BuildStatus.SUCCESS,
            # I have a theory that this should instead be CloneStatus.FAILED if the system was working as expected,
            # but this is what the code currently does.
            clone_msg="Message",
        )

    @patch("assemblage.mq.client.BlockingChannel")
    @patch("assemblage.mq.client.BlockingConnection")
    @patch("assemblage.coordinator.coordinator.DBManager")
    @patch(
        "assemblage.coordinator.coordinator.threading"
    )  # patched to prevent orphan threads -- not tested on
    def test_recv_builder_registration_db(
        self, MockThreading, MockDBManager, MockConnection, MockChannel
    ):
        """
        Tests that on builder registration, the db is appropriately accessed and an acknowledgement is sent.
        Creation of dispatch threads is tested in the next test.
        """

        mock_connection, mock_channel = helper.mock_functioning_rabbitmq(
            MockConnection, MockChannel
        )
        mock_db = helper.mock_functioning_dbmanager(MockDBManager)

        # Set up required return from db
        mock_db.register_build_opt.return_value = 3

        input_method = MagicMock()
        input_props = MagicMock()
        # Frozen handshake: coordinator replies on the caller-provided reply_to
        # queue with the caller's correlation_id (not the shared builder_ctrl).
        input_props.reply_to = "builder_ctrl_6a70"
        input_props.correlation_id = "6a70..."
        mock_body = msg.BuilderRegIn(
            name="clang-builder",
            uuid="6a70...",
            compiler="clang",
            compiler_version="1.0",
            library="x64",
            language="c++",
            save_assembly=True,
            platform="linux",
            compiler_flag=None,
            build_command=None,
            build_system="all",
        )
        mock_body = mock_body.to_json()

        c = coordinator.Coordinator(DefaultSettings)
        c.recv_builder_registration(mock_channel, input_method, input_props, mock_body.encode())

        # Check that registration happened
        mock_db.register_build_opt.assert_called_once()
        # Check that the contents of the message that was registered equals that of the test
        actual_sent_msg = mock_db.register_build_opt.call_args[0][0]
        self.assertEqual(
            mock_body, actual_sent_msg.to_json(), "Wrong message sent to register build opt in db"
        )

        mock_channel.basic_ack.assert_called_once_with(delivery_tag=input_method.delivery_tag)
        mock_channel.basic_publish.assert_called_once_with(
            exchange="",
            routing_key="builder_ctrl_6a70",
            properties=BasicProperties(
                correlation_id="6a70...", delivery_mode=2, reply_to="builder_ctrl_6a70"
            ),
            body=msg.BuilderRegOut(3).to_json(),
        )

        # MockThreading.Thread.assert_called_once()  # this should be true, but will be caught by the next test if not

    @patch("assemblage.mq.client.BlockingChannel")
    @patch("assemblage.mq.client.BlockingConnection")
    @patch("assemblage.coordinator.coordinator.DBManager")
    @patch("assemblage.coordinator.coordinator.threading")
    def test_recv_builder_registration_createthread(
        self, MockThreading, MockDBManager, MockConnection, MockChannel
    ):
        """
        Tests that when a new builder registers, one thread is spun up and started.
        (Does not check db/rabbitmq accesses -- see above)
        """

        mock_connection, mock_channel = helper.mock_functioning_rabbitmq(
            MockConnection, MockChannel
        )
        mock_db = helper.mock_functioning_dbmanager(MockDBManager)

        mock_thread = MagicMock()
        MockThreading.Thread.return_value = mock_thread

        # Set up required return from db
        mock_db.register_build_opt.return_value = 3

        input_method = MagicMock()
        input_props = MagicMock()
        mock_body = msg.BuilderRegIn(
            name="clang-builder",
            uuid="6a70...",
            compiler="clang",
            compiler_version="1.0",
            library="x64",
            language="c++",
            save_assembly=True,
            platform="linux",
            compiler_flag=None,
            build_command=None,
            build_system="all",
        )
        mock_body = mock_body.to_json()

        c = coordinator.Coordinator(DefaultSettings)
        c.recv_builder_registration(mock_channel, input_method, input_props, mock_body.encode())

        MockThreading.Thread.assert_called_once_with(
            target=c._Coordinator__dispatch_task,
            args=(3, True),
            # wondering why I used _Coordinator__dispatch_task here? It's due to Python name mangling
        )
        mock_thread.start.assert_called_once()

    @patch("assemblage.mq.client.BlockingChannel")
    @patch("assemblage.mq.client.BlockingConnection")
    @patch("assemblage.coordinator.coordinator.DBManager")
    @patch("assemblage.coordinator.coordinator.threading")
    def test_recv_builder_registration_foundthread(
        self, MockThreading, MockDBManager, MockConnection, MockChannel
    ):
        """
        Tests that when a new builder registers on a buildopt that already has a thread,
        it uses that thread instead
        We test this by emulating two sent messages, presumably from two builders that are nearly identical
        except for the UUID. Only one thread should be spun up, for the first one.
        """

        mock_connection, mock_channel = helper.mock_functioning_rabbitmq(
            MockConnection, MockChannel
        )
        mock_db = helper.mock_functioning_dbmanager(MockDBManager)

        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        MockThreading.Thread.return_value = mock_thread

        # Set up required return from db
        mock_db.register_build_opt.return_value = 3

        input_method = MagicMock()
        input_props = MagicMock()
        mock_body = msg.BuilderRegIn(
            name="clang-builder",
            uuid="6a70...",
            compiler="clang",
            compiler_version="1.0",
            library="x64",
            language="c++",
            save_assembly=True,
            platform="linux",
            compiler_flag=None,
            build_command=None,
            build_system="all",
        )
        mock_body1 = mock_body.to_json()
        mock_body.uuid = "92e8..."
        mock_body2 = mock_body.to_json()

        c = coordinator.Coordinator(DefaultSettings)
        c.recv_builder_registration(mock_channel, input_method, input_props, mock_body1.encode())

        # Assert that these were both called on the first build message request
        MockThreading.Thread.assert_called_once_with(
            target=c._Coordinator__dispatch_task, args=(3, True)
        )
        mock_thread.start.assert_called_once()

        c.recv_builder_registration(mock_channel, input_method, input_props, mock_body2.encode())

        # Assert that these are NOT called again
        MockThreading.Thread.assert_called_once()
        mock_thread.start.assert_called_once()

    @patch("assemblage.mq.client.BlockingChannel")
    @patch("assemblage.mq.client.BlockingConnection")
    @patch("assemblage.coordinator.coordinator.DBManager")
    @patch("assemblage.coordinator.coordinator.threading")
    def test_recv_builder_registration_two_diffopts(
        self, MockThreading, MockDBManager, MockConnection, MockChannel
    ):
        """
        Tests that when two separate builders register after each other with different buildopts,
        two threads are spun up.

        """

        mock_connection, mock_channel = helper.mock_functioning_rabbitmq(
            MockConnection, MockChannel
        )
        mock_db = helper.mock_functioning_dbmanager(MockDBManager)

        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        MockThreading.Thread.return_value = mock_thread

        # Set up required return from db
        mock_db.register_build_opt.side_effect = [3, 4]

        input_method = MagicMock()
        input_props = MagicMock()
        mock_body = msg.BuilderRegIn(
            name="clang-builder",
            uuid="6a70...",
            compiler="clang",
            compiler_version="1.0",
            library="x64",
            language="c++",
            save_assembly=True,
            platform="linux",
            compiler_flag=None,
            build_command=None,
            build_system="all",
        )
        mock_body1 = mock_body.to_json()
        mock_body.uuid = "92e8..."
        mock_body.compiler = "clangn't"
        mock_body2 = mock_body.to_json()

        c = coordinator.Coordinator(DefaultSettings)
        c.recv_builder_registration(mock_channel, input_method, input_props, mock_body1.encode())
        c.recv_builder_registration(mock_channel, input_method, input_props, mock_body2.encode())

        MockThreading.Thread.assert_any_call(target=c._Coordinator__dispatch_task, args=(3, True))
        MockThreading.Thread.assert_any_call(target=c._Coordinator__dispatch_task, args=(4, True))

        self.assertEqual(mock_thread.start.call_count, 2)
        self.assertEqual(MockThreading.Thread.call_count, 2)

    @patch("assemblage.mq.client.BlockingChannel")
    @patch("assemblage.mq.client.BlockingConnection")
    @patch("assemblage.coordinator.coordinator.DBManager")
    @patch("assemblage.coordinator.coordinator.time")  # patched purely to skip the waiting
    def test_dispatch_thread_empty(self, MockTime, MockDBManager, MockConnection, MockChannel):
        """
        Tests that when no messages are present to be dispatched, the thread idles
        """
        mock_connection, mock_channel = helper.mock_functioning_rabbitmq(
            MockConnection, MockChannel
        )
        mock_db = helper.mock_functioning_dbmanager(MockDBManager)

        # no ready-to-dispatch threads found
        mock_db.get_dispatch_task.return_value = None

        c = coordinator.Coordinator(DefaultSettings)
        # note mangling
        c._Coordinator__dispatch_task(2, sleep=True, only_run_once=True)

        # important: the dispatch thread currently creates its OWN channel and does NOT use the mqclient.
        # so mock_channel picks up any calls on both 'thread_channel' AND 'self.mq_client'.
        # This shouldn't matter too much, but may cause issues if we need to differentiate the two later.
        mock_channel.basic_publish.assert_not_called()
        mock_db.update_repo_status.assert_not_called()

    @patch("assemblage.mq.client.BlockingChannel")
    @patch("assemblage.mq.client.BlockingConnection")
    @patch("assemblage.coordinator.coordinator.DBManager")
    @patch("assemblage.coordinator.coordinator.time")  # patched purely to skip the waiting
    def test_dispatch_thread_onemsg(self, MockTime, MockDBManager, MockConnection, MockChannel):
        """
        Tests that when one entry in the database is ready to be sent, a message is sent for it
        NOTICE: this function could really benefit from some autospecs replacing
        the mock db messages, to test the somewhat complex database accesses that happen
        within the dispatch thread.
        """
        mock_connection, mock_channel = helper.mock_functioning_rabbitmq(
            MockConnection, MockChannel
        )
        mock_db = helper.mock_functioning_dbmanager(MockDBManager)

        MockTime.time.return_value = 50

        expected_build_message = msg.BuilderTaskOut(
            name="RepoName",
            url="123ABC",
            task_id=44284,
            opt_id=2,
            output_dir=f"{BIN_DIR}/44284",
            repo_id=7921,
            updated_at="placeholder_updatetime",
            build_system="MaybeClangOrSomething",
            msg_time=MockTime.time(),
            compiler_flag="-O2",
        )
        mock_db.get_dispatch_task.return_value = expected_build_message
        # dispatch checks queue depth via passive declare before sending
        mock_channel.queue_declare.return_value.method.message_count = 0

        c = coordinator.Coordinator(DefaultSettings)
        # note mangling
        c._Coordinator__dispatch_task(2, sleep=True, only_run_once=True)

        # important: the dispatch thread currently creates its OWN channel and does NOT use the mqclient.
        # so mock_channel picks up any calls on both 'thread_channel' AND 'self.mq_client'.
        # This shouldn't matter too much, but may cause issues if we need to differentiate the two later.
        mock_channel.basic_publish.assert_called_once()
        mock_db.update_repo_status.assert_called_once()

        # check that args are as expected.
        publish_args: unittest.mock.call = mock_channel.basic_publish.call_args
        # Was called with correct settings
        self.assertEqual(publish_args.kwargs["exchange"], "build_opt")
        self.assertEqual(publish_args.kwargs["routing_key"], "build_opt_2")

        # Body was correct
        self.assertEqual(publish_args.kwargs["body"], expected_build_message.to_json().encode())

    @patch("assemblage.mq.client.BlockingChannel")
    @patch("assemblage.mq.client.BlockingConnection")
    @patch("assemblage.coordinator.coordinator.DBManager")
    @patch("assemblage.coordinator.coordinator.time")  # patched purely to skip the waiting
    @unittest.skip("Trying to think of a good way to test that RabbitMQ reconnects properly")
    def test_dispatch_thread_reconnect(
        self, MockTime, MockDBManager, MockConnection, MockChannel
    ):  #
        """
        Tests that if the dispatch thread encounters an error in dispatch,
        it will reopen the channel and continue
        """

        mock_connection, mock_channel = helper.mock_functioning_rabbitmq(
            MockConnection, MockChannel
        )
        mock_db = helper.mock_functioning_dbmanager(MockDBManager)

        mock_db.find_status_by_status_code.return_value = ["Bad Task"]

        c = coordinator.Coordinator(DefaultSettings)

        # Ensure that an exception will be raised inside of the dispatch task (since it's caught before
        # this test function will get to see it)

        self.assertEqual(c.mq_client.get_connection(f"{c}").chan, mock_channel)

        self.assertRaises(
            Exception, c._dispatch_to_builder, 2, c.mq_client.get_connection(f"{c}").chan, True, 0
        )

        # note mangling
        c._Coordinator__dispatch_task(2, sleep=True, only_run_once=True)

    @patch("assemblage.mq.client.BlockingChannel")
    @patch("assemblage.mq.client.BlockingConnection")
    @patch("assemblage.coordinator.coordinator.MessageClient")
    @unittest.skip(
        "Working on repairing this test to work with new infinite-retry consume. As is, it's not very informative"
    )
    def test_basic_consume_from_queue(self, MockConnection, MockChannel, MockClient):
        """
        Tests that a valid queue name will be consumed from
        """

        mock_connection, mock_channel = helper.mock_functioning_rabbitmq(
            MockConnection, MockChannel
        )
        mock_client = MockClient.return_value

        c = coordinator.Coordinator(DefaultSettings)

        c._Coordinator__consume_from_queue(InputQueue.SCRAPE, only_run_once=True)

        # Check that the consume function was successfully called
        c.mq_client.start_consumer.assert_called_once()
        # c.mq_client.start_consumer.assert_called_with(
        #     conn=ok_mock,
        #     queue=client.MQQueue(
        #         name=InputQueue.SCRAPE, callback=c.recv_scrape_info
        #         ),
        #     retry_delay=ANY
        # )

    @patch("assemblage.mq.client.BlockingChannel")
    @patch("assemblage.mq.client.BlockingConnection")
    def test_basic_consume_from_queue_invalid(self, MockConnection, MockChannel):
        """
        Tests that an invalid queue name will not be permitted
        """

        mock_connection, mock_channel = helper.mock_functioning_rabbitmq(
            MockConnection, MockChannel
        )

        c = coordinator.Coordinator(DefaultSettings)
        c._Coordinator__consume_from_queue("invalid", only_run_once=True)

        # Check that the consume function was successfully NOT called
        mock_channel.start_consuming.assert_not_called()


if __name__ == "__main__":
    logger.info("Starting tests in coordinator_test.py")
    unittest.main()

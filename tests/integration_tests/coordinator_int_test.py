"""
[Stub] Tests the coordinator on the test db
"""

import logging
import unittest
from unittest.mock import MagicMock, patch

import assemblage.config as settings
import assemblage.consts as const
import assemblage.database.models as model
import assemblage.mq.messages as msg
import pytest
import sqlalchemy as sqla
from assemblage.coordinator.coordinator import Coordinator

import tests.integration_tests.integration_helper as helper
from tests.constants import TEST_MESSAGE_LEVEL

pytestmark = pytest.mark.integration  # needs the live test database
logging.basicConfig(
    format="%(asctime)s [TEST] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=TEST_MESSAGE_LEVEL,
)
logger = logging.getLogger(__name__)


class TestDBManager(unittest.TestCase):
    @patch("assemblage.coordinator.coordinator.time")
    @patch("assemblage.mq.client.BlockingChannel")
    @patch("assemblage.mq.client.BlockingConnection")
    def test_multiple_dispatches(self, MockConn, MockChan, MockSleep):
        """
        Tests that when _dispatch_to_builder is run multiple times on the same DB, it appropriately
        consumes the entries from that DB and (mocked) sends expected (i.e. all different) messages.
        """

        # Must patch the coordinator to use the test database. Currently also mocks rabbitMQ.
        patched_settings = settings.CoordinatorSettings()
        helper.apply_test_db_settings(patched_settings)

        c = Coordinator(patched_settings)

        c._dispatch_queue_map = (
            MagicMock()
        )  # just to run w/out errors. This func doesn't test dispatch queue functionality at all
        # The per-buildopt "empty" events are normally created by the dispatch
        # thread manager; this test drives _dispatch_to_builder directly, so mock
        # the map to let the idle/request branch run without a KeyError.
        c.t_empty_built_opt = MagicMock()

        mock_connection = MagicMock()
        # Queue depth is consulted before dispatch (passive-declare threshold);
        # keep it below COORDINATOR_REPO_REQUEST_THRESHOLD so tasks are sent.
        mock_connection.get_queue.return_value.method.message_count = 0

        helper.truncate_all()
        helper.seed_database_projects()
        helper.seed_database_buildopts()
        helper.seed_database_statuses_unstarted()

        c._dispatch_to_builder(1, mock_connection, False, 0)
        c._dispatch_to_builder(1, mock_connection, False, 1)
        c._dispatch_to_builder(1, mock_connection, False, 2)
        c._dispatch_to_builder(1, mock_connection, False, 3)  # should idle

        mock_connection.send_msg.assert_called()

        # Check that three diff messages were sent
        sent_messages = self.get_msgs_from_call(mock_connection.send_msg.call_args_list)

        self.assertTrue(len(sent_messages), 3)
        sent_message_names = [sent_messages[0].name, sent_messages[1].name, sent_messages[2].name]

        self.assertTrue("PROJECT_1" in sent_message_names)
        self.assertTrue("PROJECT_2" in sent_message_names)
        self.assertTrue("PROJECT_3" in sent_message_names)

        # Check that in the db, all rows with opt 1 were changed and nothing else was changed
        ok, bad, changed = (0, 0, 0)
        with c.db_man.get_session() as session:
            query = sqla.select(model.Status)
            result = session.execute(query)
            for r in result.scalars():
                if r.clone_status == const.CloneStatus.PROCESSING and r.build_opt_id == 1:
                    ok += 1
                    changed += 1
                elif r.clone_status != const.CloneStatus.PROCESSING and r.build_opt_id != 1:
                    ok += 1
                else:
                    changed += 1
                    bad += 1

        self.assertEqual(ok, 6)
        self.assertEqual(bad, 0)
        self.assertEqual(changed, 3)

    def get_msgs_from_call(self, callarglist):

        sent_messages = []

        for call in callarglist:
            sent_messages.append(msg.BuilderTaskOut.from_json(call.kwargs["msg"]))

        return sent_messages


if __name__ == "__main__":
    logger.info("Starting tests in coordinator_int_test.py")
    unittest.main()

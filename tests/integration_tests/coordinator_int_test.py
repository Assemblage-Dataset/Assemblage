"""Integration test: the dispatcher drains a build option against the real DB."""

import logging
import threading
import unittest
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sqla
from assemblage.coordinator.dispatch import DispatcherService, StarvationSignals
from assemblage.db.models import Status
from assemblage.enums import CloneStatus
from assemblage.messages import BuildTask

import tests.integration_tests.integration_helper as helper
from tests.constants import TEST_MESSAGE_LEVEL

pytestmark = pytest.mark.integration  # needs the live test database
logging.basicConfig(
    format="%(asctime)s [TEST] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=TEST_MESSAGE_LEVEL,
)
logger = logging.getLogger(__name__)


class TestDispatcherIntegration(unittest.TestCase):
    def test_dispatches_and_marks_processing(self):
        helper.truncate_all()
        helper.seed_database_projects()
        helper.seed_database_buildopts()
        helper.seed_database_statuses_unstarted()

        store = helper.make_store()
        service = DispatcherService(
            opt_id=1, store=store, starvation=StarvationSignals(), factory=MagicMock()
        )

        publisher = MagicMock()
        publisher.queue_depth.return_value = 0
        stop = MagicMock()  # stop.wait returns immediately (no real sleeps)

        # opt 1 has three un-started tasks; a fourth step finds nothing.
        for _ in range(4):
            service.dispatch_step(publisher, stop)

        # three distinct tasks published to build_opt_1
        self.assertEqual(publisher.publish.call_count, 3)
        names = {
            BuildTask.model_validate_json(c.args[1]).name for c in publisher.publish.call_args_list
        }
        self.assertEqual(names, {"PROJECT_1", "PROJECT_2", "PROJECT_3"})

        # exactly opt-1 rows are now PROCESSING; opt-2 rows untouched
        with store._engine.connect() as conn:
            rows = conn.execute(sqla.select(Status.build_opt_id, Status.clone_status)).all()
        processing = {r[0] for r in rows if r[1] == CloneStatus.PROCESSING}
        untouched = {r[0] for r in rows if r[1] == CloneStatus.NOT_STARTED}
        self.assertEqual(processing, {1})
        self.assertEqual(untouched, {2})

    def test_starvation_flag_set_when_empty(self):
        helper.truncate_all()
        helper.seed_database_buildopts()  # no repos -> no statuses

        store = helper.make_store()
        starvation = StarvationSignals()
        service = DispatcherService(
            opt_id=1, store=store, starvation=starvation, factory=MagicMock()
        )
        publisher = MagicMock()
        publisher.queue_depth.return_value = 0

        service.dispatch_step(publisher, threading.Event())

        self.assertEqual(starvation.take_starving(), [1])
        publisher.publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""Unit tests for the re-architected coordinator.

The handlers are now pure functions of ``(store, typed message[, deps])``, so
these tests call them directly with a ``MagicMock`` store and typed messages and
assert the :class:`AckDecision` plus the store calls — no pika mocks, no channel
plumbing. The frozen behaviours the E2E gate also guards are pinned here:
reply-to routing with the caller's correlation id, the ``build_opt_{id}`` routing
key, and PROCESSING marked only *after* a confirmed publish.
"""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call

from assemblage.coordinator.dispatch import (
    DispatcherService,
    StarvationSignals,
    patch_url,
)
from assemblage.coordinator.ingest import (
    BuildStats,
    handle_binary,
    handle_build_status,
    handle_clone_status,
    handle_scrape,
)
from assemblage.coordinator.registration import (
    handle_builder_registration,
    handle_scraper_registration,
)
from assemblage.db.store import DispatchCandidate
from assemblage.enums import BuildStatus, CloneStatus, ScraperMsgType
from assemblage.messages import (
    BinaryRecordMsg,
    BuildStatusMsg,
    BuildTask,
    CloneStatusMsg,
    ScrapeBundle,
)
from assemblage.mq.consumer import AckDecision

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "messages"


def golden(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


class TestPatchUrl(unittest.TestCase):
    def test_patch_url_strips_api_and_repos(self):
        self.assertEqual(
            patch_url("https://api.github.com/repos/Assemblage-Dataset/Assemblage"),
            "https://github.com/Assemblage-Dataset/Assemblage",
        )


class TestIngest(unittest.TestCase):
    def test_handle_scrape_inserts_each_repo_and_clears_pending(self):
        store = MagicMock()
        store.insert_repos.return_value = 1
        cleared = MagicMock()
        bundle = ScrapeBundle.model_validate_json(
            (FIXTURES / "scraper_data_out_bundle.json").read_text()
        )

        decision = handle_scrape(store, bundle, cleared)

        self.assertEqual(decision, AckDecision.ACK)
        cleared.assert_called_once_with()
        self.assertEqual(store.insert_repos.call_count, 2)

    def test_handle_scrape_tolerates_one_bad_repo(self):
        store = MagicMock()
        store.insert_repos.side_effect = [RuntimeError("boom"), 1]
        bundle = ScrapeBundle.model_validate_json(
            (FIXTURES / "scraper_data_out_bundle.json").read_text()
        )

        decision = handle_scrape(store, bundle, MagicMock())

        self.assertEqual(decision, AckDecision.ACK)
        self.assertEqual(store.insert_repos.call_count, 2)

    def test_handle_binary_inserts_with_empty_description(self):
        store = MagicMock()
        decision = handle_binary(store, BinaryRecordMsg(task_id=42, file_name="doom"))
        self.assertEqual(decision, AckDecision.ACK)
        store.insert_binary.assert_called_once_with(file_name="doom", description="", status_id=42)

    def test_handle_clone_status_parses_via_clonestatus(self):
        store = MagicMock()
        msg = CloneStatusMsg.model_validate_json(
            (FIXTURES / "clone_status_msg_in.json").read_text()
        )
        decision = handle_clone_status(store, msg)
        self.assertEqual(decision, AckDecision.ACK)
        # status is a CloneStatus member, not a BuildStatus round-trip.
        store.update_repo_status.assert_called_once_with(
            status_id=42, clone_status=CloneStatus.SUCCESS, clone_msg="cloned ok"
        )
        _, kwargs = store.update_repo_status.call_args
        self.assertIsInstance(kwargs["clone_status"], CloneStatus)

    def test_handle_build_status_success_counts_no_siblings(self):
        store = MagicMock()
        stats = BuildStats()
        msg = BuildStatusMsg.model_validate_json(
            (FIXTURES / "build_status_msg_in.json").read_text()
        )
        self.assertEqual(msg.status, BuildStatus.SUCCESS)

        decision = handle_build_status(store, msg, stats)

        self.assertEqual(decision, AckDecision.ACK)
        store.update_repo_status.assert_called_once_with(
            status_id=42,
            build_status=BuildStatus.SUCCESS,
            build_time=63,
            build_msg="Build Process Finished",
            commit_hexsha="a77dbb7db76c1cff2a3c9a1049671c1e64f9d716",
        )
        self.assertEqual(stats.successes, 1)
        self.assertEqual(stats.failures, 0)
        store.fail_sibling_statuses.assert_not_called()

    def test_handle_build_status_failed_fails_siblings(self):
        store = MagicMock()
        store.get_status_row_by_id.return_value = MagicMock(repo_id=13)
        store.fail_sibling_statuses.return_value = 2
        stats = BuildStats()
        msg = BuildStatusMsg(
            url="u",
            opt_id=7,
            status=BuildStatus.FAILED,
            msg="nope",
            task_id=42,
            build_time=1,
            commit_hexsha="",
        )

        decision = handle_build_status(store, msg, stats)

        self.assertEqual(decision, AckDecision.ACK)
        store.fail_sibling_statuses.assert_called_once_with(13, 42, msg="Sibling build failed")
        # one direct failure + two skipped siblings
        self.assertEqual(stats.failures, 3)

    def test_handle_build_status_ignores_non_terminal(self):
        store = MagicMock()
        stats = BuildStats()
        msg = BuildStatusMsg(
            url="u",
            opt_id=7,
            status=BuildStatus.PROCESSING,
            msg="",
            task_id=42,
            build_time=0,
            commit_hexsha="",
        )
        handle_build_status(store, msg, stats)
        # status still recorded, but no counters/siblings for a non-terminal status
        store.update_repo_status.assert_called_once()
        store.get_status_row_by_id.assert_not_called()
        self.assertEqual((stats.successes, stats.failures), (0, 0))


class TestRegistration(unittest.TestCase):
    def test_builder_registration_replies_and_starts_dispatch(self):
        store = MagicMock()
        store.register_build_opt.return_value = 7
        replies = []
        started = []
        reg = MagicMock(
            name="gcc-O2", compiler="gcc", compiler_flag="-O2", platform="linux", language="c++"
        )

        decision = handle_builder_registration(store, reg, replies.append, started.append)

        self.assertEqual(decision, AckDecision.ACK)
        store.register_build_opt.assert_called_once_with(reg)
        self.assertEqual(len(replies), 1)
        # Reply is the frozen builder_reg_out wire form.
        self.assertEqual(json.loads(replies[0].model_dump_json()), golden("builder_reg_out"))
        self.assertEqual(started, [7])

    def test_scraper_registration_setup_claims_and_registers_queue(self):
        store = MagicMock()
        store.register_scraper.return_value = {"start_time": 100, "end_time": 50}
        replies = []
        setup_calls = []
        request = MagicMock(message_type=ScraperMsgType.SETUP, start_time=1, end_time=2)

        decision = handle_scraper_registration(
            store, request, replies.append, lambda: setup_calls.append(True), "uuid-1"
        )

        self.assertEqual(decision, AckDecision.ACK)
        store.register_scraper.assert_called_once_with("uuid-1", 1, 2)
        self.assertEqual(replies[0].message_type, ScraperMsgType.SETUP)
        self.assertEqual((replies[0].start_time, replies[0].end_time), (100, 50))
        self.assertEqual(setup_calls, [True])

    def test_scraper_registration_update_does_not_register_queue(self):
        store = MagicMock()
        replies = []
        setup_calls = []
        request = MagicMock(message_type=ScraperMsgType.UPDATE, start_time=9, end_time=2)

        handle_scraper_registration(
            store, request, replies.append, lambda: setup_calls.append(True), "uuid-1"
        )

        store.update_scraper.assert_called_once_with("uuid-1", 9, 2)
        self.assertEqual(replies[0].message_type, ScraperMsgType.UPDATE)
        self.assertEqual(setup_calls, [])


def _candidate(task_id=42, opt_id=2):
    return DispatchCandidate(
        task_id=task_id,
        opt_id=opt_id,
        repo_id=13,
        name="DOOM",
        url="https://api.github.com/repos/id-Software/DOOM",
        updated_at="05/24/2024, 13:18:59",
        build_system="make",
        compiler_flag="-O2",
    )


class TestDispatcher(unittest.TestCase):
    def _service(self, store, starvation=None):
        return DispatcherService(
            opt_id=2,
            store=store,
            starvation=starvation or StarvationSignals(),
            factory=MagicMock(),
        )

    def test_dispatch_step_publishes_then_marks_processing(self):
        store = MagicMock()
        store.next_dispatchable.return_value = _candidate()
        recorder = MagicMock()
        publisher = MagicMock()
        publisher.queue_depth.return_value = 0
        # attach both calls to one recorder to assert ordering
        publisher.publish.side_effect = lambda *a, **k: recorder("publish")
        store.mark_clone_processing.side_effect = lambda *a, **k: recorder("mark")

        stop = MagicMock()  # stop.wait returns immediately, no real sleep
        self._service(store).dispatch_step(publisher, stop)

        # published to the build_opt exchange with the build_opt_{id} routing key
        publisher.publish.assert_called_once()
        published_queue = publisher.publish.call_args[0][0]
        self.assertEqual(published_queue.name, "build_opt_2")
        self.assertEqual(published_queue.exchange, "build_opt")
        self.assertEqual(published_queue.routing, "build_opt_2")

        # body is a BuildTask carrying the patched (cloneable) url
        body = publisher.publish.call_args[0][1]
        task = BuildTask.model_validate_json(body)
        self.assertEqual(task.url, "https://github.com/id-Software/DOOM")
        self.assertEqual(task.task_id, 42)
        self.assertEqual(task.compiler_flag, "-O2")

        # PROCESSING only after the confirmed publish
        store.mark_clone_processing.assert_called_once_with(42)
        self.assertEqual(recorder.mock_calls, [call("publish"), call("mark")])

    def test_dispatch_step_marks_starvation_when_empty(self):
        store = MagicMock()
        store.next_dispatchable.return_value = None
        starvation = MagicMock()
        publisher = MagicMock()
        publisher.queue_depth.return_value = 0

        self._service(store, starvation).dispatch_step(publisher, MagicMock())

        starvation.mark.assert_called_once_with(2)
        publisher.publish.assert_not_called()
        store.mark_clone_processing.assert_not_called()

    def test_dispatch_step_backpressure_skips_when_queue_full(self):
        store = MagicMock()
        publisher = MagicMock()
        publisher.queue_depth.return_value = 999  # far above the threshold

        self._service(store).dispatch_step(publisher, MagicMock())

        store.next_dispatchable.assert_not_called()
        publisher.publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()

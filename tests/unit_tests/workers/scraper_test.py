"""Unit + regression tests for the re-architected scraper.

Ported from the pre-re-architecture ``worker/scraper.py`` suite onto the new
``assemblage.scraper.github`` (GitHubClient / GitHubRepoSearch) and
``assemblage.scraper.app`` (RepoBundler / CrawlService) classes. The behaviour
assertions are unchanged — they are the frozen spec for the rate-limit,
credential, per-repo-message and bundling logic.

Only two tests make real GitHub requests; they begin with "test_live_" and are
marked ``live_github`` (deselected by default).
"""

import json
import logging
import os
import threading
import time
import unittest
from unittest.mock import ANY, patch

import pytest
from assemblage.constants import (
    GITHUB_REPO_URL,
    SCRAPER_PAGE_SIZE,
    SCRAPER_REPO_BUNDLESIZE,
    SCRAPER_REQUEST_TIMEOUT_S,
)
from assemblage.enums import ScraperOutputPolicy
from assemblage.messages import RepoRecord
from assemblage.mq.topology import SCRAPE
from assemblage.scraper.app import CrawlControl, CrawlService, RepoBundler
from assemblage.scraper.github import GitHubClient, GitHubRepoSearch, github_time_to_mysql_time
from requests import Response

import tests.unit_tests.helper_func as helper
from tests.constants import TEST_MESSAGE_LEVEL

logging.basicConfig(
    format="%(asctime)s [TEST] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=TEST_MESSAGE_LEVEL,
)
logger = logging.getLogger(__name__)

_LIVE_TOKEN = os.getenv("GITHUB_TOKEN", "")


def make_client(token: str = "") -> GitHubClient:
    return GitHubClient(token, worker_id=0)


def make_search(token: str = "") -> GitHubRepoSearch:
    # window/interval values are arbitrary; the ported tests drive the client
    # and _process_repo_message directly, not a full crawl.
    return GitHubRepoSearch(make_client(token), {"language:c++"}, 100, 0, 10, worker_id=0)


class TestGithubTime(unittest.TestCase):
    def test_github_time_to_mysql_time_good(self):
        self.assertEqual(github_time_to_mysql_time("2025-11-01T13:38:46Z"), "2025-11-01 13:38:46")

    def test_github_time_to_mysql_time_bad(self):
        self.assertEqual(
            github_time_to_mysql_time("33333gggg"),
            "2000-01-01 01:01:01",
            "Function should return fallback value (2000-01-01...)",
        )


class TestGitHubClient(unittest.TestCase):
    @pytest.mark.live_github
    def test_live_get_request_repo_fields_as_expected(self):
        """GitHub REST API returns all the expected fields for a repo tree."""
        q = "https://api.github.com/repos/id-Software/DOOM/git/trees/master"
        expected_keys = {"sha", "url", "tree", "truncated"}
        expected_tree_keys = {"path", "mode", "type", "sha", "url"}

        client = make_client(_LIVE_TOKEN)
        output, elapsed = client.get_request(query=q)

        self.assertEqual(type(output), Response, "A valid page should be received")
        page = json.loads(output.text)
        self.assertEqual(set(page.keys()), expected_keys, "API may have changed")
        self.assertTrue(expected_tree_keys.issubset(set(page["tree"][0].keys())))
        self.assertTrue(elapsed > 0)

    @pytest.mark.live_github
    def test_live_get_request_search_fields_as_expected(self):
        """Expected fields are present in the search API."""
        query = "created:2025-10-09T08:35:13+08:00..2025-10-09T12:35:13+08:00 language:c++"
        payload = {"q": query, "per_page": SCRAPER_PAGE_SIZE, "page": 0}
        expected_keys = {"total_count", "incomplete_results", "items"}

        client = make_client(_LIVE_TOKEN)
        output, elapsed = client.get_request(GITHUB_REPO_URL, payload=payload)

        self.assertEqual(type(output), Response, "A valid page should be received")
        page = json.loads(output.text)
        self.assertEqual(expected_keys, set(page.keys()))
        self.assertTrue(len(page["items"]) > 0, "'items' should not be empty")
        self.assertTrue(elapsed > 0)

    @patch("assemblage.scraper.github.requests")
    def test_get_request_repo(self, MockRequests):
        """get_request succeeds on a valid repo (GitHub REST API)."""
        mock_response = helper.scr_full_repo_response_tree()
        MockRequests.get.return_value = mock_response
        q = "https://api.github.com/repos/query/which/gives/mock/response"

        output, _elapsed = make_client().get_request(query=q)

        self.assertEqual(output, mock_response)
        MockRequests.get.assert_called_once_with(
            q, params=None, headers=ANY, proxies=ANY, timeout=SCRAPER_REQUEST_TIMEOUT_S
        )

    @patch("assemblage.scraper.github.requests")
    def test_get_request_search(self, MockRequests):
        """get_request succeeds on a valid search (GitHub search API)."""
        payload = {"q": "filters", "per_page": SCRAPER_PAGE_SIZE, "page": 0}
        mock_response = helper.scr_full_repo_response_search()
        MockRequests.get.return_value = mock_response

        output, _elapsed = make_client().get_request(GITHUB_REPO_URL, payload=payload)

        self.assertEqual(output, mock_response)
        MockRequests.get.assert_called_once_with(
            GITHUB_REPO_URL,
            params=payload,
            headers=ANY,
            proxies=ANY,
            timeout=SCRAPER_REQUEST_TIMEOUT_S,
        )

    def test_get_request_badquery(self):
        """get_request on a non-URL query. Technically "live" but no request is made."""
        output, _elapsed = make_client().get_request(query="")
        self.assertEqual(output, None)

    @patch("assemblage.scraper.github.time")
    @patch("assemblage.scraper.github.requests")
    def test_get_request_but_rate_limit(self, MockRequests, MockTime):
        """On a hit rate limit, get_request waits for the reset then retries."""
        mock_right_now = 1762793542
        MockTime.time.return_value = mock_right_now
        fake_ok = helper.scr_full_repo_response_tree()
        fake_rate_limit = helper.scr_skeleton_rate_limit_response()
        mock_can_retry_at = int(fake_rate_limit.headers["X-RateLimit-Reset"])
        MockRequests.get.side_effect = [fake_rate_limit, fake_ok]

        output, _elapsed = make_client().get_request(query="Doesn'tMatter")
        self.assertEqual(output, fake_ok)

        MockTime.sleep.assert_called()
        must_sleep_time = mock_can_retry_at - mock_right_now + 1  # note the +1
        actual_slept_time = helper.get_slept_time_from_args(MockTime.sleep.call_args_list)
        self.assertTrue(actual_slept_time >= must_sleep_time)

    @patch("assemblage.scraper.github.time")
    @patch("assemblage.scraper.github.requests")
    def test_get_request_about_to_rate_limit(self, MockRequests, MockTime):
        """On a just-hit rate limit, get_request waits but does not retry
        (the returned message is valid data)."""
        mock_right_now = 1762793542
        MockTime.time.return_value = mock_right_now
        fake_ok_about_to_not_be = helper.scr_skeleton_about_to_hit_limit_response()
        fake_nevercalled = helper.scr_full_repo_response_tree()
        mock_can_retry_at = int(fake_ok_about_to_not_be.headers["X-RateLimit-Reset"])
        MockRequests.get.side_effect = [fake_ok_about_to_not_be, fake_nevercalled]

        output, _elapsed = make_client().get_request(query="Doesn'tMatter")
        self.assertEqual(output, fake_ok_about_to_not_be)

        MockRequests.get.assert_called_once_with(
            "Doesn'tMatter",
            params=None,
            headers=ANY,
            proxies=ANY,
            timeout=SCRAPER_REQUEST_TIMEOUT_S,
        )
        MockTime.sleep.assert_called()
        must_sleep_time = mock_can_retry_at - mock_right_now + 1
        actual_slept_time = helper.get_slept_time_from_args(MockTime.sleep.call_args_list)
        self.assertTrue(actual_slept_time >= must_sleep_time)

    @patch("assemblage.scraper.github.time")
    @patch("assemblage.scraper.github.requests")
    def test_get_request_but_bad_credentials(self, MockRequests, MockTime):
        """On bad credentials the client retries unauthenticated and returns that
        second (successful) result, having dropped the token."""
        fake_ok = helper.scr_full_repo_response_tree()
        fake_invalid = helper.scr_skeleton_bad_cred_response()
        MockRequests.get.side_effect = [fake_invalid, fake_ok]

        client = make_client(token="NONE")
        output, _elapsed = client.get_request(query="Doesn'tMatter")

        self.assertEqual(output, fake_ok)
        self.assertEqual(None, client.token)
        self.assertEqual(MockRequests.get.call_count, 2)


class TestProcessRepoMessage(unittest.TestCase):
    @patch("assemblage.scraper.github.time")
    @patch("assemblage.scraper.github.requests")
    def test_process_repo_message_basic(self, MockRequests, MockTime):
        """A search-result item is expanded into a RepoRecord + file list."""
        search_item = json.loads(helper.scr_full_repo_response_search().text)["items"][0]
        self.assertEqual(
            search_item["node_id"],
            "MDEwOlJlcG9zaXRvcnkzMzE5MDQw",
            "example_search_github no longer has DOOM as first entry",
        )
        MockRequests.get.return_value = helper.scr_full_repo_response_tree()

        message, files = make_search()._process_repo_message(search_item)

        self.assertEqual(
            files,
            ["LICENSE.TXT", "README.TXT", "README.TXT", "sndserv"],
            "Returned files were not as expected",
        )
        self.maxDiff = None
        self.assertEqual(
            json.loads(message.model_dump_json()),
            json.loads(helper.scr_doom_messagestr()),
            "Returned message not as expected",
        )

        expected_request = f"{search_item['url']}/git/trees/{search_item['default_branch']}"
        MockTime.sleep.assert_called_once()
        MockRequests.get.assert_called_once_with(
            expected_request,
            params=None,
            headers=ANY,
            proxies=ANY,
            timeout=SCRAPER_REQUEST_TIMEOUT_S,
        )

    @patch("assemblage.scraper.github.time")
    @patch("assemblage.scraper.github.requests")
    def test_process_repo_message_string(self, MockRequests, MockTime):
        """_process_repo_message errors gracefully with input of wrong type."""
        output = make_search()._process_repo_message("GOOGLE DOT COM")
        self.assertEqual(output, (None, None), "A tuple of None should be received for bad repos")
        MockRequests.get.assert_not_called()

    @patch("assemblage.scraper.github.time")
    @patch("assemblage.scraper.github.requests")
    def test_process_repo_message_invalid_data(self, MockRequests, MockTime):
        """_process_repo_message errors gracefully when the tree request 404s."""
        data = {"url": "https://api.github.com/users/id-Software", "default_branch": "foo"}
        MockRequests.get.return_value = helper.scr_skeleton_404_response()

        output = make_search()._process_repo_message(data)

        self.assertEqual(output, (None, None), "A tuple of None should be received for bad repos")
        expected_request = f"{data['url']}/git/trees/{data['default_branch']}"
        MockRequests.get.assert_called_once_with(
            expected_request,
            params=None,
            headers=ANY,
            proxies=ANY,
            timeout=SCRAPER_REQUEST_TIMEOUT_S,
        )


class _FakePublisher:
    """Records publishes instead of touching RabbitMQ."""

    def __init__(self):
        self.published: list[tuple] = []

    def publish(
        self, queue, body, *, correlation_id=None, reply_to=None, declare=True, persistent=True
    ):
        self.published.append((queue, body, correlation_id))

    def scrape_bodies(self):
        return [body for queue, body, _cid in self.published if queue is SCRAPE]


class _FakeSearch:
    """A search stand-in that just yields a fixed list of records."""

    def __init__(self, repos):
        self._repos = repos
        self.current_crawl_time = 100
        self.crawl_time_end = 0

    def __iter__(self):
        return iter(self._repos)


def _repo(index: int) -> RepoRecord:
    return RepoRecord(
        name=f"repo{index}",
        url=f"https://api.github.com/repos/e2e/repo{index}",
        language="c++",
        owner_id=index,
        description="",
        created_at="2020-01-01 00:00:00",
        updated_at="2020-01-01 00:00:00",
        size=1,
        build_system="make",
        branch="main",
    )


class TestBundling(unittest.TestCase):
    def test_bundler_flush_publishes_bare_array(self):
        pub = _FakePublisher()
        bundler = RepoBundler(SCRAPE, "uuidxxxxx", SCRAPER_REPO_BUNDLESIZE)
        record = RepoRecord.model_validate_json(helper.scr_doom_messagestr())
        bundler.add(record)

        count = bundler.flush(pub)

        self.assertEqual(count, 1)
        self.assertEqual(len(bundler), 0, "cache must clear after a confirmed publish")
        queue, body, corr = pub.published[0]
        self.assertIs(queue, SCRAPE)
        self.assertEqual(corr, "uuidxxxxx")
        decoded = json.loads(body)
        self.assertIsInstance(decoded, list)
        self.assertEqual(decoded, [json.loads(helper.scr_doom_messagestr())])

    def test_bundler_flush_empty_is_noop(self):
        pub = _FakePublisher()
        bundler = RepoBundler(SCRAPE, "uuidxxxxx", SCRAPER_REPO_BUNDLESIZE)
        self.assertEqual(bundler.flush(pub), 0)
        self.assertEqual(pub.published, [])

    def _service(self, repos, policy):
        control = CrawlControl(policy=policy, ready=True)
        control.set_last_sent_crawltime(100)  # silence the UPDATE sync
        bundler = RepoBundler(SCRAPE, "uuidxxxxx", SCRAPER_REPO_BUNDLESIZE)
        service = CrawlService(
            None, _FakeSearch(repos), control, bundler, None, "uuidxxxxx", "scraper_ctrl_x"
        )
        return service, control

    def test_continuous_flushes_every_25(self):
        repos = [_repo(i) for i in range(50)]
        pub = _FakePublisher()
        service, _control = self._service(repos, ScraperOutputPolicy.CONTINUOUS)

        service._crawl(pub, threading.Event())

        bodies = pub.scrape_bodies()
        self.assertEqual(len(bodies), 2, "50 repos on CONTINUOUS should send two bundles")
        for body in bodies:
            self.assertEqual(len(json.loads(body)), SCRAPER_REPO_BUNDLESIZE)

    def test_on_request_holds_until_requested(self):
        repos = [_repo(i) for i in range(SCRAPER_REPO_BUNDLESIZE)]
        pub = _FakePublisher()
        service, control = self._service(repos, ScraperOutputPolicy.ON_REQUEST)
        stop = threading.Event()

        thread = threading.Thread(target=service._crawl, args=(pub, stop), daemon=True)
        thread.start()
        try:
            time.sleep(0.3)
            self.assertEqual(pub.scrape_bodies(), [], "ON_REQUEST must hold at 25 until requested")

            control.bundle_requested.set()
            deadline = time.time() + 3
            while time.time() < deadline and not pub.scrape_bodies():
                time.sleep(0.02)

            bodies = pub.scrape_bodies()
            self.assertEqual(len(bodies), 1, "one bundle should be sent after the request")
            self.assertEqual(len(json.loads(bodies[0])), SCRAPER_REPO_BUNDLESIZE)
        finally:
            stop.set()
            thread.join(timeout=2)


if __name__ == "__main__":
    logger.info("Starting tests in scraper_test.py")
    unittest.main()

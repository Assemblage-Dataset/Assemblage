'''
    Some basic regression + unit tests for the scraper worker. 
    Only two tests actually make GitHub repo requests: these begin with "test_live_..."
    TODO: if the program throws exceptions then we get a bunch of resource leaks from the scrapers. fix? or reduce?
    current workaround is to just shutdown the whole docker container

    todo
    * more coverage: ensure every path is taken
'''

import json
import unittest

import pytest
from unittest.mock import patch, MagicMock, ANY
import logging
from requests import Response

import assemblage.worker.scraper as scraper
import assemblage.config as settings
from assemblage.mq.messages import ScraperDataOutSingle
from assemblage.consts import (ScrapeSource, InputQueue, SCRAPER_PAGE_SIZE, GITHUB_REPO_URL, SCRAPER_REQUEST_TIMEOUT_S, TEST_MESSAGE_LEVEL)
import tests.unit_tests.helper_func as helper


logging.basicConfig(format="%(asctime)s [TEST] %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S", level=TEST_MESSAGE_LEVEL)
logger = logging.getLogger(__name__)


ScraperSettingsGithub = settings.ScraperSettings()
ScraperSettingsGithub.source = ScrapeSource.GITHUB


LiveScraperSettingsGithub = settings.ScraperSettings()
LiveScraperSettingsGithub.source = ScrapeSource.GITHUB
## Uncomment out the below line to have actual API requests be unauthenticated
#LiveScraperSettingsGithub.git_token = None


class TestScraper(unittest.TestCase):

    def test_github_time_to_mysql_time_good(self):
        input = "2025-11-01T13:38:46Z"
        self.assertEqual(
            scraper.github_time_to_mysql_time(input), 
            "2025-11-01 13:38:46"
        )

    def test_github_time_to_mysql_time_bad(self):
        input = "33333gggg"
        FAILURE = "2000-01-01 01:01:01"
        self.assertEqual(
            scraper.github_time_to_mysql_time(input), 
            FAILURE,
            "Function should return fallback value (2000-01-01...)"
        )

    @pytest.mark.live_github
    def test_live_get_request_repo_fields_as_expected(self):
        '''
        Tests that GitHub REST API returns all the expected fields for a repo (regression test)
        '''
        
        q = 'https://api.github.com/repos/id-Software/DOOM/git/trees/master'
        expected_keys : set = {'sha', 'url', 'tree', 'truncated'}
        expected_tree_keys : set = {'path', 'mode', 'type', 'sha', 'url'} # should be present in every entry in the tree
        
        s = scraper.Scraper(LiveScraperSettingsGithub, 0)
        output : Response
        elapsed : float
        output, elapsed = s.data_source.get_request( query=q )

        # A page was received as a result (guard assertion)
        self.assertEqual( type(output), Response, "A valid page should be received" )

        page : dict = json.loads(output.text)
        actual_keys : set = set(page.keys())
        self.assertEqual( 
            actual_keys,
            expected_keys,
            f'''Expected keys did not match actual keys. 
            Expected: {expected_keys}. Actual: {actual_keys}\n
            API may have changed'''
        )
        
        actual_tree_keys : set = set(page['tree'][0].keys())
        self.assertTrue( 
            expected_tree_keys.issubset(actual_tree_keys),
            f'''Key(s) which are assumed to be guaranteed present are not present in actual request. 
            Expected: {expected_tree_keys}. Actual: {actual_tree_keys}\n
            API may have changed'''
        )
        self.assertTrue(elapsed > 0)

    @pytest.mark.live_github
    def test_live_get_request_search_fields_as_expected(self):
        '''
        Tests that expected fields are present in the search API
        '''

        query = "created:2025-10-09T08:35:13+08:00..2025-10-09T12:35:13+08:00 language:c++"
        payload : dict = {'q': query, 'per_page': SCRAPER_PAGE_SIZE, 'page': 0}
        expected_keys : set = {'total_count', 'incomplete_results', 'items'}
        expected_entry_keys : set = {'id', 'node_id', 'name', 'full_name', 'private', 'owner', 'html_url', 
            'description', 'fork', 'url', 'forks_url', 'keys_url', 'collaborators_url', 'teams_url', 
            'hooks_url', 'issue_events_url', 'events_url', 'assignees_url', 'branches_url', 'tags_url', 
            'blobs_url', 'git_tags_url', 'git_refs_url', 'trees_url', 'statuses_url', 'languages_url', 
            'stargazers_url', 'contributors_url', 'subscribers_url', 'subscription_url', 'commits_url', 
            'git_commits_url', 'comments_url', 'issue_comment_url', 'contents_url', 'compare_url', 
            'merges_url', 'archive_url', 'downloads_url', 'issues_url', 'pulls_url', 'milestones_url', 
            'notifications_url', 'labels_url', 'releases_url', 'deployments_url', 'created_at', 'updated_at', 
            'pushed_at', 'git_url', 'ssh_url', 'clone_url', 'svn_url', 'homepage', 'size', 'stargazers_count', 
            'watchers_count', 'language', 'has_issues', 'has_projects', 'has_downloads', 'has_wiki', 'has_pages', 
            'has_discussions', 'forks_count', 'mirror_url', 'archived', 'disabled', 'open_issues_count', 
            'license', 'allow_forking', 'is_template', 'web_commit_signoff_required', 'topics', 'visibility', 
            'forks', 'open_issues', 'watchers', 'default_branch', 'permissions', 'score'}

        if LiveScraperSettingsGithub.git_token == None:  # unauthenticated requests will not have permissions field
            expected_entry_keys.remove('permissions')
        s = scraper.Scraper(LiveScraperSettingsGithub, 0)
        
        output : Response
        elapsed : float
        output, elapsed = s.data_source.get_request(
            GITHUB_REPO_URL, payload=payload
        )

        # Guard assertions
        self.assertEqual( type(output), Response, "A valid page should be received" )
        page : dict = json.loads(output.text)

        actual_keys : set = set(page.keys())
        self.assertEqual( 
            expected_keys,
            actual_keys
        )
        self.assertTrue(
            len(page['items']) > 0,
            "'items' should not be empty"
        )

        for i in page['items']:
            actual_entry_keys : set = set(i.keys())
            self.assertEqual(
                actual_entry_keys,
                expected_entry_keys,
                f'''
                Actual return unexpectedly has: {actual_entry_keys.difference(expected_entry_keys)}. 
                Actual return unexpectedly lacks: {expected_entry_keys.difference(actual_entry_keys)}. \n
                API may have changed
                '''
            )

        self.assertTrue(elapsed > 0)


    @patch('assemblage.worker.scraper.time')
    @patch('assemblage.worker.scraper.requests')
    def test_process_repo_message_basic(self, MockRequests, MockTime):
        '''
            Tests that when a search result item is sent to process_repo_message,
            process_repo_message will make the appropriate http request and return
            correct message and files
        '''
        
        # Gets the search result corresponding to DOOM from the example search file
        input = helper.scr_full_repo_response_search().text
        input = json.loads(input)
        input : dict = input['items'][0]
        # Asserts that the input is still what we expect it to be!
        self.assertEqual( 
            input['node_id'], "MDEwOlJlcG9zaXRvcnkzMzE5MDQw",
            'example_search_github no longer has DOOM as first entry, which test_process_repo_message_basic relies on'
            )
        MockRequests.get.return_value = helper.scr_full_repo_response_tree()

        s = scraper.Scraper(ScraperSettingsGithub, 0)
        message : ScraperDataOutSingle
        files : list
        message, files = s.data_source._process_repo_message(input)

        self.assertEqual( 
            files, 
            ['LICENSE.TXT', 'README.TXT', 'README.TXT', 'sndserv'],
            "Returned files were not as expected"
            )
        self.maxDiff = None
        self.assertEqual( 
            message.to_json(),
            helper.scr_doom_messagestr(),
            "Returned message not as expected"
            )
        
        expected_request = f"{input['url']}/git/trees/{input['default_branch']}"
        MockTime.sleep.assert_called_once()
        MockRequests.get.assert_called_once_with(
            expected_request, params=None, headers=ANY,
            proxies=ANY, timeout=SCRAPER_REQUEST_TIMEOUT_S
        )

    @patch('assemblage.worker.scraper.time')
    @patch('assemblage.worker.scraper.requests')
    def test_process_repo_message_string(self, MockTime, MockRequests):
        '''Tests _process_repo_message errors gracefully with input of wrong type'''
        
        s = scraper.Scraper(ScraperSettingsGithub, 0)

        output = s.data_source._process_repo_message("GOOGLE DOT COM")

        self.assertEqual( 
            output, (None, None), 
            "A tuple of None should be received for bad repos"
        )
        MockRequests.get.assert_not_called()

    @patch('assemblage.worker.scraper.time')
    @patch('assemblage.worker.scraper.requests')
    def test_process_repo_message_invalid_data(self, MockRequests, MockTime):
        '''Tests _process_repo_message errors gracefully with input of invalid data 
        (can't be resolved into valid GitHub URL and fails in get_request)'''

        s = scraper.Scraper(ScraperSettingsGithub, 0)
        data_invalid_defaultbranch = { "url":"https://api.github.com/users/id-Software", "default_branch": "foo" }
        MockRequests.get.return_value = helper.scr_skeleton_404_response()

        output = s.data_source._process_repo_message(data_invalid_defaultbranch)
        self.assertEqual( 
            output, (None, None), 
            "A tuple of None should be received for bad repos"
        )
        expected_request = f"{data_invalid_defaultbranch['url']}/git/trees/{data_invalid_defaultbranch['default_branch']}"
        MockRequests.get.assert_called_once_with(
            expected_request, params=None, headers=ANY,
            proxies=ANY, timeout=SCRAPER_REQUEST_TIMEOUT_S
        )


    @patch('assemblage.worker.scraper.requests')
    def test_get_request_repo(self, MockRequests):
        '''
        Tests that get_request succeeds on a valid repo (GitHub REST API)
        '''
        
        mock_response = helper.scr_full_repo_response_tree()
        MockRequests.get.return_value = mock_response
        s = scraper.Scraper(ScraperSettingsGithub, 0)
        q = "https://api.github.com/repos/query/which/gives/mock/response"

        output : Response
        elapsed : float
        output, elapsed = s.data_source.get_request( query=q )

        self.assertEqual(
            output,
            mock_response
        )
        MockRequests.get.assert_called_once_with(
            q, params=None, headers=ANY,
            proxies=ANY, timeout=SCRAPER_REQUEST_TIMEOUT_S
            # GITHUB_REPO_URL, params=payload, headers=s.data_source.auth_headers,
            # proxies=s.data_source.random_proxy(), timeout=SCRAPER_REQUEST_TIMEOUT_S
        )
        

    @patch('assemblage.worker.scraper.requests')
    def test_get_request_search(self, MockRequests):
        '''
        Tests that get_request succeeds on a valid search (GitHub search API)
        '''
        
        query = "search_filters_that_will_return_three_items"
        payload = {'q': query, 'per_page': SCRAPER_PAGE_SIZE, 'page': 0}
        mock_response = helper.scr_full_repo_response_search()
        MockRequests.get.return_value = mock_response

        s = scraper.Scraper(ScraperSettingsGithub, 0)
        output : Response
        elapsed : float
        output, elapsed = s.data_source.get_request(
            GITHUB_REPO_URL, payload=payload
        )

        self.assertEqual(
            output,
            mock_response
        )
        MockRequests.get.assert_called_once_with(
            GITHUB_REPO_URL, params=payload, headers=ANY,
            proxies=ANY, timeout=SCRAPER_REQUEST_TIMEOUT_S
        )


    def test_get_request_badquery(self):
        '''Test get_request on a non URL query.
        Technically "live" but no http request is made.'''
        s = scraper.Scraper(ScraperSettingsGithub, 0)

        output : Response
        elapsed : float
        output, elapsed = s.data_source.get_request(query="")
        
        self.assertEqual( output, None )

    @unittest.skip("Trying to think of a way to rewrite w/out live requests (basically covers the condition where rate limit headers not included)")
    def test_live_get_request_nongithub_on_github(self):
        '''Test get_request on a valid URL that isn't GitHub'''
        s = scraper.Scraper(LiveScraperSettingsGithub, 0)
        
        output : Response
        elapsed : float
        output, elapsed = s.data_source.get_request(query="https://www.google.com")
        
        self.assertEqual( output, None )

    @patch('assemblage.worker.scraper.time')
    @patch('assemblage.worker.scraper.requests')
    def test_get_request_but_rate_limit(self, MockRequests, MockTime):
        
        '''
        Tests that if a rate limit is hit, get_request behaves accordingly
        (i.e. waits for rate limit to pass, then retries the request)
        
        '''
        mock_right_now = 1762793542
        MockTime.time.return_value = mock_right_now  # prevents ludicrously long wait times
        fake_ok : Response = helper.scr_full_repo_response_tree()
        fake_rate_limit : Response = helper.scr_skeleton_rate_limit_response()
        mock_can_retry_at = int(fake_rate_limit.headers['X-RateLimit-Reset'])
        MockRequests.get.side_effect = [  # list of Responses
            fake_rate_limit,
            fake_ok
            ]
        s = scraper.Scraper(ScraperSettingsGithub, 0)
        q = "Doesn'tMatter"

        output : Response
        elapsed : float
        output, elapsed = s.data_source.get_request( query=q )
        self.assertEqual( output, fake_ok )

        # Check that it has slept enough 
        MockTime.sleep.assert_called()
        must_sleep_time = mock_can_retry_at - mock_right_now + 1  # note the +1 
        actual_slept_time = helper.get_slept_time_from_args(MockTime.sleep.call_args_list)
        self.assertTrue(
            actual_slept_time >= must_sleep_time
        )

        
    @patch('assemblage.worker.scraper.time')
    @patch('assemblage.worker.scraper.requests')
    def test_get_request_about_to_rate_limit(self, MockRequests, MockTime):
        
        '''
        Tests that if a rate limit is just hit, get_request behaves accordingly
        (i.e. waits for rate limit to pass, but does not retry the request, because
        the returned message is valid data)
        
        '''
        mock_right_now = 1762793542
        MockTime.time.return_value = mock_right_now  # prevents ludicrously long wait times
        fake_ok_about_to_not_be : Response = helper.scr_skeleton_about_to_hit_limit_response()
        fake_nevercalled_hopefully : Response = helper.scr_full_repo_response_tree()
        mock_can_retry_at = int(fake_ok_about_to_not_be.headers['X-RateLimit-Reset'])

        MockRequests.get.side_effect = [
            fake_ok_about_to_not_be,
            fake_nevercalled_hopefully
            ]
        s = scraper.Scraper(ScraperSettingsGithub, 0)
        q = "Doesn'tMatter"  # would be erroneous input in a live setting, but since it's mocked we dont care

        output : Response
        elapsed : float
        output, elapsed = s.data_source.get_request( query=q )
        self.assertEqual( output, fake_ok_about_to_not_be )

        MockRequests.get.assert_called_once_with(
            "Doesn'tMatter", params=None, headers=ANY,
            proxies=ANY, timeout=SCRAPER_REQUEST_TIMEOUT_S
        )
        
        MockTime.sleep.assert_called()
        # Asserts that ENOUGH time was slept (note the +1)
        must_sleep_time = mock_can_retry_at - mock_right_now + 1
        actual_slept_time = helper.get_slept_time_from_args(MockTime.sleep.call_args_list)
        self.assertTrue(
            actual_slept_time >= must_sleep_time
        )

    @patch('assemblage.worker.scraper.time')
    @patch('assemblage.worker.scraper.requests')
    def test_get_request_but_bad_credentials(self, MockRequests, MockTime):
        
        '''
        Tests that if bad credentials are given, the crawler will try again unauthenticated
        and return the result of this second search (presumably successful)
        '''
        fake_ok : Response = helper.scr_full_repo_response_tree()
        fake_invalid : Response = helper.scr_skeleton_bad_cred_response()
        MockRequests.get.side_effect = [fake_invalid, fake_ok]
        q = "Doesn'tMatter"
        
        BadTokenSettings = settings.ScraperSettings()
        BadTokenSettings.git_token = "NONE"
        s = scraper.Scraper(BadTokenSettings, 0)

        output : Response
        elapsed : float
        output, elapsed = s.data_source.get_request( query=q )

        self.assertEqual( output, fake_ok )  # assert that a good request was eventually received
        self.assertEqual( None, s.data_source.token)  # assert that the token was removed
        self.assertEqual( MockRequests.get.call_count, 2)  # assert that minimum necessary requests were sent (2)

    
    @patch('assemblage.mq.client.BlockingChannel')
    @patch('assemblage.mq.client.BlockingConnection')
    def test_bundle_repos(self, MockBlockingConnection, MockBlockingChannel):
        '''
        Tests that the scraper interacts as expected with RabbitMQ
        '''


        mock_connection, mock_channel = helper.mock_functioning_rabbitmq(MockBlockingConnection, MockBlockingChannel)

        # sanity check, ensure that mocks set up OK
        self.assertEqual(mock_channel.is_closed, False)

        # Check that we correctly create our test ScraperDataOutSingle object
        # A failure here indicates an issue with ScraperDataOutSingle
        example_repo_msg : ScraperDataOutSingle = ScraperDataOutSingle.from_json(helper.scr_doom_messagestr())
        self.assertEqual(type(example_repo_msg), ScraperDataOutSingle)
        self.assertEqual(example_repo_msg.name, "DOOM")

        # Create a scraper that has one repo to send. Number of repos doesn't affect number of messages sent,
        # they're all bundled together in one message btw
        s = scraper.Scraper(ScraperSettingsGithub, 0)
        s.repocache = [example_repo_msg]
        out = s.send_bundle()

        self.assertEqual(out, 1)

        # Check that the mocks were successfully injected, and that we can use 
        # mock_channel and mock_connection as shorthands
        self.assertEqual(s.mq_client.get_connection(f'{s}').chan, mock_channel)
        self.assertEqual(s.mq_client.get_connection(f'{s}').conn, mock_connection)
        
        # Check that a publish was indeed sent
        self.assertEqual(mock_channel.basic_publish.call_count, 1)

        # Check that the publish was called with expected args
        actual_call_args = mock_channel.basic_publish.call_args
        expected_routing_key = InputQueue.SCRAPE
        actual_routing_key = actual_call_args.kwargs['routing_key']
        expected_body = '['+example_repo_msg.to_json()+']'
        actual_body = actual_call_args.kwargs['body']

        self.assertEqual(expected_routing_key, actual_routing_key)
        self.assertEqual(expected_body, actual_body)



if __name__ == '__main__':
    logger.info("Starting tests in scraper_test.py")
    unittest.main()

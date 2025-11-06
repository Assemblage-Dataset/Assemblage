'''
    Some basic regression + unit tests for the scraper worker. 
    TODO: if the program throws exceptions then we get a bunch of resource leaks from the scrapers. fix? or reduce?
    current workaround is to just shutdown the whole docker container
    todo
    * more coverage: ensure every path is taken
    * decide whether or not to mock out requests
      * keep one or two live requests in, to check that the api format remains as expected (check that format of a good request
        contains all the assumed fields, and format of a bad request contains all the assumed fields?) maybe perform unauthenticated
    * authentication/rate limit tests (must create some mock bad api requests for this one)
'''

import json
import unittest
from unittest.mock import patch, MagicMock
import logging
import time

import assemblage.worker.scraper as scraper
import assemblage.config as settings
from assemblage.mq.messages import ScraperDataOutSingle, ScraperDataOutBundle
from assemblage.consts import (ScrapeSource, InputQueue, OutputQueue, SCRAPER_PAGE_SIZE, GITHUB_REPO_URL)

EXAMPLE_REPO_DOOM = {"id": 3319040,"node_id": "MDEwOlJlcG9zaXRvcnkzMzE5MDQw","name": "DOOM","full_name": "id-Software/DOOM","private": False,"owner": {"login": "id-Software","id": 1395534,"node_id": "MDEyOk9yZ2FuaXphdGlvbjEzOTU1MzQ=","avatar_url": "https://avatars.githubusercontent.com/u/1395534?v=4","gravatar_id": "","url": "https://api.github.com/users/id-Software","html_url": "https://github.com/id-Software","followers_url": "https://api.github.com/users/id-Software/followers","following_url": "https://api.github.com/users/id-Software/following{/other_user}","gists_url": "https://api.github.com/users/id-Software/gists{/gist_id}","starred_url": "https://api.github.com/users/id-Software/starred{/owner}{/repo}","subscriptions_url": "https://api.github.com/users/id-Software/subscriptions","organizations_url": "https://api.github.com/users/id-Software/orgs","repos_url": "https://api.github.com/users/id-Software/repos","events_url": "https://api.github.com/users/id-Software/events{/privacy}","received_events_url": "https://api.github.com/users/id-Software/received_events","type": "Organization","user_view_type": "public","site_admin": False},"html_url": "https://github.com/id-Software/DOOM","description": "DOOM Open Source Release","fork": False,"url": "https://api.github.com/repos/id-Software/DOOM","forks_url": "https://api.github.com/repos/id-Software/DOOM/forks","keys_url": "https://api.github.com/repos/id-Software/DOOM/keys{/key_id}","collaborators_url": "https://api.github.com/repos/id-Software/DOOM/collaborators{/collaborator}","teams_url": "https://api.github.com/repos/id-Software/DOOM/teams","hooks_url": "https://api.github.com/repos/id-Software/DOOM/hooks","issue_events_url": "https://api.github.com/repos/id-Software/DOOM/issues/events{/number}","events_url": "https://api.github.com/repos/id-Software/DOOM/events","assignees_url": "https://api.github.com/repos/id-Software/DOOM/assignees{/user}","branches_url": "https://api.github.com/repos/id-Software/DOOM/branches{/branch}","tags_url": "https://api.github.com/repos/id-Software/DOOM/tags","blobs_url": "https://api.github.com/repos/id-Software/DOOM/git/blobs{/sha}","git_tags_url": "https://api.github.com/repos/id-Software/DOOM/git/tags{/sha}","git_refs_url": "https://api.github.com/repos/id-Software/DOOM/git/refs{/sha}","trees_url": "https://api.github.com/repos/id-Software/DOOM/git/trees{/sha}","statuses_url": "https://api.github.com/repos/id-Software/DOOM/statuses/{sha}","languages_url": "https://api.github.com/repos/id-Software/DOOM/languages","stargazers_url": "https://api.github.com/repos/id-Software/DOOM/stargazers","contributors_url": "https://api.github.com/repos/id-Software/DOOM/contributors","subscribers_url": "https://api.github.com/repos/id-Software/DOOM/subscribers","subscription_url": "https://api.github.com/repos/id-Software/DOOM/subscription","commits_url": "https://api.github.com/repos/id-Software/DOOM/commits{/sha}","git_commits_url": "https://api.github.com/repos/id-Software/DOOM/git/commits{/sha}","comments_url": "https://api.github.com/repos/id-Software/DOOM/comments{/number}","issue_comment_url": "https://api.github.com/repos/id-Software/DOOM/issues/comments{/number}","contents_url": "https://api.github.com/repos/id-Software/DOOM/contents/{+path}","compare_url": "https://api.github.com/repos/id-Software/DOOM/compare/{base}...{head}","merges_url": "https://api.github.com/repos/id-Software/DOOM/merges","archive_url": "https://api.github.com/repos/id-Software/DOOM/{archive_format}{/ref}","downloads_url": "https://api.github.com/repos/id-Software/DOOM/downloads","issues_url": "https://api.github.com/repos/id-Software/DOOM/issues{/number}","pulls_url": "https://api.github.com/repos/id-Software/DOOM/pulls{/number}","milestones_url": "https://api.github.com/repos/id-Software/DOOM/milestones{/number}","notifications_url": "https://api.github.com/repos/id-Software/DOOM/notifications{?since,all,participating}","labels_url": "https://api.github.com/repos/id-Software/DOOM/labels{/name}","releases_url": "https://api.github.com/repos/id-Software/DOOM/releases{/id}","deployments_url": "https://api.github.com/repos/id-Software/DOOM/deployments","created_at": "2012-01-31T21:28:06Z","updated_at": "2025-11-01T14:48:37Z","pushed_at": "2024-05-24T13:18:59Z","git_url": "git://github.com/id-Software/DOOM.git","ssh_url": "git@github.com:id-Software/DOOM.git","clone_url": "https://github.com/id-Software/DOOM.git","svn_url": "https://github.com/id-Software/DOOM","homepage": "","size": 149,"stargazers_count": 17270,"watchers_count": 17270,"language": "C++","has_issues": False,"has_projects": False,"has_downloads": True,"has_wiki": False,"has_pages": False,"has_discussions": False,"forks_count": 2944,"mirror_url": None,"archived": False,"disabled": False,"open_issues_count": 11,"license": {"key": "gpl-2.0","name": "GNU General Public License v2.0","spdx_id": "GPL-2.0","url": "https://api.github.com/licenses/gpl-2.0","node_id": "MDc6TGljZW5zZTg="},"allow_forking": True,"is_template": False,"web_commit_signoff_required": False,"topics": [],"visibility": "public","forks": 2944,"open_issues": 11,"watchers": 17270,"default_branch": "master","score": 1.0}

EXAMPLE_REPO_DOOM_FILES = ['LICENSE.TXT', 'README.TXT', 'ipx', 'linuxdoom-1.10', 'sersrc', 'sndserv']

EXAMPLE_REPO_DOOM_SCRAPEMSG = '{"name": "DOOM", "url": "https://api.github.com/repos/id-Software/DOOM", "language": "C++", "owner_id": 1395534, "description": "DOOM Open Source Release", "created_at": "2012-01-31 21:28:06", "updated_at": "2024-05-24 13:18:59", "size": 149, "build_system": "others", "branch": "master"}'

ScraperSettingsGithub = settings.ScraperSettings()
ScraperSettingsGithub.source = ScrapeSource.GITHUB

# TODO: set up testsettings for logging?
logging.basicConfig(format="%(asctime)s [TEST] %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S", level='DEBUG')

logger = logging.getLogger(__name__)

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
        
    # the below patch mocks out time.sleep, so tests don't sleep regardless of config
    @patch('assemblage.worker.scraper.time')
    def test_process_repo_message_basic(self, MockedTime):
        '''Tests _process_repo_message with valid input'''
        # Note: this test method does access the github page "https://api.github.com/repos/id-Software/DOOM"
        # and will fail if that repository changes.
        # TODO: change the repo msg to one we know won't change, OR mock out requests
        s = scraper.Scraper(ScraperSettingsGithub, 0)
        output = s.data_source._process_repo_message(EXAMPLE_REPO_DOOM)

        # simple checks that the return types are as expected
        self.assertEqual( type(output), tuple )
        message, files = output
        self.assertEqual( type(message), ScraperDataOutSingle )
        self.assertEqual( type(files), list )

        # assert that sleep does happen
        self.assertTrue(MockedTime.sleep.called, "Sleep function unexpectedly not called")
        
        # checks that files are as expected
        self.assertEqual( 
            files, 
            EXAMPLE_REPO_DOOM_FILES,
            "Returned files were not as expected"
            )

        self.assertEqual( 
            message.to_json(),
            EXAMPLE_REPO_DOOM_SCRAPEMSG,
            "Returned metadata were not as expected"
            )

    @patch('assemblage.worker.scraper.time')
    def test_process_repo_message_string(self, MockedTime):
        '''Tests _process_repo_message errors gracefully with input of wrong type'''
        
        s = scraper.Scraper(ScraperSettingsGithub, 0)

        output = s.data_source._process_repo_message("GOOGLE DOT COM")

        self.assertEqual( 
            output, (None, None), 
            "A tuple of None should be received for bad repos"
        )

    @patch('assemblage.worker.scraper.time')
    def test_process_repo_message_invalid_data(self, MockedTime):
        '''Tests _process_repo_message errors gracefully with input of invalid data 
        (can't be resolved into valid GitHub URL)'''

        s = scraper.Scraper(ScraperSettingsGithub, 0)
        data_invalid_defaultbranch = { "url":EXAMPLE_REPO_DOOM["url"], "default_branch": "foo" }

        output = s.data_source._process_repo_message(data_invalid_defaultbranch)
        self.assertEqual( 
            output, (None, None), 
            "A tuple of None should be received for bad repos"
        )

    def test_get_request_repo(self):
        '''
        Tests that get_request succeeds on a good repo (GitHub REST API)
        '''
        # Test a basic request on a default-config scraper (GitHub)
        s = scraper.Scraper(ScraperSettingsGithub, 0)
        q = f"{EXAMPLE_REPO_DOOM["url"]}/git/trees/{EXAMPLE_REPO_DOOM["default_branch"]}"

        output, elapsed = s.data_source.get_request( query=q )
        # A page was received as a result (guard assertion)
        self.assertNotEqual( output, None, "A valid page should be received" )
        page = json.loads(output.text)


        # Assert that the page is expected type (dict) and likely has expected contents (at least a "tree" field)
        self.assertEqual(
            type(page),
            dict,
            "The output of get_request should resolve to a dict for a repository"
        )
        self.assertTrue( 
            "tree" in page.keys(),
            "The requested page dict should contain key 'tree' for a repository"
        )
        self.assertTrue(elapsed > 0)

    def test_get_request_search(self):
        '''
        Tests that get_request succeeds on a valid search (GitHub search API)
        '''
        
        query = "created:2025-10-09T08:35:13+08:00..2025-10-09T12:35:13+08:00 language:c++"
        payload = {'q': query, 'per_page': SCRAPER_PAGE_SIZE, 'page': 0}

        s = scraper.Scraper(ScraperSettingsGithub, 0)
        output, elapsed = s.data_source.get_request(
            GITHUB_REPO_URL, payload=payload
        )

        # Guard assertions
        self.assertNotEqual( output, None, "A valid page should be received" )
        page = json.loads(output.text)

        # Assert that the page is expected type (dict) and likely has expected contents (at least an "items" field)
        self.assertEqual(
            type(page),
            dict,
            "The output of get_request should resolve to a dict for a repository"
        )
        self.assertTrue( 
            "items" in page.keys(),
            "The requested page dict should contain key 'tree' for a repository"
        )
        self.assertTrue(
            len(page['items']) > 0,
            "'items' should not be empty"
        )
        self.assertTrue(elapsed > 0)


    def test_get_request_badquery(self):
        '''Test get_request on a non URL query'''
        s = scraper.Scraper(ScraperSettingsGithub, 0)

        output, elapsed = s.data_source.get_request(query="")
        
        self.assertEqual( output, None )

    def test_get_request_nongithub_on_github(self):
        '''Test get_request on a valid URL that isn't GitHub'''
        s = scraper.Scraper(ScraperSettingsGithub, 0)
        
        output, elapsed = s.data_source.get_request(query="https://www.google.com")
        
        self.assertEqual( output, None )

    
    @patch('assemblage.mq.client.BlockingChannel')
    @patch('assemblage.mq.client.BlockingConnection')
    def test_bundle_repos(self, MockBlockingConnection, MockBlockingChannel):
        '''
        Tests that the scraper interacts as expected with RabbitMQ
        '''

        # Properly mock up our BlockingChannel and BlockingConnection, such that
        # our mock instances will properly replace RabbitMQ objects
        mock_connection = MagicMock()
        mock_channel = MagicMock()
        mock_connection.is_open = True
        mock_connection.channel = MagicMock(return_value = mock_channel)
        mock_channel.is_closed = False

        # Must ensure that our patched classes actually instantiate our custom instances above
        MockBlockingConnection.return_value = mock_connection
        MockBlockingChannel.return_value = mock_channel

        # sanity check, ensure that mocks set up OK
        self.assertEqual(mock_channel.is_closed, False)

        # Check that we correctly create our test ScraperDataOutSingle object
        # A failure here indicates an issue with ScraperDataOutSingle
        example_repo_msg : ScraperDataOutSingle = ScraperDataOutSingle.from_json(EXAMPLE_REPO_DOOM_SCRAPEMSG)
        self.assertEqual(type(example_repo_msg), ScraperDataOutSingle)
        self.assertEqual(example_repo_msg.name, EXAMPLE_REPO_DOOM['name'])

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

'''
The scraper will get the data from github with github API

scraper is in single thread mode

Jeffrey Ching
Jay Morrison
Yihao Sun

Rewrote at Jan 2022

Chang Liu
Yihao Sun

Multi thread
Multi token
No yield

2025
Mia Kerchen
'''

from abc import abstractclassmethod
from enum import Enum
import logging
import os
import time
import json
from datetime import datetime
import random

import requests

from assemblage.worker.base_worker import BasicWorker
from assemblage.mq.client import MessageClient
from assemblage.analyze.tokenchecker import TokenChecker
from assemblage.analyze.analyze import get_build_system
from assemblage.consts import (
    SCRAPER_TIMESTAMP_RECORDFILE_PATH, OLDEST_PERMITTED_DATA_TIMESTAMP, SCRAPER_PAGE_SIZE, 
    GITHUB_REPO_URL, SCRAPER_REQUEST_TIMEOUT_S, SCRAPER_REPO_BUNDLESIZE,
    SCRAPER_RATE_INTERVAL, QUERY_RATE_LIMIT_TIME, SCRAPER_RATE_LIMIT, 
    RATE_LIMIT_WAIT, SECONDARY_RATE_LIMIT_WAIT
)

logger = logging.getLogger(__name__)

# SEARCH_RATE_LIMIT = 30 # unused

'''
possible TODO:
* replace self.record_file with SCRAPER_TIMESTAMP_RECORDFILE_PATH everywhere? Or replace with db
* set start crawltime to database info directly, instead of passing as a parameter
* set crawl interval from a global const, instead of passing as a parameter
* balance scraper with other API request components to eliminate the SCRAPER_RATE_INTERVAL const
* have rate limiting checks work directly from query data rather than estimating how many queries have been done
* add support for multiple tokens
'''


def github_time_to_mysql_time(gtime: str):
    ''' change the format of time we harvest from github to mysql date string '''
    try:
        dt = datetime.strptime(gtime, '%Y-%m-%dT%H:%M:%SZ')
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except:
        return datetime.strptime(
            "2000-01-01T01:01:01Z",
            '%Y-%m-%dT%H:%M:%SZ').strftime('%Y-%m-%d %H:%M:%S')


class DataSource(object):

    def __init__(self, build_sys_callback) -> None:
        self.build_sys_callback = build_sys_callback
        self.record_file = SCRAPER_TIMESTAMP_RECORDFILE_PATH
        
        # TODO: replace with checking that DB has the appropriate data
        if not os.path.exists(self.record_file):  
            index = SCRAPER_TIMESTAMP_RECORDFILE_PATH.rfind("/")
            os.makedirs(SCRAPER_TIMESTAMP_RECORDFILE_PATH[:index], exist_ok=True) # TODO: this is a VERY rough fix to ensure that binaries folder exists
            with open(self.record_file, "w") as record_file:
                now = int(time.time())
                json.dump({"latest_crawled":now}, record_file, indent=4)
                logger.info("No saved scrape time data found at %s. Starting from (seconds since epoch) %s...", self.record_file, now)

    # TODO: I want to remove this but ONLY once we know that won't break anything else for sure. 
    def init():
        '''Deprecated'''
        logger.warning("DataSource.init() should not be called: init() functionality has been rolled into __init__, delete the line of code that uses this")


    @abstractclassmethod
    def fetch_data(self):
        """ fetch one repository from data source, return a (repository, files_in_repo) generator  """

    @abstractclassmethod
    def data_filter(self, repo,  files):
        """ take a repo and files in repo, check if its valid or need to be discarded"""
        return True

    # TODO: should this be moved into GitHubRepositories, or is this code shared across all data sources?
    def update_time_record(self, interval):
        """ Updates SCRAPER_TIMESTAMP_RECORDFILE_PATH with how far back the scraper has looked on its data source (i.e. GitHub)"""
        # TODO: replace with database query
        while os.path.exists(self.record_file+'.lock'):
            time.sleep(0.25)
            logger.debug("Scraper waiting for lock to be released (if there is only one scraper, this will never happen)")
        f = open(self.record_file+'.lock', 'w')
        f.close()

        # try to open crawled.json: if this fails, create a new one and set its time to now
        try:
            with open(self.record_file, "r") as record_file:
                crawled = json.load(record_file)
                oldtime = int(crawled["latest_crawled"])
        except:
            logger.info("Scraper record file not found or incorrect format, resetting it to defaults...")
            oldtime = int(time.time())
        
        # Update the timestamp to search earlier (default is querylap, which is 4 hours)
        newtime = oldtime - interval 
        with open(self.record_file, "w") as record_file:
            json.dump({"latest_crawled":newtime}, record_file, indent=4)
        try:
            os.remove(self.record_file+'.lock')
        except:
            pass
        return oldtime

    def __iter__(self): # iterate over self data
        for r, fs in self.fetch_data():
            if self.data_filter(r, fs):
                yield r


class GithubTimeOrder(Enum):
    CREATED = "created"
    PUSHED = "pushed"


class GithubRepositories(DataSource):
    """ a data generator for Windows c repositories """

    # TODO: crawl_time_start can be removed, replaced with db call? probably crawl_time_interval replaced with const too
    def __init__(self, git_token, qualifier, crawl_time_start, crawl_time_interval,
                 proxies, sort=GithubTimeOrder.CREATED, order="",
                 build_sys_callback=get_build_system) -> None:
        super().__init__("", build_sys_callback)
        self.token = git_token
        # self.lang = lang
        self.qualifier = qualifier # an iterable containing the qualifiers to be used in the query
        self.crawl_time_interval = crawl_time_interval
        self.crawl_time_start = crawl_time_start
        self.proxies = proxies
        self.query_pile = int(time.time())//QUERY_RATE_LIMIT_TIME # part of the rate limiting check code
        self.sort = sort
        self.order = order
        self.queries = 0 # queries performed since the last rate limit rollover
        self.parent_workerid = -1 #os.urandom(4).hex()
        if "" not in self.proxies:
            self.proxies.append("")

        if not self.token:
            logger.warning('''No Token is set, scraper will be severely rate-limited\n.
                                  Please configure PAT and add it to secrets.env as GITHUB_TOKEN and then restart
                           ''')
            self.auth_headers = {}
        else:
            self.auth_headers = {
                "Authorization": f"Bearer {self.token}",
            }
        self.token_checker = TokenChecker(self.auth_headers)


    def random_proxy(self):
        '''Returns a random proxy from the data source's defined proxies.'''
        proxy = random.choice(self.proxies)
        if self.proxies == []:
            return None
        return {
            'https': proxy,
        }

    def query_limit(self):
        '''Checks that the scraper has not performed too many queries. If so, scraper sleeps until rate limit rollover.'''
        if int(time.time())//QUERY_RATE_LIMIT_TIME != self.query_pile:
            # the time interval has rolled over without exceeding queries -- can reset rate limit
            self.query_pile = int(time.time())//QUERY_RATE_LIMIT_TIME
            self.queries = 0
        if self.queries > SCRAPER_RATE_LIMIT:
            # queries exceeded -- put the worker to sleep until query is refreshed
            # NOTE: the query is assumed to refresh every QUERY_RATE_LIMIT_TIME. Not determined directly from the message provided by GitHub
            sleeptime = QUERY_RATE_LIMIT_TIME-int(time.time()) % QUERY_RATE_LIMIT_TIME
            logger.info("Worker %s idle soon due to reaching rate limit: sleeping for %s", self.parent_workerid, sleeptime)
            time.sleep(sleeptime)
            self.queries = 0

    def _process_repo_message(self, repo):
        '''Given a single entry in the GitHub search results, requests the repository page, extracts the files, and returns select metadata with the files.'''
        time.sleep(SCRAPER_RATE_INTERVAL) # prevents scraper from monopolizing resources
        url = repo["url"]
        self.query_limit()
        # Avoid secondary rate limit
        default_branch = repo["default_branch"]
        # Accesses the repository itself in order to extract files
        try:
            page = requests.get(url + f"/git/trees/{default_branch}",
                                headers=self.auth_headers, proxies=self.random_proxy(), timeout=SCRAPER_REQUEST_TIMEOUT_S)
            if "secondary rate limit" in page.text:
                logger.warning("Secondary rate limit detected")
                logger.info(page.text.replace("\n", ""))
                time.sleep(SECONDARY_RATE_LIMIT_WAIT) 
                # TODO: this assumes that SECONDARY_RATE_LIMIT_WAIT will allow secondary rate limit to pass, which isn't necessarily the case if multiple components are making API requests
                page = requests.get(url + f"/git/trees/{default_branch}",
                                    headers=self.auth_headers, proxies=self.random_proxy(), timeout=SCRAPER_REQUEST_TIMEOUT_S)
            elif "rate limit" in page.text:
                sleep_remaining = self.token_checker.rate_reset("", self.token)

                logger.info("Rate limit hit by crawler %s. Sleeping for %ss", self.parent_workerid,
                            sleep_remaining)
                # to give updates on remaining sleep ...
                while sleep_remaining > 0:
                    sleep_chunk = min(RATE_LIMIT_WAIT, sleep_remaining)
                    logger.info("Crawler %s sleeping due to hitting rate limit... %ds remaining",
                                self.datsourceid, sleep_remaining)
                    time.sleep(sleep_chunk)
                    sleep_remaining -= sleep_chunk

                page = requests.get(url + f"/git/trees/{default_branch}",
                                    headers=self.auth_headers, proxies=self.random_proxy(), timeout=SCRAPER_REQUEST_TIMEOUT_S)
        except Exception as err:
            logger.info(err)
            return None, None
        repo_page = json.loads(page.text) # Contains the actual structure of the code within this repository
        files_list = [] # used for breaking the repo page into files
        files = [] # will be returned

        if "tree" in repo_page.keys(): # Ensure that a tree was found
            files_list = repo_page["tree"]
        else:
            return None, None
        
        # Break the repository down into files
        for record in files_list:
            if "path" in record.keys():
                files.append(record["path"])
        build_tool = self.build_sys_callback(files)
        name = repo["name"]
        #url = repo["url"]
        language = repo["language"]
        owner_id = repo["owner"]["id"]
        description = repo["description"] or ""
        created_at = github_time_to_mysql_time(repo["created_at"])
        updated_at = github_time_to_mysql_time(repo["pushed_at"])
        size = int(repo['size'])
        return {
            'name': name,
            'url': url,
            'language': language,
            'owner_id': owner_id,
            'description': description[:200],
            'created_at': created_at,
            'updated_at': updated_at,
            'size': size,
            'build_system': build_tool,
            'branch': repo["default_branch"]
        }, files

    def fetch_data(self):
        '''Requests search result pages from GitHub's Search API, then extracts the repository information from each result on each search page.'''
        if self.crawl_time_start < OLDEST_PERMITTED_DATA_TIMESTAMP: # if the crawltime is older than permitted
            logger.error("Warning: start crawl time %s is earlier than the oldest permitted timestamp %s.")
            crawl_time = self.crawl_time_start
        else:
            crawl_time = self.crawl_time_start # exact value is unnecessary: we just need it to let us run the while loop once
        # TODO: get the crawl_time from the database instead of taking it from self.
        # Also need to consider whether we want to have the options to stagger crawlers by giving each a different crawl time?
        # And how much we want to synchronize each crawler's crawl_time with the database
        while crawl_time > OLDEST_PERMITTED_DATA_TIMESTAMP: # continue until oldest files have been read
            crawl_time = self.update_time_record(self.crawl_time_interval) # Update the cache so it's now QUERYLAP ms earlier 

            # Build the query to GitHub's servers, according to the last visited data as stored in SCRAPER_TIMESTAMP_RECORDFILE_PATH
            query_time_start = datetime.utcfromtimestamp(crawl_time).isoformat()
            query_time_end = datetime.utcfromtimestamp(crawl_time + self.crawl_time_interval).isoformat()
            qualifier_str = " ".join(self.qualifier)
            query_s = f'{self.sort.value}:{query_time_start}+08:00..{query_time_end}+08:00 {qualifier_str}'

            logger.debug("Crawler query is ' %s ' (GitHub)", query_s)
            total_query_results_count = 999 # needs to be big enough to run the while loop once
            payload = {'q': query_s,
                       'per_page': SCRAPER_PAGE_SIZE, 'page': -1}
            # The payload contains the query plus some metadata. Metadata is needed because a separate request will be made 
            # for each page of GitHub search results (page1, page2, etc.), and we keep this payload persistent so we can
            # get a new page but maintain the rest of the query. 
            while payload['page'] * SCRAPER_PAGE_SIZE < total_query_results_count:
                try:
                    payload['page'] += 1
                    
                    request_start_ts = float(time.time())
                    r = requests.get(GITHUB_REPO_URL,
                                     payload,
                                     headers=self.auth_headers, proxies=self.random_proxy(), timeout=SCRAPER_REQUEST_TIMEOUT_S)
                    request_response_time = round(float(time.time()) - request_start_ts, 3)
                    logger.info("Crawler request respond in %ss", request_response_time)
                    rdict = json.loads(r.text)

                    # Catch possible request errors
                    try:
                        if "X-RateLimit-Limit" in r.headers and int(r.headers["X-RateLimit-Limit"]) < SCRAPER_RATE_LIMIT:
                            logger.warning("Rate limit is unexpectedly low (%s). This may indicate that your credentials are not as expected.", str(r.headers["X-RateLimit-Limit"]))
                    except:
                            logger.warning("Could not determine rate limit.")

                    if "message" in rdict.keys() and "Bad credentials" in rdict["message"]:
                        logger.warning("Bad credentials: the authentication token provided is not valid")
                        time.sleep(RATE_LIMIT_WAIT)

                    while "message" in rdict.keys() and "rate limit" in rdict["message"]:
                        # Rate limit detected. Sleep, then retry query until success. 

                        if "secondary" in rdict["message"]:
                            logger.info("Secondary rate limit hit by crawler %s. Sleeping for %ss", self.parent_workerid, SECONDARY_RATE_LIMIT_WAIT)
                            time.sleep(SECONDARY_RATE_LIMIT_WAIT)
                        else:
                            logger.info("Rate limit hit by crawler %s. Sleeping for %ss", self.parent_workerid, RATE_LIMIT_WAIT)
                            time.sleep(RATE_LIMIT_WAIT)

                        # Retry query
                        request_start_ts = float(time.time())
                        r = requests.get(GITHUB_REPO_URL,
                                        payload,
                                        headers=self.auth_headers, proxies=self.random_proxy(), timeout=SCRAPER_REQUEST_TIMEOUT_S)
                        request_response_time = round(float(time.time()) - request_start_ts, 3)
                        logger.info("Crawler request respond in %ss, delayed due to rate limit", request_response_time)

                        rdict = json.loads(r.text)

                    # Break down the search query
                    if 'items' in rdict.keys():
                        total_query_results_count = min(rdict["total_count"], total_query_results_count) # update total query results count in case it has changed
                        logger.info("Successful search result obtained by crawler %s. GitHub responded with %s repos", 
                                    self.parent_workerid, total_query_results_count)
                        # logger.info("Crawler query: %s ... ; page: %s; GitHub responded with %s repos",
                        #             query_time_start[:-7], payload['page'], total_query_results_count) # not sure about the query_time_start[:-7] line
                        repos_on_page = rdict["items"]
                        for repo in repos_on_page:
                            dt, fs = self._process_repo_message(repo)
                            # dt is metadata, fs is all files in repo
                            if dt and fs:
                                logger.info("Crawler %s got %s", self.parent_workerid, repo["name"])
                                # logger.info("Obtained metadata: %s", str(dt))
                                # logger.info("Obtained files %s", str(fs))
                                yield dt, fs
                except Exception as err:
                    logger.info(err)
            
            # Once all pages are exhausted, move the crawl time earlier
            crawl_time -= self.crawl_time_interval
            crawl_time = int(crawl_time)
        logger.info("scraping finished!")


class Scraper(BasicWorker):
    '''
    scraper class, wrap all github operation
    '''

    def __init__(self, rabbitmq_port, rabbitmq_host, workerid, data_source: DataSource):
        # TODO: refactor here make scraper connect to gRPC control port
        logger.info("Booting crawler %s", workerid)
        super().__init__(rabbitmq_host, rabbitmq_port, "scraper",
                         -1)
        self.data_source = data_source
        self.data_source.parent_workerid = workerid 
        #self.data_source.init()

        # Set up messaging
        self.rabbitmq_port = rabbitmq_port
        self.rabbitmq_host = rabbitmq_host
        self.mq_client = MessageClient(rabbitmq_host, rabbitmq_port, 'scraper')
        self.mq_client.add_output_queues([{
            'name': 'scrape',
            'params': {
                'durable': True
            }
        }])
        self.repocache = []
        self.workerid = workerid
        self.total_repos_sent = 0

    def run(self):
        '''Acquires repository information and sends it to coordinator on "scrape" queue until task completed'''
        logger.info("Scraper %s start", self.workerid)
        self.repocache = [] 
        for repo in iter(self.data_source):
            self.repocache.append(repo)
            
            # once enough repositories have been collected, send a message to the coordinator
            if len(self.repocache) >= SCRAPER_REPO_BUNDLESIZE:
                try:
                    self.mq_client.send_kind_msg(
                        'scrape', json.dumps(self.repocache))
                    self.repocache = []
                    self.total_repos_sent += SCRAPER_REPO_BUNDLESIZE
                    logger.info("Scraper %s bundled and sent %s repos to coordinator. Total repos sent by this scraper: %s",
                                self.workerid, SCRAPER_REPO_BUNDLESIZE, self.total_repos_sent)
                except Exception as err:
                    logger.info("Sending repos errored: %s", str(err))
                    # Reopens the message queue
                    self.mq_client = MessageClient(
                        self.rabbitmq_host, self.rabbitmq_port, 'scraper')
                    self.mq_client.add_output_queues([{
                        'name': 'scrape',
                        'params': {
                            'durable': True
                        }
                    }])

        logger.info("Crawler %s End Task", self.workerid)
        os.remove(self.record_file) # deletes the last crawled time at conclusion of task

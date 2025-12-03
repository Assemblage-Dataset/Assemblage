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
Alex Duly
'''

from abc import abstractmethod
import logging
import os
import time
import json
from datetime import datetime
import random

import requests

from assemblage.config import ScraperSettings
from assemblage.worker.base_worker import BasicWorker
from assemblage.mq.client import MQQueue, MessageClient, Connection
# from assemblage.analyze.tokenchecker import TokenChecker
from assemblage.analyze.analyze import get_build_system
from assemblage.consts import (
    SCRAPER_PAGE_SIZE,  DEBUG_SHOW_ALL_MESSAGES_SCRAPER,
    GITHUB_REPO_URL, SCRAPER_REQUEST_TIMEOUT_S, SCRAPER_REPO_BUNDLESIZE,
    SCRAPER_RATE_INTERVAL, RATE_LIMIT_WAIT, SECONDARY_RATE_LIMIT_WAIT, RATE_LIMIT_UPDATE_INTERVAL, InputQueue, OutputQueue,
    ScrapeSource, GithubTimeOrder, WorkerType, ScraperMsgType, ScraperOutputPolicy
)
from assemblage.mq.messages import ScraperDataOutSingle, ScraperDataOutBundle, ScraperControlTaskIn, ScraperControlTaskOut

logger = logging.getLogger(__name__)


'''
possible TODO:
* update crawl time from DB
* balance scraper with other API request components to eliminate the SCRAPER_RATE_INTERVAL const
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

    @classmethod
    @abstractmethod
    def fetch_data(self):
        """ fetch one repository from data source, return a (repository, files_in_repo) generator  """

    @classmethod
    @abstractmethod
    def data_filter(self, repo,  files):
        """ take a repo and files in repo, check if its valid or need to be discarded"""
        return True

    @classmethod
    @abstractmethod
    def get_request(self, query, payload=None, headers=None, proxy=None):
        '''Gets the requested query, handling rate limits as necessary.'''


    def __iter__(self):  # iterate over self data
        for r, fs in self.fetch_data():
            if self.data_filter(r, fs):
                if r is not None:
                    yield r


class GithubRepositories(DataSource):
    """A data generator which uses the GitHub REST API to scrape repository data."""

    # TODO: crawl_time_interval can be replaced with const
    def __init__(self, parent_id: int, git_token: str, qualifiers: set, crawl_time_start: int, crawl_time_end: int, crawl_time_interval: int,
                 proxies: list, sort=GithubTimeOrder.CREATED, build_sys_callback=get_build_system) -> None:
        super().__init__(build_sys_callback)

        # allows the logger to note that this data generator belongs to the parent crawler
        self.parent_workerid = parent_id

        # github authentication configuration
        self.set_token(git_token)
        self.proxies = proxies
        if "" not in self.proxies:
            self.proxies.append("")

        # Determine the time span to be scraped -- none of these variables change after assignment
        self.crawl_time_interval = crawl_time_interval
        self.crawl_time_start = crawl_time_start
        self.crawl_time_end = crawl_time_end

        # Determine format of query
        # a set containing the qualifiers to be used in the query
        self.qualifiers = qualifiers
        self.sort = sort  # sort-by method

    def random_proxy(self):
        '''Returns a random proxy from the data source's defined proxies.'''
        if self.proxies == []:
            return None
        else:
            return {
                'https': random.choice(self.proxies),
            }

    def _process_repo_message(self, repo):
        '''Given a single entry in the GitHub search results, requests the repository page, extracts the files, and returns select metadata with the files.'''
        if type(repo) is not dict:
            logger.error(f"_process_repo_message expects dictionary as input, not {str(type(repo))}")
            return None, None
        url = repo["url"]
        default_branch = repo["default_branch"]
        # Accesses the repository itself in order to extract files
        req = f"{url}/git/trees/{default_branch}"
        try:

            page, _ = self.get_request(req)
            
            if page is None:
                logger.info(f"Could not process repo {url}: error getting page {req}")
                return None, None
            
        except Exception as err:
            logger.info(err)
            return None, None
        
        time.sleep( SCRAPER_RATE_INTERVAL )  # prevents scraper from monopolizing resources
        
        # Contains the actual structure of the code within this repository
        repo_page = json.loads(page.text)
        files_list = []  # used for breaking the repo page into files
        files = []  # will be returned

        if "tree" in repo_page.keys():  # Ensure that a tree was found
            files_list = repo_page["tree"]
        else:
            return None, None

        # Break the repository down into files
        for record in files_list:
            if "path" in record.keys():
                files.append(record["path"])
        build_tool = self.build_sys_callback(files)
        name = repo["name"]
        # url = repo["url"]
        language = repo["language"]
        owner_id = repo["owner"]["id"]
        description = repo["description"] or ""
        created_at = github_time_to_mysql_time(repo["created_at"])
        updated_at = github_time_to_mysql_time(repo["pushed_at"])
        size = int(repo['size'])
        return ScraperDataOutSingle(
            name=name,
            url=url,
            language=language,
            owner_id=owner_id,
            description=description,
            created_at=created_at,
            updated_at=updated_at,
            size=size,
            build_system=build_tool,
            branch=repo["default_branch"]
            ), files

    def fetch_data(self):
        '''Requests search result pages from GitHub's Search API, then extracts the repository information from each result on each search page.'''
        if self.crawl_time_start < self.crawl_time_end:  # if the crawltime is older than permitted
            logger.error(
                "Warning: start crawl time %s is earlier than the oldest permitted timestamp %s.")
        crawl_time = self.crawl_time_start

        while crawl_time > self.crawl_time_end:  # continue until oldest files have been read
            # crawl_time = self.update_time_record(self.crawl_time_interval) # Update the cache so it's now QUERYLAP ms earlier
            # TODO: still working on properly setting up the crawl time. for now it restarts from the env variable every time

            # Build the query to GitHub's servers, according to the last visited data as stored in SCRAPER_TIMESTAMP_RECORDFILE_PATH
            query_time_start = datetime.utcfromtimestamp(
                crawl_time).isoformat()
            query_time_end = datetime.utcfromtimestamp(
                crawl_time + self.crawl_time_interval).isoformat()
            qualifier_str = " ".join(self.qualifiers)
            query_s = f'{self.sort.value}:{query_time_start}+08:00..{query_time_end}+08:00 {qualifier_str}'

            logger.debug("Crawler query is ' %s ' (GitHub)", query_s)
            total_query_results_count = 999  # needs to be big enough to run the while loop once
            payload = {'q': query_s,
                       'per_page': SCRAPER_PAGE_SIZE, 'page': -1}
            # The payload contains the query plus some metadata. Metadata is needed because a separate request will be made
            # for each page of GitHub search results (page1, page2, etc.), and we keep this payload persistent so we can
            # get a new page but maintain the rest of the query.
            while payload['page'] * SCRAPER_PAGE_SIZE < total_query_results_count:
                try:
                    payload['page'] += 1

                    r, request_response_time = self.get_request(
                        GITHUB_REPO_URL, payload=payload)
                    logger.info("Crawler request respond in %ss",
                                request_response_time)

                    rdict = json.loads(r.text)
                    # Break down the search query
                    if 'items' in rdict.keys():
                        # update total query results count in case it has changed
                        total_query_results_count = min(
                            rdict["total_count"], total_query_results_count)
                        if DEBUG_SHOW_ALL_MESSAGES_SCRAPER:
                            logger.info("Successful search result obtained by crawler %s. GitHub responded with %s repos",
                                    self.parent_workerid, total_query_results_count)
                        # logger.info("Crawler query: %s ... ; page: %s; GitHub responded with %s repos",
                        #             query_time_start[:-7], payload['page'], total_query_results_count) # not sure about the query_time_start[:-7] line
                        repos_on_page = rdict["items"]
                        for repo in repos_on_page:
                            dt, fs = self._process_repo_message(repo)
                            # dt is metadata, fs is all files in repo
                            if dt and fs:
                                if DEBUG_SHOW_ALL_MESSAGES_SCRAPER:
                                    logger.info("Crawler %s got %s",
                                                self.parent_workerid, repo["name"])
                                # logger.info("Obtained metadata: %s", str(dt))
                                # logger.info("Obtained files %s", str(fs))
                                yield dt, fs
                except Exception as err:
                    logger.info(err)

            # Once all pages are exhausted, move the crawl time earlier
            crawl_time -= self.crawl_time_interval
            crawl_time = int(crawl_time)
        logger.info("scraping finished!")

    def get_request(self, query: str, payload: set = None, headers="default", proxy: str = "random"):
        '''Gets the requested query, handling rate limits as necessary.
        query:str, payload:set, headers:set, proxy:str
        returns request, time_elapsed.
        Default behavior is to send the query with an empty payload, self.auth_headers as headers, and a random proxy.'''

        # There are two separate rate limits, for search api (from GITHUB_REPO_URL) and standard api (in process_repo_message).
        # Unauthenticated: 10/minute for search api, 60/hour for standard api
        # Authenticated: 60/minute for search api, 5000/hour for standard api (~8 req/min)

        use_headers: set = self.auth_headers if (headers == "default") else headers
        use_proxy: str = self.random_proxy() if (proxy == "random") else proxy

        start_request_time = float(time.time())
        try:
            r = requests.get(query, params=payload, headers=use_headers,
                            proxies=use_proxy, timeout=SCRAPER_REQUEST_TIMEOUT_S)
        except Exception as err:
            logger.error(f"An unexpected issue occurred when getting query {query}:")
            logger.info(err)
            return None, None

        receipt_time = float(time.time())

        if r.status_code == 404:
            logger.error(f"404 Not Found. Query: {query}:")
            return None, None


        # The rest of the function checks for rate limits and other potential issues.

        try:
            total_rate_limit = int(r.headers["X-RateLimit-Limit"])
            remaining_rate_limit = int(r.headers["X-RateLimit-Remaining"])
            rate_limit_reset_time = float(r.headers["X-RateLimit-Reset"])
        except:
            logger.warning(
                "Error when converting rate limit headers into values.")
            return None, None
            # something has gone quite wrong, so don't bother giving fallback values

        # logger.info("Rate limit is %s, %s remaining: resets in %ss", total_rate_limit, remaining_rate_limit, round(rate_limit_reset_time-float(time.time()),2 ))


        # Check for important messages and warn the user. These need to be handled manually.
        try:
            rdict = json.loads(r.text)
        except Exception as err:
            logger.error(f"Error when parsing query result (format of result may not be as expected).")
            logger.info(err)
            return None, start_request_time-receipt_time

        if "message" in rdict.keys():
            if "rate limit" in rdict["message"]:
                if "secondary" in rdict["message"]:
                    logger.warning(
                        "Secondary rate limit hit -- this indicates that GitHub has identified unusual scraper activity. Scraping should be paused.")
                    self.sleep_and_update(
                        SECONDARY_RATE_LIMIT_WAIT, reason="Secondary rate limit reached")

            if "Bad credentials" in rdict["message"]:
                logger.warning(
                    "Bad credentials: the authentication token provided is not valid. Please provide a valid token.")
                logger.info("Scraping will proceed unauthenticated.")
                self.set_token(None)
                return self.get_request(query=query, payload=payload, headers=headers, proxy=proxy)

        # Check rate limits, handle according to https://docs.github.com/en/rest/using-the-rest-api/
        if remaining_rate_limit == 0:
            time_to_reset = rate_limit_reset_time - float(time.time()) + 1
            logger.info("Rate limit (%s) reached. Crawler %s will sleep for %ss. ",
                        total_rate_limit, self.parent_workerid, round(time_to_reset, 2))
            self.sleep_and_update(time_to_reset, reason="Rate limit reached")
            # TODO: swap tokens or proxies?
            # retry after timeout
            if not r.ok:
                return self.get_request(query, payload=payload, headers=headers, proxy=proxy)

        if "Retry-After" in r.headers:
            # handles circumstances where rate limit has not been respected: wait for Retry-After seconds
            retry_time = 0
            try:
                retry_time = int(r.headers["Retry-After"])
            except:
                logger.warning(
                    "Warning: was not able to extract Retry-After time from headers (text is '%s')", r.headers["Retry-After"])
                retry_time = RATE_LIMIT_WAIT

            # just in case the scraper has already slept for remaining_rate_limit time
            time_since_response = int(time.time()) - receipt_time
            time_to_sleep = max(0, retry_time - time_since_response)
            if time_to_sleep > 0:
                # +1 is to round up and ensure not hitting rate limit
                logger.info("Github refused connection due to rate limit, sleeping for %ss", round(
                    time_to_sleep+1, 2))
                self.sleep_and_update(
                    time_to_sleep+1, reason="Rate limit reached (2)")
                return self.get_request(query, payload=payload, headers=headers, proxy=proxy)

        # Send an error if the response is bad, in a way not caught by above code
        if not r.ok:
            logger.error(
                "Crawler request was UNSUCCESSFUL (status code %s). Query: %s", r.status_code, query)

            return None, start_request_time-receipt_time
        
        elapsed_time = round(float(time.time()) - start_request_time, 2)
        return r, elapsed_time

    def sleep_and_update(self, duration, reason=""):
        '''Puts the crawler to sleep, and provides regular updates on when the crawler will awake. 
        A reason for the sleep may be optionally displayed.
        Intended for wait times that are potentially quite long.'''

        time_left = duration
        while (time_left > 0):
            logger.info("Crawler %s will wake up in %ss (%sm). Reason for sleep: %s",
                        self.parent_workerid, round(time_left, 2), round(time_left/60, 1), reason)
            time.sleep(min(RATE_LIMIT_UPDATE_INTERVAL, time_left))
            time_left -= RATE_LIMIT_UPDATE_INTERVAL
        logger.info("Crawler %s done sleeping, resuming activity...",
                    self.parent_workerid)

    def set_token(self, token):
        ''' Sets the token and updates headers accordingly. '''
        self.token = token
        if not self.token:
            logger.warning('''No Token is set. Scraper will be severely rate-limited\n.
                                  Please configure a PAT and add it to secrets.env as GITHUB_TOKEN, and then restart.''')
            self.auth_headers = {}
        else:
            self.auth_headers = {
                "Authorization": f"Bearer {self.token}",
            }
        # self.token_checker = TokenChecker(self.auth_headers)


class Scraper(BasicWorker):
    '''
    scraper class, wrap all github operation
    '''

    # def __init__(self, rabbitmq_port, rabbitmq_host, workerid, data_source: DataSource):
    def __init__(self, settings: ScraperSettings, workerid: int):
        
        logger.info("Booting scraper %s", workerid)
        super().__init__(settings.name, settings.mq_host,
                         settings.mq_port, worker_type=WorkerType.Scraper)
        if settings.source != ScrapeSource.GITHUB:
            logger.error(
                "Scrape source %s not defined: defaulting to setting up a GitHub source", settings.source)

        # as GitHub is the only valid source right now, we fallback to this in any case.
        # If more sources are added, go ahead and put this into its own if statement.
        self.data_source = GithubRepositories(
            workerid,
            git_token=settings.git_token,
            qualifiers={
                "language:c++"
            },   # TODO extract somewhere
            crawl_time_start=settings.default_start_time,
            crawl_time_end=settings.default_end_time,
            crawl_time_interval=settings.interval,
            proxies=[]  # TODO extract somewhere
        )
        self.data_source.parent_workerid = workerid

        # Set up messaging
        self.rabbitmq_port = settings.mq_port
        self.rabbitmq_host = settings.mq_host
        self.repocache = []
        self.workerid = workerid
        self.total_repos_sent = 0
        self.scrape_queue = MQQueue( name=InputQueue.SCRAPE )  # should this be a class var?

        self.ready = not settings.wait_for_config
        self.policy = settings.default_policy
        self.bundle_requested = False 
        # set to True when a message is received from coordinator requesting a bundle, OR when SCRAPER_REPO_BUNDLESIZE repos are
        # collected, depending on current policy

    def send_bundle(self):
        conn: Connection = self.mq_client.get_connection(f'{self}')

        if conn is None:
            conn: Connection = self.mq_client.create_connection(conn_name=f'{self}',
                                                                channel_name=f'{self}')
            conn.create_channel()

        if len(self.repocache) == 0:
            logger.warning("Empty bundle requested")
            return 0
        bundle = ScraperDataOutBundle(self.repocache)
        conn.send_msg(
            queue=self.scrape_queue, 
            msg=bundle.to_json(),
            corr_id=self.uuid
        )
        self.total_repos_sent += len(self.repocache)
        logger.info("Scraper %s bundled and sent %s repos to coordinator. Total repos sent by this scraper: %s",
                    self.workerid, len(self.repocache), self.total_repos_sent)
        self.repocache = []
        return 1  # does nothing but indicate successful execution for testing

    def control_message_handler(self, ch, method, props, body):
        '''
            Handle setup and config changes sent from coordinator
        '''
        
        msg = ScraperControlTaskOut.from_json(body)
        if msg.specific_recipient and props.correlation_id != self.uuid:
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            return
        

        if isinstance(self.data_source, GithubRepositories):
            
            self.handle_in_messages_github(ch, method, props, msg)

        else:
            logger.warning(f"Error: data source of type {type(self.data_source)} does not have defined behavior for responding to coordinator setup msg")

    def handle_in_messages_github(self, ch, method, props, msg):
        '''
            Handles valid messages for scrapers with a GitHub data source
        '''

        
        if msg.message_type in [ScraperMsgType.SETUP, ScraperMsgType.UPDATE] :
            if msg.start_time != None:
                self.data_source.crawl_time_start = msg.start_time
            if msg.end_time != None:
                self.data_source.crawl_time_end = msg.end_time
            if msg.policy != None:
                self.policy = msg.policy
            debug_starttime = datetime.utcfromtimestamp(self.data_source.crawl_time_start).strftime('%Y-%m-%d')
            debug_endtime = datetime.utcfromtimestamp(self.data_source.crawl_time_end).strftime('%Y-%m-%d')
            self.ready = True
            if msg.message_type == ScraperMsgType.SETUP and DEBUG_SHOW_ALL_MESSAGES_SCRAPER:
                logger.debug(f"Configured scraper {self.workerid} to start scraping backward from {debug_endtime} to {debug_starttime} on policy '{self.policy}'")

        elif msg.message_type == ScraperMsgType.REQUEST_REPOS:
            # should we restrict this type of message to only being handled by scrapers with the on_request policy?
            self.bundle_requested = True
            if DEBUG_SHOW_ALL_MESSAGES_SCRAPER:
                logger.info(f"Repo bundle request accepted by scraper {self.workerid}")
            

        ch.basic_ack(delivery_tag=method.delivery_tag)

    def run_ctrl(self):
        '''
        Sends a message to the coordinator, signaling that it's ready to receive config info
        Then it waits for a response and sets its variables accordingly.
        '''
        try:
            
            while True: 
                
                conn: Connection = self.mq_client.create_connection(conn_name=f'{self}-ctrl',
                                                                    channel_name=f'{self}-ctrl')
                conn.create_channel()

                msg = ScraperControlTaskIn(ScraperMsgType.SETUP)
                send_queue = MQQueue(InputQueue.SCRAPER_REG)
                conn.send_msg(
                    queue=send_queue, msg=msg.to_json(), corr_id=self.uuid
                )
                
                self.mq_client.start_consumer(conn=conn, queue=self.control_queue_in, retry_delay=1)

                logger.warning(f"Consume control on scraper {self.uuid} has finished.")

                time.sleep(5)
            
        except Exception as e:
            logger.error(f"Failed to create scraper control thread, exception: {e}")
            

    def run_job(self):
        '''
        Acquires repository information and sends it to coordinator on "scrape" queue until task completed
        '''

        try:
            while (not self.ready):
                time.sleep(0.1)
                if not self.ready:
                    logger.info(f"Scraper {self.workerid} waiting for config....")

            logger.info(f"Scraper {self.workerid} started.")

            for repo in iter(self.data_source):
                # continually collect repositories, sending when the SCRAPER_REPO_BUNDLESIZE has been satisfied
                self.repocache.append(repo)  # don't put this in any if statements, to avoid losing repos

                if self.policy == ScraperOutputPolicy.CONTINUOUS:
                    if len(self.repocache) >= SCRAPER_REPO_BUNDLESIZE:
                        self.send_bundle()

                # continually collect repositories up to SCRAPER_REPO_BUNDLESIZE, then sleep until bundle requested
                elif self.policy == ScraperOutputPolicy.ON_REQUEST:

                    # If repositories have been collected and were requested, send them
                    if self.bundle_requested and len(self.repocache) > 0:
                        self.send_bundle()
                        self.bundle_requested = False
                    
                    elif len(self.repocache) >= SCRAPER_REPO_BUNDLESIZE: 

                        if len(self.repocache) == SCRAPER_REPO_BUNDLESIZE:
                            logger.info(f"Scraper {self.workerid} has collected max number of repos ({SCRAPER_REPO_BUNDLESIZE}). Sleeping until request to send is received from coordinator.")

                        while len(self.repocache) >= SCRAPER_REPO_BUNDLESIZE:
                            # wait until another request is received
                            time.sleep(0.1)
                            
                            if self.bundle_requested:
                                self.send_bundle()
                                self.bundle_requested = False
                
            logger.info("Crawler %s End Task", self.workerid)
            # deletes the last crawled time at conclusion of task
        except Exception as e:
            logger.error(f"Failed to Launch Scraper {self} - {e}")

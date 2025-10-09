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
from assemblage.worker.mq import MessageClient
from assemblage.analyze.tokenchecker import TokenChecker
from assemblage.analyze.analyze import get_build_system

SEARCH_RATE_LIMIT = 30
RATE_LIMIT = 5000
from assemblage.consts import (
    SCRAPER_TIMESTAMP_RECORDFILE_PATH, OLDEST_PERMITTED_DATA_TIMESTAMP, SCRAPER_PAGE_SIZE, 
    GITHUB_REPO_URL, SCRAPER_REQUEST_TIMEOUT_S, SCRAPER_REPO_BUNDLESIZE,
    SCRAPER_RATE_INTERVAL, QUERY_RATE_LIMIT_TIME, SCRAPER_RATE_LIMIT, 
    RATE_LIMIT_WAIT, SECONDARY_RATE_LIMIT_WAIT
)

logger = logging.getLogger(__name__)


def github_time_to_mysql_time(gtime: str):
    ''' change the format of time we havevest from github to mysql date string '''
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
        # TODO: remove this hard coded path
        self.record_file = "/binaries/crawled.json"
        self.workerud = os.urandom(4).hex()

    @abstractclassmethod
    def fetch_data(self):
        """ fetch one repository from data source, return a (repository, files_in_repo) generator  """

    @abstractclassmethod
    def data_filter(self, repo,  files):
        """ take a repo and files in repo, check if its valid or need to be discarded"""
        return True

    def init(self):
        """ delay initialization, user must call this before use """
        if not os.path.exists(self.record_file):
            with open(self.record_file, "w") as record_file:
                json.dump([int(time.time())], record_file, indent=4)

    def check_cache(self, interval):
        """ update time cache """
        while os.path.exists(self.record_file+'.lock'):
            time.sleep(0.25)
        f = open(self.record_file+'.lock', 'w')
        f.close()
        try:
            with open(self.record_file, "r") as record_file:
                crawled = json.load(record_file)
        except:
            crawled = [int(time.time())]
        oldtime = int(crawled[0])
        newtime = int(crawled[0] - interval)
        with open(self.record_file, "w") as record_file:
            json.dump([newtime], record_file, indent=4)
        try:
            os.remove(self.record_file+'.lock')
        except:
            pass
        return oldtime

    def __iter__(self):
        for r, fs in self.fetch_data():
            if self.data_filter(r, fs):
                yield r


class GithubTimeOrder(Enum):
    CREATED = "created"
    PUSHED = "pushed"


class GithubRepositories(DataSource):
    """ a data generator for Windows c repositories """

    def __init__(self, git_token, qualifier, crawl_time_start, crawl_time_interval,
                 proxies, sort=GithubTimeOrder.CREATED, order="",
                 build_sys_callback=get_build_system) -> None:
        super().__init__(build_sys_callback)
        self.token = git_token
        # self.lang = lang
        self.qualifier = qualifier
        self.crawl_time_interval = crawl_time_interval
        self.crawl_time_start = crawl_time_start
        self.proxies = proxies
        self.query_pile = int(time.time())//QUERY_RATE_LIMIT_TIME # part of the rate limiting check code
        self.sort = sort
        self.order = order
        self.queries = 0
        self.workerid = os.urandom(4).hex()
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
        time.sleep(5)
        url = repo["url"]
        self.query_limit()
        # Avoid secondary rate limit
        default_branch = repo["default_branch"]
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

                interval = 60  # interval of logging updates
                logger.info("Crawler %s rate limit, sleep %ss", self.workerid,
                            sleep_remaining)
                # to give updates on remaining sleep ...
                while sleep_remaining > 0:
                    sleep_chunk = min(RATE_LIMIT_WAIT, sleep_remaining)
                    logger.info("Crawler %s sleeping due to hitting rate limit... %ds remaining",
                                self.datsourceid, sleep_remaining)
                    time.sleep(sleep_chunk)
                    sleep_remaining -= sleep_chunk

                page = requests.get(url + f"/git/trees/{default_branch}",
                                    headers=self.auth_headers, proxies=self.random_proxy(), timeout=10)
        except Exception as err:
            logger.info(err)
            return None, None
        repo_page = json.loads(page.text)
        files_list = []
        files = []
        if "tree" in repo_page.keys():
            files_list = repo_page["tree"]
        else:
            return None, None
        for record in files_list:
            if "path" in record.keys():
                files.append(record["path"])
        build_tool = self.build_sys_callback(files)
        # logger.info("Crawler got %s, %s in pool", build_tool, len(self.repocache))
        name = repo["name"]
        url = repo["url"]
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
        crawl_time = self.crawl_time_start
        while crawl_time > 1262322000:
            logger.info("Crawler checking %s",
                        datetime.utcfromtimestamp(crawl_time).isoformat())
            crawl_time = self.check_cache(self.crawl_time_interval)
            time_start = datetime.utcfromtimestamp(crawl_time).isoformat()
            time_end = datetime.utcfromtimestamp(
                crawl_time + self.crawl_time_interval).isoformat()
            qualifier_str = ""
            for qf in self.qualifier:
                qualifier_str += f"{qf} "
            query_s = f'{self.sort.value}:{time_start}+08:00..{time_end}+08:00 {qualifier_str}'
            total_count = 999
            payload = {'q': query_s,
                       'per_page': self.page_size, 'page': -1}
            while payload['page'] * self.page_size < total_count:
                try:
                    payload['page'] += 1
                    before = int(time.time())
                    r = requests.get("https://api.github.com/search/repositories",
                                     payload,
                                     headers=self.auth_headers, proxies=self.random_proxy(), timeout=10)
                    after = int(time.time())
                    logger.info(
                        "Crawler request respond in %ss",  after-before)
                    rdict = json.loads(r.text)
                    while "message" in rdict.keys() and "rate limit" in rdict["message"]:
                        logger.info("Crawler got %s",
                                    rdict["message"].replace("/n", ""))
                        time.sleep(60)
                        if "secondary" in rdict["message"]:
                            time.sleep(60)
                        before = int(time.time())
                        r = requests.get("https://api.github.com/search/repositories",
                                         payload,
                                         headers=self.auth_headers, proxies=self.random_proxy(), timeout=10)
                        after = int(time.time())
                        logger.info(
                            "Crawler request respond in %ss with rate limit", after-before)
                        rdict = json.loads(r.text)
                    if 'items' in rdict.keys():
                        total_count = min(rdict["total_count"], total_count)
                        logger.info("Crawler query: %s page:%s, GitHub respond %s repos",
                                    payload['page'], time_start[:-7], total_count)
                        repos_per_page = rdict["items"]
                        for repo in repos_per_page:
                            logger.info("Crawler got %s", repo["name"])
                            dt, fs = self._process_repo_message(repo)
                            if dt and fs:
                                yield dt, fs
                except Exception as err:
                    logger.info(err)
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
        super().__init__(rabbitmq_host, rabbitmq_port, None, "scraper",
                         -1)
        self.data_source = data_source
        self.data_source.init()
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
        self.sent = 0

    def run(self):
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
                    self.mq_client = MessageClient(
                        self.rabbitmq_host, self.rabbitmq_port, 'scraper')
                    self.mq_client.add_output_queues([{
                        'name': 'scrape',
                        'params': {
                            'durable': True
                        }
                    }])

        logger.info("Crawler %s End Task", self.workerid)
        os.remove(self.record_file)

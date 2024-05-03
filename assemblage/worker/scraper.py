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
import logging
import os
import time
import json
from datetime import datetime
import threading
import random

import requests

from assemblage.worker.base_worker import BasicWorker
from assemblage.worker.mq import MessageClient
from assemblage.analyze.tokenchecker import TokenChecker
from assemblage.analyze.analyze import get_build_system

SEARCH_RATE_LIMIT = 30
RATE_LIMIT = 5000


def github_time_to_mysql_time(gtime: str):
    ''' change the format of time we havevest from github to mysql date string '''
    try:
        dt = datetime.strptime(gtime, '%Y-%m-%dT%H:%M:%SZ')
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except:
        return datetime.strptime(
            "2000-01-01T01:01:01Z",
            '%Y-%m-%dT%H:%M:%SZ').strftime('%Y-%m-%d %H:%M:%S')


class Scraper(BasicWorker):
    '''
    scraper class, wrap all github operation
    '''

    def __init__(self, rabbitmq_port, rabbitmq_host, tmp_dir, git_token, lang,
                 workerid, sln_only, crawl_time_start, crawl_time_interval, crawl_time_lap, proxies):
        # TODO: refactor here make scraper connect to gRPC control port
        logging.info("Booting crawler %s", workerid)
        super().__init__(rabbitmq_host, rabbitmq_port, None, "scraper",
                         -1)
        self.token = git_token
        self.lang = lang
        self.token_checker = TokenChecker()
        self.page_size = 100
        self.tmp_dir = os.path.realpath(tmp_dir)
        self.rabbitmq_port = rabbitmq_port
        self.rabbitmq_host = rabbitmq_host
        self.mq_client = MessageClient(rabbitmq_host, rabbitmq_port, 'scraper')
        self.mq_client.add_output_queues([{
            'name': 'scrape',
            'params': {
                'durable': True
            }
        }])
        self.crawl_time_start = crawl_time_start
        self.repocache = []
        self.workerid = workerid
        self.queries = 0
        self.query_pile = int(time.time())//3600
        self.sent = 0
        self.bundle_number = 100
        self.sln_only = sln_only
        self.proxies = proxies
        self.crawl_time_interval = crawl_time_interval
        self.crawl_time_lap = crawl_time_lap
        self.record_file = "/binaries/crawled.json"
        if "" not in self.proxies:
            self.proxies.append("")
        if not os.path.exists(self.record_file):
            with open(self.record_file, "w") as record_file:
                json.dump([int(time.time())], record_file, indent=4)

    def check_crawled(self, interval):
        while os.path.exists('crawled.json.lock'):
            time.sleep(0.25)
        f = open('crawled.json.lock', 'w')
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
            os.remove('crawled.json.lock')
        except:
            pass
        return oldtime

    def random_proxy(self):
        proxy = random.choice(self.proxies)
        if self.proxies == []:
            return None
        return {
            'https': proxy,
        }

    def query_limit(self):
        if int(time.time())//3600 != self.query_pile:
            self.query_pile = int(time.time())//3600
            self.queries = 0
        if self.queries > RATE_LIMIT:
            logging.info("Worker %s idle soon", self.workerid)
            time.sleep(3600-int(time.time()) % 3600)
            self.queries = 0

    def send_repo(self, repo):
        ''' scrape project folder and check if build file exists '''
        try:
            url = repo["url"]
            self.query_limit()
            # Avoid secondary rate limit
            default_branch = repo["default_branch"]
            if self.sln_only:
                try:
                    page = requests.get(url + f"/git/trees/{default_branch}",
                                        auth=("", self.token), proxies=self.random_proxy(), timeout=10)
                    if "secondary rate limit" in page.text:
                        logging.info(page.text.replace("\n", ""))
                        time.sleep(120)
                        page = requests.get(url + f"/git/trees/{default_branch}",
                                            auth=("", self.token), proxies=self.random_proxy(), timeout=10)
                    elif "rate limit" in page.text:
                        logging.info("Crawler %s rate limit, sleep %ss", self.workerid,
                                    self.token_checker.rate_reset("", self.token))
                        time.sleep(self.token_checker.rate_reset("", self.token))
                        page = requests.get(url + f"/git/trees/{default_branch}",
                                            auth=("", self.token), proxies=self.random_proxy(), timeout=10)
                except Exception as err:
                    logging.info(err)
                    return
                repo_page = json.loads(page.text)
                files_list = []
                files = []
                if "tree" in repo_page.keys():
                    files_list = repo_page["tree"]
                else:
                    return
                for record in files_list:
                    if "path" in record.keys():
                        files.append(record["path"])
                build_tool = get_build_system(files)
            # logging.info("Crawler-%s got %s, %s in pool",
            #              self.workerid, build_tool, len(self.repocache))
            name = repo["name"]
            url = repo["url"]
            language = repo["language"]
            owner_id = repo["owner"]["id"]
            description = repo["description"] or ""
            created_at = github_time_to_mysql_time(repo["created_at"])
            updated_at = github_time_to_mysql_time(repo["pushed_at"])
            size = int(repo['size'])
            self.repocache.append({
                'name': name,
                'url': url,
                'language': language,
                'owner_id': owner_id,
                'description': description[:200],
                'created_at': created_at,
                'updated_at': updated_at,
                'size': size,
                'build_system': build_tool
            })
        except Exception as err:
            logging.info(err)
        while len(self.repocache) >= self.bundle_number:
            try:
                self.mq_client.send_kind_msg(
                    'scrape', json.dumps(self.repocache))
                self.repocache = []
                self.sent += self.bundle_number
                logging.info("Crawler %s sent %s repos, total: %s",
                             self.workerid, self.bundle_number, self.sent)
            except Exception as err:
                logging.info("Sending repos errored: %s", str(err))
                self.mq_client = MessageClient(
                    self.rabbitmq_host, self.rabbitmq_port, 'scraper')
                self.mq_client.add_output_queues([{
                    'name': 'scrape',
                    'params': {
                        'durable': True
                    }
                }])

    def run(self):
        crawl_time = self.crawl_time_start
        while crawl_time > 1262322000:
            # logging.info("Crawler %s checking %s", self.workerid, datetime.utcfromtimestamp(crawl_time).isoformat())
            crawl_time = self.check_crawled(self.crawl_time_interval)
            time_start = datetime.utcfromtimestamp(crawl_time).isoformat()
            time_end = datetime.utcfromtimestamp(
                crawl_time + self.crawl_time_interval).isoformat()
            if self.sln_only:
                query_s = f'created:{time_start}+08:00..{time_end}+08:00 language:{self.lang}'
            else:
                query_s = f'created:{time_start}+08:00..{time_end}+08:00 language:{self.lang}'
            total_count = 999
            payload = {'q': query_s,
                        'per_page': self.page_size, 'page': -1}
            while payload['page'] * self.page_size < total_count:
                try:
                    payload['page'] += 1
                    before = int(time.time())
                    r = requests.get("https://api.github.com/search/repositories",
                                        payload,
                                        auth=("", self.token), proxies=self.random_proxy(), timeout=10)
                    after = int(time.time())
                    logging.info("Crawler %s request respond in %ss",
                                self.workerid, after-before)
                    rdict = json.loads(r.text)
                    while "message" in rdict.keys() and "rate limit" in rdict["message"]:
                        logging.info("Crawler %s got %s", self.workerid,
                                    rdict["message"].replace("/n", ""))
                        time.sleep(360)
                        if "secondary" in rdict["message"]:
                            time.sleep(360)
                        before = int(time.time())
                        r = requests.get("https://api.github.com/search/repositories",
                                        payload,
                                        auth=("", self.token), proxies=self.random_proxy(), timeout=10)
                        after = int(time.time())
                        logging.info(
                            "Crawler %s request respond in %ss with rate limit", self.workerid, after-before)
                        rdict = json.loads(r.text)
                    if 'items' in rdict.keys():
                        total_count = min(rdict["total_count"], total_count)
                        logging.info("Crawler %s query: %s page:%s, GitHub respond %s repos",
                                    self.workerid, payload['page'], time_start[:-7], total_count)
                        repos_per_page = rdict["items"]
                        repos_added = set()
                        for repo in repos_per_page:
                            # if repo["url"] not in repos_added and repo["size"] > 15:
                                self.send_repo(repo)
                                repos_added.add(repo["url"])
                except Exception as err:
                    logging.info(err)
            crawl_time -= self.crawl_time_interval
            crawl_time = int(crawl_time)
        logging.info("Crawler %s End Task", self.workerid)

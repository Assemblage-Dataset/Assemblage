"""
constants definitions
"""
from enum import Enum
import os


class RuntimeEnv(str,Enum): 
    dev = "development"
    prod = "production"
 

class WorkerType(str, Enum):
    Coordinator = "coordinator"
    Builder = "builder"
    Scraper = "scraper"
    

class BuildStatus(str, Enum):
    """
    Clone and build status codes 
    See schema for defaults
    """
    #pylint: disable=too-few-public-methods
    INIT = "init"
    PROCESSING = "processing"
    FAILED = "failed"
    SUCCESS = "success"
    TIMEOUT = "timeout"
    BLACKLIST = "blacklist"
    OUTDATED_MSG = "outdated_msg"    # a message overtime, not build overtime
    EXCLUDE = "exclude"
    COMMAND_FAILED = "command_failed"
    def __str__(self):
        return self.name


# add a recloned status, for when a project is updated and then repulled, to tell buidler to reclone + rebuild, 
# but store binaries in a new dir 

class CloneStatus(str, Enum): 
    NOT_STARTED = "not_started"
    PROCESSING = "processing"
    FAILED = "failed"
    SUCCESS = "success"
    TIMEOUT = 'timeout'
    COMMAND_FAILED = "command_failed"
    def __str__(self):
        return self.name

class PriorityStatus(str, Enum):
    LOW = "low"
    MID = "medium"
    HIGH = "high"
    def __str__(self):
        return self.name
    
    
    


PING_INTERVAL = 10
SUPPORTED_BUILD = ["make", "cmake", "autoconf", "bootstrap", "sln"]
SUPPORTED_LANGUAGE = ['c', 'c++', 'c#']

BIN_DIR = '/binaries'
WORKER_TIMEOUT_THRESHOLD = 600
TASK_TIMEOUT_THRESHOLD = 600

# set this to max worker size for one build type
MAX_MQ_SIZE = 3600

RATELIMIT_URL = "https://api.github.com/rate_limit"

# Windows related constants
LOG_FILE = "assemblage.log"
if os.name=="nt":
    BINPATH = "C:/binaries"
else: 
    BINPATH = "/binaries"


PDBPATH = f"{BINPATH}/Pdbs" 
BUILDPATH = "Builds" # is this used
PDBJSONNAME = "pdbinfo.json"

AWS_AUTO_REBOOT_PREFIX = "auto-worker"
REPO_SIZE_THRESHOLD = 50

# Some debug constants. TODO: use the 'debug' vs 'info' states of the logger instead
DEBUG_SHOW_ALL_MESSAGES_SCRAPER = False  # set to False to quiet some messages


# Scraper constants
SCRAPER_TIMESTAMP_RECORDFILE_PATH = "/binaries/crawled.json"

SCRAPER_RATE_INTERVAL = 5 
# Controls how long between scrapings. Setting to 0 may result in the scraper choking other units.

SCRAPER_REPO_BUNDLESIZE = 10
# Controls how many repositories should be sent to the coordinator at a time. Lower = lower latency.

SCRAPER_PAGE_SIZE = 100 
# how many repos per page of search results for the crawler. Unlikely to require changing

#OLDEST_PERMITTED_DATA_TIMESTAMP = 1262322000 # (Jan 1 2010) crawler terminates when it's reached this timestamp
GITHUB_REPO_URL = "https://api.github.com/search/repositories"
SCRAPER_REQUEST_TIMEOUT_S = 10 # timeout when waiting for HTTP request reply in seconds

# SCRAPER_RATE_LIMIT = 5000 # how many queries are predicted to be permitted in a QUERY_RATE_LIMIT_TIME interval
# QUERY_RATE_LIMIT_TIME = 3600 # how often the query limit refreshes: default 1 hour

# How long to wait when a rate limit is hit before resuming operation
RATE_LIMIT_WAIT = 60
SECONDARY_RATE_LIMIT_WAIT = 120
RATE_LIMIT_UPDATE_INTERVAL = 60


# RabbitMQ / MessageClient consts

CHANNEL_HEARTBEAT = 500
CHANNEL_TIMEOUT = 350
CHANNEL_CONNECTION_ATTEMPTS = 35
CHANNEL_RETRY_DELAY = 20


# Coordinator constants

DISPATCH_INTERVAL = 0.1  # time between attempting dispatchs. 
# 0 is OK except when there is a large dispatch backlog and all workers are on the same machine,
# as working through the dispatch backlog might choke other workers

IDLE_DISPATCH_INTERVAL = 5  # when no dispatches are found, how long to wait until trying again

CLEAN_OVERTIME_INTERVAL = 600 
AWS_REBOOT_SLEEP_INTERVAL = 1200

COORDINATOR_DATABASE_SYNC_TIMEOUT = 10  
# If the coordinator may be reading an outdated entry in the database (see recv_build_info),
# wait for these many seconds for the database to update before continuing

'''
For the context of the direction of queues. It is from the perspective of the coordinator
i.e inputs for the cooordinator, will be outputs for the workers

'''
class InputQueue(str, Enum):
    CLONE = 'clone'
    SCRAPE = 'scrape'
    BUILD = 'build'
    BINARY = 'binary'
    POST_ANALYSIS = 'post_analysis'
    BUILD_REG = 'builder_reg'
    def __str__(self):
        # to ensure compatibility with the areas where I haven't replaced channels with enums yet
        return self.value 

class OutputQueue(str, Enum):
    BUILDER_CTRL = "builder_ctrl"
    SCRAPER_CTRL = "scraper_ctrl"
    BUILD_OPT = "build_opt" 
    def __str__(self):
        return self.value
# Used by the scraper to name a valid source of data (currently just from GitHub)
class ScrapeSource(str, Enum):
    GITHUB = "github"
    def __str__(self):
        return self.name


class GithubTimeOrder(Enum):
    CREATED = "created"
    PUSHED = "pushed"
    
class SupportedPlatform(str,Enum):
    WINDOWS = 'windows'
    LINUX = 'linux'

# support optimization levels, roughly translated/made generic
class OptLevel(Enum):
    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    def __str__(self):
        return f"opt_{self.name}"
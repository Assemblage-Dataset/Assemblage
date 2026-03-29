"""
constants definitions
"""
from enum import Enum
import os


class RuntimeEnv(str, Enum):
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
    # pylint: disable=too-few-public-methods
    INIT = "init"
    PROCESSING = "processing"
    FAILED = "failed"
    SUCCESS = "success"
    TIMEOUT = "timeout"  # i think unused
    BLACKLIST = "blacklist"  # unused
    OUTDATED_MSG = "outdated_msg"    # a message overtime, not build overtime
    EXCLUDE = "exclude"  # unused
    COMMAND_FAILED = "command_failed"  # unused
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
    COMMAND_FAILED = "command_failed"  # unused
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

# TODO do we need both BIN_DIR and BINPATH?
BIN_DIR = '/binaries'
#WORKER_TIMEOUT_THRESHOLD = 600
TASK_TIMEOUT_THRESHOLD = 600

# set this to max worker size for one build type
MAX_MQ_SIZE = 3600

# RATELIMIT_URL = "https://api.github.com/rate_limit"

# Windows related constants
LOG_FILE = "assemblage.log"
if os.name == "nt":
    BINPATH = "C:/binaries"
else:
    BINPATH = "/binaries"


PDBPATH = f"{BINPATH}/Pdbs"
BUILDPATH = "Builds"  #  is this used
PDBJSONNAME = "pdbinfo.json"

AWS_AUTO_REBOOT_PREFIX = "auto-worker"
REPO_SIZE_THRESHOLD = 50


SCRAPER_RATE_INTERVAL = 0.01
# Controls how long between scrapings. Setting to 0 may result in the scraper choking other units if they have CONTINUOUS policy.

SCRAPER_REPO_BUNDLESIZE = 25
# Controls how many repositories should be sent to the coordinator at a time. Lower = lower latency.

SCRAPER_PAGE_SIZE = 100
# how many repos per page of search results for the crawler. Unlikely to require changing

GITHUB_REPO_URL = "https://api.github.com/search/repositories"
# timeout when waiting for HTTP request reply in seconds
SCRAPER_REQUEST_TIMEOUT_S = 10

# How long to wait when a rate limit is hit before resuming operation, if it cannot be extracted from error message
RATE_LIMIT_WAIT = 60
SECONDARY_RATE_LIMIT_WAIT = 120

# How often to print update messages (that essentially say "still sleeping") to console
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

IDLE_DISPATCH_INTERVAL = 30  # when no dispatches are found, how long to wait until trying again
WAIT_AFTER_REQ_INTERVAL = 1  # How long to wait after requesting a dispatch before continuing

CLEAN_OVERTIME_INTERVAL = 600  # how often the __clean_overtime thread in coordinator runs
AWS_REBOOT_SLEEP_INTERVAL = 1200  # how often the __reboot_worker thread in coordinator runs

COORDINATOR_DATABASE_SYNC_TIMEOUT = 10
# If the coordinator may be reading an outdated entry in the database (see recv_build_info),
# wait for these many seconds for the database to update before continuing

COORDINATOR_REPO_REQUEST_THRESHOLD = 10
# How many repositories the coordinator will identify as "too few" and request more to be scraped
# (for scrapers with the ON_REQUEST policy). Should be >= number of replicas per buildopt

COORDINATOR_REPO_REQUEST_TIMEOUT = 60
# If the coordinator waits more than this many seconds to get a repo bundle, 
# resend the request

'''
For the context of the direction of queues. It is from the perspective of the coordinator
i.e inputs for the cooordinator, will be outputs for the workers

'''


class InputQueue(str, Enum):
    ''' Queues that the coordinator consumes from '''
    CLONE = 'clone'
    SCRAPE = 'scrape'
    BUILD = 'build'
    BINARY = 'binary'
    POST_ANALYSIS = 'post_analysis'
    BUILD_REG = 'builder_reg'
    SCRAPER_REG = 'scraper_reg'

    def __str__(self):
        # to ensure compatibility with the areas where I haven't replaced channels with enums yet
        return self.value


class OutputQueue(str, Enum):
    ''' Queues that workers consume from '''
    BUILDER_CTRL = "builder_ctrl"
    SCRAPER_CTRL = "scraper_ctrl"
    BUILD_OPT = "build_opt"

    def __str__(self):
        return self.value
# Used by the scraper to name a valid source of data (currently just from GitHub)


class ScrapeSource(str, Enum):
    ''' Used by the scraper to name a valid source of data (currently just from GitHub) '''
    GITHUB = "github"

    def __str__(self):
        return self.name


class ScraperMsgType(str, Enum):
    SETUP = "setup"
    UPDATE = "update"  # update policy/configurations
    # triggers sending/collection of repos when scraper out policy is ON_REQUEST
    REQUEST_REPOS = "request_repos"


class ScraperOutputPolicy(str, Enum):
    '''
        The policy attribute of the scraper determines the behavior with which repositories are sent to the coordinator.
        Use continuous for all scrapers for a "just works" setup, use on_request for a more controlled setup.
    '''
    CONTINUOUS = "continuous"  # works constantly, sending repos whenever it has collected SCRAPER_REPO_BUNDLESIZE
    # works until it reaches max bundle size then pauses. sends whenever it has when it receives a request from coordinator
    ON_REQUEST = "on_request"


class GithubTimeOrder(Enum):
    ''' Determines how the scraper's GitHub queries are sorted '''
    CREATED = "created"
    PUSHED = "pushed"


class SupportedPlatform(str, Enum):
    WINDOWS = 'windows'
    LINUX = 'linux'
    def __str__(self):
        return self.value

class SupportedLanguage(str, Enum):
    CPP = "c++"
    def __str__(self):
        return self.value
    
class SupportedCompiler(str, Enum):
    CLANG = "clang"
    GCC = "gcc"
    MSVC = "MSVC"
    
    def __str__(self):
        return self.value
class SupportedArchitecture(str, Enum):
    X64 = "x64"
    X86 = "x86"
    def __str__(self):
        return self.value
     
    

# support optimization levels, roughly translated/made generic


class OptLevel(Enum):
    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3

    def __str__(self):
        return self.to_gnu_opt()

    def to_msvc_opt(self):
        """MSVC optimization flags"""
        match self:
            case OptLevel.LOW:
                return "/O1"
            case OptLevel.MEDIUM:
                return "/O2"
            case OptLevel.HIGH:
                return "/Ox"
            case _:
                return "/Od"
    def to_gnu_opt(self):
        ''' clang/gcc optimization flags '''
        match self:

            case OptLevel.LOW:
                return "-O1"
            case OptLevel.MEDIUM:
                return "-O2"
            case OptLevel.HIGH:
                return "-O3"
            case _:
                return "-O0"  # default. none




# Testing constants


TEST_MESSAGE_LEVEL = 'DEBUG'
TEST_DB_ADDR = 'postgresql+psycopg2://assemblage:assemblage@assemblage-test-db:5432/assemblage'

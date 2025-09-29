"""
constant

"""
from enum import Enum, IntEnum
import os
from sre_constants import SUCCESS

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


class PriorityStatus(str, Enum):
    NOT_STARTED = "not_started"
    PROCESSING = "processing"
    FAILED = "failed"
    SUCCESS = "success"
    COMMAND_FAILED = "command_failed"

PING_INTERVAL = 10
SUPPORTED_BUILD = ["make", "cmake", "autoconf", "bootstrap", "sln"]
SUPPORTED_LANGUAGE = ['c', 'c++', 'c#']

BIN_DIR = '/binaries'
WORKER_TIMEOUT_THRESHOLD = 600
TASK_TIMEOUT_THRESHOLD = 600

# set this to max worker size for one build type
MAX_MQ_SIZE = 3600

# DB for metadata, repo statuses
DB_PATH = "data/ghtorrent.db"

DEST = "~/repos"

G = "b89581cf542519aa28b2e0904fbefd31ae67c19a"

RATELIMIT_URL = "https://api.github.com/rate_limit"
SCRAPE_CHECKPOINT = f"{BIN_DIR}/scrape-checkpoint"

# Windows related constants
LOG_FILE = "assemblage.log"
BINPATH = "/Binaries"
if os.name=="nt":
    BINPATH = "Binaries"

PDBPATH = "Pdbs"
BUILDPATH = "Builds"
PDBJSONNAME = "pdbinfo.json"
WIN_PREFIX = "C:\\Assemblage\\repo-scraper-builder\\Binaries\\"

AWS_AUTO_REBOOT_PREFIX = "auto-worker"
REPO_SIZE_THRESHOLD = 50
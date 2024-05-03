"""
constant

"""


class BuildStatus:
    """
    Clone and build status codes
    See schema for defaults
    """
    #pylint: disable=too-few-public-methods
    INIT = 0
    PROCESSING = 1
    FAILED = 2
    SUCCESS = 3
    TIMEOUT = 4
    BLACKLIST = 5
    OUTDATED_MSG = 6    # a message overtime, not build overtime
    EXCLUDE = 7
    COMMAND_FAILED = 10


PING_INTERVAL = 10
SUPPORTED_BUILD = ["make", "cmake", "autoconf", "bootstrap", "sln"]
SUPPORTED_LANGUAGE = ['c', 'c++', 'c#']

BIN_DIR = '/binaries'
WORKER_TIMEOUT_THRESHOLD = 600
TASK_TIMEOUT_THRESHOLD = 60

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
BINPATH = "Binaries"
PDBPATH = "Pdbs"
BUILDPATH = "Builds"
PDBJSONNAME = "pdbinfo.json"
WIN_PREFIX = "C:\\Assemblage\\repo-scraper-builder\\Binaries\\"

AWS_AUTO_REBOOT_PREFIX = "assemblage-auto-worker"
REPO_SIZE_THRESHOLD = 0
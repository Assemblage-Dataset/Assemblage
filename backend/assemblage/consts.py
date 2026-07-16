"""Backwards-compatibility shim.

The canonical definitions now live in :mod:`assemblage.enums` (enumerations)
and :mod:`assemblage.constants` (tunables). This module re-exports them so that
un-ported modules keep importing ``assemblage.consts`` unchanged.

Everything under "Legacy holdouts" below is scheduled for deletion: those names
only survive here because coordinator/builder/scraper (rewritten in P6-P8) and
the integration tests still import them. This shim dies with its last consumer.
"""

import os
from enum import StrEnum

from assemblage.constants import (
    BIN_DIR,
    CHANNEL_HEARTBEAT,
    CHANNEL_TIMEOUT,
    COORDINATOR_REPO_REQUEST_THRESHOLD,
    COORDINATOR_REPO_REQUEST_TIMEOUT,
    DISPATCH_INTERVAL,
    GITHUB_REPO_URL,
    IDLE_DISPATCH_INTERVAL,
    RATE_LIMIT_UPDATE_INTERVAL,
    RATE_LIMIT_WAIT,
    SCRAPER_PAGE_SIZE,
    SCRAPER_RATE_INTERVAL,
    SCRAPER_REPO_BUNDLESIZE,
    SCRAPER_REQUEST_TIMEOUT_S,
    SECONDARY_RATE_LIMIT_WAIT,
    WAIT_AFTER_REQ_INTERVAL,
)
from assemblage.enums import (
    BuildStatus,
    CloneStatus,
    GithubTimeOrder,
    OptLevel,
    PriorityStatus,
    RuntimeEnv,
    ScraperMsgType,
    ScraperOutputPolicy,
    ScrapeSource,
    SupportedArchitecture,
    SupportedCompiler,
    SupportedLanguage,
    SupportedPlatform,
    WorkerType,
)

__all__ = [
    "AWS_AUTO_REBOOT_PREFIX",
    "AWS_REBOOT_SLEEP_INTERVAL",
    "BINPATH",
    # constants
    "BIN_DIR",
    "CHANNEL_CONNECTION_ATTEMPTS",
    "CHANNEL_HEARTBEAT",
    "CHANNEL_RETRY_DELAY",
    "CHANNEL_TIMEOUT",
    "CLEAN_OVERTIME_INTERVAL",
    "COORDINATOR_DATABASE_SYNC_TIMEOUT",
    "COORDINATOR_REPO_REQUEST_THRESHOLD",
    "COORDINATOR_REPO_REQUEST_TIMEOUT",
    "DISPATCH_INTERVAL",
    "GITHUB_REPO_URL",
    "IDLE_DISPATCH_INTERVAL",
    "MAX_MQ_SIZE",
    "RATE_LIMIT_UPDATE_INTERVAL",
    "RATE_LIMIT_WAIT",
    "REPO_SIZE_THRESHOLD",
    "SCRAPER_PAGE_SIZE",
    "SCRAPER_RATE_INTERVAL",
    "SCRAPER_REPO_BUNDLESIZE",
    "SCRAPER_REQUEST_TIMEOUT_S",
    "SECONDARY_RATE_LIMIT_WAIT",
    "TASK_TIMEOUT_THRESHOLD",
    "TEST_DB_ADDR",
    "TEST_MESSAGE_LEVEL",
    "WAIT_AFTER_REQ_INTERVAL",
    # enums
    "BuildStatus",
    "CloneStatus",
    "GithubTimeOrder",
    # legacy holdouts
    "InputQueue",
    "OptLevel",
    "OutputQueue",
    "PriorityStatus",
    "RuntimeEnv",
    "ScrapeSource",
    "ScraperMsgType",
    "ScraperOutputPolicy",
    "SupportedArchitecture",
    "SupportedCompiler",
    "SupportedLanguage",
    "SupportedPlatform",
    "WorkerType",
]


# --- Legacy holdouts ---------------------------------------------------------
# Consumed only by still-fenced modules (coordinator/builder/scraper) and the
# integration tests. Deleted alongside their consumers in P6-P8; the new mq
# topology (assemblage.mq.topology) supersedes InputQueue/OutputQueue.


class InputQueue(StrEnum):
    """Queues that the coordinator consumes from (superseded by mq.topology)."""

    CLONE = "clone"
    SCRAPE = "scrape"
    BUILD = "build"
    BINARY = "binary"
    POST_ANALYSIS = "post_analysis"
    BUILD_REG = "builder_reg"
    SCRAPER_REG = "scraper_reg"


class OutputQueue(StrEnum):
    """Queues that workers consume from (superseded by mq.topology)."""

    BUILDER_CTRL = "builder_ctrl"
    SCRAPER_CTRL = "scraper_ctrl"
    BUILD_OPT = "build_opt"


if os.name == "nt":
    BINPATH = "C:/binaries"
else:
    BINPATH = "/binaries"

TASK_TIMEOUT_THRESHOLD = 600
MAX_MQ_SIZE = 3600
REPO_SIZE_THRESHOLD = 50
CLEAN_OVERTIME_INTERVAL = 600
COORDINATOR_DATABASE_SYNC_TIMEOUT = 10
CHANNEL_CONNECTION_ATTEMPTS = 35
CHANNEL_RETRY_DELAY = 20
AWS_AUTO_REBOOT_PREFIX = "auto-worker"
AWS_REBOOT_SLEEP_INTERVAL = 1200

# Integration-test configuration (moves into tests/ when those tests are
# rewritten). The conftest refuses to run against a DB literally named
# 'assemblage' so the live corpus can never be truncated by a test.
TEST_MESSAGE_LEVEL = "DEBUG"
TEST_DB_ADDR = os.getenv(
    "TEST_DB_ADDR",
    "postgresql+psycopg2://assemblage:assemblage@assemblage-test-db:5432/assemblage",
)
